"""Closing and opening trays from the drive cards, locally and on workers."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ripshelf.dev.fake import DRIVES, FakeDrives
from ripshelf.hw.drives import SysfsDrives
from ripshelf.main import create_app
from ripshelf.workers.agent import Agent
from ripshelf.workers.hub import RemoteBackend, WorkerHub

from .conftest import posix_only


def test_close_tray_api(settings):
    backend = FakeDrives(present=set())
    calls = []
    backend.close_tray = calls.append
    with TestClient(create_app(settings, backend=backend)) as c:
        assert c.post("/api/drives/fake-ihas122/close").status_code == 204
        assert calls == ["/dev/fake-sr1"]
        assert c.post("/api/drives/nope/close").status_code == 404

        def jammed(device):
            raise OSError(5, "Input/output error")

        backend.close_tray = jammed
        r = c.post("/api/drives/fake-ihas122/close")
        assert r.status_code == 409 and "can't close themselves" in r.json()["detail"]


def test_close_tray_refused_while_makemkv_has_the_drive(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        d = c.app.state.manager.drives["fake-ihas122"]
        d.scan = object()
        try:
            r = c.post("/api/drives/fake-ihas122/close")
            assert r.status_code == 409 and "busy" in r.json()["detail"]
        finally:
            d.scan = None


@posix_only
def test_close_tray_missing_device_raises(tmp_path):
    with pytest.raises(OSError):
        SysfsDrives().close_tray(str(tmp_path / "sr9"))


def test_remote_close_tray_needs_the_worker_online():
    with pytest.raises(OSError, match="offline"):
        RemoteBackend(WorkerHub(), "gone").close_tray("/dev/sr0")


async def test_worker_runs_close_tray(settings):
    backend = FakeDrives(present=set())
    calls = []
    backend.close_tray = calls.append
    agent = Agent(settings, backend)
    hw = DRIVES["/dev/fake-sr1"][0]
    agent.drives = {"d1": hw}
    agent.handle({"type": "close_tray", "drive": "d1"})
    await asyncio.gather(*agent.tasks)
    assert calls == ["/dev/fake-sr1"]
    await agent.client.aclose()


@pytest.mark.slow
async def test_stale_disc_status_after_eject_starts_no_job(harness, settings, monkeypatch):
    # A worker's drive status is up to a heartbeat old, so just after Eject it can still say
    # "disc". That must not start a new job on the disc that is leaving.
    from ripshelf.models import JobState

    from .conftest import until

    monkeypatch.setattr("ripshelf.jobs.EJECT_SETTLE_S", 1.0)
    settings.auto_eject = False
    async with harness("/dev/fake-sr1") as h:
        first = await h.wait_job(lambda j: j.state in (JobState.review, JobState.ripping))
        await h.wait_job(lambda j: j.rip.state == "done", timeout=30)
        h.backend.eject = lambda device: None  # the tray "opens" but the status stays "disc"
        await h.m.eject("fake-ihas122")
        await asyncio.sleep(0.6)
        assert [j.id for j in h.db.list_jobs()] == [first.id]
        assert h.m.drives["fake-ihas122"].ejecting_until > 0
        # Seeing the tray empty ends the wait at once.
        h.backend.present.discard("/dev/fake-sr1")
        await until(lambda: h.m.drives["fake-ihas122"].ejecting_until == 0.0)
