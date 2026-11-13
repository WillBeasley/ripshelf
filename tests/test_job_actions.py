"""What the user's buttons do when the job, the drive or the app isn't ready for them,
and how a job that crashed is left: failed with a reason and a retry, never stuck."""

from __future__ import annotations

import json

import pytest

from ripshelf.models import Candidate, JobState, RipState

from .conftest import until
from .test_scans import picture

PM = "/dev/fake-sr1"
ORCHARD = Candidate(source="manual", title="Moonlit Orchard", year=1998)


async def test_a_crash_while_identifying_fails_the_job_and_retry_carries_on(harness, monkeypatch):
    async with harness(PM) as h:
        real_identify = h.m._identify

        async def boom(job_id, **kw):
            raise RuntimeError("the provider sent nonsense")

        monkeypatch.setattr(h.m, "_identify", boom)
        await h.insert(PM)
        failed = await h.wait_job(lambda j: j.state == JobState.failed)
        assert failed.problem.code == "internal" and "retry" in failed.problem.actions
        assert failed.problem.message == "Internal error: the provider sent nonsense"
        monkeypatch.setattr(h.m, "_identify", real_identify)
        await h.m.retry(failed.id)
        await h.wait_job(lambda j: j.state == JobState.review)


async def test_a_crash_after_the_rip_fails_the_job_instead_of_leaving_it_finalising(harness, monkeypatch):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")

        async def boom(job):
            raise KeyError("titles")

        monkeypatch.setattr(h.m, "_finalise", boom)
        await h.m.confirm(job.id, ORCHARD)
        failed = await h.wait_job(lambda j: j.state == JobState.failed)
        assert failed.problem.code == "internal" and failed.problem.message.startswith("Internal error: KeyError")


async def test_retry_and_identify_again_refuse_what_they_cant_do(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        with pytest.raises(ValueError, match="only failed jobs can be retried"):
            await h.m.retry(job.id)
        await h.m.cancel(job.id)
        with pytest.raises(ValueError, match="cannot re-identify this job now"):
            await h.m.identify_again(job.id)


async def test_a_failed_rip_must_be_retried_before_identifying_again(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.cancel(job.id)
        job = h.job(job.id)
        job.state, job.rip.state = JobState.failed, RipState.failed
        h.db.save_job(job)
        with pytest.raises(ValueError, match="retry the job first"):
            await h.m.identify_again(job.id)


async def test_retrying_a_rip_needs_the_disc_back(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.cancel(job.id)
        h.backend.present.discard(PM)
        job = h.job(job.id)
        job.state, job.rip.state, job.rip.file = JobState.failed, RipState.failed, None
        h.db.save_job(job)
        with pytest.raises(ValueError, match="put the disc back in the drive first"):
            await h.m.retry(job.id)


async def test_a_tray_that_wont_open_puts_the_job_back(harness, monkeypatch):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")

        def stuck(device):
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(h.backend, "eject", stuck)
        with pytest.raises(ValueError, match="eject failed"):
            await h.m.check_disc(job.id)
        job = h.job(job.id)
        assert job.state == JobState.review and job.checking_from is None


async def test_check_disc_when_the_disc_has_gone(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        h.m.drives["fake-ihas122"].job_id = None  # the drive has moved on to another disc
        with pytest.raises(ValueError, match="no longer in its drive"):
            await h.m.check_disc(job.id)


async def test_reading_the_label_needs_a_job_in_review(harness):
    async with harness(PM) as h:
        with pytest.raises(ValueError, match="no such job"):
            await h.m.read_label(99)
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.cancel(job.id)
        with pytest.raises(ValueError, match="isn't waiting for review"):
            await h.m.read_label(job.id)


async def test_snapshots_and_logs_of_jobs_without_them(harness, settings):
    settings.rip_mode = "backup"
    async with harness("/dev/fake-sr2") as h:
        with pytest.raises(ValueError, match="no such job"):
            await h.m.snapshots(99)
        job = await h.wait_job(lambda j: j.state == JobState.review)
        assert "backed up whole" in (await h.m.snapshots(job.id)).note
        assert h.m.log_tail(job.id, "transcode-0") == []


async def test_retry_scan_on_the_drive_card(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        with pytest.raises(ValueError, match="the drive is busy"):
            await h.m.rescan_drive("fake-ihas122")
        await h.m.cancel(job.id)
        await until(lambda: not h.m._busy(h.m.drives["fake-ihas122"]), what="the drive to be free")
        again = await h.m.rescan_drive("fake-ihas122")  # the same disc, read as a new job
        assert again.id != job.id
        await h.wait_job(lambda j: j.id == again.id and j.state == JobState.review)


async def test_retry_scan_of_a_failed_job_retries_it(harness, monkeypatch):
    async with harness(PM) as h:
        real = h.m._identify

        async def boom(job_id, **kw):
            raise RuntimeError("no")

        monkeypatch.setattr(h.m, "_identify", boom)
        failed = await h.wait_job(lambda j: j.state == JobState.failed)
        monkeypatch.setattr(h.m, "_identify", real)
        assert (await h.m.rescan_drive("fake-ihas122")).id == failed.id
        await h.wait_job(lambda j: j.id == failed.id and j.state == JobState.review)


async def test_thediscdb_being_down_is_a_warning_not_a_failure(harness, discdb, monkeypatch):
    from ripshelf.providers import thediscdb


    async def down(self, fps):
        raise thediscdb.DiscDbError("HTTP 503")

    monkeypatch.setattr(thediscdb.TheDiscDb, "lookup", down)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        assert any(e.message == "TheDiscDB: HTTP 503" for e in h.db.events(job.id))


async def on_state(h, job_id: int, state: str, q) -> None:
    """Return as soon as the job's update saying ``state`` is published: the moment the
    UI could show it, and the user act on it."""
    while True:
        msg = json.loads(await q.get())
        if msg.get("type") == "job" and msg["job"]["id"] == job_id and msg["job"]["state"] == state:
            return


async def test_a_job_can_be_deleted_the_moment_it_shows_done(harness):
    # Filing used to carry on after "done" (copying the cover scans in), so a Delete
    # straight away was refused as "still running".
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
        await h.m.add_scan(job.id, picture(), "outside_full", "upload")
        with h.bus.subscribe() as q:
            await h.m.confirm(job.id, ORCHARD)
            await on_state(h, job.id, "done", q)
            assert h.m.delete_jobs([job.id]) == ([job.id], [])
        assert h.job(job.id) is None


async def test_a_failed_move_can_be_put_back_the_moment_it_shows_failed(harness, settings, monkeypatch):
    from ripshelf import library

    from .test_reidentify import RIGHT, filed_film

    settings.movies.mkdir(parents=True, exist_ok=True)

    def full(src, dst):
        raise OSError(28, "No space left on device")

    async with harness() as h:
        job, _, dest = filed_film(h, settings)
        await h.m.reidentify(job.id)
        monkeypatch.setattr(library, "move_path", full)
        with h.bus.subscribe() as q:
            await h.m.confirm(job.id, RIGHT)
            await on_state(h, job.id, "failed", q)
            back = await h.m.keep_identity(job.id)
        assert back.state == JobState.done and dest.read_bytes() == b"film"
