"""A pretend TMDB for the UI's browser tests (``RIPSHELF_FAKE_TMDB=1`` with fake hardware).

It answers the v3 calls ripshelf makes from the made-up films and shows in
tests/fixtures/tmdb and tests/fixtures/tmdb_tv, so a film or a show can be searched for and
confirmed without a key or the internet. A search finds a fixture when every word of the
fixture's query is in it, as "Pippa & Goose" is in the label PIPPA_&_GOOSE_KITE_TRAM.
Posters are left out, so a page never loads one from the real TMDB.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .fake import FIXTURES


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def no_poster(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("poster_path", "backdrop_path")}


class FakeTmdb:
    def __init__(self, fixtures: Path = FIXTURES):
        self.films = [json.loads(p.read_text()) for p in sorted((fixtures / "tmdb").glob("*.json"))]
        self.shows = [json.loads(p.read_text()) for p in sorted((fixtures / "tmdb_tv").glob("*.json"))]

    def router(self) -> APIRouter:
        r = APIRouter()

        def found(fixtures: list[dict], query: str) -> list[dict]:
            asked = words(query)
            return [f for f in fixtures if words(f["query"]) <= asked]

        @r.get("/configuration")
        async def configuration():
            return {"images": {}}

        @r.get("/search/movie")
        async def search_movie(query: str, year: int | None = None):
            results = [no_poster(m) for f in found(self.films, query) for m in f["results"]]
            if year:
                results = [m for m in results if str(m.get("release_date", "")).startswith(str(year))]
            return {"page": 1, "results": results, "total_results": len(results)}

        @r.get("/movie/{tmdb_id}")
        async def movie(tmdb_id: int):
            for f in self.films:
                for m in f["results"]:
                    if m["id"] == tmdb_id:
                        return no_poster({**m, **f["details"].get(str(tmdb_id), {})})
            raise HTTPException(404)

        @r.get("/movie/{tmdb_id}/release_dates")
        async def release_dates(tmdb_id: int):
            return {"id": tmdb_id, "results": []}

        @r.get("/find/{external_id}")
        async def find(external_id: str):
            return {"movie_results": [], "tv_results": []}

        @r.get("/search/tv")
        async def search_tv(query: str):
            results = [no_poster(s) for f in found(self.shows, query) for s in f["search"]["results"]]
            return {"page": 1, "results": results, "total_results": len(results)}

        @r.get("/tv/{tmdb_id}")
        async def tv_show(tmdb_id: int):
            for f in self.shows:
                if f["show"]["id"] == tmdb_id:
                    return no_poster(f["show"])
            raise HTTPException(404)

        @r.get("/tv/{tmdb_id}/season/{season}")
        async def season(tmdb_id: int, season: int):
            for f in self.shows:
                if f["show"]["id"] == tmdb_id and str(season) in f["seasons"]:
                    return f["seasons"][str(season)]
            raise HTTPException(404)

        return r
