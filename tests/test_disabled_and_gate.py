"""Disabled drives are left alone; the start-up gate names who holds it and gives up."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ripshelf.makemkv import runner
from ripshelf.models import JobState

from .conftest import until

PM = "/dev/fake-sr1"
ID = "fake-ihas122"


async def test_disabled_drive_gets_no_job_and_no_actions(harness, settings):
    settings.disabled_drives = [ID]
    async with harness(PM) as h:
        d = h.m.drives[ID]
        await until(lambda: d.identified)
        assert d.label == "MOONLIT_ORCHARD" and h.m.drive_view(d).disc_label == "MOONLIT_ORCHARD"
        assert h.db.list_jobs() == []
        for action in (h.m.rescan_drive(ID), h.m.close_tray(ID), h.m.eject(ID)):
            with pytest.raises(ValueError, match="disabled"):
                await action
        assert PM in h.backend.present, "not ejected"
        # Enabled again, the disc is read and handled like a new one.
        settings.disabled_drives = []
        h.m.drives_toggled()
        job = await h.wait_job(lambda j: j.state in (JobState.review, JobState.ripping))
        assert job.drive_id == ID and d.label is None


async def test_discs_wait_until_the_makemkv_eula_is_accepted(harness, settings, tmp_path):
    eula = tmp_path / "eula_en_linux.txt"
    eula.write_text("MakeMKV EULA", encoding="utf-8")
    settings.makemkv_eula_file = eula
    async with harness(PM) as h:
        d = h.m.drives[ID]
        await until(lambda: d.identified)
        assert h.db.list_jobs() == []
        with pytest.raises(ValueError, match="EULA"):
            await h.m.rescan_drive(ID)
        problem = next(p for p in h.m.problems() if p.code == "makemkv_eula")
        assert problem.command == 'RIPSHELF_ACCEPT_MAKEMKV_EULA: "yes"'
        settings.accept_makemkv_eula = True
        assert not any(p.code == "makemkv_eula" for p in h.m.problems())
        h.m.drives_toggled()
        await h.wait_job(lambda j: j.state in (JobState.review, JobState.ripping))


async def test_retry_needing_the_drive_is_refused_when_disabled(harness, settings, mkv_script):
    settings.makemkvcon = mkv_script(info="out('TCOUNT:0')")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.failed)
        settings.disabled_drives = [ID]
        with pytest.raises(ValueError, match="disabled"):
            await h.m.retry(job.id)


async def test_gate_names_the_holder_and_gives_up_after_the_cap():
    gate = runner.OpenGate(max_hold_s=0.3)
    release = await gate.enter("/dev/sr2")
    assert gate.busy and gate.holder == "/dev/sr2" and gate.since is not None
    loop = asyncio.get_running_loop()
    started = loop.time()
    release2 = await asyncio.wait_for(gate.enter("/dev/sr1"), 2)  # the cap frees it
    assert 0.25 < loop.time() - started < 1.5
    assert gate.holder == "/dev/sr1"
    release()  # the first holder's late release must not free the second's slot
    assert gate.busy and gate.holder == "/dev/sr1"
    release2()
    assert not gate.busy and gate.holder is None


def test_default_cap_is_short():
    assert runner.OPEN_GATE_MAX_S <= 60


async def test_waiting_message_names_the_drive(harness, settings, mkv_script):
    # sr1's scan never opens its disc, so sr2 waits at the gate.
    settings.makemkvcon = mkv_script(info="import time; time.sleep(30) if 'sr1' in args[1] else replay('scan-dvd-moonlit-orchard.log')")
    async with harness(PM) as h:
        await h.wait_job(lambda j: j.state == JobState.scanning)
        h.backend.insert("/dev/fake-sr0")
        later = await until(lambda: next((j for j in h.db.list_jobs() if j.device == "/dev/fake-sr0"), None))
        msgs = await until(lambda: [e.message for e in h.db.events(later.id) if "Waiting for" in e.message])
    assert msgs[0].startswith("Waiting for /dev/fake-sr1 to open its disc (for ")
    assert "at most" in msgs[0]


async def test_missing_java_is_a_setup_problem(harness, settings):
    # The Brass Heron's real scan log has MSG 3345 (no Java runtime).
    async with harness("/dev/fake-sr2") as h:
        job = await h.wait_job(lambda j: j.state not in (JobState.detected, JobState.scanning), timeout=60)
        assert any("no Java runtime" in e.message for e in h.db.events(job.id))
        assert "java_missing" in [p.code for p in h.m.problems()]


async def test_no_jobs_until_the_setup_is_finished(harness, settings):
    settings.setup_complete = False
    async with harness(PM) as h:
        d = h.m.drives[ID]
        await until(lambda: d.identified)
        assert d.label == "MOONLIT_ORCHARD" and h.db.list_jobs() == []  # shown, not ripped
        with pytest.raises(ValueError, match="setup"):
            await h.m.rescan_drive(ID)
        settings.setup_complete = True  # the setup page's Finish (or Skip)
        h.m.drives_toggled()
        job = await h.wait_job(lambda j: j.state in (JobState.review, JobState.ripping))
        assert job.drive_id == ID


async def test_a_virtual_drive_starts_disabled(harness, settings, monkeypatch):
    from ripshelf.dev.fake import FakeDrives

    real = FakeDrives.discover

    def discover(self, uhd_models):
        # Copies, because the fake drives are shared by every test. The iHAS122 plays a VM's drive.
        return [replace(hw, virtual=hw.id == ID) for hw in real(self, uhd_models)]

    monkeypatch.setattr(FakeDrives, "discover", discover)
    async with harness(PM) as h:
        assert ID in settings.disabled_drives and len(settings.disabled_drives) == 1
        await until(lambda: h.m.drives[ID].identified)
        assert h.db.list_jobs() == []
        # Enabled in Settings, it stays enabled. Only a drive seen for the first time is disabled.
        settings.save_overrides({"disabled_drives": []})
        h.m.discover()
        assert settings.disabled_drives == []
