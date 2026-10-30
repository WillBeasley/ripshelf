"""Contributing discs to TheDiscDB (opt-in, Settings → Metadata providers).

TheDiscDB takes discs from rippers through its import API (built for the Engram
ripper; written here from the API's description, not from Engram's code):

  POST {base}/api/engram/disc                         the disc, as JSON (below)
  POST {base}/api/engram/disc/{content_hash}/logs/scan  MakeMKV's scan (text/plain)

The disc is keyed by its content hash (the one the lookups use), so sending it
again replaces the earlier copy. Discs sent with the same ``release_id`` become one
release. Nothing is published straight away, because the person finishes the release on
the site ({base}/contribute/engram/{release_id}), signed in, and submits it for review.

Only what describes the disc is sent. That's the content hash, volume label, disc type,
what it is (TMDB id, title, season) and, for each title kept, its name, length, size and
which film, episode or extra it is. The scan log is cut down to MakeMKV's disc, title and
stream lines. Drive lines (model, serial, device path) and messages (which can name
folders, drives and the LibreDrive id) are left out.

TheDiscDB makes one item for each title listed in the disc JSON (matched to the scan by
segment map, then index), named after its ``source_filename``, and skips the scan's other
titles. So only the film, the episodes and the extras the user named are listed, each with
its name there: the film's title, the episode's name, or the user's name for the extra.
The playlist itself still reaches TheDiscDB through the scan log.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from .. import __version__
from ..models import ContributionTitle, DiscKind, MediaKind, TitleRole

if TYPE_CHECKING:
    from ..config import Settings
    from ..db import JobRecord

log = logging.getLogger(__name__)

TIMEOUT_S = 30
# A disc confirmed within this long of another of the same title (and season, and
# disc type) joins its release, like the other discs of a box set or season.
RELEASE_WINDOW_DAYS = 7
CONTENT_TYPES = {DiscKind.dvd: "dvd", DiscKind.bluray: "blu-ray", DiscKind.uhd: "4k"}
# The scan lines TheDiscDB's log parser builds the disc from. The rest (DRV, MSG) can
# name the drive, its serial, device paths and folders, so they are never sent.
LOG_KEEP = ("CINFO:", "TINFO:", "SINFO:", "TCOUNT:")
RELEASE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
HASH = re.compile(r"[A-Fa-f0-9]{8,128}")


class ContributeError(Exception):
    pass


def base_url(s: Settings) -> str:
    return (s.thediscdb_contribute_url or "https://thediscdb.com").strip().rstrip("/")


def contribute_url(s: Settings, release_id: str) -> str:
    return f"{base_url(s)}/contribute/engram/{release_id}"


def new_release_id() -> str:
    return str(uuid.uuid4())


# --- what may be sent --------------------------------------------------------------


def content_hash(job: JobRecord) -> str | None:
    h = (job.disc_ids or {}).get("content_hash")
    return h.upper() if h and HASH.fullmatch(h) else None


def scan_log(path: Path) -> str | None:
    """The job's MakeMKV scan, as sent, with only the disc, title and stream lines."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    kept = [ln for ln in text.splitlines() if ln.startswith(LOG_KEEP)]
    return "\n".join(kept) + "\n" if any(ln.startswith("TINFO:") for ln in kept) else None


def eligibility(s: Settings, job: JobRecord, log_path: Path) -> tuple[bool, str | None, bool]:
    """(can it be sent, why not, does TheDiscDB know it already)."""
    if s.thediscdb_contribute == "off":
        return False, "Contributing is off (Settings → Metadata providers)", False
    if job.disc_match and job.disc_match.provider == "thediscdb":
        return False, "TheDiscDB already knows this disc, because it identified it", True
    if job.mode == "backup" and job.media == MediaKind.tv:
        return False, "A full disc backup has no episodes to describe", False
    if job.disc.kind not in CONTENT_TYPES:
        return False, "Only DVDs, Blu-rays and UHD Blu-rays can be added", False
    if not job.identity or not job.identity.tmdb_id:
        return False, "Confirm what the disc is first", False
    if job.media == MediaKind.tv and not (job.tv and job.tv.show and job.tv.mapping):
        return False, "Confirm the episodes first", False
    if job.media == MediaKind.movie and not job.main_title:
        return False, "The scan found no main feature", False
    if not content_hash(job):
        return False, ("The disc's content hash is missing, because the sizes of its video files weren't read. "
                       "Insert the disc again to make a new job that has them"), False
    if not job.titles or scan_log(log_path) is None:
        return False, "The MakeMKV scan of the disc is missing", False
    return True, None, False


