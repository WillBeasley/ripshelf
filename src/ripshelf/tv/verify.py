"""Check episode order against the play-all, and grab thumbnails (ffmpeg).

A frame decoded from an episode at time t is bit-identical to the play-all's
frame at (start of that episode in the play-all) + t. Episode starts are the
running sum of the episodes' own lengths, in the proposed order; if every
episode's frames match, the order is proven. A small tolerance absorbs
decoders that round differently.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PROBE_W, PROBE_H = 48, 27
MATCH_MAX_MEAN_DIFF = 2.0  # per colour channel, 0..255
# Two sample points per episode, so one look-alike frame (a title card) cannot pass alone.
SAMPLE_AT = (0.3, 0.6)
FFMPEG_TIMEOUT_S = 60
# Some decoders (VC-1) give grey frames right after a seek, and grey would
# match grey. Seek this far before the wanted time and decode up to it.
SETTLE_S = 2.0


@dataclass
class OrderCheck:
    verified: bool | None  # None if it could not check (no play-all, ffmpeg missing, ...)
    note: str
    per_title: dict[int, bool]


async def _run(cmd: list[str], timeout: float = FFMPEG_TIMEOUT_S) -> bytes | None:
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
    except OSError as e:
        log.warning("%s not available: %s", cmd[0], e)
        return None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    return out if proc.returncode == 0 else None


async def duration(ffprobe: str, path: Path) -> float | None:
    out = await _run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    try:
        return float(out.decode().strip()) if out else None
    except ValueError:
        return None


async def frame(ffmpeg: str, path: Path, at_s: float) -> bytes | None:
    """One decoded frame at ``at_s``, scaled down (in colour, because grey loses red vs green)."""
    pre = min(SETTLE_S, at_s)
    out = await _run([ffmpeg, "-v", "error", "-ss", f"{at_s - pre:.3f}", "-i", str(path), "-frames:v", "1",
                      "-vf", f"select=gte(t\\,{pre:.3f}),scale={PROBE_W}:{PROBE_H},format=rgb24",
                      "-f", "rawvideo", "-"])
    return out if out and len(out) == PROBE_W * PROBE_H * 3 else None


def frames_match(a: bytes, b: bytes) -> bool:
    if a == b:
        return True
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a) <= MATCH_MAX_MEAN_DIFF


async def verify_order(ffmpeg: str, ffprobe: str, play_all: Path, ordered: list[tuple[int, Path]]) -> OrderCheck:
    if len(ordered) < 2:
        return OrderCheck(None, "nothing to check, because there are fewer than two episodes", {})
    lengths = await asyncio.gather(*(duration(ffprobe, p) for _, p in ordered))
    if any(x is None for x in lengths):
        return OrderCheck(None, "could not read episode lengths (is ffprobe installed?)", {})
    total = await duration(ffprobe, play_all)
    if total is None or total + 2 < sum(lengths):
        return OrderCheck(None, "the play-all is shorter than the episodes together", {})
    per: dict[int, bool] = {}
    start = 0.0
    for (idx, path), length in zip(ordered, lengths, strict=True):
        ok = True
        for frac in SAMPLE_AT:
            t = length * frac
            a, b = await asyncio.gather(frame(ffmpeg, path, t), frame(ffmpeg, play_all, start + t))
            if a is None or b is None:
                return OrderCheck(None, "could not decode frames (is ffmpeg installed?)", per)
            ok = ok and frames_match(a, b)
        per[idx] = ok
        start += length
    bad = [i for i, ok in per.items() if not ok]
    if not bad:
        return OrderCheck(True, f"all {len(per)} episodes match the play-all frame for frame", per)
    return OrderCheck(False, "titles " + ", ".join(map(str, bad)) + " do not match the play-all at their position",
                      per)


async def thumbnail(ffmpeg: str, path: Path, out: Path, at_fraction: float = 0.1,
                    length: float | None = None) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    at = (length or 60) * at_fraction
    res = await _run([ffmpeg, "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", str(path), "-frames:v", "1",
                      "-vf", "scale=320:-2", "-q:v", "4", str(out)])
    return res is not None and out.exists()
