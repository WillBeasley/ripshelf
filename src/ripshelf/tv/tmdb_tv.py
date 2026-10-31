"""TV identification against TMDB, for shows, seasons and episodes.

Shows are scored like films (title similarity, plus how well the disc's
episode length agrees with the show's), with the same guard against extras:
"Captain Comet Confidential" and other documentary/behind-the-scenes series
about a show never win for that show's own disc.
"""

from __future__ import annotations

import asyncio
import logging
import re

from .. import providers
from ..identify.scoring import looks_like_extra, title_similarity
from ..identify.tmdb import POSTER_BASE, Tmdb, TmdbError
from ..models import Episode, Evidence, Show
from ..providers.tvmaze import Tvmaze, TvmazeError
from ..providers.tvmaze import externals as tvmaze_externals
from .labels import split_show_name
from .tvdb import Tvdb, TvdbError, tmdb_id_of, tvdb_id_of

log = logging.getLogger(__name__)

DOCUMENTARY = 99
TALK, NEWS, REALITY = 10767, 10763, 10764
SHOW_DETAILS_FOR_TOP = 4
EXCLUDED_CAP = 0.3
W_TITLE, W_RUNTIME = 0.7, 0.3
# Words that turn "X <word>" into a series about X rather than X itself.
_ABOUT = re.compile(r"\b(confidential|unscripted|uncovered|revealed|extra|extras|behind|aftershow|after show"
                    r"|companion|explained|the story|declassified|reunion)\b", re.I)


def to_show(m: dict) -> Show:
    date = str(m.get("first_air_date") or "")
    name = m.get("name") if isinstance(m.get("name"), str) and m["name"].strip() else None
    original = m.get("original_name") if isinstance(m.get("original_name"), str) else None
    genres = m.get("genre_ids")
    if not genres and isinstance(m.get("genres"), list):
        genres = [g.get("id") for g in m["genres"] if isinstance(g, dict)]
    runtimes = [r for r in (m.get("episode_run_time") or []) if isinstance(r, int) and r > 0] \
        if isinstance(m.get("episode_run_time"), list) else []
    seasons = {}
    for x in m.get("seasons") or []:
        if isinstance(x, dict) and isinstance(x.get("season_number"), int):
            seasons[x["season_number"]] = x.get("episode_count") if isinstance(x.get("episode_count"), int) else 0
    poster = m.get("poster_path") if isinstance(m.get("poster_path"), str) else None
    ext = m.get("external_ids") if isinstance(m.get("external_ids"), dict) else {}
    tvdb = m.get("tvdb_id") if isinstance(m.get("tvdb_id"), int) else ext.get("tvdb_id")
    imdb = ext.get("imdb_id") if isinstance(ext.get("imdb_id"), str) and ext["imdb_id"].startswith("tt") else None
    return Show(
        tmdb_id=m["id"], tvdb_id=tvdb if isinstance(tvdb, int) and tvdb > 0 else None, imdb_id=imdb,
        name=name or original or "?", original_name=original,
        year=int(date[:4]) if date[:4].isdigit() else None,
        poster_url=f"{POSTER_BASE}{poster}" if poster else None,
        episode_runtime=min(runtimes) if runtimes else None, seasons=seasons,
        genre_ids=[g for g in (genres or []) if isinstance(g, int)] if isinstance(genres, list) else [],
        vote_count=m.get("vote_count") if isinstance(m.get("vote_count"), int) else None,
    )


def to_episodes(season: dict) -> list[Episode]:
    out = []
    for e in season.get("episodes") or []:
        if not isinstance(e, dict):
            continue
        s, n = e.get("season_number"), e.get("episode_number")
        if not (isinstance(s, int) and isinstance(n, int)) or s < 0 or n < 0:
            continue
        rt = e.get("runtime")
        out.append(Episode(season=s, episode=n, name=e.get("name") if isinstance(e.get("name"), str) else "",
                           runtime=rt if isinstance(rt, int) and rt > 0 else None,
                           air_date=e.get("air_date") if isinstance(e.get("air_date"), str) else None))
    return sorted(out, key=lambda e: e.episode)


def queries_for(show_name: str) -> list[str]:
    """The label's show name, then shorter prefixes, because compilation labels append an
    episode name ("PIPPA_&_GOOSE_KITE_TRAM")."""
    words = show_name.split()
    out = [show_name] if show_name else []
    for k in range(len(words) - 1, 0, -1):
        q = " ".join(words[:k])
        if len(q) >= 3 and q.lower() not in ("the", "a", "an"):
            out.append(q)
    return out[:4]


