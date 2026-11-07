"""Matching the box's listing to the disc's titles.

Each listing line (an episode or an extra) is paired with at most one title by an
optimal assignment over a score made of:

- **Name.** An episode's name found in the season's episode list (TMDB, TheTVDB,
  TVmaze) gives its SxxEyy whichever title it lands on, and that episode's runtime.
- **Runtime.** Printed runtimes are rounded and approximate, so within about 10% or
  2 minutes counts as agreeing (wider for "approx"). An extra with no printed runtime
  is scored on what its type usually runs to (a trailer two minutes, a featurette
  twenty).
- **Order.** Boxes usually list things in the disc's order: a weak prior, as some
  discs scramble their playlists.
- **Tracks.** The main feature carries the audio languages and subtitles the box
  prints; extras usually have fewer tracks.

The film's main feature is picked first, from the long titles, by the printed running
time and its tracks. Each match gets a confidence (high, medium, low) and a reason,
and is compared with what ripshelf already decided (the TV mapping, the main title):
agreement raises confidence, a disagreement is shown for the user to settle. Nothing
here changes the job on its own.

Discs of one box set share its listing: lines another disc of the release took are
left out, so each disc claims the lines that fit it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..identify.scoring import PAL_SPEEDUP, title_similarity
from ..models import (
    Confidence,
    Episode,
    EpisodeRef,
    Listing,
    ListingItem,
    ScanMatch,
    TitleInfo,
    TitleMap,
    TitleMatch,
    TitleRole,
)
from .listing import LANGUAGES

NAME_MIN = 0.82  # an episode name this similar is that episode
NO_MATCH = 0.42  # below this score a line stays unmatched
EPISODE_MIN_S = 4 * 60
FEATURE_MIN_S = 40 * 60
# What an extra of each type usually runs to (minutes): (low, typical, high).
TYPE_RUNTIME = {"Trailer": (0.5, 2.2, 5), "Featurette": (4, 18, 75), "DeletedScene": (0.5, 6, 40),
                "Interview": (2, 12, 60), "Scene": (0.3, 3, 15), "Music": (1.5, 4, 10), "Short": (2, 10, 40),
                "Other": (0.5, 10, 120), "Extra": (0.5, 12, 120)}
_LANG = re.compile(r"\b(" + "|".join(LANGUAGES) + r")\b", re.I)


@dataclass
class Tracks:
    languages: set[str]
    count: int
    commentary: bool


def tracks(t: TitleInfo) -> Tracks:
    """The audio languages in MakeMKV's track descriptions ("DD Surround 5.1 English")."""
    langs = {m.group(1).capitalize() for a in t.audio if (m := _LANG.search(a))}
    return Tracks(langs, len(t.audio), any(re.search(r"comment", a, re.I) for a in t.audio))


def minutes(s: float) -> str:
    s = round(s)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def runtime_fit(title_s: float, printed_min: float, approx: bool = True, pal: bool = False) -> float:
    """1 when a title's length agrees with a printed runtime, falling to 0 well outside
    the tolerance (10% or 2 minutes, half as much again when approximate). PAL discs
    run 4% fast, and boxes often print the cinema runtime."""
    best = 0.0
    for m in (title_s / 60, title_s / 60 * PAL_SPEEDUP) if pal else (title_s / 60,):
        tol = max(2.0, 0.1 * printed_min) * (1.5 if approx else 1)
        diff = abs(m - printed_min)
        best = max(best, 1.0 if diff <= tol else max(0.0, 1 - (diff - tol) / (2 * tol)))
    return best


def type_fit(title_s: float, extra_type: str | None) -> float:
    lo, typical, hi = TYPE_RUNTIME.get(extra_type or "Extra", TYPE_RUNTIME["Extra"])
    m = title_s / 60
    if lo <= m <= hi:
        return 0.9 - 0.25 * min(1.0, abs(m - typical) / max(typical, hi - typical))
    return 0.05


def order_fit(rank: int, count: int, title_rank: int, title_count: int) -> float:
    """1 when the line and the title sit at the same place in their lists."""
    if count <= 1 or title_count <= 1:
        return 0.5
    return max(0.0, 1 - abs(rank / (count - 1) - title_rank / (title_count - 1)) * 1.5)


