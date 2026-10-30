"""SQLite storage (WAL).

Jobs are stored as a JSON document plus the columns we query on. Events are
append-only. One connection, used from the event loop, because every statement is
small, so blocking is negligible at this scale.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from .library import replace
from .models import (
    TERMINAL_STATES,
    Candidate,
    Collection,
    DriveStats,
    Job,
    JobEvent,
    JobState,
    RipState,
    TitleInfo,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE INDEX IF NOT EXISTS jobs_drive ON jobs(drive_id, id);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS fingerprints (
    fp TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    candidate TEXT NOT NULL,
    job_id INTEGER,
    confirmed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    show_tmdb_id INTEGER NOT NULL UNIQUE,
    updated_at TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drives (
    id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
-- One row per finished job, for the all-time drive statistics. No foreign key:
-- the rows stay when job history is cleared.
CREATE TABLE IF NOT EXISTS rip_stats (
    job_id INTEGER PRIMARY KEY,
    drive_id TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    kind TEXT,
    media TEXT NOT NULL,
    mode TEXT NOT NULL,
    ripped INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    seconds REAL
);
CREATE INDEX IF NOT EXISTS rip_stats_drive ON rip_stats(drive_id);
-- Remote workers added in the UI. Only a hash of each token is kept, and a token
-- works only for its own worker id.
CREATE TABLE IF NOT EXISTS worker_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    worker_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_seen_at TEXT
);
-- Discs given a TheDiscDB release (to send, or sent), for putting the discs of one
-- box set or season into one release. Kept when job history is cleared.
CREATE TABLE IF NOT EXISTS contributions (
    job_id INTEGER PRIMARY KEY,
    release_id TEXT NOT NULL,
    group_key TEXT NOT NULL,
    disc_number INTEGER,
    assigned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contributions_group ON contributions(group_key, assigned_at);
"""


def now() -> datetime:
    return datetime.now(UTC)


class JobRecord(Job):
    """Job plus internal bookkeeping that the API does not expose."""

    titles: list[TitleInfo] = []
    quick_fp: str | None = None
    content_fp: str | None = None
    disc_ids: dict[str, str] = {}  # TheDiscDB's fingerprints, disc_id and content_hash
    scan_fp: str | None = None
    makemkv_version: str | None = None
    duplicates: list[int] = []
    notified: list[str] = []
    disc_index: int | None = None  # MakeMKV's number for the drive ("backup" takes disc:N only)

    def art_url(self) -> tuple[str | None, bool]:
        """(poster URL, proposed?) for the disc, from the confirmed identity first, then the best guess."""
        tv = self.tv
        if self.identity:
            if self.media == "tv" and tv and tv.season_poster_url:
                return tv.season_poster_url, False
            return self.identity.poster_url, False
        if self.media == "tv" and tv and tv.show:
            return tv.season_poster_url or tv.show.poster_url, True
        best = next((c for c in self.candidates if not c.excluded), None)
        return (best.poster_url, True) if best else (None, False)

    def public(self) -> Job:
        job = Job.model_validate(self.model_dump(include=set(Job.model_fields)))
        url, proposed = self.art_url()
        job.art = hashlib.sha256(url.encode()).hexdigest()[:16] if url else None
        job.art_proposed = proposed and url is not None
        return job


