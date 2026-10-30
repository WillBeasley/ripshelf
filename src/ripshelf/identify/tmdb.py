"""TMDB (v3 API) movie search."""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..models import Candidate
from .scoring import looks_like_extra

API = "https://api.themoviedb.org/3"
POSTER_BASE = "https://image.tmdb.org/t/p/w342"
DETAILS_FOR_TOP = 6

log = logging.getLogger(__name__)
# httpx logs full request URLs at INFO, and the v3 API key is a query parameter.
logging.getLogger("httpx").setLevel(logging.WARNING)


RETRIES = 2
MAX_RETRY_AFTER_S = 10.0


class TmdbError(Exception):
    pass


def retry_after(header: str | None) -> float:
    """Seconds to wait for a 429, from Retry-After (seconds form), capped."""
    try:
        return min(max(float(header), 0.0), MAX_RETRY_AFTER_S) if header else 1.0
    except ValueError:
        return 1.0


class Tmdb:
    def __init__(self, api_key: str, language: str = "en-GB", client: httpx.AsyncClient | None = None):
        self._key = api_key
        self._lang = language
        self._client = client or httpx.AsyncClient(timeout=15)
        self._sleep = asyncio.sleep

    async def _get(self, path: str, **params) -> dict:
        params = {"api_key": self._key, "language": self._lang, **params}
        for attempt in range(RETRIES + 1):
            try:
                r = await self._client.get(f"{API}{path}", params=params)
            except httpx.TimeoutException:
                raise TmdbError("TMDB did not answer in time") from None
            except httpx.HTTPError as e:
                raise TmdbError(f"TMDB request failed: {type(e).__name__}") from None
            if r.status_code == 429 and attempt < RETRIES:
                await self._sleep(retry_after(r.headers.get("Retry-After")))
                continue
            if r.status_code == 401:
                raise TmdbError("TMDB rejected the API key")
            if r.status_code == 429:
                raise TmdbError("TMDB rate limit reached, try again in a minute")
            if r.status_code >= 400:
                raise TmdbError(f"TMDB returned HTTP {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                raise TmdbError("TMDB sent a response that is not JSON") from None
            if not isinstance(data, dict):
                raise TmdbError("TMDB sent an unexpected response")
            return data
        raise AssertionError("unreachable")

    async def search_movies(self, query: str, year: int | None = None) -> list[dict]:
        params = {"query": query, "include_adult": "false"}
        if year:
            params["year"] = year
        data = await self._get("/search/movie", **params)
        results = data.get("results")
        if not isinstance(results, list):
            return []
        # Entries without a numeric id cannot be looked up or remembered.
        return [r for r in results if isinstance(r, dict) and isinstance(r.get("id"), int)]

    async def movie(self, tmdb_id: int) -> dict:
        return await self._get(f"/movie/{tmdb_id}")

    async def find(self, external_id: str | int, source: str) -> dict:
        """TMDB's films and shows with an IMDb ("imdb_id") or TheTVDB ("tvdb_id") id."""
        return await self._get(f"/find/{external_id}", external_source=source)

    async def find_id(self, kind: str, tvdb_id: int | None = None, imdb_id: str | None = None) -> int | None:
        """The TMDB id of the film ("movie") or show ("tv") with one of these ids."""
        for value, source in ((tvdb_id, "tvdb_id"), (imdb_id, "imdb_id")):
            if value:
                hits = (await self.find(value, source)).get(f"{kind}_results")
                for h in hits if isinstance(hits, list) else []:
                    if isinstance(h, dict) and isinstance(h.get("id"), int):
                        return h["id"]
        return None

    # --- TV ---------------------------------------------------------------------

    async def search_tv(self, query: str) -> list[dict]:
        data = await self._get("/search/tv", query=query, include_adult="false")
        results = data.get("results")
        if not isinstance(results, list):
            return []
        return [r for r in results if isinstance(r, dict) and isinstance(r.get("id"), int)]

    async def tv_show(self, tmdb_id: int) -> dict:
        return await self._get(f"/tv/{tmdb_id}", append_to_response="external_ids")

    async def season(self, tmdb_id: int, season: int) -> dict:
        return await self._get(f"/tv/{tmdb_id}/season/{season}")

    async def candidates(self, queries: list[str], year: int | None = None) -> list[Candidate]:
        """Search every query, merge by id (best rank first), add runtimes for the top hits."""
        seen: dict[int, dict] = {}
        order: list[tuple[int, int]] = []  # (rank, id)
        for q in queries:
            results = await self.search_movies(q, year)
            if year and not results:
                results = await self.search_movies(q)
            for i, r in enumerate(results[:10]):
                if r["id"] not in seen:
                    seen[r["id"]] = r
                    order.append((i, r["id"]))
        # Extras ("The Making of X") must not crowd the film out of the detail lookups.
        order.sort(key=lambda o: (looks_like_extra(str(seen[o[1]].get("title") or ""), queries) is not None, o[0]))
        ids = [i for _, i in order][:DETAILS_FOR_TOP]
        details = await asyncio.gather(*(self.movie(i) for i in ids), return_exceptions=True)
        out = []
        for tid, d in zip(ids, details, strict=True):
            base = seen[tid]
            if isinstance(d, Exception) or not isinstance(d, dict):
                d = {}
            d = {k: v for k, v in d.items() if k != "id"}
            out.append(to_candidate({**base, **d}))
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


def to_candidate(m: dict) -> Candidate:
    date = str(m.get("release_date") or "")
    title = m.get("title") if isinstance(m.get("title"), str) and m["title"].strip() else None
    original = m.get("original_title") if isinstance(m.get("original_title"), str) else None
    poster = m.get("poster_path") if isinstance(m.get("poster_path"), str) else None
    overview = m.get("overview") if isinstance(m.get("overview"), str) else None
    genres = m.get("genre_ids")
    if not genres and isinstance(m.get("genres"), list):
        genres = [g.get("id") for g in m["genres"] if isinstance(g, dict)]
    if not isinstance(genres, list):
        genres = []
    runtime = m.get("runtime")
    imdb = m.get("imdb_id") if isinstance(m.get("imdb_id"), str) and m["imdb_id"].startswith("tt") else None
    return Candidate(
        source="tmdb",
        tmdb_id=m["id"],
        imdb_id=imdb,
        providers=["tmdb"],
        title=title or original or "?",
        original_title=original,
        year=int(date[:4]) if date[:4].isdigit() else None,
        runtime=runtime if isinstance(runtime, int) and runtime > 0 else None,
        genre_ids=[g for g in (genres or []) if isinstance(g, int)],
        vote_count=m.get("vote_count") if isinstance(m.get("vote_count"), int) else None,
        popularity=m.get("popularity") if isinstance(m.get("popularity"), (int, float)) else None,
        poster_url=f"{POSTER_BASE}{poster}" if poster else None,
        overview=overview or None,
    )
