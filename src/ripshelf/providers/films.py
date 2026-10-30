"""Film candidates from the providers in Settings' "Films" list.

Every enabled title search is asked, in order, and the results merged, so a film
found by several providers (matched by TMDB or IMDb id) is one candidate that
lists them all, with the first provider's details. OMDb's hits that TMDB's
search missed are looked up on TMDB by IMDb id, so they get TMDB's details too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..identify.tmdb import Tmdb, TmdbError, to_candidate
from ..models import Candidate, DiscMatch, Evidence
from . import enabled, label
from .omdb import Omdb, OmdbError

if TYPE_CHECKING:
    from ..config import Settings

# A disc database's record of this disc is near-certain (people can still file a disc wrongly).
DISC_MATCH_SCORE = 0.95
FIND_FOR_TOP = 3


def same(a: Candidate, b: Candidate) -> bool:
    return bool((a.tmdb_id and a.tmdb_id == b.tmdb_id) or (a.imdb_id and a.imdb_id == b.imdb_id))


def merge(into: list[Candidate], got: list[Candidate]) -> list[Candidate]:
    """Add ``got`` to ``into``; returns the ones that were new."""
    new = []
    for c in got:
        old = next((o for o in into if same(o, c)), None)
        if old is None:
            into.append(c)
            new.append(c)
            continue
        old.providers += [p for p in c.providers if p not in old.providers]
        for f in ("tmdb_id", "imdb_id", "year", "runtime", "poster_url", "overview"):
            if getattr(old, f) is None and getattr(c, f) is not None:
                setattr(old, f, getattr(c, f))
    return new


async def search(s: Settings, queries: list[str], year: int | None) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """Candidates for the queries (unranked), and (level, note) on what each provider found."""
    names = [n for n in enabled(s, "movie") if n in ("tmdb", "omdb")]
    out: list[Candidate] = []
    notes: list[tuple[str, str]] = []
    tmdb = Tmdb(s.secret("tmdb_api_key"), s.tmdb_language) if "tmdb" in names else None
    try:
        for name in names:
            try:
                if name == "tmdb":
                    got = await tmdb.candidates(queries[:3], year)
                else:
                    omdb = Omdb(s.secret("omdb_api_key"))
                    try:
                        got = await omdb.candidates(queries[:2], year)
                    finally:
                        await omdb.aclose()
            except (TmdbError, OmdbError) as e:
                notes.append(("warning", str(e)))
                continue
            new = merge(out, got)
            if tmdb and name != "tmdb":
                await _to_tmdb(tmdb, out, new[:FIND_FOR_TOP])
            notes.append(("info", f"{label(name)}: {len(got)} candidates for “{queries[0]}”"))
    finally:
        if tmdb:
            await tmdb.aclose()
    return out, notes


async def _to_tmdb(tmdb: Tmdb, out: list[Candidate], new: list[Candidate]) -> None:
    """Swap candidates only another provider found for TMDB's record of the same film."""
    for c in new:
        if c.tmdb_id or not c.imdb_id:
            continue
        try:
            tid = await tmdb.find_id("movie", imdb_id=c.imdb_id)
            if not tid:
                continue
            t = to_candidate({**await tmdb.movie(tid), "id": tid})
        except TmdbError:
            continue
        t.providers = [*c.providers, "tmdb"]
        out.remove(c)
        merge(out, [t])


async def from_disc_match(s: Settings, match: DiscMatch) -> Candidate:
    """The film a disc database says this disc is, as TMDB's record of it when there is a key."""
    key = s.secret("tmdb_api_key")
    if key and (match.tmdb_id or match.imdb_id):
        tmdb = Tmdb(key, s.tmdb_language)
        try:
            tid = match.tmdb_id or await tmdb.find_id("movie", imdb_id=match.imdb_id)
            if tid:
                c = to_candidate({**await tmdb.movie(tid), "id": tid})
                c.providers = ["thediscdb", "tmdb"]
                return c
        except TmdbError:
            pass
        finally:
            await tmdb.aclose()
    return Candidate(source="thediscdb", tmdb_id=match.tmdb_id, imdb_id=match.imdb_id, title=match.title,
                     year=match.year, providers=["thediscdb"])


def vouch(ranked: list[Candidate], match: DiscMatch, summary: str) -> list[Candidate]:
    """Put the candidate the disc database names first, saying why."""
    m = Candidate(title=match.title, tmdb_id=match.tmdb_id, imdb_id=match.imdb_id)
    for c in ranked:
        if same(c, m):
            c.excluded = False
            c.score = max(c.score, DISC_MATCH_SCORE)
            if "thediscdb" not in c.providers:
                c.providers.insert(0, "thediscdb")
            c.evidence.insert(0, Evidence(ok=True, text=f"TheDiscDB knows this disc as {summary}"))
    return sorted(ranked, key=lambda c: (not same(c, m), c.excluded, -c.score))


def cross_check(ranked: list[Candidate]) -> None:
    """Say which providers agree on each candidate."""
    for c in ranked:
        if len(c.providers) > 1:
            c.evidence.append(Evidence(ok=True, text="Found by " + " and ".join(label(p) for p in c.providers)))
