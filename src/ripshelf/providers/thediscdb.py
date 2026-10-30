"""TheDiscDB (thediscdb.com), a community database of discs and what is on them.

A disc is found by one of two fingerprints, both read without ripping:

  Disc ID       Blu-ray/UHD: SHA-1 of AACS/Unit_Key_RO.inf (the AACS Disc ID).
                DVD: libdvdread's disc id, MD5 of VIDEO_TS.IFO and the title-set
                IFOs. Looked up at /api/discid/{id}.
  content hash  MD5 over the sizes (64-bit little-endian) of BDMV/STREAM/*.m2ts,
                or of every file in VIDEO_TS, in name order. /api/dischash/{hash}.

Both are computed as TheDiscDB's own code does (TheDiscDb.Core/DiscHash). The
record says what the disc is (with TMDB, IMDb and TheTVDB ids), and for each
MakeMKV title whether it is the film, an episode (season and number) or an extra.
"""

from __future__ import annotations

import hashlib
import logging
import re
import struct
from pathlib import Path

import httpx

from .. import discfs
from ..models import DiscMatch, DiscMatchTitle, MediaKind, TitleInfo

SITE = "https://thediscdb.com"
log = logging.getLogger(__name__)
# libdvdread hashes VIDEO_TS.IFO and at most nine title-set IFOs.
DVD_MAX_FILES = 10
KINDS = {"mainmovie": "main", "episode": "episode"}


class DiscDbError(Exception):
    pass


# --- fingerprints ---------------------------------------------------------------


def aacs_disc_id(unit_key: bytes) -> str:
    return hashlib.sha1(unit_key).hexdigest().upper()


def dvd_disc_id(video_ts_ifo: bytes, vts_ifos: list[bytes | None]) -> str | None:
    if len(video_ts_ifo) < 0x40:
        return None
    sets = min(int.from_bytes(video_ts_ifo[0x3E:0x40], "big") + 1, DVD_MAX_FILES)
    h = hashlib.md5(video_ts_ifo)
    for ifo in vts_ifos[: sets - 1]:
        if ifo is not None:
            h.update(ifo)
    return h.hexdigest().upper()


def content_hash(sizes: list[tuple[str, int]]) -> str | None:
    """TheDiscDB's content hash from the disc's video file sizes (see the module docs)."""
    stream = sorted((p.rsplit("/", 1)[-1], n) for p, n in sizes
                    if re.fullmatch(r"BDMV/STREAM/[^/]+\.m2ts", p, re.I))
    files = stream or sorted((p.rsplit("/", 1)[-1], n) for p, n in sizes
                             if re.fullmatch(r"VIDEO_TS/[^/]+", p, re.I))
    if not files:
        return None
    h = hashlib.md5()
    for _, n in files:
        h.update(struct.pack("<q", n))
    return h.hexdigest().upper()


def fingerprints(structure_dir: Path) -> dict[str, str]:
    """{"disc_id": ..., "content_hash": ...} for the structure read from a disc (either may be missing)."""
    out: dict[str, str] = {}
    for rel in ("AACS/Unit_Key_RO.inf", "AACS/DUPLICATE/Unit_Key_RO.inf"):
        f = structure_dir / rel
        if f.is_file():
            out["disc_id"] = aacs_disc_id(f.read_bytes())
            break
    vmg = structure_dir / "VIDEO_TS" / "VIDEO_TS.IFO"
    if "disc_id" not in out and vmg.is_file():
        vts = [(structure_dir / "VIDEO_TS" / f"VTS_{n:02d}_0.IFO") for n in range(1, DVD_MAX_FILES)]
        did = dvd_disc_id(vmg.read_bytes(), [f.read_bytes() if f.is_file() else None for f in vts])
        if did:
            out["disc_id"] = did
    ch = content_hash(discfs.read_sizes(structure_dir) or discfs.clip_sizes(structure_dir))
    if ch:
        out["content_hash"] = ch
    return out


# --- lookup -----------------------------------------------------------------------


