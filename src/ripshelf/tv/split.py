"""TV discs with several episodes in one long title, split losslessly at chapters.

Some DVDs (and the odd Blu-ray) have no title per episode, only one long
title with a few chapters per episode. It is ripped whole like any title, then

    chapter starts (ffprobe) -> how much each chapter mark looks like a break
    between episodes (black frames and silence, ffmpeg) -> consecutive chapters
    grouped into episodes to fit the season's runtimes (dynamic programming)
    -> review, where cuts are turned on and off -> mkvmerge splits the file at
    the chosen chapters, without re-encoding

A title with fewer chapters than episodes is searched end to end for black
frames and silence instead, and those breaks are the marks it can be cut at
(by timestamp). A cut can also be put anywhere by hand, moved to a break
nearby when there is one.

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
# A title this long may hold several episodes (two 20 min ones), whatever its chapters.
LONG_MIN_S = 40 * 60
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
# Searching a whole title for breaks decodes all of it.
SCAN_TIMEOUT_S = 4 * 3600
# In a scan, silence alone counts as a break when it lasts this long (black frames always do)...
SILENCE_ALONE_S = 1.0
# ...breaks closer than this are one break, and none this near the start or end counts.
SAME_BREAK_S = 2.0
EDGE_S = 60.0
# A cut put in by hand moves to the best break within this of it.
SNAP_S = 10.0


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


def may_hold_several(t: TitleInfo) -> bool:
    """Long enough to be several episodes, before the show (and its runtimes) is known."""
    return t.duration_s >= LONG_MIN_S


def detect(titles: list[TitleInfo], roles: dict[int, TitleRole], episodes: list[Episode]) -> Candidate | None:
    """The title holding several episodes back to back, when the disc has no title per episode.

    That is a title at least ``LONG_FACTOR`` times the season's typical runtime, on a
    disc where fewer than two other titles are one episode long (with those, a long
    title is a play-all, or a feature-length extra). Without a chapter per episode it
    is searched for breaks instead, so chapters don't decide."""
    runtimes = [e.runtime for e in episodes if e.runtime]
    if not runtimes:
        return None
    typical = statistics.median(runtimes) * 60
    # Within the runtimes, but near the typical one, as a whole show's (a compilation's) can span a lot.
    lo = max(min(runtimes) * 60 * (1 - EPISODE_WINDOW), typical / 2)
    hi = min(max(runtimes) * 60 * (1 + EPISODE_WINDOW), typical * 1.5)
    usable = [t for t in titles if roles.get(t.index) not in (TitleRole.play_all, TitleRole.duplicate)]
    long = [t for t in usable if t.duration_s >= LONG_FACTOR * typical]
    if not long:
        return None
    t = max(long, key=lambda t: (t.duration_s, -t.index))
    singles = [o for o in usable if o.index != t.index and lo <= o.duration_s <= hi]
    if len(singles) >= 2:
        return None
    n = max(2, round(t.duration_s / typical))
    marks = f"{t.chapters} chapters" if enough_chapters(t.chapters, n) else \
        ("no chapters" if t.chapters <= 1 else f"only {t.chapters} chapters")
    return Candidate(t.index, n, f"title {t.index} is {round(t.duration_s / 60)} min with {marks}, "
                                 f"about {n} episodes of {round(typical / 60)} min, and no title is one episode")


def enough_chapters(chapters: int, episodes: int) -> bool:
    """Whether a title's chapters can be where its episodes start (else it's searched for
    breaks): one per episode will do, as on a Fraggle Rock DVD with two episodes in one
    title and a chapter at the start of each."""
    return chapters >= max(2, episodes)


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


Span = tuple[float, float]


