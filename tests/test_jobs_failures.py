"""Pipeline failures. The disc goes, MakeMKV dies, the library is not there."""

from __future__ import annotations

import asyncio
from itertools import product
from pathlib import Path

import pytest

from ripshelf.db import JobRecord, now
from ripshelf.models import TRANSITIONS, Candidate, JobState, Problem, RipState

from .conftest import needs_unix_permissions, until

PM = "/dev/fake-sr1"  # Practical Magic DVD
HANG = "/dev/fake-sr3"  # scan never finishes
MANUAL = Candidate(source="manual", title="Practical Magic", year=1998)


async def test_disc_removed_mid_scan(harness):
    async with harness(HANG) as h:
        job = await h.wait_job(lambda j: j.state == JobState.scanning)
        d = h.m.drives[job.drive_id]
        await until(lambda: d.scan is not None and d.scan.running, what="scan running")
        h.backend.eject(HANG)
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "disc_removed"
        await until(lambda: d.scan is None and not d.tasks, what="scan stopped")
        assert job.rip.state == RipState.pending, "no rip was started"


async def test_disc_removed_mid_rip(harness, monkeypatch, settings):
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "30")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.rip.state == RipState.running)
        d = h.m.drives[job.drive_id]
        await until(lambda: d.rip is not None and d.rip.running, what="rip running")
        h.backend.eject(PM)
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "disc_removed" and job.rip.state == RipState.failed
        await until(lambda: d.rip is None and not d.tasks, what="rip stopped")
        assert not (settings.scratch_dir / f"job-{job.id}").exists()
        # Confirming afterwards must not resurrect it.
        with pytest.raises(ValueError):
            await h.m.confirm(job.id, MANUAL)


async def test_makemkv_killed_mid_rip(harness, settings, mkv_script):
    settings.makemkvcon = mkv_script(rip="""
        out('PRGT:5024,0,"Saving all titles to MKV files"')
        out("PRGV:0,30000,65536")
        out('MSG:5003,0,0,"Failed to save title 0 to file x.mkv","%1"')
        os.kill(os.getpid(), 9)
    """)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "rip_failed"
        assert "Failed to save title 0" in job.problem.message
        assert job.rip.state == RipState.failed


async def test_rip_exits_zero_without_saving(harness, settings, mkv_script):
    settings.makemkvcon = mkv_script(rip="out('PRGT:5024,0,\"Saving\"')")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "rip_failed"


@pytest.mark.parametrize("info, code", [
    ("replay('scan-uhd-volume-key-unknown.log')", "volume_key_unknown"),
    ("replay('scan-bad-key.log')", "makemkv_key"),
    ("out('TCOUNT:0')", "no_titles"),
    ("pass", "no_titles"),
    ("out('\\x00garbage\\xff:::'); out('TINFO:x')", "no_titles"),
])
async def test_scan_problems(harness, settings, mkv_script, info, code):
    settings.makemkvcon = mkv_script(info=info)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == code
        if code in ("volume_key_unknown", "makemkv_key"):
            assert "setup" in job.problem.actions
            assert any(p.code == code for p in h.m.problems())


async def test_no_output_then_timeout(harness, settings, mkv_script):
    settings.scan_timeout_s = 1
    settings.makemkvcon = mkv_script(info="time.sleep(60)")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "scan_timeout"
        assert "no output for 1 s" in job.problem.message
        assert h.m.drives[job.drive_id].scan is None


async def test_makemkv_missing(harness, settings):
    settings.makemkvcon = "/nonexistent/makemkvcon"
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "makemkv_missing"


async def test_second_job_on_busy_drive_is_refused(harness, monkeypatch):
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "30")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.rip.state == RipState.running)
        with pytest.raises(ValueError, match="busy"):
            await h.m.rescan_drive(job.drive_id)
        with pytest.raises(ValueError, match="busy"):
            await h.m.eject(job.drive_id)
        assert len(h.db.list_jobs()) == 1
        await h.m.cancel(job.id)


async def test_retry_needs_the_disc(harness):
    async with harness(HANG) as h:
        h.s.scan_timeout_s = 1
        job = await h.wait_job(lambda j: j.state == JobState.failed, timeout=40)
        h.backend.eject(HANG)
        await until(lambda: h.m.drives[job.drive_id].status.value == "empty")
        with pytest.raises(ValueError, match="disc back"):
            await h.m.retry(job.id)


