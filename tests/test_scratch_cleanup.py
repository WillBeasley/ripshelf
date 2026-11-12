"""Room for rips: failed jobs' scratch cleared after a while, and the library checked for space."""

from __future__ import annotations

import os
import shutil
import time
from collections import namedtuple
from pathlib import Path

from ripshelf import library, scratchjobs
from ripshelf.models import Candidate, JobState

PM = "/dev/fake-sr1"  # Moonlit Orchard DVD
MANUAL = Candidate(source="manual", title="Moonlit Orchard", year=1998)
Usage = namedtuple("Usage", "total used free")
DAY = 86400


def failed_job(h, settings, state=JobState.failed):
    job = h.db.create_job("fake-ihas122", PM)
    job.state = state
    h.db.save_job(job)
    folder = settings.scratch_dir / f"job-{job.id}"
    folder.mkdir(parents=True)
    (folder / "title_t00.mkv").write_bytes(b"x" * 4096)
    return job, folder


def days_later(monkeypatch, days: float) -> None:
    real = time.time
    monkeypatch.setattr(scratchjobs.time, "time", lambda: real() + days * DAY)


async def test_failed_scratch_kept_by_default(harness, settings, monkeypatch):
    async with harness() as h:
        job, folder = failed_job(h, settings)
        days_later(monkeypatch, 400)
        assert await h.m.clear_failed_scratch() == 0
        assert folder.is_dir()


async def test_failed_scratch_cleared_after_the_days_set(harness, settings, monkeypatch):
    settings.failed_scratch_days = 7
    async with harness() as h:
        job, folder = failed_job(h, settings)
        days_later(monkeypatch, 6)
        assert await h.m.clear_failed_scratch() == 0, "not old enough yet"
        assert folder.is_dir()
        days_later(monkeypatch, 8)
        assert await h.m.clear_failed_scratch() == 1
        assert not folder.exists()
        assert not list(settings.scratch_dir.iterdir()), "nothing left behind"
        assert h.job(job.id).state == JobState.failed, "the job itself stays"
        assert any("Deleted its rip from scratch" in e.message and "7 days" in e.message
                   for e in h.db.events(job.id))
        assert await h.m.clear_failed_scratch() == 0, "once only"


async def test_only_failed_jobs_lose_their_scratch(harness, settings, monkeypatch):
    settings.failed_scratch_days = 1
    async with harness() as h:
        _, waiting = failed_job(h, settings, JobState.review)
        _, failed = failed_job(h, settings)
        days_later(monkeypatch, 30)
        assert await h.m.clear_failed_scratch() == 1
        assert waiting.is_dir() and not failed.exists()


async def test_scratch_changed_lately_is_kept(harness, settings, monkeypatch):
    settings.failed_scratch_days = 7
    async with harness() as h:
        _, folder = failed_job(h, settings)
        days_later(monkeypatch, 8)
        newer = time.time() + 5 * DAY
        (folder / "title_t01.mkv").write_bytes(b"y")
        os.utime(folder / "title_t01.mkv", (newer, newer))
        assert await h.m.clear_failed_scratch() == 0
        assert folder.is_dir()


async def test_half_deleted_scratch_goes_on_the_next_pass(harness, settings):
    async with harness() as h:
        left = settings.scratch_dir / f"{scratchjobs.DELETING_PREFIX}job-9"
        left.mkdir(parents=True)
        (left / "title_t00.mkv").write_bytes(b"x")
        await h.m.clear_failed_scratch()
        assert not left.exists()


def test_space_shortfall_counts_files_and_folders(tmp_path, monkeypatch):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x" * 1000)
    d = tmp_path / "backup"
    (d / "BDMV").mkdir(parents=True)
    (d / "BDMV" / "index.bdmv").write_bytes(b"x" * 1000)
    monkeypatch.setattr(library.shutil, "disk_usage", lambda p: Usage(10**6, 0, 2100))
    assert library.space_shortfall(tmp_path, [f, d]) is None
    monkeypatch.setattr(library.shutil, "disk_usage", lambda p: Usage(10**6, 0, 2000))
    msg = library.space_shortfall(tmp_path, [f, d], "movies folder")
    assert msg and "movies folder" in msg and "Free some space" in msg


def test_space_unknown_tries_the_copy(tmp_path, monkeypatch):
    def unreadable(p):
        raise OSError("no statvfs here")
    monkeypatch.setattr(library.shutil, "disk_usage", unreadable)
    assert library.space_shortfall(tmp_path, [tmp_path]) is None


async def test_full_library_fails_before_the_copy(harness, settings, monkeypatch):
    real = shutil.disk_usage
    room = {"free": 1024}

    def usage(path):
        u = real(path)
        if Path(path) == settings.library_dir:
            return Usage(u.total, u.used, room["free"])
        return Usage(u.total, u.used, 2**40)

    monkeypatch.setattr(library.shutil, "disk_usage", usage)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.confirm(job.id, MANUAL)
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "library_full" and "library folder" in job.problem.message
        assert Path(job.rip.file).is_file(), "the rip stays in scratch"
        assert not list(settings.library_dir.rglob("*.mkv")), "nothing half copied"
        room["free"] = 2**40
        await h.m.retry(job.id)
        await h.wait_job(lambda j: j.state == JobState.done)