def score_show(show: Show, names: list[str], episode_s: float | None) -> Show:
    evidence: list[Evidence] = []
    best_sim, best_name = 0.0, ""
    for n in names:
        for t in filter(None, (show.name, show.original_name)):
            sim = title_similarity(n, t)
            # A label "Pippa & Goose Kite Tram" contains the show name, so that is a match.
            if title_similarity(n[: len(t)], t) >= 0.95 and len(t) >= 4:
                sim = max(sim, 0.97)
            if sim > best_sim:
                best_sim, best_name = sim, n
    if best_sim >= 0.95:
        evidence.append(Evidence(ok=True, text=f"Show name matches the disc (“{best_name}”)"))
    elif best_sim >= 0.6:
        evidence.append(Evidence(ok=None, text=f"Show name is close to “{best_name}” ({best_sim:.0%})"))
    else:
        evidence.append(Evidence(ok=False, text=f"Show name differs from the disc ({best_sim:.0%})"))
    parts = [(W_TITLE, best_sim)]
    if episode_s and show.episode_runtime:
        minutes = episode_s / 60
        diff = abs(minutes - show.episode_runtime)
        rt = 1.0 if diff <= max(3, show.episode_runtime * 0.15) else max(0.0, 1 - diff / show.episode_runtime)
        parts.append((W_RUNTIME, rt))
        ok = True if rt >= 0.99 else (None if rt > 0.5 else False)
        evidence.append(Evidence(ok=ok, text=f"Episodes on the disc are about {round(minutes)} min; "
                                             f"TMDB says {show.episode_runtime} min"))
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    reasons = []
    for t in filter(None, (show.name, show.original_name)):
        extra = looks_like_extra(t, names)
        if extra:
            reasons.append(extra)
            break
    if not reasons and any(g in show.genre_ids for g in (DOCUMENTARY, TALK, NEWS, REALITY)):
        about = _ABOUT.search(show.name)
        if about or (best_sim < 0.95 and any(n and n.lower() in show.name.lower() for n in names)):
            reasons.append(f"a {'documentary' if DOCUMENTARY in show.genre_ids else 'talk/reality'} "
                           f"series about the show")
    if not reasons and _ABOUT.search(show.name) and not any(_ABOUT.search(n) for n in names):
        reasons.append(f"a companion series, “{_ABOUT.search(show.name).group(1)}”")
    show.excluded = bool(reasons)
    if reasons:
        score = min(score, EXCLUDED_CAP)
        evidence.insert(0, Evidence(ok=False, text="Excluded (" + ", ".join(reasons) + ")"))
    show.score = round(score, 3)
    show.evidence = evidence
    return show


def rank_shows(shows: list[Show], names: list[str], episode_s: float | None,
               year: int | None = None) -> list[Show]:
    scored = [score_show(s, names, episode_s) for s in shows]
    if year:
        for s in scored:
            if s.year and abs(s.year - year) <= 1:
                s.evidence.append(Evidence(ok=True, text=f"First aired {s.year}"))
            elif s.year:
                s.score = round(s.score * 0.9, 3)
    return sorted(scored, key=lambda s: (s.excluded, -s.score, -(s.vote_count or 0)))


