"""TV without a TMDB key: shows found on TVmaze or TheTVDB, keyed by their own ids,
through review, filing and the collection. TVmaze and TheTVDB mocked with respx."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from ripshelf.db import Database
from ripshelf.models import (
    Collection,
    DiscMatch,
    JobState,
    MediaKind,
    Show,
    TitleRole,
    TvProposeRequest,
    parse_show_key,
)
from ripshelf.providers.tvmaze import API as MAZE
from ripshelf.providers.tvmaze import Tvmaze
from ripshelf.tv import keyless
from ripshelf.tv.tvdb import API as TVDB
from ripshelf.tv.tvdb import Tvdb
from ripshelf.tvjobs import _same_show

from .tvfix import load

SD = "/dev/fake-sr0"  # PIPPA_&_GOOSE_KITE_TRAM, a compilation of ten episodes
CASE_ORDER = ("Kite Tram, Puddle Dash, Beard Trim, Seaside Jig Off, Venus as Comet, Pony Hop, "
              "Tower Doodling, Button Maker, Fog Knot, Cushion Stuff")
MAZE_ID = 5501

PIPPA = {"id": MAZE_ID, "name": "Pippa & Goose", "type": "Animation", "premiered": "2013-02-18",
         "averageRuntime": 7, "weight": 80, "image": {"medium": "https://static.tvmaze.com/p/5501.jpg"},
         "externals": {"thetvdb": 270001, "imdb": "tt9000001"}}
# A series about the show, which must not win for its disc.
PIPPA_DOC = {"id": 5502, "name": "Pippa & Goose Confidential", "type": "Documentary", "premiered": "2016-01-01",
             "averageRuntime": 30, "weight": 10, "externals": {}}


def maze_episodes() -> list[dict]:
    """The Pippa & Goose fixture's seasons 1-3, as TVmaze lists them."""
    out = []
    for n, season in load("pippa-goose")["seasons"].items():
        if n == "0":
            continue  # TVmaze keeps specials unnumbered
        for e in season["episodes"]:
            out.append({"id": int(n) * 1000 + e["episode_number"], "season": int(n), "number": e["episode_number"],
                        "name": e["name"], "runtime": e.get("runtime"), "airdate": e.get("air_date") or ""})
    return out


def mock_tvmaze(router, results=None):
    hits = [{"score": 0.9, "show": s} for s in (results if results is not None else [PIPPA, PIPPA_DOC])]
    router.get(f"{MAZE}/search/shows").mock(return_value=httpx.Response(200, json=hits))
    router.get(f"{MAZE}/shows/{MAZE_ID}").mock(return_value=httpx.Response(200, json=PIPPA))
    router.get(f"{MAZE}/shows/{MAZE_ID}/episodes").mock(return_value=httpx.Response(200, json=maze_episodes()))


@pytest.fixture
def keyless_tv(settings):
    """No TMDB key, TVmaze on for TV shows."""
    settings.tmdb_api_key = None
    settings.metadata_providers = {"tv": [{"name": "thediscdb", "enabled": True}, {"name": "tmdb", "enabled": True},
                                          {"name": "tvdb", "enabled": True}, {"name": "tvmaze", "enabled": True}]}
    return settings


# --- the show's key ------------------------------------------------------------------------


def test_show_key_and_old_rows():
    assert Show(tmdb_id=46866, name="Pippa & Goose").key == "tmdb:46866"
    old = Show.model_validate_json('{"tmdb_id": 46866, "name": "Pippa & Goose"}')  # saved before keys
    assert (old.source, old.key) == ("tmdb", "tmdb:46866")
    assert Show(source="tvmaze", tvmaze_id=MAZE_ID, name="Pippa & Goose").key == f"tvmaze:{MAZE_ID}"
    with pytest.raises(ValidationError):
        Show(source="tvdb", name="Pippa & Goose")  # no TheTVDB id
    assert parse_show_key("tvdb:270001") == ("tvdb", 270001)
    assert parse_show_key("46866") == ("tmdb", 46866)
    for bad in ("imdb:1", "tvmaze:", "tvmaze:-3", "x"):
        with pytest.raises(ValueError):
            parse_show_key(bad)


def test_propose_request_takes_a_key_or_a_tmdb_id():
    assert TvProposeRequest(show_tmdb_id=46866).show_key == "tmdb:46866"
    assert TvProposeRequest(show_key=f"tvmaze:{MAZE_ID}").show_key == f"tvmaze:{MAZE_ID}"
    with pytest.raises(ValidationError):
        TvProposeRequest()
    with pytest.raises(ValidationError):
        TvProposeRequest(show_key="omdb:1")


