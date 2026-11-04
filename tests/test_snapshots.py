"""Review help for a disc nothing identified: frames from its titles, a web search, and eject to check."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ripshelf import snapshots
from ripshelf.dev.fake import FakeDrives
from ripshelf.identify import catalogue
from ripshelf.main import create_app
from ripshelf.models import JobState, RipState, TitleInfo

from .conftest import until
from .test_jobs_identify import PM

SEARX = "http://searx.test"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")


def title(**kw) -> TitleInfo:
    return TitleInfo(**{"index": 3, "duration_s": 7000, "size_bytes": 1000 * 2**20, "chapters": 20, **kw})


# --- which frames a title can give ------------------------------------------------------------


def test_positions_skip_the_very_start_and_end():
    at = snapshots.positions(700)
    assert len(at) == snapshots.COUNT and at[0] == 100 and at[-1] == 600


def test_a_title_not_ripped_has_no_frames():
    assert snapshots.plan(title(), None, False, None).frames == []


def test_a_title_being_ripped_gives_the_frames_already_written(tmp_path):
    f = tmp_path / "title_t03.mkv"
    f.write_bytes(b"\0" * (500 * 2**20))  # half of it
    view = snapshots.plan(title(), f, False, None)
    assert view.ripped == 0.5
    assert view.frames == [0, 1, 2]  # 1000, 2000 and 3000 s; 4000 s isn't written yet
    assert view.at_s == [1000, 2000, 3000]


def test_a_title_barely_started_gives_none(tmp_path):
    f = tmp_path / "title_t03.mkv"
    f.write_bytes(b"\0" * 1024)
    assert snapshots.plan(title(), f, False, None).frames == []


def test_a_finished_title_uses_the_files_own_length(tmp_path):
    f = tmp_path / "title_t03.mkv"
    f.write_bytes(b"x")
    view = snapshots.plan(title(), f, True, 14.0)
    assert view.ripped == 1.0 and view.frames == list(range(snapshots.COUNT)) and view.at_s[0] == 2


def test_title_file_finds_makemkvs_name(tmp_path):
    (tmp_path / "Some Film_t03.mkv").write_bytes(b"x")
    assert snapshots.title_file(tmp_path, title(), {}) == tmp_path / "Some Film_t03.mkv"
    assert snapshots.title_file(tmp_path, title(index=4), {}) is None


# --- web search ------------------------------------------------------------------------------


async def test_web_search_gives_pages_and_the_names_in_them(settings):
    settings.searxng_url = SEARX  # the automatic catalogue lookup stays off
    with respx.mock() as router:
        route = router.get(f"{SEARX}/search").mock(return_value=httpx.Response(200, json={"results": [
            {"title": "Moonlit Orchard [DVD] [1998] | Example-discs.co.uk", "url": "https://shop.example/mo",
             "content": "Region 2"},
            {"title": "Sneaky", "url": "javascript:alert(1)"},
            {"title": "", "url": "https://shop.example/empty"},
        ]}))
        found = await catalogue.web_search(settings, '"MOONLIT_ORCHARD" dvd')
    assert route.calls[0].request.url.params["q"] == '"MOONLIT_ORCHARD" dvd'
    [r] = found
    assert r.url == "https://shop.example/mo" and r.suggestion == "Moonlit Orchard" and r.snippet == "Region 2"


async def test_web_search_needs_an_address(settings):
    with pytest.raises(catalogue.CatalogueError, match="no SearXNG address"):
        await catalogue.web_search(settings, "x")


def test_bluray_catalogue_numbers_are_searched_as_bluray():
    assert catalogue.query_for("EO51710", "bluray") == '"EO51710" blu-ray'


# --- through the API -------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        yield c


def test_web_search_api(client, settings):
    assert client.get("/api/status").json()["web_search"] is False
    r = client.post("/api/web-search", json={"query": "x"})
    assert r.status_code == 400 and "SearXNG" in r.json()["detail"]


@needs_ffmpeg
async def test_frames_from_a_ripped_film(harness, settings):
    async with harness(PM) as h:  # no TMDB key, so the disc waits in review with no candidates
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.done)
        assert not job.candidates
        shots = await h.m.snapshots(job.id)
        [t] = shots.titles
        assert t.title_index == job.main_title.index and t.ripped == 1.0 and t.frames
        jpg = await h.m.snapshot(job.id, t.title_index, t.frames[0])
        assert jpg and jpg.read_bytes()[:2] == b"\xff\xd8"
        assert await h.m.snapshot(job.id, t.title_index, 99) is None
        assert await h.m.snapshot(job.id, t.title_index + 50, 0) is None
        # Once the scratch copy is gone, the frames made earlier are still listed.
        shutil.rmtree(h.m.scratch_dir(job))
        [kept] = (await h.m.snapshots(job.id)).titles
        assert kept.frames == [t.frames[0]]


async def test_frames_wait_for_the_rip(harness, settings, monkeypatch):
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "6")
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.running)
        shots = await h.m.snapshots(job.id)
        assert shots.titles == [] and "few minutes" in shots.note


# --- eject to check a film mid-rip -----------------------------------------------------------


async def test_a_film_ejected_mid_rip_rips_again_when_it_is_back(harness, settings, monkeypatch):
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "4")
    settings.auto_eject = False
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.running)
        with pytest.raises(ValueError, match="starts again when the disc is back"):
            await h.m.check_disc(job.id)
        checking = await h.m.check_disc(job.id, pause_rip=True)
        assert checking.state == JobState.checking and checking.checking_from == JobState.review
        assert checking.rip.state == RipState.pending and checking.rip.paused
        assert PM not in h.backend.present
        monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.3")
        h.backend.insert(PM)
        done = await h.wait_job(lambda j: j.rip.state == RipState.done and j.state == JobState.review)
        assert done.id == job.id and not done.rip.all_titles and Path(done.rip.file).exists()
        assert any("starts again when the disc is back" in e.message for e in h.db.events(job.id))
        await until(lambda: len(h.db.list_jobs()) == 1, what="no new job")