async def find_shows(tmdb: Tmdb, show_name: str, episode_s: float | None,
                     year: int | None = None, tvdb: Tvdb | None = None,
                     sources: list[str] | None = None, tvmaze: Tvmaze | None = None) -> list[Show]:
    """Shows for a name from the disc or typed in. Season, disc and volume markers are
    dropped first ("Boardwalk Empire Season 1 Disc 2" searches "Boardwalk Empire").
    ``sources`` (default TMDB, then TheTVDB) are asked in order until one finds
    something; TheTVDB's and TVmaze's hits are looked up on TMDB."""
    show_name = split_show_name(show_name).show
    sources = sources or ["tmdb", "tvdb"]
    seen: dict[int, dict] = {}
    via: dict[int, str] = {}
    for src in sources:
        if seen:
            break
        if src == "tmdb":
            for q in queries_for(show_name):
                for r in (await tmdb.search_tv(q))[:8]:
                    seen.setdefault(r["id"], r)
                if len(seen) >= 3:
                    break
        elif src == "tvdb" and tvdb:
            via |= dict.fromkeys(await _tvdb_hits(tmdb, tvdb, show_name, seen), "tvdb")
        elif src == "tvmaze" and tvmaze:
            via |= dict.fromkeys(await _tvmaze_hits(tmdb, tvmaze, show_name, seen), "tvmaze")
    names = [show_name, *queries_for(show_name)[1:2]]
    first = rank_shows([to_show(r) for r in seen.values()], names, None, year)
    top = [s.tmdb_id for s in first[:SHOW_DETAILS_FOR_TOP]]
    details = await asyncio.gather(*(tmdb.tv_show(t) for t in top), return_exceptions=True)
    shows = []
    for s in first:
        d = details[top.index(s.tmdb_id)] if s.tmdb_id in top else None
        if isinstance(d, dict):
            shows.append(to_show({**seen[s.tmdb_id], **{k: v for k, v in d.items() if k != "id"},
                                  "id": s.tmdb_id}))
        else:
            shows.append(to_show(seen[s.tmdb_id]))
    ranked = rank_shows(shows, names, episode_s, year)
    missed = " (TMDB's search missed it)" if "tmdb" in sources else ""
    for s in ranked:
        if s.tmdb_id in via:
            s.providers = [via[s.tmdb_id], "tmdb"]
            s.evidence.append(Evidence(ok=None, text=f"Found through {providers.label(via[s.tmdb_id])}{missed}"))
        else:
            s.providers = ["tmdb"]
    return ranked


async def _tvdb_hits(tmdb: Tmdb, tvdb: Tvdb, show_name: str, seen: dict[int, dict]) -> set[int]:
    """Add TheTVDB's hits that carry a TMDB id to ``seen``; returns those ids."""
    found: set[int] = set()
    try:
        for q in queries_for(show_name)[:2]:
            for r in (await tvdb.search_series(q))[:SHOW_DETAILS_FOR_TOP]:
                tid = tmdb_id_of(r)
                if tid and tid not in seen:
                    try:
                        seen[tid] = {**await tmdb.tv_show(tid), "id": tid, "tvdb_id": tvdb_id_of(r)}
                    except TmdbError:
                        continue
                    found.add(tid)
            if found:
                break
    except TvdbError as e:
        log.warning("TheTVDB search for %r failed: %s", show_name, e)
    return found


async def _tvmaze_hits(tmdb: Tmdb, tvmaze: Tvmaze, show_name: str, seen: dict[int, dict]) -> set[int]:
    """Add TVmaze's hits to ``seen``, looked up on TMDB by their TheTVDB or IMDb id."""
    found: set[int] = set()
    try:
        for q in queries_for(show_name)[:2]:
            for r in (await tvmaze.search(q))[:SHOW_DETAILS_FOR_TOP]:
                tvdb_id, imdb_id = tvmaze_externals(r)
                try:
                    tid = await tmdb.find_id("tv", tvdb_id, imdb_id)
                    if tid and tid not in seen:
                        seen[tid] = {**await tmdb.tv_show(tid), "id": tid}
                        found.add(tid)
                except TmdbError:
                    continue
            if found:
                break
    except TvmazeError as e:
        log.warning("TVmaze search for %r failed: %s", show_name, e)
    return found


async def season_episodes(tmdb: Tmdb, tmdb_id: int, season: int) -> list[Episode]:
    return (await season_details(tmdb, tmdb_id, season))[0]


async def season_details(tmdb: Tmdb, tmdb_id: int, season: int) -> tuple[list[Episode], str | None]:
    """Episodes of a season and its poster URL."""
    data = await tmdb.season(tmdb_id, season)
    poster = data.get("poster_path") if isinstance(data.get("poster_path"), str) else None
    return to_episodes(data), (f"{POSTER_BASE}{poster}" if poster else None)


async def all_episodes(tmdb: Tmdb, show: Show) -> list[Episode]:
    """Every episode of every season (compilations), specials last."""
    seasons = sorted(show.seasons, key=lambda n: (n == 0, n)) or [1]
    got = await asyncio.gather(*(tmdb.season(show.tmdb_id, n) for n in seasons), return_exceptions=True)
    out = []
    for g in got:
        if isinstance(g, TmdbError):
            continue
        if isinstance(g, dict):
            out.extend(to_episodes(g))
    return sorted(out, key=lambda e: (e.season == 0, e.season, e.episode))
