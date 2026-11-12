"""A job's debug bundle: everything ripshelf has about one job, in a zip, for working out
what went wrong.

The bundle has the job's record and timeline, the disc's titles, its MakeMKV and ffmpeg
logs, the box scans' readings, the lines of ripshelf's own log about it, the drive and
worker it ran on, the settings and the versions of everything. Whatever isn't there (a
job that never got as far as ripping, a log file that was never written) is noted in
manifest.json instead.

Secrets are taken out of everything in it, by name (settings, JSON keys, headers, URL
parameters) and by value (the keys set in Settings, wherever they turn up in a log).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import io
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import __version__, applog, proc
from .config import SECRETS, install_dir

if TYPE_CHECKING:
    from .jobs import Manager

REDACTED = "[REDACTED]"

# Files from the job's folder: text is put in (redacted), pictures only if small, the rest listed.
TEXT_SUFFIXES = {".log", ".json", ".txt", ".xml", ".csv"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
IMAGE_MAX = 512 * 1024
TEXT_MAX = 20 * 1024 * 1024  # the end of a bigger log is kept
APP_LOG_MAX_LINES = 20_000
# ripshelf's log from a little before the job started to a little after it ended.
WINDOW_MARGIN = timedelta(minutes=2)

# What ripshelf doesn't keep, so no bundle has it (listed in the manifest).
NOT_STORED = [
    "metadata lookups (provider, query, response): only the candidates they gave and their scores are kept",
    "the vision model's prompts: only its readings of the scans (scans/*.vision.json) are kept",
    "who confirmed or changed the identity: ripshelf has no user accounts; the timeline has when, and "
    "identity_source says whether it was automatic, by hand or from the disc cache",
    "the remote worker's own log: its MakeMKV output is streamed to the controller and is in the logs here",
    "ripshelf's log from before its last restart beyond the rotated log files (5 x 5 MB)",
]

# --- redaction -------------------------------------------------------------------

# Key names whose values are secrets, matched against the parts of a key split at "_"
# ("tmdb_api_key", "X-Plex-Token", "pushover_user_key").
_SECRET_PARTS = re.compile(
    r"(?:^|_)(?:api_?key|apikey|key|token|secret|password|passwd|pwd|pin|authorization|auth|cookie|"
    r"set_cookie|credentials?|private_key|client_secret|serial)(?:_|$)")
# Keys that look like secrets but are ripshelf's own ids.
_NOT_SECRET = {"show_key", "group_key", "sort_key", "token_id", "keywords"}
# A show's key ("tmdb:1399") under a bare "key".
_ID_VALUE = re.compile(r"^(?:tmdb|tvmaze|tvdb|imdb):\w+$")

_KEYS = "api[_-]?key|apikey|key|access[_-]?token|token|secret|client[_-]?secret|password|passwd|pin|sig|signature|auth"
_TEXT_RULES = [
    # URL parameters: ?api_key=..., &token=...
    (re.compile(rf"([?&;](?:{_KEYS})=)[^&\s#\"'<>]+", re.I), rf"\1{REDACTED}"),
    # Credentials in a URL: https://user:pass@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I), rf"\1{REDACTED}@"),
    # Headers, as written in a log or a dumped request.
    (re.compile(r"\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|x-plex-token|"
                r"x-emby-token|x-mediabrowser-token)(\"?\s*[:=]\s*\"?)[^\r\n\"]+", re.I), rf"\1\2{REDACTED}"),
    (re.compile(r"\b(Bearer|Basic|Token)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    # key=value and "key": "value" in a log line.
    (re.compile(r"\b((?:[a-z0-9]+[_-])*(?:api[_-]?key|apikey|token|secret|password|passwd|"
                r"client[_-]?secret|registration[_ -]?key|makemkv[_-]?key)[\"']?\s*[=:]\s*[\"']?)"
                r"[^\s\"',&}]+", re.I), rf"\1{REDACTED}"),
    # A MakeMKV registration key (T-...), or its beta key.
    (re.compile(r"\bT-[A-Za-z0-9@_]{40,}"), REDACTED),
]


def secret_key(name: str) -> bool:
    """Whether a setting, JSON key or header with this name holds a secret."""
    n = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    return n not in _NOT_SECRET and (n in SECRETS or bool(_SECRET_PARTS.search(n)))


def redact_text(text: str, values: list[str] | tuple[str, ...] = ()) -> str:
    """Secrets out of free text: the given values wherever they are, then anything that looks
    like a key, token or password."""
    for v in sorted({v for v in values if v and len(v) >= 4}, key=len, reverse=True):
        text = text.replace(v, REDACTED)
    for pattern, repl in _TEXT_RULES:
        text = pattern.sub(repl, text)
    return text


def redact(value: Any, values: list[str] | tuple[str, ...] = ()) -> Any:
    """A copy of JSON-like data with every secret replaced: values under secret-looking keys,
    and secrets in any string (see ``redact_text``)."""
    if isinstance(value, dict):
        return {k: (REDACTED if v not in (None, "", [], {}) and secret_key(k)
                    and not (isinstance(v, str) and _ID_VALUE.match(v)) else redact(v, values))
                for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v, values) for v in value]
    if isinstance(value, str):
        return redact_text(value, values)
    return value


def secret_values(manager: Manager) -> list[str]:
    """The secrets set here, so they're taken out wherever they turn up."""
    s = manager.s
    out = [s.secret(n) for n in SECRETS if getattr(s, n, None)]
    if getattr(s, "worker_token", None):
        out.append(s.worker_token.get_secret_value())
    return [v for v in out if v]


