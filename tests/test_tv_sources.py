"""Show names from disc names, TheTVDB as a second source, and episode titles picked
from the season's real runtimes."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
import respx
from pydantic import SecretStr

from ripshelf import checks
from ripshelf.identify.tmdb import API as TMDB
from ripshelf.identify.tmdb import Tmdb
from ripshelf.models import (
    CollectionDisc,
    DiscKind,
    Episode,
    EpisodeRef,
    SettingsUpdate,
    Show,
    TitleInfo,
    TitleRole,
    TvPlan,
)
from ripshelf.tv import mapping as tvmap
from ripshelf.tv import tmdb_tv
from ripshelf.tv.labels import split_show_name
from ripshelf.tv.tvdb import API as TVDB
from ripshelf.tv.tvdb import Tvdb, TvdbError, tmdb_id_of, tvdb_id_of
from ripshelf.tvjobs import TvMixin

HBK = {"id": 1621, "name": "Harbour Kingdom", "first_air_date": "2010-09-19", "genre_ids": [80, 18],
       "seasons": [{"season_number": 1, "episode_count": 12}], "external_ids": {"tvdb_id": 84947}}
HBK_RUNTIMES = [73, 53, 52, 54, 54, 60, 53, 50, 53, 59, 57, 61]


def login(router, token="tok"):
    return router.post(f"{TVDB}/login").mock(return_value=httpx.Response(200, json={"data": {"token": token}}))


# --- show names ------------------------------------------------------------------------


@pytest.mark.parametrize("name, show, season, disc, volume", [
    ("Harbour Kingdom Season 1 Disc 2", "Harbour Kingdom", 1, 2, None),
    ("Harbour Kingdom - Season 1", "Harbour Kingdom", 1, None, None),
    ("The Signal: The Complete Second Season", "The Signal", 2, None, None),
    ("The Depot Season 2 (Disc 1)", "The Depot", 2, 1, None),
    ("Copper Men Season One", "Copper Men", 1, None, None),
    ("Lodgers S03D04", "Lodgers", 3, 4, None),
    ("Captain Comet Series 1 Vol 3", "Captain Comet", 1, None, 3),
    ("24 Season 4", "24", 4, None, None),
    ("Vane: Series 3", "Vane", 3, None, None),
    ("A Catalogue of Curious Mishaps", "A Catalogue of Curious Mishaps", None, None, None),
    ("A Catalogue of Curious Mishaps Season 2", "A Catalogue of Curious Mishaps", 2, None, None),
    ("The Complete Series", "The Complete Series", None, None, None),
    ("", "", None, None, None),
])
def test_split_show_name(name, show, season, disc, volume):
    t = split_show_name(name)
    assert (t.show, t.season, t.disc, t.volume) == (show, season, disc, volume)


@respx.mock
async def test_find_shows_searches_the_show_not_the_disc_name():
    search = respx.get(f"{TMDB}/search/tv").mock(return_value=httpx.Response(200, json={"results": [HBK]}))
    respx.get(f"{TMDB}/tv/1621").mock(return_value=httpx.Response(200, json=HBK))
    tmdb = Tmdb("k")
    try:
        shows = await tmdb_tv.find_shows(tmdb, "Harbour Kingdom Season 1 Disc 2", None)
    finally:
        await tmdb.aclose()
    assert search.calls[0].request.url.params["query"] == "Harbour Kingdom"
    assert shows[0].name == "Harbour Kingdom" and shows[0].score >= 0.95
    assert shows[0].tvdb_id == 84947


# --- TheTVDB ------------------------------------------------------------------------------


def test_remote_ids():
    r = {"tvdb_id": "84947", "remote_ids": [{"id": "tt0979432", "sourceName": "IMDB"},
                                            {"id": "1621", "sourceName": "TheMovieDB.com"}]}
    assert (tmdb_id_of(r), tvdb_id_of(r)) == (1621, 84947)
    assert tmdb_id_of({"remote_ids": [{"id": "x", "sourceName": "TheMovieDB.com"}]}) is None
    assert tvdb_id_of({"id": "series-12"}) == 12 and tvdb_id_of({}) is None


@respx.mock
async def test_tmdb_misses_tvdb_finds():
    respx.get(f"{TMDB}/search/tv").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.get(f"{TMDB}/tv/1621").mock(return_value=httpx.Response(200, json=HBK))
    login(respx)
    hits = {"data": [{"tvdb_id": "84947", "name": "Harbour Kingdom",
                      "remote_ids": [{"id": "1621", "sourceName": "TheMovieDB.com"}]}]}
    respx.get(f"{TVDB}/search").mock(return_value=httpx.Response(200, json=hits))
    tmdb, tvdb = Tmdb("k"), Tvdb("key")
    try:
        shows = await tmdb_tv.find_shows(tmdb, "BWE Season 1", None, tvdb=tvdb)
    finally:
        await tmdb.aclose()
        await tvdb.aclose()
    assert [s.tmdb_id for s in shows] == [1621] and shows[0].tvdb_id == 84947
    assert any("TheTVDB" in e.text for e in shows[0].evidence)


@respx.mock
async def test_tvdb_failure_is_not_fatal():
    respx.get(f"{TMDB}/search/tv").mock(return_value=httpx.Response(200, json={"results": []}))
    respx.post(f"{TVDB}/login").mock(return_value=httpx.Response(401))
    tmdb, tvdb = Tmdb("k"), Tvdb("bad")
    try:
        assert await tmdb_tv.find_shows(tmdb, "Nothing", None, tvdb=tvdb) == []
    finally:
        await tmdb.aclose()
        await tvdb.aclose()


@respx.mock
async def test_tvdb_episodes_translated_then_default():
    login(respx)
    eps = [{"seasonNumber": 1, "number": n, "name": f"Ep {n}", "runtime": 50, "aired": "2010-01-01"}
           for n in (2, 1)] + [{"seasonNumber": 2, "number": 1, "name": "other season"},
                               {"seasonNumber": 1, "number": 0, "name": "bad"}]
    translated = respx.get(f"{TVDB}/series/9/episodes/dvd/deu").mock(return_value=httpx.Response(404))
    default = respx.get(f"{TVDB}/series/9/episodes/dvd").mock(side_effect=[
        httpx.Response(200, json={"data": {"episodes": eps[:1]}, "links": {"next": "page=1"}}),
        httpx.Response(200, json={"data": {"episodes": eps[1:]}, "links": {"next": None}}),
    ])
    tvdb = Tvdb("key", "1234", "de-DE")
    try:
        got = await tvdb.episodes(9, 1, "dvd")
    finally:
        await tvdb.aclose()
    assert [(e.episode, e.name) for e in got] == [(1, "Ep 1"), (2, "Ep 2")]
    assert translated.called and default.call_count == 2
    assert default.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert respx.calls[0].request.content == b'{"apikey":"key","pin":"1234"}'


@respx.mock
async def test_tvdb_errors():
    respx.post(f"{TVDB}/login").mock(return_value=httpx.Response(200, json={"data": {}}))
    tvdb = Tvdb("key")
    with pytest.raises(TvdbError, match="unexpected login"):
        await tvdb.search_series("x")
    await tvdb.aclose()


@respx.mock
async def test_settings_test_button(settings):
    login(respx)
    respx.get(f"{TVDB}/search").mock(return_value=httpx.Response(200, json={"data": [{"tvdb_id": "1"}]}))
    assert not (await checks.tvdb(settings, SettingsUpdate())).ok
    r = await checks.tvdb(settings, SettingsUpdate(tvdb_api_key="typed"))
    assert r.ok and "1 series" in r.details[0]
    respx.post(f"{TVDB}/login").mock(return_value=httpx.Response(401))
    r = await checks.tvdb(settings, SettingsUpdate(tvdb_api_key="bad"))
    assert not r.ok and "refused" in r.message


# --- the job pipeline's pieces -----------------------------------------------------------------


class Tv(TvMixin):
    def __init__(self, settings, collection=None):
        self.s = settings
        self.db = SimpleNamespace(collection_for_show=lambda _id: collection)


def hbk_disc1():
    """Harbour Kingdom S1 disc 1 as scanned, with a 72 min pilot, a 52 min episode, duplicate
    playlists of both, and four short extras (three of about 4 min)."""
    t = [(0, 245, "10"), (1, 4341, "1"), (2, 3152, "2"), (3, 259, "11"), (4, 137, "12"), (5, 4341, "1"),
         (6, 3152, "2"), (7, 193, "13")]
    titles = [TitleInfo(index=i, duration_s=d, size_bytes=d * 1000 + i, chapters=8, segments_map=seg)
              for i, d, seg in t]
    return SimpleNamespace(titles=titles, disc=SimpleNamespace(kind=DiscKind.bluray, label="BWE_S1_D1",
                                                               name="Harbour Kingdom Season 1 Disc 1"))


def season1():
    return [Episode(season=1, episode=n, name=f"E{n}", runtime=r) for n, r in enumerate(HBK_RUNTIMES, 1)]


def test_reshape_from_season_runtimes(settings):
    tv, job = Tv(settings), hbk_disc1()
    from ripshelf.tv import shape
    plan = TvPlan(mapping=tv._shape_mapping(job, shape.analyse(job.titles, True)))
    before = [m.title_index for m in plan.mapping if m.role == TitleRole.episode]
    assert before == [0, 3, 7], "the short extras look like the episodes until the runtimes are known"
    note = tv._reshape(job, plan, season1())
    assert note and "50–73 min" in note
    ordered = tv._ordered_episode_titles(job, plan)
    assert [t.index for t in ordered] == [1, 2]
    mapping = tvmap.propose(ordered, {}, season1(), 1, False)
    assert [(m.title_index, m.episodes, m.match_cost) for m in mapping] == [
        (1, [EpisodeRef(season=1, episode=1)], 0.0), (2, [EpisodeRef(season=1, episode=2)], 0.0)]


def test_reshape_leaves_hand_edits_alone(settings):
    tv, job = Tv(settings), hbk_disc1()
    from ripshelf.tv import shape
    plan = TvPlan(mapping=tv._shape_mapping(job, shape.analyse(job.titles, True)))
    plan.mapping[0].role = TitleRole.skip
    assert tv._reshape(job, plan, season1()) is None
    assert tv._reshape(job, TvPlan(mapping=[]), [Episode(season=1, episode=1)]) is None  # no runtimes


def test_next_disc_follows_only_the_disc_before(settings):
    show = Show(tmdb_id=1621, name="Harbour Kingdom")
    disc2 = CollectionDisc(job_id=21, season=1, disc_number=2, label="BWE_S1_D2",
                           episodes=[EpisodeRef(season=1, episode=n) for n in (3, 4, 5)])
    tv = Tv(settings, SimpleNamespace(discs=[disc2]))
    assert tv._next_for(show, 1, 1) == (1, 1, None), "disc 1 after disc 2 starts the season"
    assert tv._next_for(show, 1, 3) == (1, 3, 6)
    assert tv._next_for(show, 1, None) == (1, 3, 6)
    assert tv._next_for(show, 1, 3, job_id=21) == (1, None, None), "a job does not follow itself"


@respx.mock
async def test_episodes_from_tvdb(settings):
    settings.tvdb_api_key = SecretStr("key")
    login(respx)
    eps = [{"seasonNumber": 1, "number": n, "name": f"T{n}", "runtime": None} for n in (1, 2)]
    respx.get(f"{TVDB}/series/84947/episodes/default/eng").mock(
        return_value=httpx.Response(200, json={"data": {"episodes": eps}}))
    respx.get(f"{TMDB}/tv/1621").mock(return_value=httpx.Response(200, json=HBK))
    tmdb = Tmdb("k")
    show = Show(tmdb_id=1621, name="Harbour Kingdom")  # no TheTVDB id yet, so asked of TMDB
    try:
        got, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
        assert [(e.name, e.runtime) for e in got] == [("T1", 73), ("T2", 53)]  # TMDB's runtimes
        assert note == "Episodes from TheTVDB, aired order" and show.tvdb_id == 84947

        settings.tv_episode_order = "tmdb"
        got, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
        assert len(got) == 12 and note is None
    finally:
        await tmdb.aclose()


@respx.mock
async def test_dvd_order_falls_back_to_aired_then_tmdb(settings):
    settings.tvdb_api_key, settings.tvdb_episode_order = SecretStr("key"), "dvd"
    login(respx)
    empty = httpx.Response(200, json={"data": {"episodes": []}})
    respx.get(url__regex=rf"{TVDB}/series/84947/episodes/dvd.*").mock(return_value=empty)
    aired = respx.get(url__regex=rf"{TVDB}/series/84947/episodes/default.*").mock(return_value=empty)
    tmdb = Tmdb("k")
    show = Show(tmdb_id=1621, tvdb_id=84947, name="Harbour Kingdom")
    try:
        got, note = await Tv(settings)._episode_list(show, 1, season1(), tmdb)
    finally:
        await tmdb.aclose()
    assert aired.called and len(got) == 12 and "no season 1" in note