class TheDiscDb:
    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=15, headers={"User-Agent": "ripshelf"})

    async def _get(self, path: str, **params) -> object | None:
        try:
            r = await self._client.get(f"{SITE}{path}", params=params or None)
        except httpx.TimeoutException:
            raise DiscDbError("TheDiscDB did not answer in time") from None
        except httpx.HTTPError as e:
            raise DiscDbError(f"could not reach TheDiscDB ({type(e).__name__})") from None
        if r.status_code in (400, 404):
            return None
        if r.status_code >= 400:
            raise DiscDbError(f"TheDiscDB returned HTTP {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise DiscDbError("TheDiscDB sent a response that is not JSON") from None

    async def lookup(self, fps: dict[str, str]) -> DiscMatch | None:
        """The disc's record, by Disc ID first, then by content hash."""
        if did := fps.get("disc_id"):
            got = await self._get(f"/api/discid/{did}")
            first = got[0] if isinstance(got, list) and got else None
            if isinstance(first, dict) and (m := to_match(first, "disc_id")):
                return m
        if ch := fps.get("content_hash"):
            got = await self._get(f"/api/dischash/{ch}")
            if isinstance(got, dict):
                return to_match(got, "content_hash")
        return None

    async def search(self, query: str) -> list[dict]:
        """Title search (used by the Settings test)."""
        got = await self._get("/api/search", q=query, limit=5)
        return [r for r in got if isinstance(r, dict)] if isinstance(got, list) else []

    async def aclose(self) -> None:
        await self._client.aclose()


def _int(v: object) -> int | None:
    try:
        return int(str(v).strip()) if v not in (None, "") else None
    except ValueError:
        return None


def _duration(v: object) -> int | None:
    m = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", str(v or ""))
    return int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) if m else None


def to_match(d: dict, by: str) -> DiscMatch | None:
    media = d.get("media") if isinstance(d.get("media"), dict) else None
    if not media or not isinstance(media.get("title"), str):
        return None
    kind = str(media.get("type") or "").lower()
    if kind not in ("movie", "series"):
        return None
    ext = media.get("external_ids") if isinstance(media.get("external_ids"), dict) else {}
    release = d.get("release") if isinstance(d.get("release"), dict) else {}
    disc = d.get("disc") if isinstance(d.get("disc"), dict) else {}
    titles = []
    for t in d.get("titles") or []:
        if not isinstance(t, dict) or not isinstance(t.get("index"), int):
            continue
        item = t.get("item") if isinstance(t.get("item"), dict) else None
        kind_of = None
        if item:
            kind_of = KINDS.get(str(item.get("type") or "").lower(), "extra")
        titles.append(DiscMatchTitle(
            index=t["index"], source_file=t.get("source_file") or None, segment_map=t.get("segment_map") or None,
            duration_s=_duration(t.get("duration")), size_bytes=t.get("size") if isinstance(t.get("size"), int) else None,
            kind=kind_of, name=(item or {}).get("title") or None,
            season=_int((item or {}).get("season")), episode=_int((item or {}).get("episode"))))
    imdb = ext.get("imdb") if isinstance(ext.get("imdb"), str) and ext["imdb"].startswith("tt") else None
    year = media.get("year") if isinstance(media.get("year"), int) and media["year"] > 0 else None
    return DiscMatch(
        by=by, media=MediaKind.movie if kind == "movie" else MediaKind.tv, title=media["title"], year=year,
        tmdb_id=_int(ext.get("tmdb")), imdb_id=imdb, tvdb_id=_int(ext.get("tvdb")),
        release=release.get("title") or None, disc_name=disc.get("name") or None, titles=titles)


# --- this disc's titles ----------------------------------------------------------------


def place_titles(match: DiscMatch, scan: list[TitleInfo]) -> int:
    """Set each described title's ``scan_index`` (the MakeMKV title it is here); returns
    how many were placed. Blu-ray titles match by playlist and clips; DVD titles (no
    playlist) by title number and length."""
    used: set[int] = set()
    placed = 0
    for t in match.titles:
        t.scan_index = None
        hit = None
        if t.source_file:
            hit = next((s for s in scan if s.index not in used and s.source_file == t.source_file
                        and (not t.segment_map or not s.segments_map or s.segments_map == t.segment_map)), None)
        if hit is None and t.duration_s is not None:
            hit = next((s for s in scan if s.index == t.index and s.index not in used
                        and abs(s.duration_s - t.duration_s) <= 2
                        and (not t.source_file or not s.source_file)), None)
        if hit:
            t.scan_index = hit.index
            used.add(hit.index)
            placed += 1
    return placed


def summary(match: DiscMatch) -> str:
    where = ", ".join(x for x in (match.release, match.disc_name) if x)
    year = f" ({match.year})" if match.year else ""
    return f"{match.title}{year}" + (f", {where}" if where else "")