# --- ripshelf's own log ------------------------------------------------------------

_LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) ")


def _line_time(line: str) -> datetime | None:
    m = _LOG_TS.match(line)
    if not m:
        return None
    # The log file is in local time (logging's asctime).
    return datetime.strptime(m[1], "%Y-%m-%d %H:%M:%S").astimezone().astimezone(UTC)


def _log_records(data_dir: Path) -> list[tuple[datetime | None, str]]:
    """ripshelf's log, oldest first, as (time, record) with a traceback kept with its line.
    From the log files, or the in-memory buffer when there are none."""
    path = applog.log_file(data_dir)
    files = [path.with_name(f"{path.name}.{i}") for i in range(applog.FILE_COUNT - 1, 0, -1)] + [path]
    records: list[tuple[datetime | None, str]] = []
    for f in files:
        if not f.is_file():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            ts = _line_time(line)
            if ts is None and records:
                records[-1] = (records[-1][0], records[-1][1] + "\n" + line)
            else:
                records.append((ts, line))
    if records:
        return records
    entries, _ = applog.BUFFER.since(0, "DEBUG", applog.BUFFER_LINES)
    return [(e.ts, f"{e.ts.isoformat()} {e.level} {e.logger}: {e.message}") for e in entries]


def job_log(data_dir: Path, job_id: int, start: datetime, end: datetime) -> tuple[list[str], list[str]]:
    """(the log records that name the job, every record from while it ran)."""
    names = re.compile(rf"\bjob[ #:]*{int(job_id)}\b", re.I)
    first, last = start - WINDOW_MARGIN, end + WINDOW_MARGIN
    about, during = [], []
    for ts, rec in _log_records(data_dir):
        if names.search(rec):
            about.append(rec)
        if ts is not None and first <= ts <= last:
            during.append(rec)
    return about[-APP_LOG_MAX_LINES:], during[-APP_LOG_MAX_LINES:]


# --- versions ----------------------------------------------------------------------


