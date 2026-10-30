"""OMDb (omdbapi.com) has IMDb's titles, years and runtimes, with a free API key.

A second film search. Its hits carry IMDb ids, which TMDB can look up, so a
film both find is one candidate (and says both found it). Without TMDB, OMDb's
own details (title, year and runtime) are enough to score a candidate.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from ..models import Candidate

API = "https://www.omdbapi.com/"
DETAILS_FOR_TOP = 4
DOCUMENTARY = 99
log = logging.getLogger(__name__)


class OmdbError(Exception):
    pass


class Omdb:
    def __init__(self, api_key: str, client: httpx.AsyncClient | None = None):
        self._key = api_key
        self._client = client or httpx.AsyncClient(timeout=15)

    async def _get(self, **params) -> dict:
        try:
            r = await self._client.get(API, params={"apikey": self._key, **params})
        except httpx.TimeoutException:
            raise OmdbError("OMDb did not answer in time") from None
        except httpx.HTTPError as e:
            raise OmdbError(f"could not reach OMDb ({type(e).__name__})") from None
        try:
            data = r.json()
        except ValueError:
            data = None
        error = data.get("Error") if isinstance(data, dict) else None
        if r.status_code == 401:
            raise OmdbError("OMDb refused the API key" if not error or "key" in error.lower() else f"OMDb: {error}")
        if r.status_code >= 400:
            raise OmdbError(f"OMDb returned HTTP {r.status_code}")
        if not isinstance(data, dict):
            raise OmdbError("OMDb sent an unexpected response")
        return data

    async def search(self, query: str, year: int | None = None) -> list[dict]:
        data = await self._get(s=query, type="movie", **({"y": year} if year else {}))
        hits = data.get("Search")
        return [h for h in hits if isinstance(h, dict) and str(h.get("imdbID", "")).startswith("tt")] \
            if isinstance(hits, list) else []

    async def movie(self, imdb_id: str) -> dict:
        return await self._get(i=imdb_id)

    async def candidates(self, queries: list[str], year: int | None = None) -> list[Candidate]:
        """Search every query (with the year, then without), details for the top hits."""
        seen: dict[str, dict] = {}
        for q in queries:
            hits = await self.search(q, year)
            if year and not hits:
                hits = await self.search(q)
            for h in hits[:10]:
                seen.setdefault(h["imdbID"], h)
        ids = list(seen)[:DETAILS_FOR_TOP]
        details = await asyncio.gather(*(self.movie(i) for i in ids), return_exceptions=True)
        out = []
        for i, d in zip(ids, details, strict=True):
            out.append(to_candidate({**seen[i], **(d if isinstance(d, dict) and d.get("Response") != "False" else {})}))
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


def _num(v: object) -> int | None:
    digits = re.sub(r"[^\d]", "", str(v or ""))
    return int(digits) if digits else None


def to_candidate(m: dict) -> Candidate:
    year = re.match(r"\d{4}", str(m.get("Year") or ""))
    runtime = re.match(r"(\d+)\s*min", str(m.get("Runtime") or ""))
    poster = m.get("Poster") if str(m.get("Poster") or "").startswith("http") else None
    plot = m.get("Plot") if isinstance(m.get("Plot"), str) and m["Plot"] != "N/A" else None
    genres = [g.strip().lower() for g in str(m.get("Genre") or "").split(",")]
    return Candidate(
        source="omdb", imdb_id=m["imdbID"], title=str(m.get("Title") or "?"),
        year=int(year[0]) if year else None, runtime=int(runtime[1]) if runtime and int(runtime[1]) > 0 else None,
        poster_url=poster, overview=plot, genre_ids=[DOCUMENTARY] if "documentary" in genres else [],
        vote_count=_num(m.get("imdbVotes")), providers=["omdb"],
    )
