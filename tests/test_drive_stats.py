"""All-time drive statistics, recorded as jobs finish, backfilled, kept when history is cleared."""

from __future__ import annotations

import dataclasses
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from ripshelf.db import Database, now
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.makemkv import runner
from ripshelf.makemkv.parse import RipProgress
from ripshelf.models import Candidate, DiscKind, JobState, MediaKind, RipState

PM = "/dev/fake-sr1"


async def test_finished_rip_is_counted_and_survives_deletion(harness):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
        done = await h.wait_job(lambda j: j.state == JobState.done)
        assert done.rip.size_bytes and done.rip.seconds is not None
        st = next(s for s in h.m.drive_stats() if s.drive_id == "fake-ihas122")
        assert st.connected and st.discs_ripped == 1 and st.movies == 1
        assert st.data_bytes == done.rip.size_bytes
        h.m.delete_jobs([done.id])
        st = next(s for s in h.m.drive_stats() if s.drive_id == "fake-ihas122")
        assert st.discs_ripped == 1 and st.data_bytes == done.rip.size_bytes


def _finished(db: Database, drive: str, state: JobState, ripped: bool, kind=DiscKind.dvd, size=0):
    job = db.create_job(drive, "/dev/sr0")
    job.state = state
    job.finished_at = now()
    job.disc.kind = kind
    if ripped:
        job.rip.state = RipState.done
        job.rip.progress = RipProgress(bytes_done=size, bytes_total=size)
    db.save_job(job)
    return job


def test_backfill_from_history(tmp_path):
    path = tmp_path / "ripshelf.db"
    db = Database(path)
    db.conn.execute("DROP TABLE rip_stats")  # history from before the statistics existed
    a = _finished(db, "d1", JobState.done, True, DiscKind.uhd, 50 * 2**30)
    _finished(db, "d1", JobState.done, True, DiscKind.dvd, 5 * 2**30)
    _finished(db, "d1", JobState.failed, False)
    _finished(db, "d1", JobState.done, False)  # skipped, because it's already in the library
    _finished(db, "d2", JobState.cancelled, False)
    db.create_job("d2", "/dev/sr1")  # still running, so not counted
    t0 = now()
    for ts, msg in ((t0, "Ripping main feature"), (t0 + timedelta(seconds=600), "Rip complete: x.mkv (50 GB)")):
        db.conn.execute("INSERT INTO events (job_id, ts, kind, message) VALUES (?,?,?,?)",
                        (a.id, ts.isoformat(), "rip", msg))
    db.close()

    db = Database(path)
    stats = {s.drive_id: s for s in db.drive_stats()}
    d1 = stats["d1"]
    assert d1.discs_ripped == 2 and d1.data_bytes == 55 * 2**30
    assert d1.by_kind == {"uhd": 1, "dvd": 1}
    assert d1.failed == 1 and d1.skipped == 1 and d1.cancelled == 0
    assert d1.rip_seconds == 600 and d1.timed_bytes == 50 * 2**30
    assert stats["d2"].discs_ripped == 0 and stats["d2"].cancelled == 1
    # A second start counts nothing twice.
    db.close()
    db = Database(path)
    assert {s.drive_id: s for s in db.drive_stats()}["d1"].discs_ripped == 2


def test_retried_job_counts_once(tmp_path):
    db = Database(tmp_path / "ripshelf.db")
    job = _finished(db, "d1", JobState.failed, False)
    db.record_stats(job)
    job.state = JobState.done
    job.media = MediaKind.tv
    job.rip.state = RipState.done
    job.rip.size_bytes = 10
    job.rip.seconds = 2.0
    db.record_stats(job)
    (st,) = db.drive_stats()
    assert (st.discs_ripped, st.failed, st.tv, st.data_bytes, st.rip_seconds) == (1, 0, 1, 10, 2.0)


def test_stats_api_lists_attached_drives_without_history(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        m = c.app.state.manager
        m.db.record_stats(_finished(m.db, "gone-drive", JobState.done, True, size=7))
        body = c.get("/api/drives/stats").json()
        by_id = {s["drive_id"]: s for s in body}
        assert by_id["gone-drive"]["connected"] is False and by_id["gone-drive"]["data_bytes"] == 7
        attached = [s for s in body if s["connected"]]
        assert attached and all(s["discs_ripped"] == 0 for s in attached)


class MovedDrives(FakeDrives):
    """The iHAS122 as it now discovers, with a serial-based id, with the port-based id it had."""

    def discover(self, uhd_models):
        return [dataclasses.replace(hw, legacy_id="ihas122-0a1b") if hw.id == "fake-ihas122" else hw
                for hw in super().discover(uhd_models)]


def test_old_port_based_id_is_carried_over(settings):
    db = Database(settings.db_path)
    _finished(db, "ihas122-0a1b", JobState.done, True, size=5)
    db.record_stats(db.list_jobs()[0])
    db.save_drive_data("ihas122-0a1b", {"model": "iHAS122", "serial": "SN0000000001"})
    db.conn.close()
    settings.save_overrides({"drive_modes": {"ihas122-0a1b": "backup", "other": "titles"},
                             "disabled_drives": ["ihas122-0a1b"], "drive_names": {"ihas122-0a1b": "Top bay"}})
    conf = runner.drive_home(settings.makemkv_dir, "ihas122-0a1b") / ".MakeMKV"
    conf.mkdir(parents=True)
    (conf / "dump_0123.tgz").write_bytes(b"dump")
    with TestClient(create_app(settings, backend=MovedDrives(present=set()))) as c:
        m = c.app.state.manager
        by_id = {s["drive_id"]: s for s in c.get("/api/drives/stats").json()}
        assert "ihas122-0a1b" not in by_id
        assert by_id["fake-ihas122"]["discs_ripped"] == 1 and by_id["fake-ihas122"]["name"] == "Top bay"
        [job] = c.get("/api/jobs").json()
        assert job["drive_id"] == "fake-ihas122" and m.db.latest_job_for_drive("fake-ihas122")
        assert m.db.drive_data("fake-ihas122")["serial"] == "SN0000000001"
        assert m.db.drive_data("ihas122-0a1b") == {}
    assert settings.drive_modes == {"other": "titles", "fake-ihas122": "backup"}
    assert settings.disabled_drives == ["fake-ihas122"]
    assert json.loads(settings.settings_file.read_text())["drive_names"] == {"fake-ihas122": "Top bay"}
    assert runner.has_dump(settings.makemkv_dir, "fake-ihas122")
    assert not conf.exists()


def test_home_is_merged_when_the_new_one_exists(tmp_path):
    for d, files in (("old", {"_private_data.tar": "p", "dump_1.tgz": "d"}), ("new", {"settings.conf": "s"})):
        conf = tmp_path / d / ".MakeMKV"
        conf.mkdir(parents=True)
        for k, v in files.items():
            (conf / k).write_text(v)
    assert runner.migrate_home(tmp_path, "old", "new")
    assert sorted(p.name for p in (tmp_path / "new/.MakeMKV").iterdir()) == \
        ["_private_data.tar", "dump_1.tgz", "settings.conf"]
    assert not runner.migrate_home(tmp_path, "old", "new"), "nothing more to copy"
    assert not runner.migrate_home(tmp_path, "gone", "new")
