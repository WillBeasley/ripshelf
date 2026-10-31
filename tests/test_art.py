"""Disc art. Which poster, the local cache, and what is never fetched."""

from __future__ import annotations

import os
import time

import httpx
import pytest
import respx

from ripshelf import art
from ripshelf.db import JobRecord, now
from ripshelf.models import Candidate, JobState, Show, TvPlan

POSTER = "https://image.tmdb.org/t/p/w342/abc.jpg"
SEASON = "https://image.tmdb.org/t/p/w342/season3.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def job(**kw) -> JobRecord:
    return JobRecord(id=1, drive_id="d", state=JobState.review, created_at=now(), updated_at=now(), **kw)


def test_confirmed_movie_poster():
    j = job(identity=Candidate(title="Ember", poster_url=POSTER))
    assert j.art_url() == (POSTER, False)
    pub = j.public()
    assert pub.art == art.key(POSTER) and not pub.art_proposed


def test_best_guess_is_proposed_and_extras_are_skipped():
    j = job(candidates=[Candidate(title="Making of", excluded=True, poster_url="https://image.tmdb.org/x.jpg"),
                        Candidate(title="Ember", poster_url=POSTER)])
    assert j.art_url() == (POSTER, True) and j.public().art_proposed


def test_tv_prefers_the_season_poster():
    show = Show(tmdb_id=1, name="S", poster_url=POSTER)
    proposed = job(media="tv", tv=TvPlan(show=show, season_poster_url=SEASON))
    assert proposed.art_url() == (SEASON, True)
    confirmed = job(media="tv", tv=TvPlan(show=show, season_poster_url=SEASON),
                    identity=Candidate(title="S", poster_url=POSTER))
    assert confirmed.art_url() == (SEASON, False)
    no_season = job(media="tv", tv=TvPlan(show=show))
    assert no_season.art_url() == (POSTER, True)


def test_no_art():
    pub = job().public()
    assert pub.art is None and not pub.art_proposed


@respx.mock
async def test_cache_downloads_once(tmp_path):
    route = respx.get(POSTER).mock(return_value=httpx.Response(200, content=JPEG,
                                                               headers={"content-type": "image/jpeg"}))
    p1 = await art.cached(tmp_path, POSTER)
    p2 = await art.cached(tmp_path, POSTER)
    assert p1 == p2 and p1.read_bytes() == JPEG and route.call_count == 1
    assert not list(tmp_path.glob("*.part"))


@respx.mock
async def test_old_posters_are_downloaded_again_and_pruned(tmp_path):
    route = respx.get(POSTER).mock(return_value=httpx.Response(200, content=JPEG,
                                                               headers={"content-type": "image/jpeg"}))
    p = await art.cached(tmp_path, POSTER)
    old = time.time() - art.MAX_AGE_S - 60
    os.utime(p, (old, old))
    assert await art.cached(tmp_path, POSTER) == p and route.call_count == 2
    assert art.prune(tmp_path) == 0 and p.exists()
    os.utime(p, (old, old))
    assert art.prune(tmp_path) == 1 and not p.exists()
    assert art.prune(tmp_path / "missing") == 0


@pytest.mark.parametrize("url", ["http://image.tmdb.org/x.jpg", "https://evil.example/x.jpg",
                                 "file:///etc/passwd", "https://image.tmdb.org.evil.example/x.jpg"])
async def test_only_tmdb_images_are_fetched(tmp_path, url):
    with respx.mock(assert_all_called=False) as router:
        route = router.route().mock(return_value=httpx.Response(200, content=JPEG))
        assert await art.cached(tmp_path, url) is None
        assert route.call_count == 0


@respx.mock
@pytest.mark.parametrize("resp", [
    httpx.Response(404),
    httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"}),
    httpx.Response(200, content=b"x" * (art.MAX_BYTES + 10), headers={"content-type": "image/jpeg"}),
    httpx.Response(302, headers={"location": "https://evil.example/x.jpg"}),
])
async def test_bad_responses_are_not_cached(tmp_path, resp):
    respx.get(POSTER).mock(return_value=resp)
    assert await art.cached(tmp_path, POSTER) is None
    assert not list(tmp_path.iterdir())


@respx.mock
async def test_network_down(tmp_path):
    respx.get(POSTER).mock(side_effect=httpx.ConnectError("down"))
    assert await art.cached(tmp_path, POSTER) is None


def test_art_endpoint(settings):
    from fastapi.testclient import TestClient

    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with respx.mock(assert_all_called=False) as router:
        router.get(POSTER).mock(return_value=httpx.Response(200, content=JPEG,
                                                            headers={"content-type": "image/jpeg"}))
        router.route(host="testserver").pass_through()
        app = create_app(settings, backend=FakeDrives(present=set()))
        with TestClient(app) as c:
            m = app.state.manager
            j = m.db.create_job("fake-ihas122", "/dev/fake-sr1")
            assert c.get(f"/api/jobs/{j.id}/art").status_code == 404  # nothing to show
            j.identity = Candidate(title="Ember", poster_url=POSTER)
            m.db.save_job(j)
            r = c.get(f"/api/jobs/{j.id}/art?k={art.key(POSTER)}")
            assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
            assert c.get(f"/api/jobs/{j.id}").json()["art"] == art.key(POSTER)
            assert c.get("/api/jobs/999/art").status_code == 404
            j.identity = Candidate(title="Ember", poster_url="https://evil.example/x.jpg")
            m.db.save_job(j)
            assert c.get(f"/api/jobs/{j.id}/art").status_code == 404
