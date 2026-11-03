"""TV discs with several episodes in one long title, split losslessly at chapters.

Some DVDs (and the odd Blu-ray) have no title per episode, only one long
title with a few chapters per episode. It is ripped whole like any title, then

    chapter starts (ffprobe) -> how much each chapter mark looks like a break
    between episodes (black frames and silence, ffmpeg) -> consecutive chapters
    grouped into episodes to fit the season's runtimes (dynamic programming)
    -> review, where cuts are turned on and off -> mkvmerge splits the file at
    the chosen chapters, without re-encoding

Each part is a title of its own in the mapping (``PART_BASE`` + its number),
so the usual episode mapping, transcode and copy carry on from there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import statistics
from dataclasses import dataclass
from pathlib import Path

from .. import proc as procs
from ..models import Episode, TitleInfo, TitleRole
from .mapping import runtime_cost

log = logging.getLogger(__name__)

# Part k of the split title is title PART_BASE + k, well above MakeMKV's title numbers.
PART_BASE = 1000
# A title this many typical episodes long, or more, may hold several episodes.
LONG_FACTOR = 1.8
MIN_CHAPTERS = 3
# Other titles within this of the season's runtimes are episodes of their own.
EPISODE_WINDOW = 0.15
# A cut where neither black nor silence was found costs this (a full mismatch costs 1).
CUT_COST = 0.3
# Black frames and silence are looked for this far either side of a chapter mark...
BREAK_WINDOW_S = 4.0
# ...and count when they come within this of it.
BREAK_NEAR_S = 1.5
# Chapters past this many are not checked for breaks (they can still be cut at).
MAX_CHECKED = 99
THUMB_AFTER_S = 8.0
PROBE_TIMEOUT_S = 60
SPLIT_TIMEOUT_S = 4 * 3600


class SplitError(Exception):
    pass


def is_part(title_index: int) -> bool:
    return title_index >= PART_BASE


def part_index(k: int) -> int:
    return PART_BASE + k


# --- which title --------------------------------------------------------------------


@dataclass
class Candidate:
    title_index: int
    episodes: int  # about how many it holds
    reason: str


def detect(titles: list[TitleInfo], roles: dict[int, TitleRole], episodes: list[Episode]) -> Candidate | None:
    """The title holding several episodes back to back, when the disc has no title per episode.

    That is a title at least ``LONG_FACTOR`` times the season's typical runtime with a
    chapter or more per episode, on a disc where fewer than two other titles are one
    episode long (with those, a long title is a play-all, or a feature-length extra)."""
    runtimes = [e.runtime for e in episodes if e.runtime]
    if not runtimes:
        return None
    typical = statistics.median(runtimes) * 60
    # Within the runtimes, but near the typical one, as a whole show's (a compilation's) can span a lot.
    lo = max(min(runtimes) * 60 * (1 - EPISODE_WINDOW), typical / 2)
    hi = min(max(runtimes) * 60 * (1 + EPISODE_WINDOW), typical * 1.5)
    usable = [t for t in titles if roles.get(t.index) not in (TitleRole.play_all, TitleRole.duplicate)]
    long = [t for t in usable if t.duration_s >= LONG_FACTOR * typical
            and t.chapters >= max(MIN_CHAPTERS, round(t.duration_s / typical))]
    if not long:
        return None
    t = max(long, key=lambda t: (t.duration_s, -t.index))
    singles = [o for o in usable if o.index != t.index and lo <= o.duration_s <= hi]
    if len(singles) >= 2:
        return None
    n = max(2, round(t.duration_s / typical))
    return Candidate(t.index, n, f"title {t.index} is {round(t.duration_s / 60)} min with {t.chapters} chapters, "
                                 f"about {n} episodes of {round(typical / 60)} min, and no title is one episode")


# --- where to cut --------------------------------------------------------------------


@dataclass
class Proposal:
    cuts: list[int]  # chapters (0-based) where an episode starts; never 0
    confidence: float  # 0..1
    costs: list[float]  # each part's runtime disagreement, 0 = exact
    note: str


def _cut_cost(breaks: list[float | None], k: int) -> float:
    b = breaks[k] if k < len(breaks) else None
    return CUT_COST * (1 - b) if b is not None else CUT_COST / 2


def propose_cuts(chapters: list[float], total_s: float, breaks: list[float | None], episodes: list[Episode],
                 pal: bool = False) -> Proposal:
    """Group consecutive chapters into parts, one per episode in order, as well as they fit.

    Dynamic programming over (chapters used, episodes used). A part costs how far its
    length is from its episode's runtime, plus a little for a cut where the chapter
    mark doesn't look like a break. Fewer episodes than the season has is fine (the
    disc holds a run of them); the cheapest number of parts wins. Past the season's
    last episode, parts fit its typical runtime (the start episode is likely wrong)."""
    k_n = len(chapters)
    known = [e.runtime for e in episodes if e.runtime]
    if k_n < 2 or not known:
        return Proposal([], 0.0, [], "no chapters to cut at" if k_n < 2 else "no episode runtimes to fit")
    fallback = statistics.median(known)
    runtimes = [e.runtime or fallback for e in episodes]
    # No more parts than twice the episodes the title could hold, to keep the table small.
    m = min(k_n, max(len(runtimes), 2 * round(total_s / 60 / fallback) + 1))
    runtimes = (runtimes + [fallback] * m)[:m]
    edges = [*chapters, total_s]
    edges[0] = 0.0
    inf = float("inf")
    best = [[inf] * (m + 1) for _ in range(k_n + 1)]
    back = [[0] * (m + 1) for _ in range(k_n + 1)]
    best[0][0] = 0.0
    for i in range(1, k_n + 1):
        for j in range(1, min(i, m) + 1):
            for p in range(j - 1, i):
                if best[p][j - 1] == inf:
                    continue
                c = best[p][j - 1] + runtime_cost(int(edges[i] - edges[p]), runtimes[j - 1], pal)
                if p:
                    c += _cut_cost(breaks, p)
                if c < best[i][j]:
                    best[i][j], back[i][j] = c, p
    n = min(range(1, m + 1), key=lambda j: (best[k_n][j], j))
    starts, i = [], k_n
    for j in range(n, 0, -1):
        i = back[i][j]
        starts.append(i)
    starts.reverse()
    cuts = starts[1:]
    bounds = [*starts, k_n]
    costs = [round(runtime_cost(int(edges[b] - edges[a]), runtimes[j], pal), 3)
             for j, (a, b) in enumerate(zip(bounds, bounds[1:], strict=False))]
    fit = 1 - sum(costs) / len(costs)
    seen = [breaks[c] for c in cuts if c < len(breaks) and breaks[c] is not None]
    confidence = fit if not seen else 0.7 * fit + 0.3 * (sum(seen) / len(seen))
    clean = sum(1 for b in seen if b >= 0.99)
    note = f"{sum(1 for c in costs if c == 0)} of {n} runtimes match"
    if cuts:
        note += f", {clean} of {len(cuts)} cuts at black and silence" if seen else ", breaks not checked"
    if n > len(episodes):
        note += f"; the season has only {len(episodes)} more, so check the start episode"
    return Proposal(cuts, round(max(0.0, confidence), 2), costs, note)


def parts(chapters: list[float], total_s: float, cuts: list[int]) -> list[tuple[float, float, int]]:
    """(start, end, first chapter) of each part for these cuts."""
    starts = [0, *sorted(c for c in set(cuts) if 0 < c < len(chapters))]
    out = []
    for a, b in zip(starts, [*starts[1:], None], strict=True):
        out.append((0.0 if a == 0 else chapters[a], total_s if b is None else chapters[b], a))
    return out


# --- ffprobe, ffmpeg, mkvmerge -------------------------------------------------------


async def _run(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes] | None:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        log.warning("%s not available: %s", cmd[0], e)
        return None
    done = await procs.communicate(proc, timeout)
    return (proc.returncode, *done) if done is not None else None


async def read_chapters(ffprobe: str, path: Path) -> list[float] | None:
    """Chapter start times (seconds) of a ripped MKV, in order."""
    got = await _run([ffprobe, "-v", "error", "-show_chapters", "-of", "json", str(path)], PROBE_TIMEOUT_S)
    if not got or got[0] != 0:
        return None
    return parse_chapters(got[1])


def parse_chapters(out: bytes) -> list[float] | None:
    try:
        data = json.loads(out or b"{}")
        return sorted(float(c["start_time"]) for c in data.get("chapters") or [])
    except (ValueError, KeyError, TypeError):
        return None


def break_command(ffmpeg: str, path: Path, at_s: float) -> tuple[list[str], float]:
    """ffmpeg looking for black frames and silence around ``at_s``, and where the mark is in its output."""
    start = max(0.0, at_s - BREAK_WINDOW_S)
    return [ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{2 * BREAK_WINDOW_S:.3f}",
            "-i", str(path), "-vf", "blackdetect=d=0.05:pix_th=0.10", "-af", "silencedetect=n=-45dB:d=0.25",
            "-f", "null", "-"], at_s - start


def score_break(log_text: str, mark: float) -> float:
    """0..1: half for black frames near the mark, half for silence near it."""
    def near(spans: list[tuple[float, float]]) -> bool:
        return any(a - BREAK_NEAR_S <= mark <= b + BREAK_NEAR_S for a, b in spans)

    black = [(float(a), float(b)) for a, b in
             re.findall(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)", log_text)]
    silence: list[tuple[float, float]] = []
    opened: float | None = None
    for kind, t in re.findall(r"silence_(start|end):\s*(-?[\d.]+)", log_text):
        if kind == "start":
            opened = float(t)
        elif opened is not None:
            silence.append((opened, float(t)))
            opened = None
    if opened is not None:  # still silent when the window ended
        silence.append((opened, 2 * BREAK_WINDOW_S))
    return 0.5 * near(black) + 0.5 * near(silence)


async def break_score(ffmpeg: str, path: Path, at_s: float) -> float | None:
    cmd, mark = break_command(ffmpeg, path, at_s)
    got = await _run(cmd, PROBE_TIMEOUT_S)
    if not got or got[0] != 0:
        return None
    return score_break(got[2].decode(errors="replace"), mark)


def default_mkvmerge(command: str) -> str:
    """The default "mkvmerge" is on the PATH on Linux; on Windows MKVToolNix installs it to Program Files."""
    if command != "mkvmerge" or not procs.WINDOWS or shutil.which(command):
        return command
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and (Path(base) / "MKVToolNix" / "mkvmerge.exe").is_file():
            return str(Path(base) / "MKVToolNix" / "mkvmerge.exe")
    return command


def split_command(mkvmerge: str, src: Path, out_dir: Path, cuts: list[int]) -> list[str]:
    """mkvmerge splitting ``src`` before the given chapters (0-based here, 1-based for mkvmerge).

    It writes part-001.mkv, part-002.mkv, ... copying every track as it is."""
    chapters = ",".join(str(c + 1) for c in sorted(set(cuts)))
    return [mkvmerge, "--quiet", "-o", str(out_dir / "part.mkv"), "--split", f"chapters:{chapters}", str(src)]


async def split_file(mkvmerge: str, src: Path, out_dir: Path, cuts: list[int]) -> list[Path]:
    """The parts of ``src``, in order. With no cuts that's the file itself."""
    if not cuts:
        return [src]
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("part*.mkv"):
        old.unlink()
    got = await _run(split_command(mkvmerge, src, out_dir, cuts), SPLIT_TIMEOUT_S)
    if got is None:
        raise SplitError(f"{mkvmerge} could not be run (is MKVToolNix installed?) or took too long")
    code, out, err = got
    if code not in (0, 1):  # 1 is "finished with warnings"
        lines = (out + err).decode(errors="replace").strip().splitlines()
        raise SplitError(f"mkvmerge failed: {lines[-1] if lines else f'exit code {code}'}")
    files = sorted(out_dir.glob("part-*.mkv"))
    if len(files) != len(set(cuts)) + 1:
        raise SplitError(f"mkvmerge made {len(files)} parts, not {len(set(cuts)) + 1}")
    return files
