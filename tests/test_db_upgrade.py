"""Databases written by older releases, opened by this one: the schema version, its
migrations, the copy taken first, and rows that no longer fit the models."""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ripshelf import db as dbmod
from ripshelf.db import SCHEMA_VERSION, Database, NewerDatabase
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import JobState

FIXTURES = Path(__file__).parent / "fixtures" / "db"
# Built by fixtures/db/make_fixture.py with each release's own code: Pippa & Goose (TV)
# done, Moonlit Orchard done, a scan that timed out, and The Brass Heron in review.
RELEASES = ["0.5.0", "0.6.0"]
STATES = {1: JobState.done, 2: JobState.done, 3: JobState.failed, 4: JobState.review}


def old_db(tmp_path: Path, release: str, name: str = "ripshelf.db") -> Path:
    path = tmp_path / name
    shutil.copy(FIXTURES / f"ripshelf-{release}.db", path)
    return path


def rows(path: Path, sql: str) -> list:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


@pytest.mark.parametrize("release", RELEASES)
def test_old_database_opens_and_reads(tmp_path, release):
    path = old_db(tmp_path, release)
    db = Database(path)
    try:
        assert db.version == SCHEMA_VERSION
        jobs = db.list_jobs()
        assert {j.id: j.state for j in jobs} == STATES
        for j in jobs:
            j.public()  # what the API sends
            assert db.events(j.id)
        tv = db.get_job(1)
        assert tv.media == "tv" and tv.tv.show.name == "Pippa & Goose" and tv.tv.consistent
        assert db.get_job(2).identity.title == "Moonlit Orchard"
        assert db.get_job(3).problem.code == "scan_timeout"
        [c] = db.collections()
        assert c.show.name == "Pippa & Goose"
        assert db.collection_for_show(c.show.key).id == c.id
        stats = {s.drive_id: s for s in db.drive_stats()}
        assert sum(s.discs_ripped for s in stats.values()) == 2
        assert db.recall(db.get_job(2).quick_fp)[0].title == "Moonlit Orchard"
        # Still writable: a new job, and a saved change to an old one.
        assert db.create_job("fake-ihas122", "/dev/fake-sr1").id == 5
        old = db.get_job(4)
        old.notified.append("review")
        db.save_job(old)
        assert db.get_job(4).notified == ["review"]
    finally:
        db.close()


@pytest.mark.parametrize("release", RELEASES)
def test_old_database_is_copied_before_upgrading(tmp_path, release):
    path = old_db(tmp_path, release)
    Database(path).close()
    copy = tmp_path / f"ripshelf-before-v{SCHEMA_VERSION}.db"
    assert rows(copy, "PRAGMA user_version") == [(0,)], "the copy is the database as it was"
    assert rows(copy, "SELECT COUNT(*) FROM jobs") == [(4,)]
    copy.unlink()
    Database(path).close()
    assert not copy.exists(), "no copy once it's up to date"


def test_new_database_is_not_copied(tmp_path):
    Database(tmp_path / "ripshelf.db").close()
    assert sorted(p.name for p in tmp_path.glob("*.db")) == ["ripshelf.db"]
    assert rows(tmp_path / "ripshelf.db", "PRAGMA user_version") == [(SCHEMA_VERSION,)]


@pytest.mark.parametrize("release", RELEASES)
def test_app_starts_on_an_old_database(settings, release):
    shutil.copy(FIXTURES / f"ripshelf-{release}.db", settings.db_path)
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        jobs = c.get("/api/jobs").json()
        # The Brass Heron was still ripping when the database was taken, so the restart fails it.
        assert {j["id"]: j["state"] for j in jobs} == {1: "done", 2: "done", 3: "failed", 4: "failed"}
        assert "restarted" in c.get("/api/jobs/4").json()["problem"]["message"]
        assert c.get("/api/jobs/1").json()["tv"]["show"]["name"] == "Pippa & Goose"
        assert c.get("/api/collections").status_code == 200