def test_same_show_as_the_disc_database():
    match = DiscMatch(media=MediaKind.tv, title="Pippa & Goose", tvdb_id=None, imdb_id="tt9000001")
    maze = keyless.tvmaze_show(PIPPA)
    assert _same_show(maze, match), "by IMDb id, for a show found without TMDB"
    tmdb_show = Show(tmdb_id=46866, imdb_id="tt9000001", name="Pippa & Goose")
    assert not _same_show(tmdb_show, match), "a TMDB show matches by TMDB or TheTVDB id, as before"
    assert _same_show(tmdb_show, match.model_copy(update={"tmdb_id": 46866}))


# --- collections keyed by show ---------------------------------------------------------------


def test_old_collections_are_keyed_on_open(tmp_path):
    path = tmp_path / "old.db"
    show = Show(tmdb_id=46866, name="Pippa & Goose")
    data = Collection(id=0, show=show, updated_at="2026-01-01T00:00:00+00:00").model_dump_json()
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "show_tmdb_id INTEGER NOT NULL UNIQUE, updated_at TEXT NOT NULL, data TEXT NOT NULL)")
    conn.execute("INSERT INTO collections (id, show_tmdb_id, updated_at, data) VALUES (7, 46866, ?, ?)",
                 ("2026-01-01T00:00:00+00:00", data))
    conn.commit()
    conn.close()
    db = Database(path)
    try:
        assert db.collection_for_show("tmdb:46866").id == 7
        assert db.collection_for_show(46866).id == 7, "a bare TMDB id still finds it"
        maze = Collection(id=0, show=keyless.tvmaze_show(PIPPA), updated_at="2026-01-02T00:00:00+00:00")
        db.save_collection(maze)
        assert db.collection_for_show(f"tvmaze:{MAZE_ID}").show.name == "Pippa & Goose"
        assert len(db.collections()) == 2
    finally:
        db.close()
    Database(path).close()  # opening again leaves it be


# --- searches --------------------------------------------------------------------------------


def test_tvmaze_show():
    s = keyless.tvmaze_show(PIPPA)
    assert (s.source, s.tvmaze_id, s.tvdb_id, s.imdb_id) == ("tvmaze", MAZE_ID, 270001, "tt9000001")
    assert (s.name, s.year, s.episode_runtime) == ("Pippa & Goose", 2013, 7)
    assert s.poster_url == "https://static.tvmaze.com/p/5501.jpg"
    assert keyless.tvmaze_show(PIPPA_DOC).genre_ids == [99]


def test_tvdb_show():
    r = {"id": "series-270001", "tvdb_id": "270001", "name": "Pipa y Ganso", "year": "2013",
         "translations": {"eng": "Pippa & Goose"}, "image_url": "https://artworks.thetvdb.com/p.jpg",
         "remote_ids": [{"id": "tt9000001", "sourceName": "IMDB"}]}
    s = keyless.tvdb_show(r)
    assert (s.key, s.name, s.original_name, s.year, s.imdb_id) == (
        "tvdb:270001", "Pippa & Goose", "Pipa y Ganso", 2013, "tt9000001")
    assert keyless.tvdb_show({"name": "No Id"}) is None


@respx.mock
async def test_find_shows_on_tvmaze_ranks_and_guards():
    mock_tvmaze(respx)
    maze = Tvmaze()
    shows = await keyless.find_shows("Pippa & Goose Season 3", 7 * 60, sources=["tvmaze"], tvmaze=maze)
    await maze.aclose()
    assert [s.key for s in shows] == [f"tvmaze:{MAZE_ID}", "tvmaze:5502"]
    assert not shows[0].excluded and shows[1].excluded
    assert any("TVmaze says 7 min" in e.text for e in shows[0].evidence)
    assert respx.calls[0].request.url.params["q"] == "Pippa & Goose", "season markers dropped"


@respx.mock
async def test_find_shows_falls_back_in_order():
    """TheTVDB first (it finds nothing), then TVmaze."""
    respx.post(f"{TVDB}/login").mock(return_value=httpx.Response(200, json={"data": {"token": "t"}}))
    tvdb_search = respx.get(f"{TVDB}/search").mock(return_value=httpx.Response(200, json={"data": []}))
    mock_tvmaze(respx, [PIPPA])
    tvdb, maze = Tvdb("key"), Tvmaze()
    shows = await keyless.find_shows("Pippa & Goose", None, sources=["tvdb", "tvmaze"], tvdb=tvdb, tvmaze=maze)
    assert tvdb_search.called and [s.key for s in shows] == [f"tvmaze:{MAZE_ID}"]