class Database:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.backfill_stats()

    def close(self) -> None:
        self.conn.close()

    def backup(self, dest: Path) -> None:
        """A consistent copy while in use (SQLite online backup), written atomically."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        out = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(out)
        finally:
            out.close()
        replace(tmp, dest)  # retried on Windows while a scanner has the new file open

    # --- jobs ---------------------------------------------------------------

    def create_job(self, drive_id: str, device: str) -> JobRecord:
        ts = now()
        job = JobRecord(id=0, drive_id=drive_id, device=device, state=JobState.detected,
                        created_at=ts, updated_at=ts)
        cur = self.conn.execute(
            "INSERT INTO jobs (drive_id, state, created_at, updated_at, data) VALUES (?,?,?,?,?)",
            (drive_id, job.state, ts.isoformat(), ts.isoformat(), "{}"),
        )
        job.id = cur.lastrowid
        self.save_job(job)
        return job

    def save_job(self, job: JobRecord) -> None:
        job.updated_at = now()
        self.conn.execute(
            "UPDATE jobs SET state=?, updated_at=?, data=? WHERE id=?",
            (job.state, job.updated_at.isoformat(), job.model_dump_json(), job.id),
        )

    def get_job(self, job_id: int) -> JobRecord | None:
        row = self.conn.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return JobRecord.model_validate_json(row["data"]) if row else None

    def list_jobs(self, states: list[str] | None = None, limit: int = 200) -> list[JobRecord]:
        if states:
            q = f"SELECT data FROM jobs WHERE state IN ({','.join('?' * len(states))}) ORDER BY id DESC LIMIT ?"
            rows = self.conn.execute(q, (*states, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT data FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [JobRecord.model_validate_json(r["data"]) for r in rows]

    def latest_job_for_drive(self, drive_id: str) -> JobRecord | None:
        row = self.conn.execute(
            "SELECT data FROM jobs WHERE drive_id=? ORDER BY id DESC LIMIT 1", (drive_id,)
        ).fetchone()
        return JobRecord.model_validate_json(row["data"]) if row else None

    def jobs_sending_contribution(self) -> list[int]:
        return [r[0] for r in self.conn.execute(
            "SELECT id FROM jobs WHERE json_extract(data, '$.contribution.status')='sending'")]

    def count_state(self, state: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM jobs WHERE state=?", (state,)).fetchone()[0]

    # --- events -------------------------------------------------------------

    def delete_jobs(self, ids: list[int]) -> None:
        """Jobs and their events. The disc cache (fingerprints) and collections stay:
        they describe what is in the library, not the job history."""
        marks = ",".join("?" * len(ids))
        self.conn.execute("BEGIN")
        try:
            self.conn.execute(f"DELETE FROM events WHERE job_id IN ({marks})", ids)
            self.conn.execute(f"DELETE FROM jobs WHERE id IN ({marks})", ids)
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def add_event(self, job_id: int, kind: str, message: str) -> JobEvent:
        ts = now()
        cur = self.conn.execute(
            "INSERT INTO events (job_id, ts, kind, message) VALUES (?,?,?,?)",
            (job_id, ts.isoformat(), kind, message),
        )
        return JobEvent(id=cur.lastrowid, job_id=job_id, ts=ts, kind=kind, message=message)

    def events(self, job_id: int) -> list[JobEvent]:
        rows = self.conn.execute(
            "SELECT id, job_id, ts, kind, message FROM events WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()
        return [JobEvent(**dict(r)) for r in rows]

    # --- fingerprint cache --------------------------------------------------

    def remember(self, fp: str, kind: str, candidate: Candidate, job_id: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO fingerprints (fp, kind, candidate, job_id, confirmed_at) VALUES (?,?,?,?,?)",
            (fp, kind, candidate.model_dump_json(), job_id, now().isoformat()),
        )

    def recall(self, fp: str | None) -> tuple[Candidate, str] | None:
        if not fp:
            return None
        row = self.conn.execute(
            "SELECT candidate, confirmed_at FROM fingerprints WHERE fp=?", (fp,)
        ).fetchone()
        if not row:
            return None
        return Candidate.model_validate_json(row["candidate"]), row["confirmed_at"]

    # --- drives -------------------------------------------------------------

    def drive_data(self, drive_id: str) -> dict:
        row = self.conn.execute("SELECT data FROM drives WHERE id=?", (drive_id,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def save_drive_data(self, drive_id: str, data: dict | BaseModel) -> None:
        if isinstance(data, BaseModel):
            data = data.model_dump(mode="json")
        self.conn.execute(
            "INSERT OR REPLACE INTO drives (id, data) VALUES (?, ?)", (drive_id, json.dumps(data))
        )

    def rename_drive(self, old: str, new: str) -> int:
        """Move a drive's jobs, statistics and stored data from an old id to its new one.
        Returns how many jobs and statistics rows moved."""
        c = self.conn
        c.execute("BEGIN IMMEDIATE")
        try:
            moved = c.execute("UPDATE jobs SET drive_id=?, data=json_set(data, '$.drive_id', ?) "
                              "WHERE drive_id=?", (new, new, old)).rowcount
            moved += c.execute("UPDATE rip_stats SET drive_id=? WHERE drive_id=?", (new, old)).rowcount
            old_data = self.drive_data(old)
            if old_data:
                self.save_drive_data(new, {**old_data, **self.drive_data(new)})
                c.execute("DELETE FROM drives WHERE id=?", (old,))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        return moved

    # --- remote worker tokens --------------------------------------------------

    def add_worker_token(self, name: str, worker_id: str, token_hash: str) -> dict:
        cur = self.conn.execute(
            "INSERT INTO worker_tokens (name, worker_id, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, worker_id, token_hash, now().isoformat()))
        return self.worker_token(cur.lastrowid)

    def worker_token(self, token_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM worker_tokens WHERE id=?", (token_id,)).fetchone()
        return dict(row) if row else None

    def worker_token_by_hash(self, token_hash: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM worker_tokens WHERE token_hash=?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def worker_tokens(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM worker_tokens ORDER BY id")]

    def touch_worker_token(self, token_id: int) -> None:
        self.conn.execute("UPDATE worker_tokens SET last_seen_at=? WHERE id=?", (now().isoformat(), token_id))

    def delete_worker_token(self, token_id: int) -> bool:
        return self.conn.execute("DELETE FROM worker_tokens WHERE id=?", (token_id,)).rowcount > 0

    # --- TheDiscDB releases -----------------------------------------------------

    def recent_release(self, group_key: str, since: datetime, job_id: int) -> tuple[str, int] | None:
        """The newest release of another disc in ``group_key`` assigned since ``since``,
        and how many discs it has (not counting ``job_id``)."""
        row = self.conn.execute(
            "SELECT release_id FROM contributions WHERE group_key=? AND assigned_at>=? AND job_id!=? "
            "ORDER BY assigned_at DESC, job_id DESC LIMIT 1", (group_key, since.isoformat(), job_id)).fetchone()
        if not row:
            return None
        n = self.conn.execute("SELECT COUNT(*) FROM contributions WHERE release_id=? AND job_id!=?",
                              (row["release_id"], job_id)).fetchone()[0]
        return row["release_id"], n

    def release_discs(self, release_id: str) -> list[int]:
        """Disc numbers already taken in a release."""
        return [r[0] for r in self.conn.execute(
            "SELECT disc_number FROM contributions WHERE release_id=? AND disc_number IS NOT NULL", (release_id,))]

    def assign_release(self, job_id: int, release_id: str, group_key: str, disc_number: int | None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO contributions (job_id, release_id, group_key, disc_number, assigned_at) "
            "VALUES (?,?,?,?,?)", (job_id, release_id, group_key, disc_number, now().isoformat()))

    # --- drive statistics ----------------------------------------------------

    def _rip_seconds_from_events(self, job_id: int) -> float | None:
        """For jobs from before rips were timed, the time from the last rip start to its completion."""
        rows = self.conn.execute(
            "SELECT ts, message FROM events WHERE job_id=? AND kind='rip' ORDER BY id", (job_id,)
        ).fetchall()
        start = None
        took = None
        for r in rows:
            if r["message"].startswith(("Rip complete", "Backup complete")):
                if start:
                    took = (datetime.fromisoformat(r["ts"]) - start).total_seconds()
            else:
                start = datetime.fromisoformat(r["ts"])
        return took

    def record_stats(self, job: JobRecord, replace: bool = True) -> None:
        """Count a finished job in its drive's statistics (again, if it finished before)."""
        if job.state not in TERMINAL_STATES:
            return
        ripped = job.rip.state == RipState.done
        size = job.rip.size_bytes
        if size is None and ripped and job.rip.progress:
            size = job.rip.progress.bytes_done
        seconds = job.rip.seconds
        if seconds is None and ripped:
            seconds = self._rip_seconds_from_events(job.id)
        self.conn.execute(
            f"INSERT OR {'REPLACE' if replace else 'IGNORE'} INTO rip_stats "
            "(job_id, drive_id, finished_at, outcome, kind, media, mode, ripped, bytes, seconds) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job.id, job.drive_id, (job.finished_at or job.updated_at).isoformat(), job.state.value,
             job.disc.kind.value if job.disc.kind else None, job.media.value, job.mode,
             int(ripped), (size or 0) if ripped else 0, seconds if ripped else None),
        )

    def backfill_stats(self) -> None:
        """Count finished jobs from before the statistics existed. Cheap once done, because only
        jobs without a row are read."""
        marks = ",".join("?" * len(TERMINAL_STATES))
        rows = self.conn.execute(
            f"SELECT data FROM jobs WHERE state IN ({marks}) "
            "AND id NOT IN (SELECT job_id FROM rip_stats)", [s.value for s in TERMINAL_STATES]
        ).fetchall()
        for r in rows:
            self.record_stats(JobRecord.model_validate_json(r["data"]), replace=False)

    def drive_stats(self) -> list[DriveStats]:
        """Totals per drive, for every drive that has finished a job."""
        out: dict[str, DriveStats] = {}
        for r in self.conn.execute("""
            SELECT drive_id,
                   SUM(ripped) AS discs_ripped,
                   SUM(bytes) AS data_bytes,
                   COALESCE(SUM(CASE WHEN ripped AND seconds > 0 THEN seconds END), 0) AS rip_seconds,
                   COALESCE(SUM(CASE WHEN ripped AND seconds > 0 THEN bytes END), 0) AS timed_bytes,
                   SUM(outcome='failed' AND NOT ripped) AS failed,
                   SUM(outcome='cancelled' AND NOT ripped) AS cancelled,
                   SUM(outcome='done' AND NOT ripped) AS skipped,
                   SUM(ripped AND mode='titles' AND media='movie') AS movies,
                   SUM(ripped AND mode='titles' AND media='tv') AS tv,
                   SUM(ripped AND mode='backup') AS backups,
                   MIN(finished_at) AS first_at,
                   MAX(finished_at) AS last_at
            FROM rip_stats GROUP BY drive_id ORDER BY drive_id"""):
            out[r["drive_id"]] = DriveStats(model=self.drive_data(r["drive_id"]).get("model"),
                                            connected=False, by_kind={}, **dict(r))
        for r in self.conn.execute(
                "SELECT drive_id, COALESCE(kind, 'other') AS k, COUNT(*) AS n FROM rip_stats "
                "WHERE ripped GROUP BY drive_id, k"):
            out[r["drive_id"]].by_kind[r["k"]] = r["n"]
        return list(out.values())

    # --- TV collections -------------------------------------------------------

    def _collection(self, row) -> Collection:
        c = Collection.model_validate_json(row["data"])
        c.id = row["id"]
        return c

    def collection(self, collection_id: int) -> Collection | None:
        row = self.conn.execute("SELECT id, data FROM collections WHERE id=?", (collection_id,)).fetchone()
        return self._collection(row) if row else None

    def collection_for_show(self, tmdb_id: int) -> Collection | None:
        row = self.conn.execute("SELECT id, data FROM collections WHERE show_tmdb_id=?", (tmdb_id,)).fetchone()
        return self._collection(row) if row else None

    def collections(self) -> list[Collection]:
        rows = self.conn.execute("SELECT id, data FROM collections ORDER BY updated_at DESC").fetchall()
        return [self._collection(r) for r in rows]

    def save_collection(self, c: Collection) -> Collection:
        c.updated_at = now()
        if c.id:
            self.conn.execute("UPDATE collections SET updated_at=?, data=? WHERE id=?",
                              (c.updated_at.isoformat(), c.model_dump_json(), c.id))
        else:
            cur = self.conn.execute(
                "INSERT INTO collections (show_tmdb_id, updated_at, data) VALUES (?,?,?)",
                (c.show.tmdb_id, c.updated_at.isoformat(), c.model_dump_json()))
            c.id = cur.lastrowid
        return c