def todo(job: JobRecord) -> str | None:
    """What the user still has to do before an eligible disc is sent: go through its titles,
    naming the extras worth keeping, so TheDiscDB gets names rather than playlists."""
    c = job.contribution
    if not c or not c.items_confirmed:
        return "Name the extras worth keeping, or say there are none, before it is sent"
    return None


def auto_send(s: Settings, job: JobRecord) -> bool:
    """Send without asking, in "automatic" mode, and only identities the user confirmed."""
    return s.thediscdb_contribute == "auto" and job.identity_source == "user"


# --- the payload ----------------------------------------------------------------------


def segment_count(segments_map: str | None) -> int | None:
    if not segments_map:
        return None
    n = 0
    for part in segments_map.split(","):
        a, _, b = part.strip().partition("-")
        if not a.isdigit() or (b and not b.isdigit()):
            return None
        n += int(b) - int(a) + 1 if b else 1
    return n or None


def _confidence(job: JobRecord) -> float:
    if job.identity_source == "user":
        return 1.0
    score = job.identity.score if job.identity else 0.0
    return round(min(max(score, 0.0), 1.0), 3)


def _episode_name(job: JobRecord, season: int, episode: int) -> str:
    for e in job.tv.episodes if job.tv else []:
        if (e.season, e.episode) == (season, episode) and e.name.strip():
            return e.name.strip()
    return f"Episode {episode}"


def _fixed(job: JobRecord) -> dict[int, dict[str, Any]]:
    """Title index -> how it is sent, for the film and the episodes, named after them."""
    out: dict[int, dict[str, Any]] = {}
    if job.media == MediaKind.movie:
        if job.main_title and job.identity:
            out[job.main_title.index] = {"title_type": "MainMovie", "name": job.identity.title}
        return out
    for m in job.tv.mapping if job.tv else []:
        if m.role == TitleRole.episode and m.episodes:
            ep = m.episodes[0]
            out[m.title_index] = {"title_type": "Episode", "season": str(ep.season), "episode": str(ep.episode),
                                  "name": " / ".join(_episode_name(job, e.season, e.episode) for e in m.episodes)}
    return out


def _title_roles(job: JobRecord) -> dict[int, dict[str, Any]]:
    """Title index -> its type and name, for every title that is sent: the film or the
    episodes, and the extras the user named. Everything else is left out."""
    out = _fixed(job)
    indexes = {t.index for t in job.titles}
    for x in job.contribution.extras if job.contribution else []:
        if x.title_index in indexes and x.title_index not in out:
            out[x.title_index] = {"title_type": x.kind, "name": x.name}
    return out


def naming_titles(job: JobRecord) -> list[ContributionTitle]:
    """The disc's titles for the naming list: the film or episodes (named already) and the
    rest, which the user names as extras or leaves out."""
    fixed = _fixed(job)
    tv_extras = {m.title_index for m in job.tv.mapping if m.role == TitleRole.extra} \
        if job.media == MediaKind.tv and job.tv else set()
    return [ContributionTitle(
        title_index=t.index, source=t.source_file, duration_s=t.duration_s, size_bytes=t.size_bytes,
        chapters=t.chapters, fixed=fixed.get(t.index, {}).get("title_type"), name=fixed.get(t.index, {}).get("name"),
        suggested=t.index in tv_extras,
    ) for t in sorted(job.titles, key=lambda t: t.index)]