def _git_sha() -> str | None:
    if sha := os.environ.get("RIPSHELF_GIT_SHA"):
        return sha
    root = install_dir()
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _tool_version(command: str, *args: str) -> str | None:
    """The first line a program prints with its version flag."""
    try:
        out = subprocess.run([*proc.split_command(command), *args], capture_output=True, text=True,
                             timeout=10, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else None


def _in_container() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        return any(k in Path("/proc/1/cgroup").read_text() for k in ("docker", "containerd", "kubepods", "libpod"))
    except OSError:
        return False


def versions(manager: Manager, job_makemkv: str | None) -> dict:
    s = manager.s
    db = manager.db.conn
    return {
        "ripshelf": __version__,
        "git_sha": _git_sha(),
        "makemkv_this_job": job_makemkv,
        "makemkv_latest_scan": manager.makemkv_version,
        "ffmpeg": _tool_version(s.ffmpeg, "-version"),
        "ffprobe": _tool_version(s.ffprobe, "-version"),
        "mkvmerge": _tool_version(s.mkvmerge or "mkvmerge", "--version"),
        "handbrake": None,  # ripshelf transcodes with ffmpeg
        "python": sys.version,
        "sqlite": sqlite3.sqlite_version,
        "db_user_version": db.execute("PRAGMA user_version").fetchone()[0],
        "db_tables": [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "container": _in_container(),
        "fake_hardware": s.fake_hardware,
    }


# --- the bundle --------------------------------------------------------------------


def _jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


def _drive_and_worker(manager: Manager, drive_id: str) -> tuple[dict | None, dict | None]:
    """The job's drive as it is now, and its remote worker (None for a drive on this machine)."""
    d = manager.drives.get(drive_id)
    drive = {
        "id": drive_id,
        "name": manager.s.drive_name(drive_id),
        "rip_mode": manager.s.rip_mode_for(drive_id),
        "stored": manager.db.drive_data(drive_id),
        "attached": d is not None,
    }
    if d is not None:
        drive |= {"hw": _jsonable(d.hw), "status": d.status.value, "current_job": d.job_id,
                  "remote": list(d.remote) if d.remote else None}
    worker = None
    if d is not None and d.remote:
        w = manager.hub.workers.get(d.remote[0])
        token = next((t for t in manager.db.worker_tokens() if t["worker_id"] == d.remote[0]), None)
        worker = {"id": d.remote[0], "drive_on_worker": d.remote[1], "connected": w is not None}
        if w is not None:
            worker |= {"name": w.name, "online": w.online, "encoders": w.encoders,
                       "shared_scratch": w.shared_scratch, "makemkv_eula_pending": w.makemkv_eula_pending,
                       "last_seen_s_ago": round(time.monotonic() - w.last_seen, 1),
                       "drives": [{"local_id": rd.local_id, "hw": _jsonable(rd.hw), "status": rd.status.value}
                                  for rd in w.drives.values()]}
        if token:
            worker["added"] = {k: token[k] for k in ("name", "created_at", "last_seen_at")}
    return drive, worker


def _db_rows(manager: Manager, job) -> dict:
    """The job's rows in the other tables: statistics, TheDiscDB release, and the disc cache."""
    conn = manager.db.conn

    def rows(q: str, args: tuple) -> list[dict]:
        return [dict(r) for r in conn.execute(q, args)]

    fps = [fp for fp in (job.quick_fp, job.content_fp, job.scan_fp) if fp]
    cache = rows(f"SELECT * FROM fingerprints WHERE fp IN ({','.join('?' * len(fps))}) OR job_id=?",
                 (*fps, job.id))
    for r in cache:
        r["candidate"] = json.loads(r["candidate"])
    return {
        "rip_stats": rows("SELECT * FROM rip_stats WHERE job_id=?", (job.id,)),
        "contributions": rows("SELECT * FROM contributions WHERE job_id=?", (job.id,)),
        "disc_cache": cache,
    }


class Bundle:
    """The zip being written, and its manifest."""

    def __init__(self, fh, values: list[str]):
        self.zip = zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        self.values = values
        self.files: list[dict] = []
        self.listed: list[dict] = []
        self.sections: dict[str, str] = {}

    def add(self, path: str, data: bytes | str, about: str) -> None:
        raw = data.encode("utf-8") if isinstance(data, str) else data
        self.zip.writestr(path, raw)
        self.files.append({"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                           "about": about})

    def add_json(self, path: str, data: Any, about: str) -> None:
        self.add(path, json.dumps(redact(data, self.values), indent=2, default=str) + "\n", about)

    def add_text(self, path: str, text: str, about: str) -> None:
        self.add(path, redact_text(text, self.values), about)

    def section(self, name: str, state: str) -> None:
        self.sections[name] = state


def _add_folder(b: Bundle, root: Path, prefix: str, about: str) -> int:
    """The files of the job's folder: text redacted, small pictures as they are, the rest listed."""
    n = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        size = p.stat().st_size
        suffix = p.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            with p.open("rb") as f:
                if size > TEXT_MAX:
                    f.seek(size - TEXT_MAX)
                text = f.read().decode("utf-8", errors="replace")
            if size > TEXT_MAX:
                text = f"[the first {size - TEXT_MAX} bytes are left out]\n" + text
            b.add_text(f"{prefix}/{rel}", text, about)
        elif suffix in IMAGE_SUFFIXES and size <= IMAGE_MAX:
            b.add(f"{prefix}/{rel}", p.read_bytes(), about)
        else:
            why = "picture over 512 KB" if suffix in IMAGE_SUFFIXES else "not text"
            b.listed.append({"path": str(p), "bytes": size, "why": why})
        n += 1
    return n


def _readme(job, problem: str | None, sections: dict[str, str]) -> str:
    title = job.identity.title if job.identity else (job.disc.label or "unidentified disc")
    if job.identity and job.identity.year:
        title += f" ({job.identity.year})"
    missing = [f"  {k}: {v}" for k, v in sections.items() if v != "included"]
    return "\n".join([
        f"ripshelf debug bundle for job {job.id}",
        "",
        f"Title:    {title}",
        f"Disc:     {job.disc.label or '-'} ({job.disc.kind.value if job.disc.kind else 'kind unknown'})",
        f"Media:    {job.media.value}, mode {job.mode}",
        f"Drive:    {job.drive_id}",
        f"State:    {job.state.value}",
        f"Started:  {job.created_at.isoformat()}",
        f"Finished: {job.finished_at.isoformat() if job.finished_at else '-'}",
        f"Problem:  {problem or '-'}",
        "",
        "manifest.json lists every file in the bundle, and what wasn't there.",
        *(["", "Not in this bundle:", *missing] if missing else []),
        "",
        "Secrets (API keys, tokens, passwords, the MakeMKV key) have been replaced with [REDACTED].",
        "",
    ])


def file_name(job_id: int, at: datetime) -> str:
    return f"ripshelf-job-{job_id}-{at.strftime('%Y%m%dT%H%M%SZ')}.zip"


async def build(manager: Manager, job_id: int):
    """(file name, the zip as an open file at its start). KeyError for no such job."""
    job = manager.db.get_job(job_id)
    if job is None:
        raise KeyError(job_id)
    # What lives in memory is read here, on the event loop; the files in a thread.
    drive, worker = _drive_and_worker(manager, job.drive_id)
    events = manager.db.events(job.id)
    rows = _db_rows(manager, job)
    collection = None
    if job.tv and job.tv.show:
        c = manager.db.collection_for_show(job.tv.show.key)
        collection = c.model_dump(mode="json") if c else None
    settings_dump = manager.s.model_dump(mode="json")
    work, scratch = manager.work_dir(job), manager.scratch_dir(job)
    status = manager.status().model_dump(mode="json")
    at = datetime.now(UTC)

    def write():
        values = secret_values(manager)
        fh = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
        b = Bundle(fh, values)

        b.add_json("job.json", job.model_dump(mode="json"), "the job's whole record, with the fields the "
                   "API doesn't show (fingerprints, disc ids, every title)")
        b.section("job", "included")
        b.add_json("timeline.json", [e.model_dump(mode="json") for e in events],
                   "every state change, decision and message, with its time")
        b.section("timeline", "included" if events else "missing: no events recorded")
        b.add_json("disc/titles.json", [t.model_dump(mode="json") for t in job.titles],
                   "MakeMKV's titles, as parsed from the scan")
        b.section("titles", "included" if job.titles else "missing: the disc wasn't scanned")
        b.add_json("disc/identity.json", {
            "disc": job.disc.model_dump(mode="json"), "quick_fp": job.quick_fp, "content_fp": job.content_fp,
            "scan_fp": job.scan_fp, "disc_ids": job.disc_ids, "disc_match": _jsonable(job.disc_match),
            "media": job.media.value, "media_evidence": job.media_evidence,
            "candidates": [c.model_dump(mode="json") for c in job.candidates],
            "identity": _jsonable(job.identity), "identity_source": job.identity_source,
            "tv": _jsonable(job.tv), "preset": _jsonable(job.preset), "refile": _jsonable(job.refile),
            "duplicates": job.duplicates,
        }, "the disc's label, ids and fingerprints, every candidate and its score, and the identity chosen")
        b.section("identification", "included" if job.candidates or job.identity or job.tv
                  else "missing: no candidates found")
        b.add_json("disc/scans.json", {
            "scans": [s.model_dump(mode="json") for s in job.scans], "scanner": _jsonable(job.scanner),
            "listing": _jsonable(job.listing), "scan_match": _jsonable(job.scan_match),
        }, "the box scans (pictures under work/scans/), their reading and the match to the titles")
        b.section("box scans", "included" if job.scans else "missing: no scans of the box")
        b.add_json("output.json", {
            "mode": job.mode, "rip": job.rip.model_dump(mode="json"), "transcode": _jsonable(job.transcode),
            "step": _jsonable(job.step), "destination": job.destination, "filed_extras": job.filed_extras,
            "clash": _jsonable(job.clash), "problem": _jsonable(job.problem), "contribution": _jsonable(job.contribution),
            "scratch_files": [{"path": str(p), "bytes": p.stat().st_size} for p in sorted(scratch.rglob("*"))
                              if p.is_file()] if scratch.is_dir() else [],
            "destination_exists": Path(job.destination).exists() if job.destination else None,
        }, "the rip and transcode, where the files went, and the problem if it failed")
        b.add_json("db.json", rows | {"collection": collection},
                   "the job's statistics row, TheDiscDB release, disc cache entries and TV collection")
        b.add_json("drive.json", drive, "the drive the job ran on, as it is now")
        b.section("drive", "included" if drive and drive["attached"] else "partial: the drive isn't attached now")
        if worker:
            b.add_json("worker.json", worker, "the remote worker the drive is on")
            b.section("worker", "included")
        else:
            b.section("worker", "missing: the drive is on this machine" if drive and drive["attached"] and not
                      drive.get("remote") else "missing: the drive isn't attached now, so its worker is unknown")

        if work.is_dir() and _add_folder(b, work, "work", "from the job's folder: MakeMKV logs (scan.log, "
                                         "rip.log), ffmpeg logs (transcode-N.log), scan readings, frames"):
            b.section("job folder", "included")
        else:
            b.section("job folder", f"missing: {work} doesn't exist or is empty")
        for kind in ("scan", "rip"):
            if not (work / f"{kind}.log").is_file():
                b.section(f"{kind}.log", "missing: MakeMKV didn't get this far")
        if not list(work.glob("transcode-*.log")):
            b.section("transcode logs", "missing: not transcoded")

        end = job.finished_at or datetime.now(UTC)
        about, during = job_log(manager.s.data_dir, job.id, job.created_at, end)
        b.add_text("logs/ripshelf-job.log", "\n".join(about) + "\n",
                   "ripshelf's log records that name this job")
        b.add_text("logs/ripshelf-during-job.log", "\n".join(during) + "\n",
                   "all of ripshelf's log from 2 minutes before the job started to 2 minutes after it ended")
        b.section("app log", "included" if about or during
                  else "missing: nothing in ripshelf's log from then (rotated away, or a restart)")

        b.add_json("settings.json", settings_dump, "every setting, with secrets taken out")
        b.add_json("versions.json", versions(manager, job.makemkv_version) | {"status": status},
                   "ripshelf, MakeMKV, ffmpeg, Python, SQLite and OS versions, and the setup problems")

        b.add_text("README.txt", _readme(job, job.problem.message if job.problem else None, b.sections),
                   "a summary")
        manifest = {
            "format": 1, "job_id": job.id, "created_at": at.isoformat(), "ripshelf": __version__,
            "files": b.files + [{"path": "manifest.json", "about": "this list"}],
            "listed_not_included": b.listed, "sections": b.sections, "not_stored_by_ripshelf": NOT_STORED,
            "redaction": f"values under keys that name a secret, URL parameters, headers, Bearer tokens and "
                         f"MakeMKV keys are replaced with {REDACTED}, as are the secrets set in Settings "
                         f"wherever they appear",
        }
        b.zip.writestr("manifest.json", json.dumps(redact(manifest, values), indent=2, default=str) + "\n")
        b.zip.close()
        fh.seek(0)
        return fh

    return file_name(job.id, at), await asyncio.to_thread(write)


def chunks(fh: io.IOBase, size: int = 256 * 1024):
    """The zip in pieces, for a streamed response, closing it after."""
    try:
        while data := fh.read(size):
            yield data
    finally:
        fh.close()