async def test_drive_status_error_is_published(harness):
    async with harness() as h:
        d = h.m.drives["fake-pioneer"]
        h.backend.status = lambda device: (_ for _ in ()).throw(OSError("gone"))  # noqa: B023
        # A failing status call must not kill the poller.
        await until(lambda: True)
        assert h.m._poller and not h.m._poller.done()
        del h.backend.status
        assert d is h.m.drives["fake-pioneer"]


# --- state machine -----------------------------------------------------------------


async def test_every_invalid_transition_is_rejected(harness):
    async with harness() as h:
        for src, dst in product(JobState, JobState):
            if src == dst or dst in TRANSITIONS[src]:
                continue
            job = h.db.create_job("fake-pioneer", "/dev/fake-sr2")
            job.state = src
            h.db.save_job(job)
            h.m._set_state(job, dst)
            assert h.db.get_job(job.id).state == src, f"{src} -> {dst} was allowed"


async def test_terminal_states():
    assert TRANSITIONS[JobState.cancelled] == set()
    assert JobState.cancelled not in TRANSITIONS[JobState.done]


@pytest.mark.parametrize("state, rip, survives", [
    (JobState.detected, RipState.pending, False),
    (JobState.scanning, RipState.pending, False),
    (JobState.identifying, RipState.running, False),
    (JobState.review, RipState.running, False),
    (JobState.review, RipState.done, True),
    (JobState.ripping, RipState.running, False),
    (JobState.finalising, RipState.done, False),
    (JobState.done, RipState.done, True),
    (JobState.failed, RipState.failed, True),
    (JobState.cancelled, RipState.cancelled, True),
])
async def test_restart_leaves_no_job_stuck(settings, harness, state, rip, survives):
    from ripshelf.db import Database

    db = Database(settings.db_path)
    job = db.create_job("fake-ihas122", PM)
    job.state, job.rip.state = state, rip
    db.save_job(job)
    db.close()
    async with harness() as h:
        after = h.job(job.id)
        if survives:
            assert after.state == state
        else:
            assert after.state == JobState.failed
            assert after.problem.code == "interrupted" and "retry" in after.problem.actions
            assert after.rip.state != RipState.running
        assert after.state not in (JobState.detected, JobState.scanning, JobState.identifying,
                                   JobState.ripping, JobState.finalising)


async def test_interrupted_finalise_can_be_retried(settings, harness):
    from ripshelf.db import Database

    rip = settings.scratch_dir / "job-1" / "t.mkv"
    rip.parent.mkdir(parents=True)
    rip.write_bytes(b"x" * 1000)
    db = Database(settings.db_path)
    job = db.create_job("fake-ihas122", PM)
    job.state = JobState.finalising
    job.rip.state, job.rip.file = RipState.done, str(rip)
    job.identity = MANUAL
    db.save_job(job)
    db.close()
    async with harness() as h:
        assert h.job(job.id).state == JobState.failed
        await h.m.retry(job.id)
        done = await h.wait_job(lambda j: j.state == JobState.done)
        assert Path(done.destination).read_bytes() == b"x" * 1000


# --- user actions in the wrong state -----------------------------------------------


async def test_actions_in_wrong_states(harness):
    async with harness() as h:
        job = h.db.create_job("fake-ihas122", PM)
        job.state = JobState.done
        h.db.save_job(job)
        with pytest.raises(ValueError):
            await h.m.confirm(job.id, MANUAL)
        with pytest.raises(ValueError):
            await h.m.identify_again(job.id)
        with pytest.raises(ValueError):
            await h.m.cancel(job.id)
        with pytest.raises(ValueError):
            await h.m.retry(job.id)
        with pytest.raises(ValueError):
            await h.m.confirm(999, MANUAL)
        job.state = JobState.finalising
        h.db.save_job(job)
        with pytest.raises(ValueError, match="copying"):
            await h.m.cancel(job.id)


# --- finalise ------------------------------------------------------------------------


async def confirm_and_wait(h, state=JobState.failed):
    job = await h.wait_job(lambda j: j.state == JobState.review)
    await h.m.confirm(job.id, MANUAL)
    return await h.wait_job(lambda j: j.state == state)


async def test_destination_exists_is_never_overwritten(harness, settings):
    dest = settings.library_dir / "Movies/Practical Magic (1998)/Practical Magic (1998).mkv"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"precious")
    async with harness(PM) as h:
        job = await confirm_and_wait(h, JobState.clash)
        assert job.clash.files[0].path == str(dest)
        assert dest.read_bytes() == b"precious"
        await asyncio.sleep(0.5)
        assert h.job(job.id).state == JobState.clash, "it waits for a decision"


