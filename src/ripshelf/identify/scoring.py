"""Score identification candidates against what we know about the disc.

The score is a weighted mix of title similarity, runtime agreement and (when
the label carries one) release year, in 0..1. Every component adds a
human-readable evidence line so the review card can show *why*.

TMDB search for a film also returns its extras, such as "The Making of X", "Inside X",
featurettes, documentaries and same-named shorts. Those are *excluded*. They're capped
far below the auto-confirm line, ranked last, and labelled with the reason.
"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

from ..models import Candidate, DiscFacts, Evidence

W_TITLE = 0.6
W_RUNTIME = 0.4
W_YEAR = 0.15
# Without a runtime to compare, title agreement alone never auto-confirms.
TITLE_ONLY_CAP = 0.85
AMBIGUITY_GAP = 0.05
AMBIGUITY_FACTOR = 0.92
# Near-equal contenders are capped below any sensible auto-confirm line.
AMBIGUOUS_CAP = 0.85

PAL_SPEEDUP = 25 / 23.976
# Extended cuts up to this much longer than TMDB's runtime are scored as a partial match.
EXTENDED_MAX = 0.35
EXTENDED_SCORE = 0.6

DOCUMENTARY = 99
TV_MOVIE = 10770
FEATURE_MIN_S = 60 * 60
# A candidate this much shorter than a feature-length main title is an extra or a short.
SHORT_FRACTION = 0.5
EXCLUDED_CAP = 0.3
DOCUMENTARY_FACTOR = 0.75
TV_MOVIE_FACTOR = 0.92
# A same-title tie is settled by TMDB vote count when one side is this much better known.
POPULAR_FACTOR = 10
POPULAR_MIN_VOTES = 50

# Words that mark a title as an extra about another film, wherever they appear.
_EXTRA_MARKERS = re.compile(
    r"\b(making[ -]of|the making|behind[ -]the[ -]scenes|featurettes?|documentary|deleted scenes"
    r"|bloopers?|gag reel|special features|first look|sneak peek)\b",
    re.I,
)
# Prefixes that only mark an extra when the rest is the disc's title ("Halfway Out" is a film).
_EXTRA_PREFIX = re.compile(
    r"^\s*(inside|anatomy of|the story of|the art of|the science of|the magic of|creating"
    r"|bringing|a look at|making|discovering|exploring|unboxing)\b[\s:,.-]*(.+)$",
    re.I,
)


def normalise(s: str) -> str:
    # Drop accents but keep non-Latin scripts (a Japanese title must match itself).
    s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[\W_]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def title_similarity(a: str, b: str) -> float:
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # token_sort handles word order; ratio penalises extra words ("Moonlit Orchard 2").
    return max(fuzz.ratio(na, nb), fuzz.token_sort_ratio(na, nb) * 0.98) / 100


def runtime_score(disc_minutes: float, runtime: int, pal: bool) -> tuple[float, float, bool]:
    """Returns (score, difference in minutes, matched using PAL speed-up)."""
    diff = abs(runtime - disc_minutes)
    used_pal = False
    if pal:
        pal_diff = abs(runtime - disc_minutes * PAL_SPEEDUP)
        if pal_diff < diff:
            diff, used_pal = pal_diff, True
    if diff <= 2:
        score = 1.0
    elif diff >= 15:
        score = 0.0
    else:
        score = 1 - (diff - 2) / 13
    return score, diff, used_pal


def looks_like_extra(title: str, disc_names: list[str] | tuple[str, ...] = ()) -> str | None:
    """Why ``title`` reads like an extra about another film, or None.

    Marker words count unless the disc's own name has them too (a bonus disc
    called "The Making of X" is identified as itself). Prefixes like "Inside"
    count only when the rest of the title is the disc's name.
    """
    names = [n for n in disc_names if n]
    m = _EXTRA_MARKERS.search(title)
    if m and not any(_EXTRA_MARKERS.search(n) for n in names):
        word = re.sub(r"[ -]+", "-", m.group(1).lower())
        return "making-of" if "making" in word else word
    p = _EXTRA_PREFIX.match(title)
    if p and names:
        rest = p.group(2)
        for n in names:
            if title_similarity(n, title) < 0.95 and title_similarity(n, rest) >= 0.85:
                return f"“{p.group(1).strip()} …” feature about the film"
    return None


def fmt_duration(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def score_candidate(c: Candidate, disc: DiscFacts) -> Candidate:
    evidence: list[Evidence] = []
    names = [n for n in disc.names if n]

    best_sim, best_name = 0.0, ""
    for n in names:
        for t in filter(None, {c.title, c.original_title}):
            sim = title_similarity(n, t)
            if sim > best_sim:
                best_sim, best_name = sim, n
    if best_sim >= 0.95:
        evidence.append(Evidence(ok=True, text=f"Title matches disc name “{best_name}” ({best_sim:.0%})"))
    elif best_sim >= 0.6:
        evidence.append(Evidence(ok=None, text=f"Title is close to disc name “{best_name}” ({best_sim:.0%})"))
    else:
        evidence.append(Evidence(ok=False, text=f"Title differs from the disc name ({best_sim:.0%})"))

    parts = [(W_TITLE, best_sim)]
    rt = None
    if c.runtime and disc.main_duration_s:
        disc_min = disc.main_duration_s / 60
        rt, diff, used_pal = runtime_score(disc_min, c.runtime, disc.pal)
        disc_txt = fmt_duration(disc.main_duration_s)
        pal_txt = " allowing for PAL 25 fps speed-up" if used_pal else ""
        # An extended cut runs longer than TMDB's (theatrical) runtime. With the
        # title matching exactly, that is likely, and the label may even say so.
        longer = disc_min - c.runtime
        extended = rt < EXTENDED_SCORE and best_sim >= 0.95 and 0 < longer <= c.runtime * EXTENDED_MAX
        if extended:
            rt = 0.9 if disc.extended else EXTENDED_SCORE
        parts.append((W_RUNTIME, rt))
        if extended:
            label_txt = " (the label says extended)" if disc.extended else ""
            evidence.append(Evidence(ok=True if disc.extended else None,
                                     text=f"Main feature {disc_txt} is {longer:.0f} min longer than TMDB's "
                                          f"{c.runtime} min, so probably an extended cut{label_txt}"))
        elif rt >= 0.99:
            evidence.append(Evidence(ok=True, text=f"Runtime {c.runtime} min matches main feature {disc_txt}{pal_txt}"))
        elif rt > 0:
            evidence.append(Evidence(ok=None, text=f"Runtime {c.runtime} min is {diff:.0f} min off main feature {disc_txt}{pal_txt}"))
        else:
            evidence.append(Evidence(ok=False, text=f"Runtime {c.runtime} min does not match main feature {disc_txt}"))
    elif disc.main_duration_s:
        evidence.append(Evidence(ok=None, text="TMDB has no runtime for this title"))

    if disc.label_year:
        if c.year == disc.label_year:
            ys = 1.0
            evidence.append(Evidence(ok=True, text=f"Year {c.year} matches the disc label"))
        elif c.year and abs(c.year - disc.label_year) == 1:
            ys = 0.7
            evidence.append(Evidence(ok=None, text=f"Year {c.year} is one off the label's {disc.label_year}"))
        else:
            ys = 0.0
            evidence.append(Evidence(ok=False, text=f"Year {c.year or '?'} differs from the label's {disc.label_year}"))
        parts.append((W_YEAR, ys))

    total = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    if rt is None:
        total = min(total, TITLE_ONLY_CAP)

    reasons = _exclusion_reasons(c, disc, names)
    c.excluded = bool(reasons)
    if reasons:
        total = min(total, EXCLUDED_CAP)
        evidence.insert(0, Evidence(ok=False, text="Excluded (" + ", ".join(reasons) + ")"))
    c.score = round(total, 3)
    c.evidence = evidence
    return c


def _feature_length(disc: DiscFacts) -> bool:
    return bool(disc.main_duration_s and disc.main_duration_s >= FEATURE_MIN_S)


def _exclusion_reasons(c: Candidate, disc: DiscFacts, names: list[str]) -> list[str]:
    reasons = []
    extra = None
    for t in filter(None, (c.title, c.original_title)):
        extra = extra or looks_like_extra(t, names)
    if extra:
        reasons.append(extra)
    short = bool(_feature_length(disc) and c.runtime
                 and c.runtime < disc.main_duration_s / 60 * SHORT_FRACTION)
    if DOCUMENTARY in c.genre_ids and _feature_length(disc) and (extra or short):
        reasons.append("documentary")
    if short:
        reasons.append(f"{c.runtime} min against a {fmt_duration(disc.main_duration_s)} main feature")
    return reasons


def _similar_to_disc(c: Candidate, disc: DiscFacts) -> float:
    return max((title_similarity(n, t) for n in disc.names if n
                for t in filter(None, (c.title, c.original_title))), default=0.0)


def _penalise_kinds(scored: list[Candidate], disc: DiscFacts) -> None:
    """Documentaries and TV movies lose to a same-named theatrical film."""
    live = [c for c in scored if not c.excluded]
    for c in live:
        for genre, factor, what in ((DOCUMENTARY, DOCUMENTARY_FACTOR, "a documentary"),
                                    (TV_MOVIE, TV_MOVIE_FACTOR, "a TV movie")):
            if genre not in c.genre_ids or (genre == DOCUMENTARY and not _feature_length(disc)):
                continue
            rival = next((o for o in live if o is not c and genre not in o.genre_ids
                          and DOCUMENTARY not in o.genre_ids
                          and _similar_to_disc(o, disc) >= 0.8), None)
            if rival:
                c.score = round(c.score * factor, 3)
                year = f" ({rival.year})" if rival.year else ""
                c.evidence.append(Evidence(ok=False, text=f"TMDB lists this as {what}; “{rival.title}”{year} is not"))


def _order(c: Candidate) -> tuple:
    return (c.excluded, -c.score, -(c.vote_count or 0), -(c.popularity or 0))


def rank(candidates: list[Candidate], disc: DiscFacts) -> list[Candidate]:
    scored = [score_candidate(c, disc) for c in candidates]
    _penalise_kinds(scored, disc)
    scored.sort(key=_order)
    live = [c for c in scored if not c.excluded]
    if live and _similar_to_disc(live[0], disc) < 0.95:
        # The runtime favours a differently named film ("Tin Parade 2" for a disc
        # labelled "Tin Parade"), so a human should decide.
        exact = next((o for o in live[1:] if _similar_to_disc(o, disc) >= 0.95), None)
        if exact:
            live[0].score = round(min(live[0].score, 0.89) * AMBIGUITY_FACTOR, 3)
            live[0].evidence.append(Evidence(
                ok=False, text=f"“{exact.title}”{_yr(exact)} matches the disc name exactly"))
            scored.sort(key=_order)
            live = [c for c in scored if not c.excluded]
    if len(live) >= 2 and live[0].score - live[1].score < AMBIGUITY_GAP:
        close = [c for c in live if live[0].score - c.score < AMBIGUITY_GAP]
        leader, rest = close[0], close[1:]
        best_rival_votes = max((o.vote_count or 0) for o in rest)
        if (leader.vote_count or 0) >= POPULAR_MIN_VOTES \
                and (leader.vote_count or 0) >= POPULAR_FACTOR * max(best_rival_votes, 1):
            # Same title, but one is the film everyone means (the original vs a
            # same-named short or obscure namesake).
            other = max(rest, key=lambda o: o.vote_count or 0)
            leader.evidence.append(Evidence(
                ok=True, text=f"Far better known on TMDB than “{other.title}”{_yr(other)} "
                              f"({leader.vote_count} vs {other.vote_count or 0} votes)"))
            for o in rest:
                o.score = round(min(o.score * AMBIGUITY_FACTOR, AMBIGUOUS_CAP), 3)
                o.evidence.append(Evidence(ok=False, text=f"“{leader.title}”{_yr(leader)} is far better known"))
        else:
            # With two near-equal candidates (a remake, a same-named film), neither may
            # auto-confirm. Penalise every close contender equally to keep the order.
            for c in close:
                other = next(o for o in close if o is not c)
                c.score = round(min(c.score * AMBIGUITY_FACTOR, AMBIGUOUS_CAP), 3)
                c.evidence.append(Evidence(ok=False, text=f"“{other.title}”{_yr(other)} scores almost as high"))
        scored.sort(key=_order)
    return scored


def _yr(c: Candidate) -> str:
    return f" ({c.year})" if c.year else ""