@respx.mock
async def test_tvmaze_all_episodes():
    mock_tvmaze(respx)
    maze = Tvmaze()
    every = await maze.all_episodes(MAZE_ID)
    assert len(every) == 120 and (every[0].season, every[0].episode) == (1, 1)
    assert (every[-1].season, every[-1].episode) == (3, 40)


@respx.mock
async def test_tvdb_all_episodes_and_series():
    respx.post(f"{TVDB}/login").mock(return_value=httpx.Response(200, json={"data": {"token": "t"}}))
    eps = [{"seasonNumber": s, "number": n, "name": f"Part {s}.{n}"} for s in (0, 1, 2) for n in (1, 2)]
    respx.get(f"{TVDB}/series/270001/episodes/default/eng").mock(
        return_value=httpx.Response(200, json={"data": {"episodes": eps}}))
    respx.get(f"{TVDB}/series/270001").mock(return_value=httpx.Response(200, json={
        "data": {"id": 270001, "name": "Pipa y Ganso", "year": "2013"}}))
    respx.get(f"{TVDB}/series/270001/translations/eng").mock(return_value=httpx.Response(200, json={
        "data": {"name": "Pippa & Goose"}}))
    tvdb = Tvdb("key")
    every = await tvdb.all_episodes(270001)
    assert [(e.season, e.episode) for e in every] == [(1, 1), (1, 2), (2, 1), (2, 2), (0, 1), (0, 2)]
    show = keyless.tvdb_show(await tvdb.series(270001), tvdb.language)
    assert (show.key, show.name) == ("tvdb:270001", "Pippa & Goose")


async def test_disc_match_show_without_tmdb():
    match = DiscMatch(media=MediaKind.tv, title="Pippa & Goose", year=2013, tvdb_id=270001)
    maze = SimpleNamespace(lookup=None)

    async def lookup(tvdb_id, imdb_id):
        assert tvdb_id == 270001
        return PIPPA

    maze.lookup = lookup
    assert (await keyless.disc_match_show(match, ["tvmaze"], maze)).key == f"tvmaze:{MAZE_ID}"
    tvdb = await keyless.disc_match_show(match, ["tvdb"], None)
    assert (tvdb.key, tvdb.name, tvdb.year) == ("tvdb:270001", "Pippa & Goose", 2013)


def test_tvmaze_show_with_gaps():
    s = keyless.tvmaze_show({"id": 7, "name": "  ", "premiered": None, "averageRuntime": None, "runtime": 22,
                             "weight": "high", "image": "not a dict", "type": "Talk Show"})
    assert (s.name, s.year, s.episode_runtime, s.vote_count, s.poster_url) == ("?", None, 22, None, None)
    assert s.genre_ids == [keyless.TALK], "kept apart from the show it is about"


def test_tvdb_show_with_gaps():
    s = keyless.tvdb_show({"tvdb_id": "270001", "name": "Pippa & Goose", "first_air_time": "2013-02-18",
                           "thumbnail": "http://insecure.example/p.jpg", "genres": ["Documentary"],
                           "translations": {"spa": "Pipa y Ganso"}, "remote_ids": [{"id": "EP0001"}, "junk"]})
    assert (s.name, s.original_name, s.year, s.imdb_id, s.poster_url) == (
        "Pippa & Goose", None, 2013, None, None), "no English translation, no https poster, no IMDb id"
    assert s.genre_ids == [keyless.DOCUMENTARY]
    assert keyless.tvdb_show({"id": "series-9", "translations": "eng"}).name == "?"


class FakeTvdb:
    language = "eng"

    def __init__(self, results=None, fail=False, episodes=None):
        self.results, self.fail, self.queries = results or {}, fail, []
        self.episode_lists = episodes or {}
        self.asked: list[tuple] = []

    async def search_series(self, q):
        self.queries.append(q)
        if self.fail:
            raise keyless.TvdbError("401 Unauthorized")
        return self.results.get(q, [])

    async def episodes(self, series, season, order):
        self.asked.append((series, season, order))
        return self.episode_lists.get(order, [])

    async def all_episodes(self, series, order):
        self.asked.append((series, order))
        return ["every episode"]


class FakeMaze:
    def __init__(self, results=None, fail=False, found=None):
        self.results, self.fail, self.found, self.queries = results or {}, fail, found, []

    async def search(self, q):
        self.queries.append(q)
        if self.fail:
            raise keyless.TvmazeError("TVmaze is down")
        return self.results.get(q, [])

    async def lookup(self, tvdb_id, imdb_id):
        if self.fail:
            raise keyless.TvmazeError("TVmaze is down")
        return self.found

    async def episodes(self, show_id, season):
        return [f"{show_id} s{season}"]

    async def all_episodes(self, show_id):
        return [f"all of {show_id}"]


