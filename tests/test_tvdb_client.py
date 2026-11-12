"""TheTVDB's client on its own: logging in, errors and retries, paging, and turning its
episode records into episodes. TheTVDB is a scripted httpx transport."""

from __future__ import annotations

import httpx
import pytest

from ripshelf.tv import tvdb as tvdbmod
from ripshelf.tv.tvdb import Tvdb, TvdbError, tmdb_id_of, to_episodes

TOKEN = {"data": {"token": "tok"}}


class Site:
    """TheTVDB: ``routes`` maps a path to a response, or a list of them given in turn."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        r = self.routes.get(request.url.path.removeprefix("/v4"))
        if isinstance(r, list):
            r = r.pop(0)
        if isinstance(r, Exception):
            raise r
        if r is None:
            return httpx.Response(404)
        return r if isinstance(r, httpx.Response) else httpx.Response(200, json=r)

    def paths(self) -> list[str]:
        return [r.url.path.removeprefix("/v4") for r in self.requests]


def client(routes: dict, **kw) -> tuple[Tvdb, Site]:
    site = Site({"/login": TOKEN, **routes})
    t = Tvdb("key", client=httpx.AsyncClient(transport=httpx.MockTransport(site)), **kw)
    naps = []

    async def sleep(s):
        naps.append(s)

    t._sleep = sleep
    t.naps = naps
    return t, site


@pytest.mark.parametrize(("reply", "message"), [
    (httpx.ConnectError("refused"), "could not reach TheTVDB (ConnectError)"),
    (httpx.Response(401), "refused the API key (or the PIN)"),
    (httpx.Response(503), "returned HTTP 503"),
    (httpx.Response(200, text="<html>"), "unexpected login response"),
    (httpx.Response(200, json={"data": {"token": ""}}), "sent no token"),
    (httpx.Response(200, json={"data": {"token": 42}}), "sent no token"),
])
async def test_login_failures(reply, message):
    t, _ = client({"/login": reply})
    with pytest.raises(TvdbError, match=message.replace("(", r"\(").replace(")", r"\)")):
        await t.search_series("Seagull Manor")


async def test_logs_in_once_and_sends_the_token():
    t, site = client({"/search": [{"data": []}, {"data": []}]})
    await t.search_series("Seagull Manor")
    await t.search_series("Paper Kites")
    assert site.paths() == ["/login", "/search", "/search"]
    assert all(r.headers["Authorization"] == "Bearer tok" for r in site.requests[1:])
    assert site.requests[0].content == b'{"apikey":"key"}', "no PIN unless one is set"
    assert site.requests[1].url.params["type"] == "series"


@pytest.mark.parametrize(("reply", "message"), [
    (httpx.ReadTimeout("slow"), "did not answer in time"),
    (httpx.RemoteProtocolError("reset"), "request failed: RemoteProtocolError"),
    (httpx.Response(401), "refused the API key"),
    (httpx.Response(404), "no such record"),
    (httpx.Response(500), "returned HTTP 500"),
    (httpx.Response(200, text="not json"), "not JSON"),
    (httpx.Response(200, json=["a", "list"]), "unexpected response"),
])
async def test_request_failures(reply, message):
    t, _ = client({"/search": reply})
    with pytest.raises(TvdbError, match=message):
        await t.search_series("Seagull Manor")


async def test_too_many_requests_waits_as_told_then_gives_up():
    busy = httpx.Response(429, headers={"Retry-After": "3"})
    t, site = client({"/search": [busy, busy, {"data": [{"tvdb_id": "1"}]}]})
    assert await t.search_series("Seagull Manor") == [{"tvdb_id": "1"}]
    assert t.naps == [3.0, 3.0]
    t, site = client({"/search": [busy] * (tvdbmod.RETRIES + 1)})
    with pytest.raises(TvdbError, match="HTTP 429"):
        await t.search_series("Seagull Manor")
    assert site.paths().count("/search") == tvdbmod.RETRIES + 1


async def test_search_keeps_only_records():
    t, _ = client({"/search": {"data": [{"tvdb_id": "1"}, "junk", None]}})
    assert await t.search_series("Seagull Manor") == [{"tvdb_id": "1"}]
    t, _ = client({"/search": {"data": {"not": "a list"}}})
    assert await t.search_series("Seagull Manor") == []


async def test_series_with_and_without_a_translation():
    record = {"data": {"name": "Möwenhof", "firstAired": "1975-09-19", "image": "https://artworks.thetvdb.com/s.jpg"}}
    t, _ = client({"/series/81189": record, "/series/81189/translations/eng": {"data": {"name": "Seagull Manor"}}})
    assert await t.series(81189) == {"tvdb_id": 81189, "name": "Möwenhof", "year": "1975-09-19",
                                     "image_url": "https://artworks.thetvdb.com/s.jpg",
                                     "translations": {"eng": "Seagull Manor"}}
    t, _ = client({"/series/81189": record})  # the translation is a 404
    assert "translations" not in await t.series(81189)
    t, _ = client({"/series/81189": {"data": None}})
    with pytest.raises(TvdbError, match="no such series"):
        await t.series(81189)


def test_language_codes():
    assert Tvdb("k", language="de-DE").language == "deu"
    assert Tvdb("k", language="NB").language == "nor"
    assert Tvdb("k", language="xx-YY").language == "eng", "unknown languages get English names"


def ep(season, number, name="", **kw):
    return {"seasonNumber": season, "number": number, "name": name, **kw}


async def test_episodes_fall_back_to_the_shows_own_language_when_the_translation_is_empty():
    t, site = client({"/series/81189/episodes/default/eng": {"data": {"episodes": []}},
                      "/series/81189/episodes/default": {"data": {"episodes": [ep(1, 1, "Ein Hauch von Stil")]}}})
    got = await t.episodes(81189, 1)
    assert [e.name for e in got] == ["Ein Hauch von Stil"]
    assert site.requests[-1].url.params["season"] == "1"


async def test_paging_follows_next_links_and_stops():
    pages = [{"data": {"episodes": [ep(1, n)]}, "links": {"next": f"page={n}"}} for n in range(1, 4)]
    pages[-1]["links"] = {"next": None}
    t, site = client({"/series/81189/episodes/dvd/eng": pages})
    assert [e.episode for e in await t.episodes(81189, 1, "dvd")] == [1, 2, 3]
    assert [r.url.params["page"] for r in site.requests[1:]] == ["0", "1", "2"]

    endless = [{"data": {"episodes": [ep(1, n)]}, "links": {"next": "more"}} for n in range(1, 30)]
    t, site = client({"/series/81189/episodes/default/eng": endless})
    assert len(await t.episodes(81189, 1)) == tvdbmod.MAX_PAGES, "a site that always says next is cut off"


async def test_all_episodes_puts_specials_last():
    raw = [ep(0, 1, "Christmas"), ep(2, 1), ep(1, 2), ep(1, 1), ep(None, 3), ep("1", 4)]
    t, _ = client({"/series/81189/episodes/default/eng": httpx.Response(500),
                   "/series/81189/episodes/default": {"data": {"episodes": raw}}})
    got = await t.all_episodes(81189)
    assert [(e.season, e.episode) for e in got] == [(1, 1), (1, 2), (2, 1), (0, 1)]


def test_to_episodes_skips_bad_rows_and_keeps_the_first_of_each_number():
    raw = [ep(1, 2, "Second", runtime=29, aired="1975-09-26"), ep(1, 1, "First", runtime=0),
           ep(1, 1, "First again"), ep(1, 0, "Pilot no number"), ep(1, -1), ep(2, 1, "Other season"),
           ep(1, "3"), {"seasonNumber": 1, "number": 3, "name": None, "runtime": "30", "aired": 19750101}]
    got = to_episodes(raw, 1)
    assert [(e.episode, e.name, e.runtime, e.air_date) for e in got] == [
        (1, "First", None, None), (2, "Second", 29, "1975-09-26"), (3, "", None, None)]


def test_tmdb_id_of():
    assert tmdb_id_of({"remote_ids": [{"id": "tt0072500", "sourceName": "IMDB"},
                                      {"id": "2207", "sourceName": "TheMovieDB.com"}]}) == 2207
    assert tmdb_id_of({"remote_ids": [{"id": "abc", "sourceName": "TheMovieDB.com"}]}) is None
    assert tmdb_id_of({"remote_ids": ["junk"]}) is None
    assert tmdb_id_of({}) is None