def hungarian(cost: list[list[float]]) -> list[int]:
    """The minimum-cost assignment of rows to columns (rows ≤ columns): for each row,
    its column. The classic O(n²m) shortest-augmenting-path version."""
    n, m = len(cost), len(cost[0]) if cost else 0
    if not n:
        return []
    inf = float("inf")
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [inf] * (m + 1)
        used = [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if not j0:
                break
    out = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            out[p[j] - 1] = j - 1
    return out


def confidence(score: float) -> Confidence:
    return "high" if score >= 0.78 else "medium" if score >= 0.6 else "low"


def _code(e: EpisodeRef) -> str:
    return f"S{e.season:02d}E{e.episode:02d}"


def _norm_part(name: str) -> str:
    """"Part One" and "Pt. 1" compare as "part 1"."""
    words = {"one": "1", "two": "2", "three": "3", "i": "1", "ii": "2", "iii": "3"}
    return re.sub(r"\b(?:part|pt\.?)\s*(\w+)", lambda m: f"part {words.get(m.group(1).lower(), m.group(1))}",
                  name, flags=re.I)


def episode_for(name: str, episodes: list[Episode], season: int | None) -> tuple[Episode | None, float]:
    """The episode a printed name is, from the show's episode list, and how sure."""
    best, score = None, 0.0
    target = _norm_part(name)
    for e in episodes:
        if not e.name:
            continue
        s = title_similarity(target, _norm_part(e.name))
        if season is not None and e.season == season:
            s += 0.01  # the season on the disc wins a tie
        if s > score:
            best, score = e, s
    return (best, min(score, 1.0)) if score >= NAME_MIN else (None, score)


def _bluray(t: TitleInfo) -> bool:
    """A Blu-ray playlist, whose segment map names the disc's clips (a DVD's numbers
    each title's own cells from 1, so two titles' maps say nothing about each other)."""
    return bool(t.source_file and t.source_file.lower().endswith(".mpls"))


def _candidates(titles: list[TitleInfo], mapping: list[TitleMap]) -> list[TitleInfo]:
    """Titles worth matching: not play-alls or duplicates (by the TV mapping, the same
    clips as an earlier Blu-ray playlist, or one title as long as several others
    together), and not tiny."""
    skip = {m.title_index for m in mapping if m.role in (TitleRole.play_all, TitleRole.duplicate)}
    out, seen = [], set()
    for t in sorted(titles, key=lambda t: t.index):
        key = (t.duration_s, t.segments_map)
        if t.index in skip or t.duration_s < 20 or (_bluray(t) and t.segments_map and key in seen):
            continue
        seen.add(key)
        out.append(t)
    for t in list(out):
        others = [o for o in out if o is not t and o.duration_s < t.duration_s]
        if len(others) >= 3 and abs(sum(o.duration_s for o in others) - t.duration_s) < 0.03 * t.duration_s:
            out.remove(t)  # all the others back to back
    return out


def _feature(cands: list[TitleInfo], listing: Listing, pal: bool) -> tuple[TitleInfo | None, float, str]:
    """The main feature among the long titles: the printed running time first, then
    the printed audio languages and a commentary track, then length."""
    longs = [t for t in cands if t.duration_s >= FEATURE_MIN_S] or sorted(cands, key=lambda t: -t.duration_s)[:1]
    if not longs:
        return None, 0.0, ""
    d = listing.details
    want = {a.language for a in d.audio}
    best, best_score, why = None, -1.0, ""
    longest = max(t.duration_s for t in longs)
    for t in longs:
        parts, reasons = [], []
        if d.feature_runtime_min:
            fit = runtime_fit(t.duration_s, d.feature_runtime_min, pal=pal)
            parts.append((0.55, fit))
            reasons.append(f"runs {minutes(t.duration_s)} against {d.feature_runtime_min:g} min printed")
        tr = tracks(t)
        if want:
            overlap = len(want & tr.languages) / len(want)
            parts.append((0.3, overlap))
            reasons.append(f"has {len(want & tr.languages)} of the {len(want)} audio languages printed")
        if tr.commentary:
            parts.append((0.1, 1.0))
            reasons.append("has a commentary track")
        parts.append((0.15, t.duration_s / longest))
        score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
        if len(longs) == 1 and not d.feature_runtime_min:
            score = max(score, 0.7)
            reasons.append("the only long title")
        if score > best_score:
            best, best_score, why = t, score, "; ".join(reasons) or f"the longest title ({minutes(t.duration_s)})"
    return best, best_score, why


def match(titles: list[TitleInfo], listing: Listing, *, media: str = "movie", episodes: list[Episode] = (),
          season: int | None = None, mapping: list[TitleMap] = (), main_title: int | None = None,
          disc: int | None = None, claimed: set[int] = frozenset(), pal: bool = False) -> ScanMatch:
    """The listing's lines matched to the titles. ``claimed`` are listing items other
    discs of the release already took."""
    out = ScanMatch(claimed=sorted(claimed))
    cands = _candidates(titles, list(mapping))
    current = {m.title_index: m for m in mapping}

    feature = None
    if media == "movie" and cands:
        feature, fscore, why = _feature(cands, listing, pal)
        if feature:
            agrees = None if main_title is None else feature.index == main_title
            if agrees:
                fscore = min(1.0, fscore + 0.1)
            out.matches.append(TitleMatch(
                title_index=feature.index, kind="feature", name=listing.details.title or "The film",
                score=round(fscore, 3), confidence=confidence(fscore), reason=why, agrees=agrees,
                current=None if agrees in (None, True) else f"title {main_title} is ripped as the film"))
            cands = [t for t in cands if t is not feature]

    items = [(i, it) for i, it in enumerate(listing.items)
             if i not in claimed and not (disc and it.disc and it.disc != disc)]
    if items and cands:
        _assign(out, items, cands, episodes, season, pal, current)
    taken = {m.item for m in out.matches if m.item is not None}
    out.unmatched_items = [i for i, _ in items if i not in taken]
    matched = {m.title_index for m in out.matches}
    out.unmatched_titles = [t.index for t in cands if t.index not in matched]
    _whole_disc(out, listing, cands, titles)
    return out


def _assign(out: ScanMatch, items: list[tuple[int, ListingItem]], cands: list[TitleInfo],
            episodes: list[Episode], season: int | None, pal: bool, current: dict[int, TitleMap]) -> None:
    eps_items = [x for x in items if x[1].kind == "episode"]
    ext_items = [x for x in items if x[1].kind == "extra"]
    ep_titles = [t for t in cands if t.duration_s >= EPISODE_MIN_S] or cands
    named = {i: episode_for(it.title, list(episodes), season) for i, it in eps_items} if episodes else {}
    # Episode-length titles: about as long as the episodes printed or known.
    typical = [it.runtime_min for _, it in eps_items if it.runtime_min]
    typical += [e.runtime for e, _ in named.values() if e and e.runtime]

    scores: list[list[float]] = []
    reasons: list[list[str]] = []
    for i, it in items:
        row, why_row = [], []
        same = eps_items if it.kind == "episode" else ext_items
        rank = next(n for n, (j, _) in enumerate(same) if j == i)
        pool = ep_titles if it.kind == "episode" else cands
        for t in cands:
            parts: list[tuple[float, float]] = []
            why: list[str] = []
            if it.runtime_min:
                fit = runtime_fit(t.duration_s, it.runtime_min, pal=pal)
                parts.append((0.5, fit))
                why.append(f"runs {minutes(t.duration_s)} against {it.runtime_min:g} min printed")
            elif it.kind == "episode":
                ep = named.get(i, (None, 0))[0]
                ref = ep.runtime if ep and ep.runtime else (sum(typical) / len(typical) if typical else None)
                if ref:
                    parts.append((0.3, runtime_fit(t.duration_s, ref, pal=pal)))
                    why.append(f"runs {minutes(t.duration_s)}, an episode is about {ref:g} min")
                elif t not in ep_titles:
                    parts.append((0.3, 0.2))
            else:
                parts.append((0.35, type_fit(t.duration_s, it.extra_type)))
                why.append(f"runs {minutes(t.duration_s)}, {_type_words(it.extra_type)}")
            if t in pool:
                o = order_fit(rank, len(same), pool.index(t), len(pool))
                parts.append((0.15, o))
                if o >= 0.9:
                    why.append(f"listed {_nth(rank + 1)}, title {t.index} is {_nth(pool.index(t) + 1)}")
            else:
                parts.append((0.15, 0.0))
            if it.kind == "episode" and named.get(i, (None, 0))[0]:
                parts.append((0.15, 1.0))  # the name is known: sure it's an episode
            row.append(sum(w * v for w, v in parts) / sum(w for w, _ in parts))
            why_row.append("; ".join(why))
        scores.append(row)
        reasons.append(why_row)

    # Square it up with "no title" columns, so a poor fit stays unmatched.
    n, m = len(items), len(cands)
    cost = [[1 - s for s in row] + [1 - NO_MATCH] * n for row in scores]
    pick = hungarian(cost)
    for r, c in enumerate(pick):
        if c < 0 or c >= m:
            continue
        i, it = items[r]
        t = cands[c]
        score = scores[r][c]
        reason = reasons[r][c]
        others = sorted((x for j, x in enumerate(scores[r]) if j != c), reverse=True)
        if not others or score - others[0] >= 0.2:
            score = min(1.0, score + 0.12)  # no other title comes close
        eps: list[EpisodeRef] = []
        ep, name_score = named.get(i, (None, 0.0))
        if ep:
            eps = [EpisodeRef(season=ep.season, episode=ep.episode)]
            score = min(1.0, 0.55 + 0.45 * name_score) if score >= 0.35 else score
            reason = f"“{it.title}” is {_code(ep)} “{ep.name}”" + (f"; {reason}" if reason else "")
        agrees, cur = _compare(current.get(t.index), it, eps)
        if agrees:
            score = min(1.0, score + 0.1)
        elif agrees is False:
            score = min(score, 0.7)
        out.matches.append(TitleMatch(
            title_index=t.index, kind=it.kind, item=i, name=it.title, episodes=eps, extra_type=it.extra_type,
            score=round(score, 3), confidence=confidence(score), reason=reason or "the best fit left",
            agrees=agrees, current=cur))
    out.matches.sort(key=lambda m: m.title_index)


def _compare(tm: TitleMap | None, it: ListingItem, eps: list[EpisodeRef]) -> tuple[bool | None, str | None]:
    """Whether the existing TV mapping says the same about the title."""
    if tm is None:
        return None, None
    if it.kind == "episode":
        if tm.role == TitleRole.episode and tm.episodes:
            if not eps:
                return None, None
            same = {(e.season, e.episode) for e in tm.episodes} == {(e.season, e.episode) for e in eps}
            return same, None if same else "mapped as " + ", ".join(_code(e) for e in tm.episodes)
        if tm.role in (TitleRole.extra, TitleRole.skip):
            return False, f"mapped as {tm.role.value}"
        return None, None
    if tm.role == TitleRole.extra:
        return True, None
    if tm.role == TitleRole.episode:
        return False, "mapped as " + (", ".join(_code(e) for e in tm.episodes) or "an episode")
    return None, None


def _whole_disc(out: ScanMatch, listing: Listing, cands: list[TitleInfo], titles: list[TitleInfo]) -> None:
    eps = [m for m in out.matches if m.kind == "episode"]
    d = listing.details
    listed_eps = [it for it in listing.items if it.kind == "episode"]
    ep_titles = [t for t in cands if t.duration_s >= EPISODE_MIN_S]
    if listed_eps and len(ep_titles) == len([i for i in out.unmatched_items if listing.items[i].kind == "episode"]) \
            + len(eps) and len(eps) == len(listed_eps):
        out.notes.append(f"{len(eps)} episodes listed and {len(eps)} episode-length titles")
    if d.episode_count and eps and d.episode_count != len(ep_titles):
        out.notes.append(f"The box says {d.episode_count} episodes; the disc has {len(ep_titles)} episode-length "
                         f"titles")
    if listed_eps and d.feature_runtime_min and eps:
        total = sum(t.duration_s for t in titles if t.index in {m.title_index for m in eps}) / 60
        if abs(total - d.feature_runtime_min) <= max(3, 0.1 * d.feature_runtime_min):
            out.notes.append(f"The episodes add up to {total:.0f} min, as the box's {d.feature_runtime_min:g} min")
    if out.matches:
        worst = min(m.score for m in out.matches)
        avg = sum(m.score for m in out.matches) / len(out.matches)
        disagree = sum(m.agrees is False for m in out.matches)
        overall = confidence(min(avg, worst + 0.15))
        if disagree or (out.unmatched_items and overall == "high"):
            overall = "medium" if overall == "high" else overall
        out.confidence = overall
        if disagree:
            out.notes.append(f"{disagree} match{'es' if disagree != 1 else ''} disagree with what ripshelf "
                             f"decided before; check them")


def _type_words(extra_type: str | None) -> str:
    lo, typical, hi = TYPE_RUNTIME.get(extra_type or "Extra", TYPE_RUNTIME["Extra"])
    what = {"Trailer": "a trailer", "Featurette": "a featurette", "DeletedScene": "deleted scenes",
            "Interview": "an interview", "Scene": "a scene", "Music": "a music video", "Short": "a short",
            "Other": "an extra", "Extra": "an extra"}[extra_type or "Extra"]
    return f"{what} usually runs {lo:g}–{hi:g} min"


def _nth(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
