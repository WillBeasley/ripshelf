"""Poster cache for the Drives screen (and anything else that shows a disc's art).

Posters come from TMDB's image server only, because a candidate's poster URL can be
typed in by hand (manual confirm), and the server must not be talked into
fetching arbitrary addresses. Each poster is downloaded into ``<data>/art/<key>.jpg``
and kept for at most MAX_AGE_S. TMDB's terms allow caching its content for up to six
months, so an older copy is downloaded again when it's next shown, and prune()
deletes copies nobody has shown for that long.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

ALLOWED_PREFIX = "https://image.tmdb.org/"
MAX_BYTES = 5 * 1024 * 1024
MAX_AGE_S = 90 * 24 * 3600
_locks: dict[str, asyncio.Lock] = {}


def key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


async def cached(art_dir: Path, url: str, client: httpx.AsyncClient | None = None) -> Path | None:
    """The poster as a local file, downloading it the first time. None if it can't be had."""
    if not url.startswith(ALLOWED_PREFIX):
        return None
    k = key(url)
    path = art_dir / f"{k}.jpg"
    if _fresh(path):
        return path
    async with _locks.setdefault(k, asyncio.Lock()):
        if _fresh(path):
            return path
        art_dir.mkdir(parents=True, exist_ok=True)
        own = client is None
        client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        try:
            async with client.stream("GET", url) as r:
                if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                    return None
                data = bytearray()
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        return None
        except httpx.HTTPError as e:
            log.info("poster download failed: %s", type(e).__name__)
            return None
        finally:
            if own:
                await client.aclose()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(bytes(data))
        os.replace(tmp, path)
        return path


def _fresh(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < MAX_AGE_S
    except FileNotFoundError:
        return False


def prune(art_dir: Path) -> int:
    """Delete posters downloaded more than MAX_AGE_S ago. Returns how many went."""
    n = 0
    for p in art_dir.glob("*.jpg") if art_dir.is_dir() else ():
        if not _fresh(p):
            p.unlink(missing_ok=True)
            n += 1
    return n
