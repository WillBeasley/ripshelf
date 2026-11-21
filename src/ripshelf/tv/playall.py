"""Which titles a play-all plays, and in what order, from the ripped files.

A box that doesn't list its episodes still has the disc's own play-all, which
is the episodes in the order they were broadcast. Two ways to read it:

- Chapters. Each episode's chapters run the same lengths inside the play-all,
  so an episode is where its run of chapter lengths sits in the play-all's.
  A DVD play-all drops each episode's short closing cell (and some a short
  opening one, which shortens the first chapter), so a running sum of episode
  lengths drifts (half a second an episode) and frames taken from it miss; the
  chapter marks inside an episode don't drift. When those inner chapters run
  the same lengths to within a frame, that alone proves the order.
- Frames, for files with no chapters: walk the play-all from the start and
  find, at each point, the title whose frames are there.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import verify

# Chapters shorter than this are a DVD's closing cell or a gap, so not compared.
MIN_CHAPTER_S = 1.0
# How far one chapter's length may be from the same chapter in the play-all: this
# share of it, at least the floor (a dropped DVD cell is 0.4 s) and at most the cap.
CHAPTER_SLACK = 0.02
CHAPTER_SLACK_MIN_S = 0.5
CHAPTER_SLACK_MAX_S = 1.5
# A title needs this many chapters to be found by chapters (one length alone is a weak match).
MIN_CHAPTERS = 2
# The episodes found must cover this much of the play-all for it to count as read.
MIN_COVERED = 0.9
# Chapters after a title's first that run within this of the play-all's (a frame or
# so) are the same chapters, not ones of about the same length.
CHAPTER_EXACT_S = 0.05
# A title needs this many such chapters for its place to be proven by them.
MIN_EXACT_CHAPTERS = 3


@dataclass
class PlayAllRead:
    order: list[int] = field(default_factory=list)  # titles, as the play-all plays them
    # Where each title's time 0 falls in the play-all (s): its frame at t is the play-all's at start + t.
    starts: dict[int, float] = field(default_factory=dict)
    how: str = ""  # "chapters" or "frames"
    note: str = ""
    # Titles whose inner chapters run the play-all's to within a frame, where they sit.
    exact: set[int] = field(default_factory=set)

    @property
    def found(self) -> bool:
        return len(self.order) >= 2

    def proves(self, ordered: list[int]) -> bool:
        """Whether the chapters alone prove ``ordered`` is the play-all's order."""
        return self.order == ordered and len(ordered) >= 2 and set(ordered) <= self.exact


async def chapters(ffprobe: str, path: Path) -> list[tuple[float, float]] | None:
    """(start, end) of each chapter in a ripped file, or None if ffprobe can't say."""
    out = await verify._run([ffprobe, "-v", "error", "-show_entries", "chapter=start_time,end_time",
                             "-of", "json", str(path)])
    if out is None:
        return None
    try:
        return [(float(c["start_time"]), float(c["end_time"])) for c in json.loads(out).get("chapters", [])]
    except (ValueError, KeyError, TypeError):
        return None


def _long(chs: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(a, b) for a, b in chs if b - a >= MIN_CHAPTER_S]


def match_chapters(play_all: list[tuple[float, float]],
                   titles: dict[int, list[tuple[float, float]]]) -> tuple[list[int], dict[int, float]]:
    """Titles whose chapter lengths run in a row inside the play-all's, by where they sit,
    and where each one's time 0 falls in the play-all."""
    order, starts, _ = _match_chapters(play_all, titles)
    return order, starts