async def test_find_shows_moves_on_when_a_provider_fails(caplog):
    tvdb = FakeTvdb(fail=True)
    maze = FakeMaze({"Pippa & Goose": [PIPPA]})
    shows = await keyless.find_shows("Pippa & Goose", None, sources=["tvdb", "tvmaze"], tvdb=tvdb, tvmaze=maze)
    assert [s.key for s in shows] == [f"tvmaze:{MAZE_ID}"]
    assert "TheTVDB search for 'Pippa & Goose' failed: 401 Unauthorized" in caplog.text


async def test_find_shows_tries_a_shorter_name_and_keeps_one_of_each():
    """A compilation's label adds an episode name; the show is found by a shorter name,
    and a show listed twice is kept once."""
    maze = FakeMaze({"Pippa & Goose": [PIPPA, PIPPA_DOC, PIPPA]})
    shows = await keyless.find_shows("Pippa & Goose Kite Tram", None, sources=["tvmaze"], tvmaze=maze)
    assert maze.queries == ["Pippa & Goose Kite Tram", "Pippa & Goose Kite"], "two names at most"
    assert shows == []
    maze.results["Pippa & Goose Kite"] = [PIPPA, PIPPA_DOC, PIPPA]
    shows = await keyless.find_shows("Pippa & Goose Kite Tram", None, sources=["tvmaze"], tvmaze=maze)
    assert sorted(s.key for s in shows) == [f"tvmaze:{MAZE_ID}", "tvmaze:5502"]


async def test_find_shows_skips_a_provider_without_a_client():
    tvdb = FakeTvdb({"Pippa & Goose": [{"tvdb_id": "270001", "name": "Pippa & Goose", "year": "2013"},
                                       {"name": "no id, so left out"}]})
    assert await keyless.find_shows("Pippa & Goose", None, sources=["tvmaze"], tvdb=tvdb) == []
    shows = await keyless.find_shows("Pippa & Goose", None, year=2013, tvdb=tvdb)
    assert [s.key for s in shows] == ["tvdb:270001"]
    assert any(e.text == "First aired 2013" for e in shows[0].evidence)


async def test_disc_match_show_falls_through_the_providers(caplog):
    match = DiscMatch(media=MediaKind.tv, title="Pippa & Goose", year=2013, tvdb_id=270001, imdb_id="tt9000001")
    down = FakeMaze(fail=True)
    show = await keyless.disc_match_show(match, ["tvmaze", "tvdb"], down)
    assert show.key == "tvdb:270001" and "TVmaze lookup of Pippa & Goose failed" in caplog.text
    unknown = FakeMaze(found=None)
    assert (await keyless.disc_match_show(match, ["tvmaze", "tvdb"], unknown)).key == "tvdb:270001"
    assert await keyless.disc_match_show(match, ["tvmaze"], unknown) is None
    no_ids = DiscMatch(media=MediaKind.tv, title="Pippa & Goose")
    assert await keyless.disc_match_show(no_ids, ["tvmaze", "tvdb"], FakeMaze(found=PIPPA)) is None


async def test_season_episodes_come_from_the_shows_own_provider():
    maze_show, tvdb_show = keyless.tvmaze_show(PIPPA), Show(source="tvdb", tvdb_id=270001, name="Pippa & Goose")
    assert await keyless.season_episodes(maze_show, 2, None, FakeMaze()) == ([f"{MAZE_ID} s2"], "aired order")
    with pytest.raises(keyless.TvmazeError, match="off"):
        await keyless.season_episodes(maze_show, 2, FakeTvdb(), None)
    with pytest.raises(keyless.TvdbError, match="no TheTVDB key"):
        await keyless.season_episodes(tvdb_show, 2, None, FakeMaze())

    tvdb = FakeTvdb(episodes={"dvd": ["dvd ep"], "default": ["aired ep"]})
    assert await keyless.season_episodes(tvdb_show, 1, tvdb, None, dvd=True) == (["dvd ep"], "DVD order")
    assert await keyless.season_episodes(tvdb_show, 1, tvdb, None) == (["aired ep"], "aired order")
    no_dvd = FakeTvdb(episodes={"default": ["aired ep"]})
    assert await keyless.season_episodes(tvdb_show, 1, no_dvd, None, dvd=True) == (
        ["aired ep"], "aired order (no DVD order)")
    assert no_dvd.asked == [(270001, 1, "dvd"), (270001, 1, "default")]