def payload(job: JobRecord, release_id: str, disc_number: int | None, exported_at: datetime | None = None) -> dict:
    """The disc as TheDiscDB's import API takes it. Fields without a value are left out."""
    source = "user" if job.identity_source == "user" else "ripshelf"
    confidence = _confidence(job)
    roles, fixed = _title_roles(job), _fixed(job)
    titles = []
    for t in sorted(job.titles, key=lambda t: t.index):
        if t.index not in roles:
            continue
        role = dict(roles[t.index])
        titles.append(_compact({
            # TheDiscDB names the item after this, so it's the name rather than the playlist.
            "index": t.index, "source_filename": role.pop("name"), "duration_seconds": t.duration_s,
            "size_bytes": t.size_bytes, "chapter_count": t.chapters, "segment_count": segment_count(t.segments_map),
            "segment_map": t.segments_map, **role,
            # The extras are the user's own picks and names.
            **({"match_confidence": confidence, "match_source": source} if t.index in fixed
               else {"match_confidence": 1.0, "match_source": "user"}),
        }))
    season = job.tv.season if job.media == MediaKind.tv and job.tv else None
    return _compact({
        "engram_version": f"ripshelf/{__version__}",
        "export_version": "1",
        "exported_at": (exported_at or datetime.now(UTC)).isoformat(timespec="seconds"),
        "contribution_tier": 2,
        "disc": _compact({
            "content_hash": content_hash(job), "volume_label": job.disc.label,
            "content_type": CONTENT_TYPES.get(job.disc.kind), "disc_number": disc_number, "release_id": release_id,
        }),
        "identification": _compact({
            "tmdb_id": job.identity.tmdb_id if job.identity else None,
            "detected_title": job.identity.title if job.identity else None,
            "detected_season": season,
            "classification_source": "ripshelf",
            "classification_confidence": confidence,
        }),
        "titles": titles,
    })


def _compact(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def group_key(job: JobRecord) -> tuple[str, int, int | None, str] | None:
    """Discs with the same key, sent close together, are one release."""
    if not job.identity or not job.identity.tmdb_id or job.disc.kind not in CONTENT_TYPES:
        return None
    season = job.tv.season if job.media == MediaKind.tv and job.tv else None
    return job.media.value, job.identity.tmdb_id, season, CONTENT_TYPES[job.disc.kind]


# --- sending ----------------------------------------------------------------------------


class Contributor:
    def __init__(self, s: Settings, client: httpx.AsyncClient | None = None):
        self.base = base_url(s)
        headers = {"User-Agent": f"ripshelf/{__version__}"}
        if key := s.secret("thediscdb_api_key"):
            headers["Authorization"] = f"ApiKey {key}"
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._headers = headers

    async def _post(self, path: str, what: str, **kw) -> httpx.Response:
        try:
            r = await self._client.post(f"{self.base}{path}", headers={**self._headers, **kw.pop("headers", {})}, **kw)
        except httpx.TimeoutException:
            raise ContributeError(f"TheDiscDB did not answer in time ({what})") from None
        except httpx.HTTPError as e:
            raise ContributeError(f"could not reach TheDiscDB ({type(e).__name__}, {what})") from None
        if r.status_code >= 400:
            detail = r.text.strip().strip('"')[:200] if r.text else ""
            raise ContributeError(f"TheDiscDB refused the {what}: HTTP {r.status_code}"
                                  + (f" ({detail})" if detail else ""))
        return r

    async def send(self, body: dict, scan: str) -> dict:
        """Post the disc, then its scan log (TheDiscDB needs the disc first). Returns the disc reply."""
        ch = body["disc"]["content_hash"]
        if not HASH.fullmatch(ch) or not RELEASE_ID.fullmatch(body["disc"].get("release_id", "")):
            raise ContributeError("the content hash or release id is malformed")
        r = await self._post("/api/engram/disc", "disc", json=body)
        try:
            reply = r.json()
        except ValueError:
            reply = {}
        await self._post(f"/api/engram/disc/{ch}/logs/scan", "scan log", content=scan.encode(),
                         headers={"Content-Type": "text/plain; charset=utf-8"})
        return reply if isinstance(reply, dict) else {}

    async def aclose(self) -> None:
        await self._client.aclose()
