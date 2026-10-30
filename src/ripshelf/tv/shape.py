"""What kind of disc this is, from its titles alone.

A TV disc has several titles of about the same length (episodes), often a
play-all title that is all of them back to back, and sometimes duplicates
(the same episode under another playlist, angle or audio language). A film
disc has one long title and short extras.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..models import TitleInfo, TitleRole

MIN_EPISODE_S = 3 * 60
# Titles within this fraction of the typical episode length count as episodes.
CLUSTER_TOLERANCE = 0.35
# A play-all is within this of the sum of the episodes it contains.
PLAY_ALL_TOLERANCE = 0.02
PLAY_ALL_SLACK_S = 15
FEATURE_MIN_S = 60 * 60


@dataclass
class DiscShape:
    roles: dict[int, TitleRole]
    notes: dict[int, str] = field(default_factory=dict)
    same_as: dict[int, int] = field(default_factory=dict)
    episode_s: float | None = None  # typical episode length on this disc
    evidence: list[str] = field(default_factory=list)

    @property
    def episodes(self) -> list[int]:
        return [i for i, r in self.roles.items() if r == TitleRole.episode]

    @property
    def play_all(self) -> list[int]:
        return [i for i, r in self.roles.items() if r == TitleRole.play_all]

    feature: int | None = None  # a feature-length title that is not a play-all, so a film

    @property
    def looks_like_tv(self) -> bool:
        if self.feature is not None:
            return False
        return len(self.episodes) >= 3 or (len(self.episodes) >= 2 and bool(self.play_all))


def clip_set(t: TitleInfo) -> list[str]:
    return [c.strip() for c in (t.segments_map or "").split(",") if c.strip()]


def analyse(titles: list[TitleInfo], bluray: bool, episode_window: tuple[float, float] | None = None,
            ) -> DiscShape:
    """Classify every title. ``episode_window`` (seconds) comes from TMDB runtimes when known."""
    roles: dict[int, TitleRole] = {}
    notes: dict[int, str] = {}
    same_as: dict[int, int] = {}
    evidence: list[str] = []
    ts = sorted(titles, key=lambda t: t.index)

    # 1. Duplicates have the same clips (Blu-ray), or same length and size (either).
    for i, t in enumerate(ts):
        for o in ts[:i]:
            if o.index in same_as:
                continue
            same_clips = bluray and t.segments_map and t.segments_map == o.segments_map
            # Exactly the same size, because different episodes of one show can be within 0.1%.
            same_size = abs(t.duration_s - o.duration_s) <= 1 and o.size_bytes and t.size_bytes == o.size_bytes
            if same_clips or same_size:
                roles[t.index] = TitleRole.duplicate
                same_as[t.index] = o.index
                notes[t.index] = f"same content as title {o.index}"
                break
    rest = [t for t in ts if t.index not in roles]

    # 2. Typical episode length.
    if episode_window:
        lo, hi = episode_window
    else:
        candidates = [t.duration_s for t in rest if t.duration_s >= MIN_EPISODE_S]
        if not candidates:
            return DiscShape(roles={**roles, **{t.index: TitleRole.extra for t in rest}}, notes=notes,
                             same_as=same_as)
        typical = _typical(candidates)
        lo, hi = typical * (1 - CLUSTER_TOLERANCE), typical * (1 + CLUSTER_TOLERANCE)

    # 3. A play-all is longer than an episode and the sum of the episode-length titles it covers.
    in_window = [t for t in rest if lo <= t.duration_s <= hi]
    for t in sorted(rest, key=lambda t: -t.duration_s):
        if t.duration_s <= hi or len(in_window) < 2:
            continue
        covered = _covered(t, in_window, bluray)
        if covered:
            roles[t.index] = TitleRole.play_all
            notes[t.index] = f"plays titles {', '.join(map(str, covered))} back to back"
            evidence.append(f"title {t.index} is a play-all of {len(covered)} episodes")

    # 4. Episodes, two-part (double-length) episodes, extras.
    typical_ep = statistics.median([t.duration_s for t in in_window]) if in_window else (lo + hi) / 2
    for t in rest:
        if t.index in roles:
            continue
        if lo <= t.duration_s <= hi:
            roles[t.index] = TitleRole.episode
        elif 2 * lo * 0.95 <= t.duration_s <= 2 * hi and len(in_window) >= 1:
            roles[t.index] = TitleRole.episode
            notes[t.index] = "double length, so probably a two-part episode"
        else:
            roles[t.index] = TitleRole.extra
            notes[t.index] = "not episode length"
    eps = [i for i, r in roles.items() if r == TitleRole.episode]
    if len(eps) >= 2:
        evidence.insert(0, f"{len(eps)} titles of episode length (about {round(typical_ep / 60)} min)")
    feature = next((t.index for t in sorted(rest, key=lambda t: -t.duration_s)
                    if t.duration_s >= FEATURE_MIN_S and roles.get(t.index) == TitleRole.extra), None)
    if feature is not None:
        evidence.append(f"title {feature} is feature length and not a play-all, so it looks like a film")
    return DiscShape(roles=dict(sorted(roles.items())), notes=notes, same_as=same_as,
                     episode_s=typical_ep if eps else None, evidence=evidence, feature=feature)


def _typical(durations: list[int]) -> float:
    """The length shared by most titles, from the densest cluster, not just the median."""
    best, best_n = durations[0], 0
    for d in durations:
        n = sum(1 for o in durations if abs(o - d) <= d * CLUSTER_TOLERANCE / 2)
        if n > best_n or (n == best_n and d < best):
            best, best_n = d, n
    members = [o for o in durations if abs(o - best) <= best * CLUSTER_TOLERANCE / 2]
    return statistics.median(members)


def _covered(t: TitleInfo, episodes: list[TitleInfo], bluray: bool) -> list[int]:
    """Episode titles a long title plays back to back, or [] if it is not a play-all."""
    if bluray and t.segments_map:
        mine = clip_set(t)
        inside = [e for e in episodes if clip_set(e) and all(c in mine for c in clip_set(e))]
        if len(inside) >= 2:
            # Ordered by where their clips sit in the play-all.
            inside.sort(key=lambda e: mine.index(clip_set(e)[0]))
            return [e.index for e in inside]
    total = sum(e.duration_s for e in episodes)
    if abs(t.duration_s - total) <= max(total * PLAY_ALL_TOLERANCE, PLAY_ALL_SLACK_S):
        return [e.index for e in sorted(episodes, key=lambda e: e.index)]
    return []


def play_all_order(play_all: TitleInfo | None, episodes: list[TitleInfo], bluray: bool) -> list[int] | None:
    """Episode order taken from the play-all's clip list (Blu-ray only)."""
    if not (play_all and bluray and play_all.segments_map):
        return None
    covered = _covered(play_all, episodes, bluray=True)
    if covered and len(covered) == len(episodes):
        return covered
    return None
