"""Map a disc's episode titles onto a season's episodes.

Box-set discs hold a run of consecutive episodes. Titles are put in disc order
(the play-all's clip order on Blu-ray, else title number), then aligned
against the season starting at the next expected episode, scoring how well
each title's length agrees with TMDB's runtime. A title about twice an
episode's length can take two episodes (a two-part story on one title).
Compilation discs instead pick episodes from anywhere in the show, by name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..identify.scoring import PAL_SPEEDUP, title_similarity
from ..models import Episode, EpisodeRef, TitleInfo, TitleMap, TitleRole

UNKNOWN_RUNTIME_COST = 0.15
TWO_PART_PENALTY = 0.1
EXTRA_COST = 0.6
NAME_MATCH_MIN = 0.75


@dataclass
class Assignment:
    title_index: int
    episodes: list[Episode]
    cost: float


def runtime_cost(duration_s: int, runtime_min: float | None, pal: bool) -> float:
    """0 = perfect, 1 = hopeless, relative to the episode runtime."""
    if not runtime_min:
        return UNKNOWN_RUNTIME_COST
    minutes = duration_s / 60
    diff = abs(minutes - runtime_min)
    if pal:
        diff = min(diff, abs(minutes * PAL_SPEEDUP - runtime_min))
    # TMDB runtimes are whole minutes and often rounded, so 2 minutes or 6% is "the same".
    slack = max(2.0, runtime_min * 0.06)
    return min(1.0, max(0.0, diff - slack) / runtime_min * 2)


def align(titles: list[TitleInfo], episodes: list[Episode], pal: bool = False) -> list[Assignment | None]:
    """Best monotone alignment of ordered titles to ordered episodes (None = not an episode).

    Dynamic programming over (titles used, episodes used). Each title takes
    one episode, two consecutive episodes, or none (an extra).
    """
    n, m = len(titles), len(episodes)
    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, int] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n):
        for j in range(m + 1):
            cur = best[i][j]
            if cur == inf:
                continue
            d = titles[i].duration_s
            options = [(j, EXTRA_COST)]  # not an episode
            if j < m:
                options.append((j + 1, runtime_cost(d, episodes[j].runtime, pal)))
            if j + 1 < m:
                r = (episodes[j].runtime or 0) + (episodes[j + 1].runtime or 0)
                both = runtime_cost(d, r if episodes[j].runtime and episodes[j + 1].runtime else None, pal)
                options.append((j + 2, both + TWO_PART_PENALTY))
            for nj, c in options:
                if cur + c < best[i + 1][nj]:
                    best[i + 1][nj] = cur + c
                    back[i + 1][nj] = (i, j, nj - j)
    # Prefer using episodes (a run), then the lowest cost.
    j = min(range(m + 1), key=lambda k: (best[n][k], -k))
    out: list[Assignment | None] = [None] * n
    i = n
    while i > 0:
        pi, pj, took = back[i][j]
        if took:
            eps = episodes[pj:pj + took]
            r = sum(e.runtime or 0 for e in eps) if all(e.runtime for e in eps) else None
            out[pi] = Assignment(titles[pi].index, list(eps), runtime_cost(titles[pi].duration_s, r, pal))
        i, j = pi, pj
    return out


def describe(title: TitleInfo, a: Assignment | None, pal: bool) -> str:
    if a is None:
        return "no episode fits this title's length"
    minutes = round(title.duration_s / 60)
    names = " + ".join(f"“{e.name}”" for e in a.episodes) or "?"
    runtimes = [e.runtime for e in a.episodes]
    if not all(runtimes):
        return f"{minutes} min; TMDB has no runtime for {names}"
    r = sum(runtimes)
    verdict = "matches" if a.cost == 0 else ("is close to" if a.cost < 0.5 else "does not match")
    pal_txt = " (PAL speed-up)" if pal and abs(title.duration_s / 60 * PAL_SPEEDUP - r) < abs(title.duration_s / 60 - r) else ""
    return f"{minutes} min {verdict} {names} ({r} min){pal_txt}"


def propose(ordered: list[TitleInfo], others: dict[int, TitleMap], season_episodes: list[Episode],
            start_episode: int | None, pal: bool) -> list[TitleMap]:
    """Mapping for a box-set disc, with ``ordered`` episode titles from ``start_episode`` on."""
    eps = sorted(season_episodes, key=lambda e: e.episode)
    if start_episode:
        eps = [e for e in eps if e.episode >= start_episode]
    found = align(ordered, eps, pal)
    out = []
    for t, a in zip(ordered, found, strict=True):
        if a is None:
            out.append(TitleMap(title_index=t.index, duration_s=t.duration_s, role=TitleRole.extra,
                                note=describe(t, None, pal)))
        else:
            out.append(TitleMap(title_index=t.index, duration_s=t.duration_s, role=TitleRole.episode,
                                episodes=[EpisodeRef(season=e.season, episode=e.episode) for e in a.episodes],
                                note=describe(t, a, pal), match_cost=round(a.cost, 3)))
    return sorted([*out, *others.values()], key=lambda m: m.title_index)


# --- compilations, matched by episode name ----------------------------------------------


def split_names(text: str) -> list[str]:
    """Episode names typed or pasted from a case, one per line or separated by commas/semicolons."""
    lines = [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    if len(lines) == 1:
        lines = re.split(r"[;,|]", lines[0])
    out = []
    for ln in lines:
        ln = re.sub(r"^\s*(?:\d+\s*[.):\-]\s*|[-*•]\s*)", "", ln)  # "1. ", "2) ", "- "
        ln = re.sub(r"\s+", " ", ln).strip(" \"'“”")
        if ln:
            out.append(ln)
    return out


def match_names(names: list[str], episodes: list[Episode]) -> list[tuple[str, Episode | None, float]]:
    """Each typed name with the episode whose TMDB name matches best (None if nothing is close)."""
    out = []
    taken: set[tuple[int, int]] = set()
    for name in names:
        scored = sorted(((title_similarity(name, e.name), e) for e in episodes
                         if (e.season, e.episode) not in taken),
                        key=lambda x: (-x[0], x[1].season == 0, x[1].season, x[1].episode))
        if scored and scored[0][0] >= NAME_MATCH_MIN:
            sim, ep = scored[0]
            taken.add((ep.season, ep.episode))
            out.append((name, ep, sim))
        else:
            out.append((name, None, scored[0][0] if scored else 0.0))
    return out


# --- checking a mapping ----------------------------------------------------------------


def problems(mapping: list[TitleMap], known: dict[tuple[int, int], Episode] | None) -> list[str]:
    """Why a mapping cannot be confirmed. Empty when it is consistent."""
    out = []
    seen: dict[tuple[int, int], int] = {}
    for m in mapping:
        if m.role == TitleRole.episode and not m.episodes:
            out.append(f"title {m.title_index} is marked as an episode but has none")
        if m.role != TitleRole.episode and m.episodes:
            out.append(f"title {m.title_index} has episodes but is marked {m.role.value}")
        for e in m.episodes:
            key = (e.season, e.episode)
            if key in seen:
                out.append(f"S{e.season:02d}E{e.episode:02d} is on both title {seen[key]} and title {m.title_index}")
            else:
                seen[key] = m.title_index
            if known is not None and key not in known:
                out.append(f"title {m.title_index}: S{e.season:02d}E{e.episode:02d} is not an episode of this show")
        if len(m.episodes) == 2:
            a, b = m.episodes
            if a.season != b.season or b.episode != a.episode + 1:
                out.append(f"title {m.title_index}: a two-part title needs two consecutive episodes")
        if len(m.episodes) > 2:
            out.append(f"title {m.title_index}: at most two episodes per title")
    if not any(m.role == TitleRole.episode for m in mapping):
        out.append("no title is mapped to an episode")
    return out
