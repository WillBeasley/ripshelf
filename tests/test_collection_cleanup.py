"""A show's collection follows the library: a deleted job's disc leaves it, files deleted
by hand stop counting, and a disc can be taken out of it by hand."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import Episode, EpisodeRef, TitleRole
from ripshelf.tv import naming

from .test_contribute import pippa_job, put


def filed(h, settings, job_id=2, episodes=(1, 2)):
    """A finished Pippa & Goose disc in the collection, its episodes in the library with
    the .disc record of them."""
    job = pippa_job(job_id)
    rows = [r for r in job.tv.mapping if r.role == TitleRole.episode]
    for r, n in zip(rows, episodes, strict=False):
        r.episodes = [EpisodeRef(season=3, episode=n)]
    job = put(h, job)
    h.m._add_to_collection(job)
    h.db.save_job(job)
    show, folder = job.tv.show, naming.show_folder(settings.tv, job.tv.show)
    titles = {}
    for r in rows:
        p = naming.episode_path(settings.tv, show, [Episode(season=e.season, episode=e.episode) for e in r.episodes])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        titles[str(r.title_index)] = {"file": str(p.relative_to(folder))}
    meta = naming.disc_meta_folder(settings.tv, show, job.disc.label, job.id)
    meta.mkdir(parents=True)
    (meta / "disc.json").write_text(json.dumps({"titles": titles}))
    return h.db.get_job(job.id), folder


async def test_a_deleted_jobs_disc_leaves_the_collection(harness, settings):
    async with harness() as h:
        job, folder = filed(h, settings)
        assert h.db.collection_for_show(job.tv.show.tmdb_id)
        deleted, files = h.m.delete_jobs([job.id])
        assert deleted == [job.id] and files == []
        assert h.db.collection_for_show(job.tv.show.tmdb_id) is None  # its only disc
        assert any(folder.rglob("*.mkv"))  # the library is kept


async def test_deleting_a_job_can_delete_its_files(harness, settings):
    async with harness() as h:
        job, folder = filed(h, settings)
        other = folder / "Season 03" / "something else.mkv"
        other.write_bytes(b"y")
        outside = settings.library_dir / "elsewhere.mkv"
        outside.write_bytes(b"z")
        meta = naming.disc_meta_folder(settings.tv, job.tv.show, job.disc.label, job.id)
        record = json.loads((meta / "disc.json").read_text())
        record["titles"]["99"] = {"file": "../../elsewhere.mkv"}  # never outside the TV folder
        (meta / "disc.json").write_text(json.dumps(record))
        _, files = h.m.delete_jobs([job.id], delete_files=True)
        assert len([f for f in files if f.endswith(".mkv")]) == 2
        assert other.exists() and outside.exists() and not meta.exists()
        assert sorted(p.name for p in folder.rglob("*")) == ["Season 03", "something else.mkv"]


async def test_files_deleted_by_hand_stop_counting(harness, settings):
    async with harness() as h:
        job, folder = filed(h, settings)
        show = job.tv.show
        assert h.m._next_for(show, 3)[2] == 3  # carries on after episode 2
        c = h.db.collection_for_show(show.tmdb_id)
        cells = {x.episode.episode: x.status for g in h.m.collection_detail(c.id).seasons for x in g.cells}
        assert cells[1] == cells[2] == "present"
        for p in folder.rglob("*.mkv"):
            p.unlink()
        assert h.m._next_for(show, 3)[2] is None  # nothing left to carry on from
        cells = {x.episode.episode: x.status for g in h.m.collection_detail(c.id).seasons for x in g.cells}
        assert cells[1] == cells[2] == "gone"
        settings.tv_dir = settings.library_dir / "offline"  # a library that can't be seen isn't empty
        assert h.m._next_for(show, 3)[2] == 3


def test_a_disc_can_be_taken_out_of_the_collection(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        m = client.app.state.manager
        job, _ = filed(type("H", (), {"m": m, "db": m.db})(), settings)
        second, _ = filed(type("H", (), {"m": m, "db": m.db})(), settings, job_id=3, episodes=(3, 4))
        c = m.db.collection_for_show(job.tv.show.tmdb_id)
        r = client.delete(f"/api/collections/{c.id}/discs/{job.id}")
        assert r.status_code == 200 and [d["job_id"] for d in r.json()["discs"]] == [second.id]
        assert client.delete(f"/api/collections/{c.id}/discs/{job.id}").status_code == 404
        r = client.delete(f"/api/collections/{c.id}/discs/{second.id}")
        assert r.status_code == 200 and r.json() is None
        assert client.get(f"/api/collections/{c.id}").status_code == 404


@pytest.fixture(autouse=True)
def _tv_in_library(settings):
    settings.tv.mkdir(parents=True, exist_ok=True)
