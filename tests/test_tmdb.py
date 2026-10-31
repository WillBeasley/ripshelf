"""TMDB client against a mocked API, with failures, odd data and retries."""

from __future__ import annotations

import httpx
import pytest
import respx

from ripshelf.identify import tmdb as tmdb_mod
from ripshelf.identify.tmdb import API, Tmdb, TmdbError, retry_after, to_candidate

SEARCH = f"{API}/search/movie"


@pytest.fixture
async def client():
    t = Tmdb("secret-key")
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    t._sleep = fake_sleep
    t.sleeps = sleeps
    yield t
    await t.aclose()


@respx.mock
async def test_no_results(client):
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": []}))
    assert await client.candidates(["Nothing At All"]) == []


@respx.mock
async def test_year_filter_falls_back_to_no_year(client):
    route = respx.get(SEARCH).mock(side_effect=[
        httpx.Response(200, json={"results": []}),
        httpx.Response(200, json={"results": [{"id": 1, "title": "Stowaway", "release_date": "1979-05-25"}]}),
    ])
    respx.get(f"{API}/movie/1").mock(return_value=httpx.Response(200, json={"id": 1, "runtime": 117}))
    got = await client.candidates(["Stowaway"], year=1980)
    assert [c.year for c in got] == [1979] and got[0].runtime == 117
    assert "year" in route.calls[0].request.url.params
    assert "year" not in route.calls[1].request.url.params


@respx.mock
@pytest.mark.parametrize("status, msg", [(401, "rejected the API key"), (404, "HTTP 404"), (500, "HTTP 500")])
async def test_http_errors(client, status, msg):
    respx.get(SEARCH).mock(return_value=httpx.Response(status, json={}))
    with pytest.raises(TmdbError, match=msg):
        await client.candidates(["Stowaway"])


@respx.mock
async def test_429_retries_after_retry_after(client):
    respx.get(SEARCH).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"results": [{"id": 7, "title": "Ember"}]}),
    ])
    respx.get(f"{API}/movie/7").mock(return_value=httpx.Response(200, json={"id": 7}))
    got = await client.candidates(["Ember"])
    assert [c.title for c in got] == ["Ember"]
    assert client.sleeps == [3.0]


@respx.mock
async def test_429_gives_up(client):
    respx.get(SEARCH).mock(return_value=httpx.Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(TmdbError, match="rate limit"):
        await client.candidates(["Ember"])
    assert client.sleeps == [tmdb_mod.MAX_RETRY_AFTER_S] * tmdb_mod.RETRIES


@pytest.mark.parametrize("header, want", [(None, 1.0), ("0", 0.0), ("2.5", 2.5), ("999", 10.0),
                                          ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0), ("-5", 0.0)])
def test_retry_after_parsing(header, want):
    assert retry_after(header) == want


@respx.mock
async def test_timeout(client):
    respx.get(SEARCH).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(TmdbError, match="in time"):
        await client.candidates(["Ember"])


@respx.mock
async def test_network_down(client):
    respx.get(SEARCH).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(TmdbError, match="ConnectError"):
        await client.candidates(["Ember"])


@respx.mock
async def test_api_key_never_in_error_text(client):
    respx.get(SEARCH).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(TmdbError) as e:
        await client.candidates(["Ember"])
    assert "secret-key" not in str(e.value)


@respx.mock
@pytest.mark.parametrize("body", [b"<html>502</html>", b"", b"[1,2,3]", b"null"])
async def test_malformed_json(client, body):
    respx.get(SEARCH).mock(return_value=httpx.Response(200, content=body))
    with pytest.raises(TmdbError):
        await client.candidates(["Ember"])


@respx.mock
async def test_malformed_results_are_skipped(client):
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": [
        {"title": "no id"}, {"id": "12", "title": "string id"}, None, 5,
        {"id": 3, "title": "Good", "release_date": "not a date", "genre_ids": "x", "runtime": "long"},
    ]}))
    respx.get(f"{API}/movie/3").mock(return_value=httpx.Response(200, json={"id": 999, "runtime": None}))
    got = await client.candidates(["Good"])
    assert len(got) == 1
    c = got[0]
    assert c.tmdb_id == 3  # details may not change the id
    assert c.year is None and c.runtime is None and c.genre_ids == []


@respx.mock
async def test_results_not_a_list(client):
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": {"oops": 1}}))
    assert await client.candidates(["Ember"]) == []


@respx.mock
async def test_detail_failures_keep_search_data(client):
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"results": [
        {"id": 1, "title": "Stowaway", "release_date": "1979-05-25", "vote_count": 9000}]}))
    respx.get(f"{API}/movie/1").mock(return_value=httpx.Response(500))
    got = await client.candidates(["Stowaway"])
    assert got[0].title == "Stowaway" and got[0].runtime is None and got[0].vote_count == 9000


def test_unicode_and_missing_fields():
    c = to_candidate({"id": 1, "title": "Élodie", "original_title": "Le Merveilleux Été d'Élodie Marchand"})
    assert c.title == "Élodie" and c.year is None and c.runtime is None and c.poster_url is None
    c = to_candidate({"id": 2, "title": "  ", "original_title": "星降る町の灯台", "release_date": "2001-07-20",
                      "genres": [{"id": 16}, {"name": "no id"}, "bad"], "poster_path": "/p.jpg"})
    assert c.title == "星降る町の灯台" and c.year == 2001 and c.genre_ids == [16]
    assert c.poster_url.endswith("/p.jpg")
    assert to_candidate({"id": 3}).title == "?"
