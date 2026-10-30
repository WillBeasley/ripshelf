"""Scratch space, backups, shutdown."""

from __future__ import annotations

import os
import sqlite3
import time
from collections import namedtuple
from contextlib import closing

from ripshelf import jobs as jobsmod
from ripshelf.db import Database
from ripshelf.models import JobState, RipProgress, RipState

from .conftest import needs_unix_permissions, until

PM = "/dev/fake-sr1"
Usage = namedtuple("Usage", "total used free")


async def test_rip_that_wont_fit_is_held_with_a_reason(harness, monkeypatch):
    monkeypatch.setattr(jobsmod.shutil, "disk_usage", lambda p: Usage(10**12, 10**12, 1 * 1024**3))
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
    assert job.problem.code == "scratch_full"
    assert "needs about" in job.problem.message and "1.0 GB is free" in job.problem.message
    assert "retry" in job.problem.actions and job.rip.state == RipState.failed


async def test_space_promised_to_running_rips_counts(harness, monkeypatch, settings):
    monkeypatch.setattr(jobsmod.shutil, "disk_usage", lambda p: Usage(10**12, 0, 6 * 1024**3))
    async with harness() as h:
        other = h.db.create_job("fake-pioneer", "/dev/fake-sr2")
        other.state, other.rip.state = JobState.ripping, RipState.running
        other.rip.progress = RipProgress(bytes_total=5 * 1024**3, bytes_done=1024**3)
        h.db.save_job(other)
        me = h.db.create_job("fake-ihas122", PM)
        settings.transcode_enabled = False
        assert h.m._scratch_shortfall(me, 1024**3) is None  # 6 - 4 promised = 2 GB free, needs 1.05
        msg = h.m._scratch_shortfall(me, 3 * 1024**3)
        assert msg and "after the rips already running" in msg
        settings.transcode_enabled = True
        assert h.m._scratch_shortfall(me, int(1.4 * 1024**3)) is not None  # + room for the transcoded copy


async def test_scratch_check_tolerates_a_missing_folder(harness, monkeypatch):
    def boom(p):
        raise OSError("gone")

    monkeypatch.setattr(jobsmod.shutil, "disk_usage", boom)
    async with harness() as h:
        assert h.m._scratch_shortfall(h.db.create_job("fake-ihas122", PM), 10**12) is None


async def test_backup_is_consistent_and_pruned(harness, settings):
    settings.backup_keep = 2
    async with harness() as h:
        for _ in range(3):
            h.db.create_job("fake-ihas122", PM)
        folder = settings.data_dir / "backups"
        await until(lambda: list(folder.glob("ripshelf-*.db")))  # first one at startup
        path = h.m.backup_now()
        with closing(sqlite3.connect(path)) as conn:  # closed, because Windows can't replace an open file
            n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert n == 3
        # Older files are pruned to the newest two.
        for d in ("2026-01-01", "2026-01-02", "2026-01-03"):
            (folder / f"ripshelf-{d}.db").write_bytes(b"old")
        h.m.backup_now()
        names = sorted(p.name for p in folder.glob("ripshelf-*.db"))
        assert len(names) == 2 and names[-1] == path.name
        assert not list(folder.glob("*.tmp"))


async def test_backups_off_and_due(harness, settings):
    settings.backup_keep = 0
    async with harness() as h:
        assert h.m.backup_now() is None
        settings.backup_keep = 7
        assert h.m._backup_due()
        p = h.m.backup_now()
        assert not h.m._backup_due()
        old = time.time() - jobsmod.BACKUP_INTERVAL_S - 10
        os.utime(p, (old, old))
        assert h.m._backup_due()


def test_db_backup_replaces_atomically(tmp_path):
    db = Database(tmp_path / "a.db")
    db.create_job("d", "/dev/sr0")
    dest = tmp_path / "b" / "copy.db"
    dest.parent.mkdir()
    dest.write_bytes(b"stale")
    db.backup(dest)
    assert sqlite3.connect(dest).execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    db.close()


def test_server_shutdown_is_bounded():
    from ripshelf import main

    assert 0 < main.SHUTDOWN_GRACE_S <= 5


async def test_backup_dir_must_be_mounted(harness, settings, tmp_path):
    settings.backup_dir = tmp_path / "backups-mount"  # not there, so not mounted
    async with harness() as h:
        problem = h.m.backup_problem()
        assert problem and "does not exist" in problem and "RIPSHELF_BACKUP_DIR" in problem
        assert any(p.code == "backup_dir" for p in h.m.problems())
        assert h.m.backup_now() is None
        assert not settings.backup_dir.exists(), "never created inside the container"
        settings.backup_dir.mkdir()
        assert h.m.backup_problem() is None
        assert h.m.backup_now().parent == settings.backup_dir
        assert not (settings.data_dir / "backups").exists()


@needs_unix_permissions
async def test_backup_dir_read_only(harness, settings, tmp_path):
    settings.backup_dir = tmp_path / "ro"
    settings.backup_dir.mkdir()
    settings.backup_dir.chmod(0o555)
    try:
        async with harness() as h:
            assert "not writable" in h.m.backup_problem()
    finally:
        settings.backup_dir.chmod(0o755)


def test_backup_count_and_order_check_are_settings(settings, monkeypatch):
    """Both are on the Settings page. The environment gives the starting value, and a value
    saved in Settings wins over it."""
    from fastapi.testclient import TestClient

    from ripshelf import config
    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        body = c.get("/api/settings").json()
        assert body["backup_keep"] == 7 and body["verify_order"] is True
        assert body["backups_dir"] == str(settings.data_dir / "backups")
        body = c.put("/api/settings", json={"backup_keep": 3, "verify_order": False}).json()
        assert body["backup_keep"] == 3 and body["verify_order"] is False
        assert settings.backup_keep == 3 and not settings.verify_order
        for bad in (-1, 366):
            assert c.put("/api/settings", json={"backup_keep": bad}).status_code == 422
    saved = (settings.data_dir / "settings.json").read_text(encoding="utf-8")
    assert '"backup_keep": 3' in saved and '"verify_order": false' in saved

    monkeypatch.setenv("RIPSHELF_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("RIPSHELF_BACKUP_KEEP", "10")
    assert config.load_settings().backup_keep == 3  # settings.json wins
    (settings.data_dir / "settings.json").write_text("{}", encoding="utf-8")
    assert config.load_settings().backup_keep == 10  # nothing saved, so the environment's
