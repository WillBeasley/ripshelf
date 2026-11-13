"""TV identification without a TMDB key: shows and episodes from TVmaze and TheTVDB.

A show found here is keyed by that provider's own id (``Show.source``), so its
collection, review and filing work as a TMDB show's do. Shows are scored as
tmdb_tv scores TMDB's, with the same guard against series about the show.
"""

from __future__ import annotations

import logging

from .. import providers
from ..models import DiscMatch, Episode, Show
from ..providers.tvmaze import Tvmaze, TvmazeError
from ..providers.tvmaze import externals as tvmaze_externals
from .labels import split_show_name
from .tmdb_tv import DOCUMENTARY, NEWS, REALITY, TALK, queries_for, rank_shows
from .tvdb import Tvdb, TvdbError, tvdb_id_of

log = logging.getLogger(__name__)

SOURCES = ("tvdb", "tvmaze")
# TVmaze's show types, as the TMDB genres the extras guard knows.
TVMAZE_TYPES = {"Documentary": DOCUMENTARY, "Talk Show": TALK, "News": NEWS, "Reality": REALITY}


def tvmaze_show(m: dict) -> Show:
    tvdb, imdb = tvmaze_externals(m)
    date = str(m.get("premiered") or "")
    image = m.get("image") if isinstance(m.get("image"), dict) else {}
    poster = image.get("medium") or image.get("original")
    runtime = next((r for r in (m.get("averageRuntime"), m.get("runtime")) if isinstance(r, int) and r > 0), None)
    weight = m.get("weight")
    return Show(source="tvmaze", tvmaze_id=m["id"], tvdb_id=tvdb, imdb_id=imdb,
                name=m.get("name") if isinstance(m.get("name"), str) and m["name"].strip() else "?",
                year=int(date[:4]) if date[:4].isdigit() else None,
                poster_url=poster if isinstance(poster, str) else None, episode_runtime=runtime,
                genre_ids=[g for t, g in TVMAZE_TYPES.items() if m.get("type") == t],
                vote_count=weight if isinstance(weight, int) else None, providers=["tvmaze"])


def tvdb_show(r: dict, language: str = "eng") -> Show | None:
    """A TheTVDB search result as a show, or None without its id. Named in ``language``
    (TheTVDB's three-letter code) when the result has that translation."""
    tid = tvdb_id_of(r)
    if not tid:
        return None
    year = str(r.get("year") or r.get("first_air_time") or "")
    imdb = next((str(x.get("id")) for x in r.get("remote_ids") or [] if isinstance(x, dict)
                 and str(x.get("id", "")).startswith("tt")), None)
    name = r.get("name") if isinstance(r.get("name"), str) and r["name"].strip() else None
    eng = (r.get("translations") or {}).get(language) if isinstance(r.get("translations"), dict) else None
    poster = r.get("image_url") or r.get("thumbnail")
    return Show(source="tvdb", tvdb_id=tid, imdb_id=imdb,
                name=eng if isinstance(eng, str) and eng.strip() else name or "?",
                original_name=name if isinstance(eng, str) and eng.strip() and eng != name else None,
                year=int(year[:4]) if year[:4].isdigit() else None,
                poster_url=poster if isinstance(poster, str) and poster.startswith("https://") else None,
                genre_ids=[DOCUMENTARY] if "Documentary" in (r.get("genres") or []) else [],
                providers=["tvdb"])


async def find_shows(show_name: str, episode_s: float | None, year: int | None = None,
                     sources: list[str] | None = None, tvdb: Tvdb | None = None,
                     tvmaze: Tvmaze | None = None, failed: list[str] | None = None) -> list[Show]:
    """Shows for a name, from ``sources`` (TheTVDB, TVmaze) asked in order until one
    finds something, keyed by that provider's id. A source that fails is passed over,
    and named in ``failed`` (if given)."""
    show_name = split_show_name(show_name).show
    names = [show_name, *queries_for(show_name)[1:2]]
    for src in sources or SOURCES:
        shows: dict[int, Show] = {}
        try:
            for q in queries_for(show_name)[:2]:
                if src == "tvdb" and tvdb:
                    got = [s for r in (await tvdb.search_series(q))[:8] if (s := tvdb_show(r, tvdb.language))]
                elif src == "tvmaze" and tvmaze:
                    got = [tvmaze_show(m) for m in (await tvmaze.search(q))[:8]]
                else:
                    got = []
                for s in got:
                    shows.setdefault(s.tvdb_id if src == "tvdb" else s.tvmaze_id, s)
                if shows:
                    break
        except (TvdbError, TvmazeError) as e:
            log.warning("%s search for %r failed: %s", providers.label(src), show_name, e)
            if failed is not None:
                failed.append(str(e))
        if shows:
            return rank_shows(list(shows.values()), names, episode_s, year)
    return []


async def disc_match_show(match: DiscMatch, sources: list[str], tvmaze: Tvmaze | None) -> Show | None:
    """The show a disc database names, without TMDB: TVmaze's show for its TheTVDB or
    IMDb id, else (with TheTVDB on) the TheTVDB id it carries."""
    for src in sources:
        if src == "tvmaze" and tvmaze and (match.tvdb_id or match.imdb_id):
            try:
                found = await tvmaze.lookup(match.tvdb_id, match.imdb_id)
            except TvmazeError as e:
                log.warning("TVmaze lookup of %s failed: %s", match.title, e)
                continue
            if found:
                return tvmaze_show(found)
        elif src == "tvdb" and match.tvdb_id:
            return Show(source="tvdb", tvdb_id=match.tvdb_id, imdb_id=match.imdb_id, name=match.title,
                        year=match.year, providers=["tvdb"])
    return None


async def season_episodes(show: Show, season: int, tvdb: Tvdb | None, tvmaze: Tvmaze | None,
                          dvd: bool = False) -> tuple[list[Episode], str]:
    """A season's episodes from the show's own provider, and a note on the order."""
    if show.source == "tvmaze":
        if not tvmaze:
            raise TvmazeError("TVmaze is off")
        return await tvmaze.episodes(show.tvmaze_id, season), "aired order"
    if not tvdb:
        raise TvdbError("no TheTVDB key set")
    got = await tvdb.episodes(show.tvdb_id, season, "dvd" if dvd else "default")
    if not got and dvd:
        return await tvdb.episodes(show.tvdb_id, season, "default"), "aired order (no DVD order)"
    return got, "DVD order" if dvd else "aired order"


async def all_episodes(show: Show, tvdb: Tvdb | None, tvmaze: Tvmaze | None, dvd: bool = False) -> list[Episode]:
    """Every episode of every season (compilations), specials last."""
    if show.source == "tvmaze":
        if not tvmaze:
            raise TvmazeError("TVmaze is off")
        return await tvmaze.all_episodes(show.tvmaze_id)
    if not tvdb:
        raise TvdbError("no TheTVDB key set")
    return await tvdb.all_episodes(show.tvdb_id, "dvd" if dvd else "default")

