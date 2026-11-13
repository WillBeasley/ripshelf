"""A finished job identified again: back to Review, and on confirming, its files move to
the new title's names (nothing ripped again), never over a file already there, and a
move that fails half way is retried, or put back."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ripshelf import library
from ripshelf.db import now
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import (
    Candidate,
    CoverScan,
    Episode,
    EpisodeRef,
    JobState,
    Show,
    TitleMapUpdate,
    TitleRole,
)
from ripshelf.tv import naming

from .conftest import until
from .test_api import wait
from .test_collection_cleanup import filed
from .test_contribute import heron_job, put

WRONG = Candidate(tmdb_id=101, title="Moonlit Orchard", year=2004)
RIGHT = Candidate(tmdb_id=202, title="Captain Comet", year=2011)
SEAGULL = Show(tmdb_id=303, name="Seagull Manor", year=2015)


@pytest.fixture(autouse=True)
def _library(settings):
    settings.movies.mkdir(parents=True, exist_ok=True)
    settings.tv.mkdir(parents=True, exist_ok=True)


def scan(name: str, path: Path) -> CoverScan:
    return CoverScan(id=name, kind="front", source="upload", created_at=now(), content_type="image/jpeg",
                     width=10, height=10, sha256="0" * 64, size_bytes=1, library_path=str(path))


def filed_film(h, settings, identity=WRONG):
    """A film filed as ``identity``: the film, its kept original, an extra, the .disc
    record, a cover scan, and the artwork Emby saved for it."""
    job = put(h, heron_job(identity=identity, identity_source="auto", quick_fp="quick-1"))
    folder, dest = library.movie_paths(settings.movies, identity.title, identity.year, uhd=True)
    files = {dest: b"film", dest.with_name(f"{dest.stem} - original.mkv"): b"original",
             folder / "featurettes" / "Making Of.mkv": b"extra", folder / "scans" / "front.jpg": b"jpg",
             folder / "poster.jpg": b"emby", folder / "movie.nfo": b"emby"}
    for p, data in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    library.write_disc_folder(folder, {"title": identity.title, "tmdb_id": identity.tmdb_id,
                                       "titles": {"0": {"file": dest.name, "role": "main_feature"}}}, None, None)
    job.destination = str(dest)
    job.filed_extras = {3: str(folder / "featurettes" / "Making Of.mkv")}
    job.scans = [scan("s1", folder / "scans" / "front.jpg")]
    job.candidates = [WRONG, RIGHT]
    h.db.save_job(job)
    return h.db.get_job(job.id), folder, dest


async def done(h, job_id):
    return await until(lambda: (j := h.db.get_job(job_id)).state in (JobState.done, JobState.failed) and j,
                       what="the move to finish")


# --- films --------------------------------------------------------------------------


async def test_a_film_moves_to_the_right_title(harness, settings):
    async with harness() as h:
        job, old, _ = filed_film(h, settings)
        job = await h.m.reidentify(job.id)
        assert job.state == JobState.review and job.identity is None and job.refile.title == "Moonlit Orchard (2004)"
        assert job.candidates[0].title == "Captain Comet"  # the wrong one goes last
        assert old.is_dir()  # nothing moves before the right one is confirmed
        await h.m.confirm(job.id, RIGHT)
        job = await done(h, job.id)
        assert job.state == JobState.done and job.refile is None and job.problem is None
        new = settings.movies / "Captain Comet (2011)"
        assert Path(job.destination) == new / "Captain Comet (2011) - 2160p.mkv"
        assert Path(job.destination).read_bytes() == b"film"
        assert (new / "Captain Comet (2011) - 2160p - original.mkv").read_bytes() == b"original"
        assert job.filed_extras == {3: str(new / "featurettes" / "Making Of.mkv")}
        assert job.scans[0].library_path == str(new / "scans" / "front.jpg")
        record = json.loads((new / ".disc" / "disc.json").read_text())
        assert (record["title"], record["tmdb_id"], record["identity_source"]) == ("Captain Comet", 202, "user")
        assert record["titles"]["0"]["file"] == "Captain Comet (2011) - 2160p.mkv"
        assert record["reidentified"][0]["was"] == "Moonlit Orchard (2004)"
        assert not old.exists()  # Emby's artwork of the wrong film went with it
        # The same disc is recognised as the right film from now on.
        assert h.db.recall("quick-1")[0].title == "Captain Comet"
        msgs = [e.message for e in h.db.events(job.id)]
        assert "Re-identified: Moonlit Orchard (2004) → Captain Comet (2011)" in msgs


async def test_what_isnt_the_jobs_stays_and_the_user_is_told(harness, settings, monkeypatch):
    from .test_contribute import contribute_state

    def no_record(*a):
        raise PermissionError(13, "Permission denied")

    async with harness() as h:
        job, old, _ = filed_film(h, settings)
        (old / "my notes.txt").write_text("mine")  # the user's own file in the film's folder
        job.contribution = contribute_state("sent")
        h.db.save_job(job)
        await h.m.reidentify(job.id)
        monkeypatch.setattr(h.m, "_rewrite_disc_record", no_record)
        await h.m.confirm(job.id, RIGHT)
        job = await done(h, job.id)
        msgs = [e.message for e in h.db.events(job.id)]
    assert job.state == JobState.done and (old / "my notes.txt").read_text() == "mine"
    assert f"{old} still has 1 file that isn't this job's, so it stays" in msgs
    assert "TheDiscDB was sent this disc as Moonlit Orchard (2004), so correct it there" in msgs
    assert "Could not update the .disc record: [Errno 13] Permission denied" in msgs


async def test_a_film_already_in_the_library_is_never_overwritten(harness, settings):
    async with harness() as h:
        job, old, dest = filed_film(h, settings)
        there = settings.movies / "Captain Comet (2011)" / "Captain Comet (2011) - 2160p.mkv"
        there.parent.mkdir(parents=True)
        there.write_bytes(b"someone else's")
        await h.m.reidentify(job.id)
        with pytest.raises(ValueError, match="already in the library"):
            await h.m.confirm(job.id, RIGHT)
        job = h.db.get_job(job.id)
        assert job.state == JobState.review and job.identity is None  # pick another, or keep it
        assert there.read_bytes() == b"someone else's" and dest.read_bytes() == b"film"
        job = await h.m.cancel(job.id)  # Cancel on a re-identify keeps it as it was
        assert job.state == JobState.done and job.identity == WRONG and job.destination == str(dest)
        assert job.refile is None and h.db.get_job(job.id).finished_at is not None


async def test_a_move_failing_half_way_is_retried(harness, settings, monkeypatch):
    async with harness() as h:
        job, old, _ = filed_film(h, settings)
        await h.m.reidentify(job.id)
        real, calls = library.move_path, []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) == 2:
                raise OSError("the share went away")
            real(src, dst)

        monkeypatch.setattr(library, "move_path", flaky)
        await h.m.confirm(job.id, RIGHT)
        job = await done(h, job.id)
        assert job.state == JobState.failed and job.problem.code == "refile_failed"
        assert "the share went away" in job.problem.message
        assert [mv.done for mv in job.refile.moves][:2] == [True, False]
        monkeypatch.setattr(library, "move_path", real)
        await h.m.retry(job.id)
        job = await done(h, job.id)
        assert job.state == JobState.done and job.identity.title == "Captain Comet"
        assert Path(job.destination).read_bytes() == b"film" and not old.exists()


async def test_a_failed_move_can_be_put_back(harness, settings, monkeypatch):
    async with harness() as h:
        job, old, dest = filed_film(h, settings)
        await h.m.reidentify(job.id)
        real = library.move_path
        calls = []

        def flaky(src, dst):
            calls.append(src)
            if len(calls) == 3:
                raise OSError("disk full")
            real(src, dst)

        monkeypatch.setattr(library, "move_path", flaky)
        await h.m.confirm(job.id, RIGHT)
        job = await done(h, job.id)
        assert job.state == JobState.failed and not dest.exists()
        job = await h.m.keep_identity(job.id)
        assert job.state == JobState.done and job.identity == WRONG and job.refile is None
        assert dest.read_bytes() == b"film" and (old / "featurettes" / "Making Of.mkv").exists()
        assert not (settings.movies / "Captain Comet (2011)").exists()


async def test_only_a_finished_job_can_be_identified_again(harness, settings):
    async with harness() as h:
        job, _, _ = filed_film(h, settings)
        job.state = JobState.transcoding
        h.db.save_job(job)
        with pytest.raises(ValueError, match="still transcoding"):
            await h.m.reidentify(job.id)
        job.state = JobState.done
        h.db.save_job(job)
        await h.m.reidentify(job.id)
        with pytest.raises(ValueError, match="only a finished job"):
            await h.m.reidentify(job.id)
        for refused in (h.m.identify_again(job.id), h.m.check_disc(job.id)):
            with pytest.raises(ValueError, match="being identified again"):
                await refused


async def test_identify_again_needs_the_files_still_in_the_library(harness, settings):
    async with harness() as h:
        with pytest.raises(ValueError, match="no such job"):
            await h.m.reidentify(999)
        job, folder, dest = filed_film(h, settings)
        for p in (dest, dest.with_name(f"{dest.stem} - original.mkv"), folder / "featurettes" / "Making Of.mkv"):
            p.unlink()  # moved or deleted by hand
        with pytest.raises(ValueError, match="none of this job's files are in the library any more"):
            await h.m.reidentify(job.id)
        job.destination = None
        h.db.save_job(job)
        with pytest.raises(ValueError, match="didn't put anything in the library"):
            await h.m.reidentify(job.id)
        backup = put(h, heron_job(identity=WRONG, mode="backup", destination=str(settings.library_dir / "gone")))
        with pytest.raises(ValueError, match="isn't in the library any more"):
            await h.m.reidentify(backup.id)


async def test_confirming_with_the_library_unmounted_moves_nothing(harness, settings):
    async with harness() as h:
        job, folder, dest = filed_film(h, settings)
        await h.m.reidentify(job.id)
        settings.movies_dir = settings.library_dir / "not-mounted"
        with pytest.raises(ValueError, match="does not exist"):
            await h.m.confirm(job.id, RIGHT)
        assert dest.read_bytes() == b"film" and h.db.get_job(job.id).state == JobState.review


async def test_keeping_the_old_identity_before_anything_moved(harness, settings):
    async with harness() as h:
        with pytest.raises(ValueError, match="isn't being identified again"):
            await h.m.keep_identity(999)
        job, folder, dest = filed_film(h, settings)
        await h.m.reidentify(job.id)
        back = await h.m.keep_identity(job.id)
        assert back.state == JobState.done and back.identity == WRONG and dest.read_bytes() == b"film"


async def test_a_disc_backup_folder_moves(harness, settings):
    async with harness() as h:
        job = put(h, heron_job(identity=WRONG, mode="backup"))
        old = library.backup_path(settings.disc_backups, WRONG.title, WRONG.year, uhd=True)
        (old / "BDMV").mkdir(parents=True)
        (old / "BDMV" / "index.bdmv").write_bytes(b"bd")
        library.write_disc_folder(old, {"title": WRONG.title}, None, None)
        job.destination = str(old)
        h.db.save_job(job)
        await h.m.reidentify(job.id)
        await h.m.confirm(job.id, RIGHT)
        job = await done(h, job.id)
        new = settings.disc_backups / "Captain Comet (2011) - 2160p"
        assert job.state == JobState.done and job.destination == str(new)
        assert (new / "BDMV" / "index.bdmv").read_bytes() == b"bd" and not old.exists()
        assert json.loads((new / ".disc" / "disc.json").read_text())["title"] == "Captain Comet"


# --- TV -------------------------------------------------------------------------------


def filed_tv(h, settings):
    """A finished Pippa & Goose disc, its two episodes in the library (named after themselves)."""
    job, folder = filed(h, settings)
    job.destination = str(folder)
    job.tv.episodes.append(Episode(season=3, episode=2))  # no name in TMDB
    h.db.save_job(job)
    extra = naming.extra_path(settings.tv, job.tv.show, job.disc.label, 3)  # title 3, the extra
    extra.parent.mkdir(parents=True)
    extra.write_bytes(b"x")
    meta = naming.disc_meta_folder(settings.tv, job.tv.show, job.disc.label, job.id)
    record = json.loads((meta / "disc.json").read_text())
    record["titles"]["3"] = {"file": str(extra.relative_to(folder))}
    (meta / "disc.json").write_text(json.dumps(record))
    for p in folder.rglob("*.mkv"):
        p.write_bytes(p.name.encode())
    return job, folder


def tv_files(folder: Path) -> dict[str, bytes]:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*")
            if p.is_file() and ".disc" not in p.parts}


async def test_a_tv_disc_moves_to_the_right_show(harness, settings):
    async with harness() as h:
        job, old = filed_tv(h, settings)
        job = await h.m.reidentify(job.id)
        assert job.state == JobState.review and job.tv.user_edited
        job.tv.show = SEAGULL  # picked in the review card (tv_propose, which asks TMDB)
        h.db.save_job(job)
        await h.m.tv_confirm(job.id, [])
        job = await done(h, job.id)
        assert job.state == JobState.done
        new = naming.show_folder(settings.tv, SEAGULL)
        assert job.destination == str(new) and not old.exists()
        assert tv_files(new) == {"Season 03/Seagull Manor - S03E01 - The Kite Tram.mkv":
                                 b"Pippa & Goose - S03E01.mkv",
                                 "Season 03/Seagull Manor - S03E02.mkv": b"Pippa & Goose - S03E02.mkv",
                                 "extras/PIPPA_&_GOOSE_KITE_TRAM - title 3.mkv":
                                 b"PIPPA_&_GOOSE_KITE_TRAM - title 3.mkv"}
        meta = naming.disc_meta_folder(settings.tv, SEAGULL, job.disc.label, job.id)
        record = json.loads((meta / "disc.json").read_text())
        assert record["show"]["name"] == "Seagull Manor"
        assert record["titles"]["1"]["file"] == "Season 03/Seagull Manor - S03E01 - The Kite Tram.mkv"
        # The disc left the wrong show's collection for the right one's.
        assert h.db.collection_for_show(61990) is None
        assert [d.job_id for d in h.db.collection_for_show(303).discs] == [job.id]


async def test_swapped_episodes_swap_places(harness, settings):
    """Same show, the episode order was wrong: two files trade names."""
    async with harness() as h:
        job, folder = filed_tv(h, settings)
        await h.m.reidentify(job.id)
        await h.m.tv_confirm(job.id, [
            TitleMapUpdate(title_index=1, role=TitleRole.episode, episodes=[EpisodeRef(season=3, episode=2)]),
            TitleMapUpdate(title_index=2, role=TitleRole.episode, episodes=[EpisodeRef(season=3, episode=1)])])
        job = await done(h, job.id)
        assert job.state == JobState.done, job.problem
        # Filed before TMDB named episode 1, so it's named now too.
        assert tv_files(folder) == {"Season 03/Pippa & Goose - S03E01 - The Kite Tram.mkv":
                                    b"Pippa & Goose - S03E02.mkv",
                                    "Season 03/Pippa & Goose - S03E02.mkv": b"Pippa & Goose - S03E01.mkv",
                                    "extras/PIPPA_&_GOOSE_KITE_TRAM - title 3.mkv":
                                    b"PIPPA_&_GOOSE_KITE_TRAM - title 3.mkv"}
        assert not [p for p in folder.rglob(".*refile*")]


async def test_a_title_never_filed_cant_be_mapped(harness, settings):
    async with harness() as h:
        job, folder = filed_tv(h, settings)
        await h.m.reidentify(job.id)
        with pytest.raises(ValueError, match="title 0 wasn't put in the library"):  # the play-all
            await h.m.tv_confirm(job.id, [
                TitleMapUpdate(title_index=0, role=TitleRole.episode, episodes=[EpisodeRef(season=3, episode=2)]),
                TitleMapUpdate(title_index=2, role=TitleRole.skip)])
        assert h.db.get_job(job.id).state == JobState.review
        with pytest.raises(ValueError, match="become a film"):
            await h.m.set_media(job.id, "movie")


# --- move_path / prune_folder ------------------------------------------------------------


def test_move_path_never_overwrites(tmp_path):
    a, b = tmp_path / "a.mkv", tmp_path / "x" / "b.mkv"
    a.write_bytes(b"a")
    library.move_path(a, b)
    assert b.read_bytes() == b"a" and not a.exists()
    a.write_bytes(b"new")
    with pytest.raises(FileExistsError):
        library.move_path(a, b)
    assert b.read_bytes() == b"a" and a.exists()


def test_move_path_across_filesystems(tmp_path, monkeypatch):
    import errno
    import os

    def cross(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", cross)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "f").write_bytes(b"f")
    library.move_path(tmp_path / "d", tmp_path / "e")
    assert (tmp_path / "e" / "f").read_bytes() == b"f" and not (tmp_path / "d").exists()


def test_prune_keeps_what_isnt_the_jobs(tmp_path):
    root = tmp_path / "Movies"
    folder = root / "Moonlit Orchard (2004)"
    (folder / "extras").mkdir(parents=True)
    (folder / "poster.jpg").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"mine")
    assert library.prune_folder(folder, root) == [folder / "notes.txt"]
    assert not (folder / "poster.jpg").exists() and not (folder / "extras").exists()
    (folder / "notes.txt").unlink()
    assert library.prune_folder(folder, root) == [] and not folder.exists() and root.is_dir()


# --- the API ------------------------------------------------------------------------------


def test_reidentify_through_the_api(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        assert client.post("/api/dev/insert/fake-ihas122").status_code == 204
        job = wait(client, lambda j: j["state"] == "review")
        assert client.post(f"/api/jobs/{job['id']}/reidentify").status_code == 409  # not finished
        r = client.post(f"/api/jobs/{job['id']}/confirm",
                        json={"candidate": {"title": "Moonlit Orchard", "year": 1998, "source": "manual"}})
        assert r.status_code == 200, r.text
        first = wait(client, lambda j: j["state"] == "done")
        r = client.post(f"/api/jobs/{job['id']}/reidentify")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "review" and r.json()["refile"]["title"] == "Moonlit Orchard (1998)"
        r = client.post(f"/api/jobs/{job['id']}/reidentify/keep")
        assert r.status_code == 200 and r.json()["state"] == "done" and r.json()["refile"] is None
        assert client.post(f"/api/jobs/{job['id']}/reidentify/keep").status_code == 409
        client.post(f"/api/jobs/{job['id']}/reidentify")
        r = client.post(f"/api/jobs/{job['id']}/confirm",
                        json={"candidate": {"title": "The Brass Heron", "year": 2024, "source": "manual"}})
        assert r.status_code == 200, r.text
        done = wait(client, lambda j: j["state"] == "done" and "Brass" in (j["destination"] or ""))
        assert Path(done["destination"]).parts[-2:] == ("The Brass Heron (2024)", "The Brass Heron (2024).mkv")
        assert Path(done["destination"]).is_file() and not Path(first["destination"]).parent.exists()
