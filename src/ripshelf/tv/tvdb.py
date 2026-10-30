"""TheTVDB (v4 API), a second source for shows and episodes.

TMDB stays the show's identity (collections and the library are keyed by its
id). TheTVDB adds two things, shows TMDB's search misses (TheTVDB results carry
their TMDB id), and episode lists in aired or DVD order. DVD order follows the
discs, but Emby names episodes in aired order unless the library is set to DVD
order, so aired is the default.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..identify.tmdb import RETRIES, retry_after
from ..models import Episode

API = "https://api4.thetvdb.com/v4"
MAX_PAGES = 10
log = logging.getLogger(__name__)

# TMDB language ("en-GB") -> TheTVDB's three-letter code, for episode names.
LANGUAGES = {"en": "eng", "de": "deu", "fr": "fra", "es": "spa", "it": "ita", "nl": "nld", "pt": "por",
             "sv": "swe", "da": "dan", "no": "nor", "nb": "nor", "fi": "fin", "pl": "pol", "ja": "jpn",
             "ko": "kor", "zh": "zho", "ru": "rus", "cs": "ces", "hu": "hun", "tr": "tur"}


class TvdbError(Exception):
    pass


class Tvdb:
    def __init__(self, api_key: str, pin: str | None = None, language: str = "en-GB",
                 client: httpx.AsyncClient | None = None):
        self._key, self._pin = api_key, pin
        self.language = LANGUAGES.get(language.split("-")[0].lower(), "eng")
        self._client = client or httpx.AsyncClient(timeout=15)
        self._token: str | None = None
        self._sleep = asyncio.sleep

    async def login(self) -> str:
        body = {"apikey": self._key, **({"pin": self._pin} if self._pin else {})}
        try:
            r = await self._client.post(f"{API}/login", json=body)
        except httpx.HTTPError as e:
            raise TvdbError(f"could not reach TheTVDB ({type(e).__name__})") from None
        if r.status_code == 401:
            raise TvdbError("TheTVDB refused the API key (or the PIN)")
        if r.status_code >= 400:
            raise TvdbError(f"TheTVDB returned HTTP {r.status_code}")
        try:
            token = r.json()["data"]["token"]
        except (ValueError, KeyError, TypeError):
            raise TvdbError("TheTVDB sent an unexpected login response") from None
        if not isinstance(token, str) or not token:
            raise TvdbError("TheTVDB sent no token")
        self._token = token
        return token

    async def _get(self, path: str, **params) -> dict:
        if not self._token:
            await self.login()
        for attempt in range(RETRIES + 1):
            try:
                r = await self._client.get(f"{API}{path}", params=params,
                                           headers={"Authorization": f"Bearer {self._token}"})
            except httpx.TimeoutException:
                raise TvdbError("TheTVDB did not answer in time") from None
            except httpx.HTTPError as e:
                raise TvdbError(f"TheTVDB request failed: {type(e).__name__}") from None
            if r.status_code == 429 and attempt < RETRIES:
                await self._sleep(retry_after(r.headers.get("Retry-After")))
                continue
            if r.status_code == 401:
                raise TvdbError("TheTVDB refused the API key")
            if r.status_code == 404:
                raise TvdbError("TheTVDB has no such record")
            if r.status_code >= 400:
                raise TvdbError(f"TheTVDB returned HTTP {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                raise TvdbError("TheTVDB sent a response that is not JSON") from None
            if not isinstance(data, dict):
                raise TvdbError("TheTVDB sent an unexpected response")
            return data
        raise AssertionError("unreachable")

    async def search_series(self, query: str) -> list[dict]:
        data = (await self._get("/search", query=query, type="series")).get("data")
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    async def episodes(self, series_id: int, season: int, order: str = "default") -> list[Episode]:
        """A season's episodes in ``order`` ("default" is aired order, or "dvd"), named in
        the TMDB language when TheTVDB has a translation."""
        try:
            raw = await self._episode_pages(f"/series/{series_id}/episodes/{order}/{self.language}", season)
        except TvdbError:
            raw = []
        if not raw:  # no translation, so names are in the show's own language
            raw = await self._episode_pages(f"/series/{series_id}/episodes/{order}", season)
        return to_episodes(raw, season)

    async def _episode_pages(self, path: str, season: int) -> list[dict]:
        out: list[dict] = []
        for page in range(MAX_PAGES):
            data = await self._get(path, season=season, page=page)
            body = data.get("data")
            eps = body.get("episodes") if isinstance(body, dict) else None
            out.extend(e for e in eps or [] if isinstance(e, dict))
            links = data.get("links")
            if not (isinstance(links, dict) and links.get("next")):
                break
        return out

    async def aclose(self) -> None:
        await self._client.aclose()


def to_episodes(raw: list[dict], season: int) -> list[Episode]:
    out: dict[int, Episode] = {}
    for e in raw:
        s, n = e.get("seasonNumber"), e.get("number")
        if not (isinstance(s, int) and isinstance(n, int)) or s != season or n <= 0:
            continue
        rt = e.get("runtime")
        name = e.get("name") if isinstance(e.get("name"), str) else ""
        aired = e.get("aired") if isinstance(e.get("aired"), str) else None
        out.setdefault(n, Episode(season=s, episode=n, name=name, air_date=aired,
                                  runtime=rt if isinstance(rt, int) and rt > 0 else None))
    return [out[n] for n in sorted(out)]


def tmdb_id_of(result: dict) -> int | None:
    """The TMDB id in a TheTVDB search result's remote ids."""
    for r in result.get("remote_ids") or []:
        if isinstance(r, dict) and "themoviedb" in str(r.get("sourceName", "")).lower():
            try:
                return int(r.get("id"))
            except (TypeError, ValueError):
                return None
    return None


def tvdb_id_of(result: dict) -> int | None:
    v = result.get("tvdb_id") or str(result.get("id") or "").removeprefix("series-")
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