def test_newer_database_is_refused(tmp_path):
    path = tmp_path / "ripshelf.db"
    Database(path).close()
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    conn.close()
    with pytest.raises(NewerDatabase, match="before-v"):
        Database(path)


def test_failed_step_leaves_the_database_as_it_was(tmp_path, monkeypatch):
    path = old_db(tmp_path, "0.6.0")
    Database(path).close()

    def broken(conn):
        conn.execute("DROP TABLE events")
        raise RuntimeError("step failed")

    monkeypatch.setattr(dbmod, "MIGRATIONS", [*dbmod.MIGRATIONS, broken])
    monkeypatch.setattr(dbmod, "SCHEMA_VERSION", SCHEMA_VERSION + 1)
    with pytest.raises(RuntimeError, match="step failed"):
        Database(path)
    assert rows(path, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    assert rows(path, "SELECT COUNT(*) FROM events")[0][0] > 0, "the step was rolled back"
    assert (tmp_path / f"ripshelf-before-v{SCHEMA_VERSION + 1}.db").exists()


# --- rows that no longer fit ------------------------------------------------------------------


def spoil(path: Path, sql: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(sql)
    conn.commit()
    conn.close()


def test_unreadable_job_is_skipped_and_logged_once(tmp_path, caplog):
    path = old_db(tmp_path, "0.6.0")
    Database(path).close()
    # As if a field had been retyped since job 2 was saved; with no statistics row yet,
    # so start-up reads it too.
    spoil(path, "UPDATE jobs SET data=json_set(data, '$.rip', 'gone') WHERE id=2")
    spoil(path, "DELETE FROM rip_stats WHERE job_id=2")
    with caplog.at_level(logging.WARNING, logger="ripshelf.db"):
        db = Database(path)
        try:
            assert [j.id for j in db.list_jobs()] == [4, 3, 1]
            assert [j.id for j in db.list_jobs([JobState.done.value])] == [1]
            assert db.get_job(2) is None
            assert db.get_job(1).state == JobState.done
        finally:
            db.close()
    skipped = [r for r in caplog.records if "jobs row 2" in r.getMessage()]
    assert len(skipped) == 1


def test_unreadable_job_does_not_stop_the_app(settings):
    shutil.copy(FIXTURES / "ripshelf-0.6.0.db", settings.db_path)
    spoil(settings.db_path, "UPDATE jobs SET data='{\"id\": 4}' WHERE id=4")
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        assert [j["id"] for j in c.get("/api/jobs").json()] == [3, 2, 1]
        assert c.get("/api/jobs/4").status_code == 404


def test_unreadable_collection_and_fingerprint_are_skipped(tmp_path):
    path = old_db(tmp_path, "0.6.0")
    spoil(path, "UPDATE collections SET data='{}'")
    spoil(path, "UPDATE fingerprints SET candidate='{}'")
    db = Database(path)
    try:
        assert db.collections() == []
        assert db.recall(db.get_job(2).quick_fp) is None
    finally:
        db.close()


# --- backups ---------------------------------------------------------------------------------


def test_backup_has_its_own_connection(tmp_path):
    """The backup runs in a thread while the event loop keeps using the main connection,
    here in the middle of a transaction it hasn't committed."""
    db = Database(old_db(tmp_path, "0.6.0"))
    try:
        db.conn.execute("BEGIN")
        db.add_event(1, "note", "not committed yet")
        t = threading.Thread(target=db.backup, args=(tmp_path / "copy.db",))
        t.start()
        t.join()
        db.conn.execute("COMMIT")
        assert not db.conn.in_transaction
        assert rows(tmp_path / "copy.db", "SELECT COUNT(*) FROM jobs") == [(4,)]
        assert not rows(tmp_path / "copy.db", "SELECT 1 FROM events WHERE message='not committed yet'")
    finally:
        db.close()