async def test_all_episodes_come_from_the_shows_own_provider():
    maze_show, tvdb_show = keyless.tvmaze_show(PIPPA), Show(source="tvdb", tvdb_id=270001, name="Pippa & Goose")
    assert await keyless.all_episodes(maze_show, None, FakeMaze()) == [f"all of {MAZE_ID}"]
    with pytest.raises(keyless.TvmazeError):
        await keyless.all_episodes(maze_show, FakeTvdb(), None)
    with pytest.raises(keyless.TvdbError):
        await keyless.all_episodes(tvdb_show, None, FakeMaze())
    tvdb = FakeTvdb()
    await keyless.all_episodes(tvdb_show, tvdb, None, dvd=True)
    await keyless.all_episodes(tvdb_show, tvdb, None)
    assert tvdb.asked == [(270001, "dvd"), (270001, "default")]


# --- the pipeline, end to end -------------------------------------------------------------------


async def test_no_key_and_no_search_says_so(harness, settings):
    async with harness(SD) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
        assert job.tv.show is None
        events = [e.message for e in h.db.events(job.id)]
        assert any("No TV show search is on" in e for e in events)
        with pytest.raises(ValueError, match="TMDB key, or switch TVmaze on"):
            await h.m.tv_search("Pippa")


async def test_compilation_from_tvmaze_end_to_end(harness, keyless_tv, settings):
    with respx.mock(assert_all_called=False) as router:
        mock_tvmaze(router)
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            plan = job.tv
            assert plan.show.key == f"tvmaze:{MAZE_ID}" and plan.show.name == "Pippa & Goose"
            assert plan.compilation and plan.show.providers == ["tvmaze"]
            assert [s.key for s in plan.candidates][:1] == [f"tvmaze:{MAZE_ID}"]
            first = next(m for m in plan.mapping if m.title_index == 1)
            assert [(e.season, e.episode) for e in first.episodes] == [(3, 34)], "named after Kite Tram"

            found = await h.m.tv_search("Pippa & Goose")
            assert found[0].key == f"tvmaze:{MAZE_ID}"

            job = await h.m.tv_match_text(job.id, CASE_ORDER)
            got = [m.episodes[0].episode for m in job.tv.mapping if m.role == TitleRole.episode]
            assert got == [34, 26, 21, 23, 24, 25, 29, 37, 30, 28]
            job = await h.m.tv_confirm(job.id, [])
            assert job.identity.source == "tvmaze" and job.identity.tmdb_id is None
            done = await h.wait_job(lambda j: j.state == JobState.done)
            c = h.db.collection_for_show(f"tvmaze:{MAZE_ID}")
            assert c and c.discs[0].job_id == done.id
    show = settings.library_dir / "TV" / "Pippa & Goose (2013)"
    files = sorted(p.name for p in (show / "Season 03").iterdir())
    assert "Pippa & Goose - S03E34 - Kite Tram.mkv" in files and len(files) == 10
    meta = json.loads(next((show / ".disc").glob("*/disc.json")).read_text())
    assert meta["show"]["key"] == f"tvmaze:{MAZE_ID}" and meta["show"]["tmdb_id"] is None


async def test_propose_a_season_by_key(harness, keyless_tv):
    """Box-set mode: a TVmaze show picked by its key, season 3 from episode 21."""
    with respx.mock(assert_all_called=False) as router:
        mock_tvmaze(router)
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
            job = await h.m.tv_propose(job.id, f"tvmaze:{MAZE_ID}", season=3, disc_number=None,
                                       start_episode=21, compilation=False)
            eps = [m.episodes[0].episode for m in job.tv.mapping if m.role == TitleRole.episode]
            assert eps == list(range(21, 31)) and job.tv.consistent and job.tv.season == 3
            with pytest.raises(ValueError, match="No TMDB API key"):
                await h.m.tv_propose(job.id, "tmdb:46866", season=3, disc_number=None, start_episode=None,
                                     compilation=False)


async def test_tmdb_key_keeps_tmdb_shows(harness, settings):
    """With a TMDB key, TVmaze stays a fallback and shows stay TMDB's."""
    from .tvfix import mock_show

    settings.tmdb_api_key = SecretStr("k")
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        maze = router.get(f"{MAZE}/search/shows").mock(return_value=httpx.Response(200, json=[]))
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.tv and j.tv.show, timeout=60)
            assert job.tv.show.key == "tmdb:46866" and job.tv.show.source == "tmdb"
        assert not maze.called
