"""Deleting jobs from the history, re-detecting drives, and the app log."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ripshelf import applog
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import Candidate, JobsDeleted, JobState

from .conftest import until

PM = "/dev/fake-sr1"


async def filed_film(h):
    job = await h.wait_job(lambda j: j.state == JobState.review)
    await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
    done = await h.wait_job(lambda j: j.state == JobState.done)
    return h.job(done.id)


async def test_deleting_a_film_with_its_files_removes_them_and_their_empty_folder(harness, settings):
    async with harness(PM) as h:
        done = await filed_film(h)
        film = Path(done.destination)
        extra = film.parent / "Featurettes" / "Growing the Orchard.mkv"
        extra.parent.mkdir()
        extra.write_bytes(b"x")
        done.filed_extras = {3: str(extra)}
        h.db.save_job(done)
        assert (film.parent / ".disc" / "disc.json").is_file()
        _, files = h.m.delete_jobs([done.id], delete_files=True)
        assert {str(film), str(extra)} <= set(files) and any(f.endswith("disc.json") for f in files)
        assert not film.parent.exists(), "no folder left holding only the .disc record"
        assert settings.movies.is_dir(), "the films folder itself stays"


async def test_another_version_kept_beside_the_film_keeps_the_disc_record(harness, settings):
    async with harness(PM) as h:
        done = await filed_film(h)
        film = Path(done.destination)
        other = film.with_name("Moonlit Orchard (1998) - 2160p.mkv")
        other.write_bytes(b"the other version")
        _, files = h.m.delete_jobs([done.id], delete_files=True)
        assert files == [str(film)] and other.exists() and (film.parent / ".disc" / "disc.json").is_file()


async def test_deleting_files_never_reaches_outside_the_library(harness, settings, tmp_path, caplog):
    async with harness(PM) as h:
        done = await filed_film(h)
        film = Path(done.destination)
        outside = tmp_path / "not-the-library.mkv"
        outside.write_bytes(b"keep me")
        done.filed_extras = {3: str(outside), 4: str(settings.movies)}  # odd records, or tampered ones
        h.db.save_job(done)
        with caplog.at_level(logging.WARNING):
            _, files = h.m.delete_jobs([done.id], delete_files=True)
        assert str(film) in files and str(outside) not in files and outside.read_bytes() == b"keep me"
        assert settings.movies.is_dir()


async def test_a_full_disc_backup_is_kept_when_its_job_is_deleted(harness, settings):
    settings.rip_mode = "backup"
    async with harness("/dev/fake-sr2") as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.confirm(job.id, Candidate(source="manual", title="The Brass Heron", year=2024))
        done = await h.wait_job(lambda j: j.state == JobState.done)
        assert h.m.delete_jobs([done.id], delete_files=True) == ([done.id], [])
        assert (Path(done.destination) / "BDMV").is_dir()


@pytest.mark.slow
async def test_delete_finished_job_keeps_library_and_disc_cache(harness, settings):
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
        done = await h.wait_job(lambda j: j.state == JobState.done)
        work = h.m.work_dir(done)
        assert work.exists()
        seen = []
        with h.bus.subscribe() as q:
            assert h.m.delete_jobs([done.id, done.id]) == ([done.id], [])
            while not q.empty():
                seen.append(q.get_nowait())
        assert h.db.get_job(done.id) is None and h.db.events(done.id) == []
        assert not work.exists()
        assert JobsDeleted(ids=[done.id]).model_dump_json() in seen
        assert h.m.drives["fake-ihas122"].job_id is None
        # The film stays in the library and the disc is still recognised.
        assert (settings.library_dir / "Movies/Moonlit Orchard (1998)/Moonlit Orchard (1998).mkv").exists()
        await h.insert(PM)
        again = await until(lambda: next(iter(h.db.list_jobs()), None))
        again = await h.wait_job(lambda j: j.id == again.id and j.identity is not None)
        assert again.identity_source == "cache"


async def test_running_or_unknown_jobs_cannot_be_deleted(harness, settings, mkv_script):
    settings.makemkvcon = mkv_script(info="import time; time.sleep(30)")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.scanning)
        try:
            h.m.delete_jobs([job.id])
            raise AssertionError("deleted a running job")
        except ValueError as e:
            assert "still running" in str(e)
        try:
            h.m.delete_jobs([999])
            raise AssertionError("deleted a missing job")
        except KeyError:
            pass
        assert h.db.get_job(job.id)


def test_delete_api(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        m = c.app.state.manager
        a = m.db.create_job("fake-ihas122", "/dev/fake-sr1")
        b = m.db.create_job("fake-ihas122", "/dev/fake-sr1")
        live = m.db.create_job("fake-ihas122", "/dev/fake-sr1")
        for j, st in ((a, JobState.failed), (b, JobState.cancelled), (live, JobState.review)):
            j.state = st
            m.db.save_job(j)
        assert c.post("/api/jobs/delete", json={"ids": [a.id, live.id]}).status_code == 409
        assert m.db.get_job(a.id), "all or nothing"
        r = c.post("/api/jobs/delete", json={"ids": [a.id, b.id]})
        assert r.status_code == 200 and r.json() == {"deleted": [a.id, b.id], "files_deleted": []}
        assert c.delete(f"/api/jobs/{live.id}").status_code == 409
        assert c.delete("/api/jobs/12345").status_code == 404
        assert c.post("/api/jobs/delete", json={"ids": []}).status_code == 422
        live.state = JobState.done
        m.db.save_job(live)
        assert c.delete(f"/api/jobs/{live.id}").status_code == 204
        assert c.get("/api/jobs").json() == []


def test_redetect_finds_a_new_drive(settings):
    backend = FakeDrives(present=set())
    with TestClient(create_app(settings, backend=backend)) as c:
        n = len(c.get("/api/drives").json())
        import dataclasses

        extra = dataclasses.replace(backend.discover([])[0], id="late-drive", device="/dev/fake-sr9")
        backend.discover = lambda uhd, orig=backend.discover: [*orig(uhd), extra]
        drives = c.post("/api/drives/redetect").json()
        assert len(drives) == n + 1 and "late-drive" in {d["id"] for d in drives}


def test_log_buffer_levels_and_after():
    buf = applog.BufferHandler(size=3)
    log = logging.getLogger("ripshelf.test-buffer")
    log.addHandler(buf)
    log.setLevel(logging.DEBUG)
    try:
        log.debug("one")
        log.info("two %s", "args")
        log.warning("three")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            log.exception("four")
    finally:
        log.removeHandler(buf)
    entries, last = buf.since(0, "DEBUG")
    assert last == 4 and [e.message.splitlines()[0] for e in entries] == ["two args", "three", "four"]
    assert "RuntimeError: boom" in entries[-1].message
    assert [e.message for e in buf.since(0, "WARNING")[0]][0] == "three"
    assert [e.seq for e in buf.since(3, "debug")[0]] == [4]
    assert buf.since(0, "nonsense")[0], "an unknown level shows everything"


def test_logs_api_and_file(settings):
    applog.install_buffer()
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        logging.getLogger("ripshelf.test").warning("hello from the test")
        page = c.get("/api/logs", params={"level": "WARNING"}).json()
        assert any(e["message"] == "hello from the test" for e in page["entries"])
        assert page["file"] is None and c.get("/api/logs/file").status_code == 404
        later = c.get("/api/logs", params={"after": page["last_seq"]}).json()
        assert all(e["seq"] > page["last_seq"] for e in later["entries"])
        path = applog.install_file(settings.data_dir)
        try:
            logging.getLogger("ripshelf.test").warning("to the file")
            assert "to the file" in path.read_text()
            assert c.get("/api/logs").json()["file"] == str(path)
            assert "to the file" in c.get("/api/logs/file").text
        finally:
            for h in [h for h in logging.getLogger().handlers if getattr(h, "baseFilename", None) == str(path)]:
                logging.getLogger().removeHandler(h)
                h.close()


def test_finished_pages(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        m = c.app.state.manager
        ids = []
        for i in range(7):
            j = m.db.create_job("fake-ihas122", "/dev/fake-sr1")
            j.state = JobState.review if i == 3 else (JobState.done, JobState.failed, JobState.cancelled)[i % 3]
            m.db.save_job(j)
            ids.append(j.id)
        finished = [i for n, i in enumerate(ids) if n != 3][::-1]
        page = c.get("/api/jobs/finished?limit=2").json()
        assert [j["id"] for j in page["jobs"]] == finished[:2] and page["older"] == 4
        page = c.get(f"/api/jobs/finished?limit=10&before={finished[1]}").json()
        assert [j["id"] for j in page["jobs"]] == finished[2:] and page["older"] == 0
        assert c.get(f"/api/jobs/finished?limit=0&before={finished[1]}").json() == {"jobs": [], "older": 4}
        assert [j["id"] for j in c.get(f"/api/jobs?limit=3&before={ids[4]}").json()] == ids[3::-1][:3]
        assert c.get("/api/jobs?limit=0").status_code == 422
