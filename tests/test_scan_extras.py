"""Using the box's listing: the match on the job, a film's extras ripped and filed in
Emby's extras folders, TV extras by name, and nothing in the library replaced."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ripshelf import library
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import (
    Candidate,
    JobState,
    Listing,
    ListingDetails,
    ListingItem,
    RipState,
    ScanMatch,
    TitleMatch,
    TitleRole,
)
from ripshelf.tv import naming

from .conftest import until
from .test_contribute import heron_job, pippa_job, put


def named(index: int, name: str, kind: str = "Featurette", confidence: str = "high") -> TitleMatch:
    return TitleMatch(title_index=index, kind="extra", item=0, name=name, extra_type=kind, score=0.9,
                      confidence=confidence, reason="test")


@pytest.mark.parametrize("kind, name, folder", [
    ("Featurette", "Growing the Orchard", "featurettes"), ("Featurette", "The Making of the Orchard", "behind the scenes"),
    ("Featurette", "Behind the Scenes at Heron Lock", "behind the scenes"), ("DeletedScene", "x", "deleted scenes"),
    ("Trailer", "x", "trailers"), ("Interview", "x", "interviews"), ("Scene", "x", "scenes"), ("Short", "x", "shorts"),
    ("Music", "x", "extras"), ("Other", "x", "extras"), ("Extra", "x", "extras"), (None, "x", "extras"),
])
def test_emby_folders(kind, name, folder):
    assert library.extras_folder(kind, name) == folder


def test_free_path(tmp_path):
    p = tmp_path / "Teaser.mkv"
    assert library.free_path(p) == p
    p.write_bytes(b"x")
    assert library.free_path(p) == tmp_path / "Teaser (2).mkv"
    assert library.free_path(p, {tmp_path / "Teaser (2).mkv"}) == tmp_path / "Teaser (3).mkv"


def film_with_extras(h, settings, tmp_path):
    folder = settings.movies / "The Brass Heron (2024)"
    folder.mkdir(parents=True)
    job = heron_job(destination=str(folder / "The Brass Heron (2024) - 2160p.mkv"))
    files = {}
    for i in (0, 1, 4, 5):
        f = tmp_path / f"title_t{i:02d}.mkv"
        f.write_bytes(f"title {i}".encode())
        files[i] = str(f)
    job.rip.files = files
    job.scan_match = ScanMatch(matches=[named(4, "Building the Brass Heron"),
                                        named(1, "Teaser trailer", "Trailer", "medium"),
                                        named(5, "Gag reel", "Other", "low")])
    return put(h, job), folder


async def test_a_films_extras_are_filed_by_name(harness, settings, tmp_path):
    async with harness() as h:
        job, folder = film_with_extras(h, settings, tmp_path)
        (folder / "trailers").mkdir()
        (folder / "trailers" / "Teaser trailer.mkv").write_bytes(b"someone else's")
        await h.m.file_film_extras(job, folder)
        assert (folder / "featurettes" / "Building the Brass Heron.mkv").read_bytes() == b"title 4"
        assert (folder / "trailers" / "Teaser trailer.mkv").read_bytes() == b"someone else's"
        assert (folder / "trailers" / "Teaser trailer (2).mkv").read_bytes() == b"title 1"
        assert not (folder / "extras").exists()  # the low match isn't named, and unnamed ones are skipped
        filed = h.db.get_job(job.id).filed_extras
        assert sorted(filed) == [1, 4]
        await h.m.file_film_extras(h.db.get_job(job.id), folder)  # again: nothing twice
        assert len(list(folder.rglob("*.mkv"))) == 2 + 1


async def test_an_extra_that_cant_be_copied_is_a_warning(harness, settings, tmp_path, monkeypatch):
    real = library.copy_verified

    def copy(src, dest, *a, **kw):
        if "Teaser" in dest.name:
            raise OSError(28, "No space left on device")
        return real(src, dest, *a, **kw)

    monkeypatch.setattr(library, "copy_verified", copy)
    async with harness() as h:
        job, folder = film_with_extras(h, settings, tmp_path)
        await h.m.file_film_extras(job, folder)
        assert sorted(h.db.get_job(job.id).filed_extras) == [4], "the others are still filed"
        assert any(e.message.startswith("Could not copy title 1 to") and "No space left" in e.message
                   for e in h.db.events(job.id))


async def test_ripping_the_extras_refuses_what_it_cant_do(harness, settings, tmp_path):
    async with harness() as h:
        with pytest.raises(KeyError):
            await h.m.rip_film_extras(999)
        job, _ = film_with_extras(h, settings, tmp_path)
        job = h.db.get_job(job.id)
        job.rip.files = {0: job.rip.files[0]}  # the extras not ripped yet
        h.db.save_job(job)
        settings.film_unnamed_extras = "extras"  # so every title is an extra to rip
        with pytest.raises(ValueError, match="while the disc is still in its drive"):
            await h.m.rip_film_extras(job.id)  # its drive isn't here
        settings.film_unnamed_extras = "skip"
        with pytest.raises(ValueError, match="no extras to rip"):
            await h.m.rip_film_extras(job.id)


async def test_the_cover_scan_only_sets_a_tv_discs_episodes(harness, settings, tmp_path):
    async with harness() as h:
        with pytest.raises(KeyError):
            await h.m.use_scan(999)
        job, _ = film_with_extras(h, settings, tmp_path)
        with pytest.raises(ValueError, match="only a TV disc waiting for review"):
            await h.m.use_scan(job.id)


async def test_unnamed_film_extras_when_asked(harness, settings, tmp_path):
    settings.film_unnamed_extras = "extras"
    async with harness() as h:
        job, folder = film_with_extras(h, settings, tmp_path)
        await h.m.file_film_extras(job, folder)
        assert (folder / "extras" / "Title 5.mkv").read_bytes() == b"title 5"
        assert (folder / "featurettes" / "Building the Brass Heron.mkv").exists()


async def test_named_extras_can_be_switched_off(harness, settings, tmp_path):
    settings.extras_emby_folders = False
    async with harness() as h:
        job, folder = film_with_extras(h, settings, tmp_path)
        await h.m.file_film_extras(job, folder)
        assert not any(folder.rglob("*.mkv"))
        assert h.m.film_extra_titles(job) == []


async def test_which_film_titles_are_ripped_as_extras(harness, settings, tmp_path):
    async with harness() as h:
        job, _ = film_with_extras(h, settings, tmp_path)
        assert h.m.film_extra_titles(job) == [1, 4]
        settings.film_unnamed_extras = "extras"
        many = h.m.film_extra_titles(job)
        assert 0 not in many and {1, 4, 5} <= set(many) and 13 not in many  # 13 is the film's own clip


async def test_tv_extras_by_name(harness, settings):
    async with harness() as h:
        job = pippa_job()
        extra = next(m for m in job.tv.mapping if m.role == TitleRole.extra)
        job.scan_match = ScanMatch(matches=[named(extra.title_index, "Making the Kite Tram")])
        job = put(h, job)
        show = naming.show_folder(settings.tv, job.tv.show)
        targets = {i: p for i, p, _ in h.m._tv_targets(job)}
        assert targets[extra.title_index] == show / "behind the scenes" / "Making the Kite Tram.mkv"
        (show / "behind the scenes").mkdir(parents=True)
        (show / "behind the scenes" / "Making the Kite Tram.mkv").write_bytes(b"x")
        targets = {i: p for i, p, _ in h.m._tv_targets(job)}
        assert targets[extra.title_index].name == "Making the Kite Tram (2).mkv"
        settings.extras_emby_folders = False
        targets = {i: p for i, p, _ in h.m._tv_targets(job)}
        assert targets[extra.title_index].parent.name == "extras"  # as before: <label> - title N
        settings.extras_emby_folders, settings.tv_unmatched = True, "skip"
        assert extra.title_index not in {i for i, _, _ in h.m._tv_targets(job)}


async def test_the_match_follows_the_listing(harness, settings):
    async with harness() as h:
        job = put(h, pippa_job())
        h.m.save_listing(job.id, Listing(items=[ListingItem(kind="episode", title="The Kite Tram")]))
        m = h.db.get_job(job.id).scan_match
        assert m and m.matches[0].episodes and m.matches[0].episodes[0].episode == 1
        h.m.drop_listing_edits(job.id)
        assert h.db.get_job(job.id).scan_match is None


@pytest.mark.slow
async def test_a_films_extras_ripped_and_filed(harness, settings):
    """The film rips; the box is scanned while it waits for review; its extras are
    ripped from the disc still in the drive and filed with the film."""
    settings.auto_eject = False
    async with harness("/dev/fake-sr2") as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.done, 60,
                               "the film ripped")
        h.m.save_listing(job.id, Listing(
            items=[ListingItem(kind="extra", title="Building the Brass Heron", extra_type="Featurette", runtime_min=22)],
            details=ListingDetails(feature_runtime_min=102)))
        m = h.db.get_job(job.id).scan_match
        assert [(x.title_index, x.kind) for x in m.matches] == [(0, "feature"), (4, "extra")]
        await h.m.rip_film_extras(job.id)
        await until(lambda: 4 in h.db.get_job(job.id).rip.files, 60, "the extra ripped")
        with pytest.raises(ValueError, match="no extras to rip"):
            await h.m.rip_film_extras(job.id)
        await h.m.confirm(job.id, Candidate(source="manual", title="The Brass Heron", year=2024))
        done = await h.wait_job(lambda j: j.state == JobState.done, 60, "filed")
        folder = Path(done.destination).parent
        assert (folder / "featurettes" / "Building the Brass Heron.mkv").is_file()
        assert list(done.filed_extras) == [4]


def test_api(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        m = client.app.state.manager
        rec = m.db.create_job("fake-pioneer", "/dev/sr0")
        m.db.save_job(heron_job(rec.id))
        assert client.get(f"/api/jobs/{rec.id}/scan-match").json() is None
        client.put(f"/api/jobs/{rec.id}/listing", json={"items": [], "details": {"feature_runtime_min": 102}})
        got = client.get(f"/api/jobs/{rec.id}/scan-match").json()
        assert got["matches"][0]["kind"] == "feature" and got["matches"][0]["title_index"] == 0
        assert client.post(f"/api/jobs/{rec.id}/extras/rip").status_code == 409
        s = client.put("/api/settings", json={"extras_emby_folders": False, "film_unnamed_extras": "extras"}).json()
        assert (s["extras_emby_folders"], s["film_unnamed_extras"]) == (False, "extras")
        assert client.get("/api/jobs/999/scan-match").status_code == 404

