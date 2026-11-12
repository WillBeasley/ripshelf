"""The pretend TMDB the UI's browser tests run against answers ripshelf's own TMDB client."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ripshelf.dev.fake_tmdb import FakeTmdb
from ripshelf.identify import tmdb
from ripshelf.identify.tmdb import Tmdb


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(FakeTmdb().router(), prefix="/fake-tmdb/3")
    monkeypatch.setattr(tmdb, "API", "http://fake/fake-tmdb/3")
    return Tmdb("fake", client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app)))


async def test_films_are_found_by_the_words_of_their_title(client):
    found = await client.candidates(["The Brass Heron"])
    heron = next(c for c in found if c.tmdb_id == 1184918)
    assert (heron.title, heron.year, heron.runtime) == ("The Brass Heron", 2024, 102)
    assert all(c.poster_url is None for c in found)
    assert {c.tmdb_id for c in await client.candidates(["THE BRASS HERON UHD"])} == {c.tmdb_id for c in found}
    assert await client.candidates(["Moonlit Orchard"]) == []


async def test_a_year_narrows_the_search(client):
    assert [m["id"] for m in await client.search_movies("The Hollow", 1982)] == [1091]
    assert len(await client.search_movies("The Hollow")) == 3


async def test_shows_and_their_seasons(client):
    [show] = await client.search_tv("Pippa & Goose Kite Tram")
    assert show["id"] == 46866 and "poster_path" not in show
    assert (await client.tv_show(46866))["name"] == "Pippa & Goose"
    season = await client.season(46866, 1)
    assert season["episodes"][13]["name"] == "Doubles"
    with pytest.raises(tmdb.TmdbError, match="404"):
        await client.season(46866, 9)