def parse_spans(log_text: str, end_s: float) -> tuple[list[Span], list[Span]]:
    """(black, silence) spans from ffmpeg's blackdetect and silencedetect lines. Silence
    still going when the input ended runs to ``end_s``."""
    black = [(float(a), float(b)) for a, b in
             re.findall(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)", log_text)]
    silence: list[Span] = []
    opened: float | None = None
    for kind, t in re.findall(r"silence_(start|end):\s*(-?[\d.]+)", log_text):
        if kind == "start":
            opened = float(t)
        elif opened is not None:
            silence.append((opened, float(t)))
            opened = None
    if opened is not None:
        silence.append((opened, end_s))
    return black, silence


def _near(spans: list[Span], a: float, b: float) -> bool:
    return any(x - BREAK_NEAR_S <= b and a <= y + BREAK_NEAR_S for x, y in spans)


def score_break(log_text: str, mark: float) -> float:
    """0..1: half for black frames near the mark, half for silence near it."""
    black, silence = parse_spans(log_text, 2 * BREAK_WINDOW_S)
    return 0.5 * _near(black, mark, mark) + 0.5 * _near(silence, mark, mark)


@dataclass
class Break:
    at: float  # seconds
    score: float  # 0.5 for black frames or silence, 1 for both


def find_breaks(black: list[Span], silence: list[Span], start_s: float = 0.0, end_s: float = float("inf"),
                edge_s: float = 0.0) -> list[Break]:
    """Where black frames and silence say one episode may end and the next begin.

    Black frames count, more with silence alongside; silence alone counts when it's
    long. Each break is the middle of its black frames (or silence). Breaks within
    ``SAME_BREAK_S`` of each other are one, the better; none within ``edge_s`` of the
    start or end counts."""
    found = [Break((a + b) / 2, 0.5 + 0.5 * _near(silence, a, b)) for a, b in black]
    found += [Break((a + b) / 2, 0.5) for a, b in silence
              if b - a >= SILENCE_ALONE_S and not _near(black, a, b)]
    out: list[Break] = []
    for br in sorted(found, key=lambda br: br.at):
        if not start_s + edge_s <= br.at <= end_s - edge_s:
            continue
        if out and br.at - out[-1].at < SAME_BREAK_S:
            if br.score > out[-1].score:
                out[-1] = br
            continue
        out.append(br)
    return out


def scan_command(ffmpeg: str, path: Path) -> list[str]:
    """ffmpeg looking for black frames and silence through the whole title."""
    return [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-vf", "blackdetect=d=0.1:pix_th=0.10",
            "-af", "silencedetect=n=-45dB:d=0.4", "-f", "null", "-"]


async def scan_breaks(ffmpeg: str, path: Path, length_s: float) -> list[Break] | None:
    """Every break in a title, for one with no chapters to cut at. None if ffmpeg failed."""
    got = await _run(scan_command(ffmpeg, path), SCAN_TIMEOUT_S)
    if not got or got[0] != 0:
        return None
    black, silence = parse_spans(got[2].decode(errors="replace"), length_s)
    return find_breaks(black, silence, 0.0, length_s, EDGE_S)


async def snap(ffmpeg: str, path: Path, at_s: float) -> Break | None:
    """The best break within ``SNAP_S`` of ``at_s`` (the nearest of equals), or None."""
    start = max(0.0, at_s - SNAP_S)
    got = await _run([ffmpeg, "-hide_banner", "-nostats", "-ss", f"{start:.3f}", "-t", f"{2 * SNAP_S:.3f}",
                      "-i", str(path), "-vf", "blackdetect=d=0.05:pix_th=0.10",
                      "-af", "silencedetect=n=-45dB:d=0.25", "-f", "null", "-"], PROBE_TIMEOUT_S)
    if not got or got[0] != 0:
        return None
    black, silence = parse_spans(got[2].decode(errors="replace"), 2 * SNAP_S)
    found = find_breaks(black, silence)
    best = max(found, key=lambda br: (br.score, -abs(br.at + start - at_s)), default=None)
    return Break(round(best.at + start, 3), best.score) if best else None


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


def timestamp(s: float) -> str:
    """Seconds as mkvmerge's HH:MM:SS.nnn."""
    ms = round(s * 1000)
    return f"{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def split_command(mkvmerge: str, src: Path, out_dir: Path, cuts: list[int],
                  times: list[float] | None = None) -> list[str]:
    """mkvmerge splitting ``src`` before the given chapters (0-based here, 1-based for mkvmerge),
    or, with ``times``, at those timestamps (each at the next keyframe).

    It writes part-001.mkv, part-002.mkv, ... copying every track as it is."""
    if times is not None:
        how = "timestamps:" + ",".join(timestamp(t) for t in sorted(set(times)))
    else:
        how = "chapters:" + ",".join(str(c + 1) for c in sorted(set(cuts)))
    return [mkvmerge, "--quiet", "-o", str(out_dir / "part.mkv"), "--split", how, str(src)]


async def split_file(mkvmerge: str, src: Path, out_dir: Path, cuts: list[int],
                     times: list[float] | None = None) -> list[Path]:
    """The parts of ``src``, in order. With no cuts that's the file itself."""
    if not cuts:
        return [src]
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("part*.mkv"):
        old.unlink()
    got = await _run(split_command(mkvmerge, src, out_dir, cuts, times), SPLIT_TIMEOUT_S)
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
