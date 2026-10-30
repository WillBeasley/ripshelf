"""End to end on the fake drives. Scan, identify, review, rip, finalise."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ripshelf.bus import EventBus
from ripshelf.config import Settings
from ripshelf.db import Database
from ripshelf.dev.fake import FakeDrives
from ripshelf.jobs import Manager
from ripshelf.models import Candidate, JobState

from .conftest import until


async def run_manager(settings: Settings, present: set[str]):
    db = Database(settings.db_path)
    backend = FakeDrives(present=present)
    m = Manager(settings, db, backend, EventBus())
    await m.start()
    return m, db, backend


async def test_review_confirm_finalise(settings: Settings):
    m, db, backend = await run_manager(settings, {"/dev/fake-sr1"})
    try:
        # With no TMDB key, the disc goes to review while the rip carries on.
        job = await until(lambda: next(iter(db.list_jobs([JobState.review.value])), None))
        assert job.disc.label == "PRACTICAL_MAGIC"
        assert job.main_title and job.main_title.duration_s > 3600
        await m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
        done = await until(lambda: (j := db.get_job(job.id)) and j.state == JobState.done and j)
        dest = Path(done.destination)
        assert dest == settings.library_dir / "Movies/Practical Magic (1998)/Practical Magic (1998).mkv"
        assert dest.stat().st_size > 0
        meta = json.loads((dest.parent / ".disc/disc.json").read_text())
        assert meta["title"] == "Practical Magic" and meta["label"] == "PRACTICAL_MAGIC"
        assert (dest.parent / ".disc/makemkv-scan.log").exists()
        assert (dest.parent / ".disc/VIDEO_TS/VIDEO_TS.IFO").exists()
        assert not any(settings.scratch_dir.iterdir()), "scratch cleaned up"
        assert "/dev/fake-sr1" not in backend.present, "auto-ejected"
        states = [e.message for e in db.events(job.id) if e.kind == "state"]
        assert any("Waiting for review" in s for s in states)
    finally:
        await m.stop()
        db.close()


@pytest.mark.slow
async def test_same_disc_again_is_recognised_from_cache(settings: Settings):
    m, db, backend = await run_manager(settings, {"/dev/fake-sr1"})
    try:
        job = await until(lambda: next(iter(db.list_jobs([JobState.review.value])), None))
        await m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
        await until(lambda: db.get_job(job.id).state == JobState.done)
        # Remove the first copy so the second can land, then insert the disc again.
        Path(db.get_job(job.id).destination).unlink()
        backend.insert("/dev/fake-sr1")
        second = await until(lambda: next((j for j in db.list_jobs() if j.id != job.id), None))
        second = await until(lambda: (j := db.get_job(second.id)) and j.state == JobState.done and j)
        assert second.identity_source == "cache"
    finally:
        await m.stop()
        db.close()


async def test_hanging_scan_times_out(settings: Settings):
    settings.scan_timeout_s = 2
    m, db, _ = await run_manager(settings, {"/dev/fake-sr3"})
    try:
        job = await until(lambda: next(iter(db.list_jobs([JobState.failed.value])), None), timeout=20)
        assert job.problem.code == "scan_timeout"
        assert "retry_scan" in job.problem.actions
    finally:
        await m.stop()
        db.close()


async def test_cancel_during_rip(settings: Settings, monkeypatch):
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "30")
    m, db, _ = await run_manager(settings, {"/dev/fake-sr1"})
    try:
        job = await until(lambda: next((j for j in db.list_jobs() if j.rip.state == "running"), None))
        await m.cancel(job.id)
        job = db.get_job(job.id)
        assert job.state == JobState.cancelled
        assert not (settings.scratch_dir / f"job-{job.id}").exists()
        d = m.drives[job.drive_id]
        await until(lambda: d.rip is None)
    finally:
        await m.stop()
        db.close()