def _match_chapters(play_all: list[tuple[float, float]], titles: dict[int, list[tuple[float, float]]]
                    ) -> tuple[list[int], dict[int, float], set[int]]:
    """As match_chapters, and which titles' places are exact (see CHAPTER_EXACT_S)."""
    pa = _long(play_all)
    lengths = [b - a for a, b in pa]
    starts: dict[int, float] = {}
    exact: set[int] = set()
    taken: set[int] = set()
    # Titles with more chapters first, so a short title can't take a longer one's place.
    for idx in sorted(titles, key=lambda i: (-len(_long(titles[i])), i)):
        own = _long(titles[idx])
        mine = [b - a for a, b in own]
        if len(mine) < MIN_CHAPTERS:
            continue
        for k in range(len(lengths) - len(mine) + 1):
            span = range(k, k + len(mine))
            if taken.intersection(span):
                continue
            if all(abs(lengths[k + j] - x) <= _slack(x) for j, x in enumerate(mine)):
                # The first chapter may have lost an opening cell, so place the title by
                # the marks between its chapters: where most of them say.
                marks = sorted(pa[k + j][0] - own[j][0] for j in range(1, len(own)))
                starts[idx] = marks[len(marks) // 2]
                inner = [abs(lengths[k + j] - x) for j, x in enumerate(mine) if j]
                if len(inner) >= MIN_EXACT_CHAPTERS and max(inner) <= CHAPTER_EXACT_S:
                    exact.add(idx)
                taken.update(span)
                break
    order = sorted(starts, key=lambda i: starts[i])
    return order, starts, exact


def _slack(length: float) -> float:
    return min(CHAPTER_SLACK_MAX_S, max(CHAPTER_SLACK_MIN_S, length * CHAPTER_SLACK))


async def _frames_at(ffmpeg: str, title: Path, length: float, play_all: Path, at: float) -> bool | None:
    for frac in verify.SAMPLE_AT:
        t = length * frac
        a, b = await asyncio.gather(verify.frame(ffmpeg, title, t), verify.frame(ffmpeg, play_all, at + t))
        if a is None or b is None:
            return None
        if not verify.frames_match(a, b):
            return False
    return True


async def match_frames(ffmpeg: str, ffprobe: str, play_all: Path,
                       titles: list[tuple[int, Path]]) -> tuple[list[int], dict[int, float]] | None:
    """Walk the play-all: at each point, the title whose frames are there comes next."""
    lengths = dict(zip([i for i, _ in titles],
                       await asyncio.gather(*(verify.duration(ffprobe, p) for _, p in titles)), strict=True))
    total = await verify.duration(ffprobe, play_all)
    if total is None or any(x is None for x in lengths.values()):
        return None
    paths = dict(titles)
    left = [i for i, _ in titles]
    order: list[int] = []
    starts: dict[int, float] = {}
    at = 0.0
    while left and at < total - 1:
        nxt = None
        for i in left:
            if at + lengths[i] > total + 2:
                continue
            hit = await _frames_at(ffmpeg, paths[i], lengths[i], play_all, at)
            if hit is None:
                return None
            if hit:
                nxt = i
                break
        if nxt is None:
            break
        order.append(nxt)
        starts[nxt] = at
        left.remove(nxt)
        at += lengths[nxt]
    return order, starts


async def read(ffmpeg: str, ffprobe: str, play_all: Path, titles: list[tuple[int, Path]],
               play_all_s: float, frames: bool = True) -> PlayAllRead:
    """What ``play_all`` plays out of ``titles`` (each (title number, ripped file)).
    ``frames=False`` only reads chapters, which is quick."""
    pa_chapters = await chapters(ffprobe, play_all)
    if pa_chapters is None:
        return PlayAllRead(note="could not read the ripped files (is ffprobe installed?)")
    lengths: dict[int, float] = {}
    if len(_long(pa_chapters)) >= MIN_CHAPTERS:
        each = await asyncio.gather(*(chapters(ffprobe, p) for _, p in titles))
        order, starts, exact = _match_chapters(pa_chapters,
                                               {i: c or [] for (i, _), c in zip(titles, each, strict=True)})
        for (i, _), c in zip(titles, each, strict=True):
            if c:
                lengths[i] = c[-1][1] - c[0][0]
        if len(order) >= 2 and sum(lengths.get(i, 0) for i in order) >= play_all_s * MIN_COVERED:
            return PlayAllRead(order, starts, "chapters", _said(order, "chapters"), exact & set(order))
    if not frames:
        return PlayAllRead(note="the play-all's chapters don't line up with the titles")
    framed = await match_frames(ffmpeg, ffprobe, play_all, titles)
    if framed is None:
        return PlayAllRead(note="could not decode frames (is ffmpeg installed?)")
    order, starts = framed
    if len(order) >= 2:
        return PlayAllRead(order, starts, "frames", _said(order, "frames"))
    return PlayAllRead(note="no title's chapters or frames line up with the play-all")


def _said(order: list[int], how: str) -> str:
    return f"the play-all plays titles {', '.join(map(str, order))}, in that order (by their {how})"
