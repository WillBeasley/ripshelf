"""TVmaze (api.tvmaze.com) has shows and episode lists, free and without a key.

Its shows carry TheTVDB and IMDb ids, which is how a show found here is looked
up on TMDB, and how a TMDB show is found here for its episodes. The API allows
20 requests per 10 seconds; a 429 is waited out like TMDB's.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..identify.tmdb import RETRIES, retry_after
from ..models import Episode

API = "https://api.tvmaze.com"
log = logging.getLogger(__name__)


class TvmazeError(Exception):
    pass


class Tvmaze:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=15, follow_redirects=True,
                                                    headers={"User-Agent": "ripshelf"})
        self._sleep = asyncio.sleep

    async def _get(self, path: str, **params) -> object | None:
        for attempt in range(RETRIES + 1):
            try:
                r = await self._client.get(f"{API}{path}", params=params or None)
            except httpx.TimeoutException:
                raise TvmazeError("TVmaze did not answer in time") from None
            except httpx.HTTPError as e:
                raise TvmazeError(f"could not reach TVmaze ({type(e).__name__})") from None
            if r.status_code == 429 and attempt < RETRIES:
                await self._sleep(retry_after(r.headers.get("Retry-After")))
                continue
            if r.status_code == 404:
                return None
            if r.status_code >= 400:
                raise TvmazeError(f"TVmaze returned HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError:
                raise TvmazeError("TVmaze sent a response that is not JSON") from None
        raise AssertionError("unreachable")

    async def search(self, query: str) -> list[dict]:
        """Shows for ``query``, best first."""
        got = await self._get("/search/shows", q=query)
        if not isinstance(got, list):
            return []
        return [r["show"] for r in got if isinstance(r, dict) and isinstance(r.get("show"), dict)
                and isinstance(r["show"].get("id"), int)]

    async def lookup(self, tvdb_id: int | None = None, imdb_id: str | None = None) -> dict | None:
        """The show with this TheTVDB or IMDb id, if TVmaze has it."""
        for key, value in (("thetvdb", tvdb_id), ("imdb", imdb_id)):
            if value:
                got = await self._get("/lookup/shows", **{key: value})
                if isinstance(got, dict) and isinstance(got.get("id"), int):
                    return got
        return None

    async def episodes(self, show_id: int, season: int) -> list[Episode]:
        """A season's numbered episodes (TVmaze keeps specials unnumbered in their season,
        so it has no season 0)."""
        got = await self._get(f"/shows/{show_id}/episodes")
        return to_episodes(got if isinstance(got, list) else [], season)

    async def aclose(self) -> None:
        await self._client.aclose()


def to_episodes(raw: list, season: int) -> list[Episode]:
    out: dict[int, Episode] = {}
    for e in raw:
        if not isinstance(e, dict) or e.get("season") != season:
            continue
        n, rt, aired = e.get("number"), e.get("runtime"), e.get("airdate")
        if not isinstance(n, int) or n <= 0:
            continue
        out.setdefault(n, Episode(season=season, episode=n, name=e.get("name") if isinstance(e.get("name"), str) else "",
                                  air_date=aired if isinstance(aired, str) and aired else None,
                                  runtime=rt if isinstance(rt, int) and rt > 0 else None))
    return [out[n] for n in sorted(out)]


def externals(show: dict) -> tuple[int | None, str | None]:
    """(TheTVDB id, IMDb id) of a TVmaze show."""
    ext = show.get("externals") if isinstance(show.get("externals"), dict) else {}
    tvdb = ext.get("thetvdb") if isinstance(ext.get("thetvdb"), int) else None
    imdb = ext.get("imdb") if isinstance(ext.get("imdb"), str) and ext["imdb"].startswith("tt") else None
    return tvdb, imdb