async def test_library_missing(harness, settings):
    settings.library_dir = settings.library_dir / "not-mounted"
    async with harness(PM) as h:
        assert any(p.code == "library_missing" for p in h.m.problems())
        job = await confirm_and_wait(h)
        assert job.problem.code == "library_missing"
        assert not settings.library_dir.exists(), "the library root is never created"


@needs_unix_permissions
async def test_library_read_only(harness, settings):
    settings.library_dir.chmod(0o555)
    try:
        async with harness(PM) as h:
            job = await confirm_and_wait(h)
            assert job.problem.code == "library_missing"
            assert "not writable" in job.problem.message
    finally:
        settings.library_dir.chmod(0o755)


async def test_copy_failure_leaves_no_partial(harness, settings, monkeypatch):
    from ripshelf import library

    real_copy = library.copy_verified

    def full_disk(src, dst, on_progress=None, overwrite=False):
        def boom(done, total, _verifying):
            raise OSError(28, "No space left on device")
        return real_copy(src, dst, boom, overwrite)

    monkeypatch.setattr(library, "copy_verified", full_disk)
    async with harness(PM) as h:
        job = await confirm_and_wait(h)
        assert job.problem.code == "copy_failed" and "No space" in job.problem.message
        folder = settings.library_dir / "Movies/Practical Magic (1998)"
        assert not any(p.name.endswith(".partial") for p in folder.rglob("*"))
        # Free space and retry, and it completes.
        monkeypatch.setattr(library, "copy_verified", real_copy)
        await h.m.retry(job.id)
        await h.wait_job(lambda j: j.state == JobState.done)


async def test_scratch_full_during_rip(harness, settings, mkv_script):
    settings.makemkvcon = mkv_script(rip="""
        out('PRGT:5024,0,"Saving all titles to MKV files"')
        out('MSG:5003,0,0,"Failed to save title 0: no space left on device","%1"')
        sys.exit(1)
    """)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert "no space" in job.problem.message


async def test_fail_records_problem_when_already_failed(harness):
    async with harness() as h:
        job = h.db.create_job("fake-ihas122", PM)
        job.state = JobState.failed
        h.db.save_job(job)
        h.m._fail(JobRecord.model_validate(job.model_dump()), Problem(code="x", message="second"))
        assert h.job(job.id).problem.code == "x"
        assert h.job(job.id).finished_at is None or h.job(job.id).finished_at <= now()


async def test_unexpected_crash_fails_the_job_instead_of_leaving_it_stuck(harness, monkeypatch):
    async with harness(PM) as h:
        async def broken(job):
            raise RuntimeError("bug in finalise")

        monkeypatch.setattr(h.m, "_finalise", broken)
        job = await confirm_and_wait(h)
        assert job.problem.code == "internal" and "bug in finalise" in job.problem.message
        assert "retry" in job.problem.actions


async def test_libredrive_required_is_a_setup_problem(harness, settings, mkv_script):
    # A real log of a UHD disc in the Pioneer before its private data was imported.
    settings.makemkvcon = mkv_script(info="replay('scan-uhd-libredrive-required.log')")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        assert job.problem.code == "decrypt_failed"
        assert "_private_data.tar" in job.problem.message
        assert any(p.code == "decrypt_failed" for p in h.m.problems())


async def test_a_handled_disc_is_read_once_per_insertion(harness, monkeypatch):
    """Idle drives with a finished disc in them must not be read on every poll (activity lights)."""
    async with harness(PM) as h:
        await h.wait_job(lambda j: j.state == JobState.review)
        calls = []
        real = h.backend.disc_identity

        async def counting(device):
            calls.append(device)
            return await real(device)

        monkeypatch.setattr(h.backend, "disc_identity", counting)
        # The disc stays in; many polls go by.
        await asyncio.sleep(h.s.poll_interval_s * 10)
        assert calls == []
        # Out and back in, so read exactly once more.
        h.backend.eject(PM)
        await until(lambda: h.m.drives["fake-ihas122"].status.value == "empty")
        h.backend.insert(PM)
        await until(lambda: calls)
        await asyncio.sleep(h.s.poll_interval_s * 5)
        assert calls == [PM]
