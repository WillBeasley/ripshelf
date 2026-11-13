"""What the user's buttons do when the job, the drive or the app isn't ready for them,
and how a job that crashed is left: failed with a reason and a retry, never stuck."""

from __future__ import annotations

import pytest

from ripshelf.models import Candidate, JobState, RipState

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
