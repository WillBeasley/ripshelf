"""Frames from a disc's titles, to help tell what the disc is.

They come from the titles ripped to scratch, so a film disc has its main
feature and a TV disc every title. A title that is still being ripped gives
frames from the part already written (MakeMKV writes the file from the start).
Each frame is made by ffmpeg the first time it's asked for, and kept in the
job's folder.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from . import library
from .models import TitleInfo, TitleSnapshots
from .tv import verify

COUNT = 6
WIDTH = 640
# A frame this close to the end of what's been written may not be there yet.
MARGIN_S = 30
# A file is checked once more than this much of it is written.
MIN_BYTES = 8 * 1024 * 1024


def positions(length: float) -> list[float]:
    """Evenly spread times, missing the very start and end (logos, credits)."""
    return [round(length * (i + 1) / (COUNT + 1), 2) for i in range(COUNT)]


def title_file(out_dir: Path, title: TitleInfo, ripped: dict[int, str]) -> Path | None:
    if title.index in ripped and Path(ripped[title.index]).exists():
        return Path(ripped[title.index])
    names = [title.output_file] if title.output_file else []
    names += sorted(p.name for p in out_dir.glob(f"*_t{title.index:02d}.mkv")) if out_dir.is_dir() else []
    return next((out_dir / n for n in names if (out_dir / n).exists()), None)


def plan(title: TitleInfo, path: Path | None, finished: bool, length: float | None) -> TitleSnapshots:
    """Which frames the title can give now. ``length`` is the file's own length, once finished."""
    view = TitleSnapshots(title_index=title.index, duration_s=title.duration_s)
    if path is None:
        return view
    total = length if finished and length else float(title.duration_s)
    if finished:
        view.ripped = 1.0
        written = total
    else:
        size = path.stat().st_size if path.exists() else 0
        if size < MIN_BYTES or not title.size_bytes:
            return view
        view.ripped = round(min(size / title.size_bytes, 1.0), 3)
        written = total * view.ripped - MARGIN_S
    at = positions(total)
    view.frames = [i for i, t in enumerate(at) if t <= written]
    view.at_s = [int(at[i]) for i in view.frames]
    return view


class Grabber:
    """Makes the frames, two ffmpeg runs at a time, and each frame once."""

    def __init__(self):
        self._slots = asyncio.Semaphore(2)
        self._locks: dict[Path, asyncio.Lock] = {}

    async def frame(self, ffmpeg: str, src: Path, at_s: float, out: Path) -> Path | None:
        lock = self._locks.setdefault(out, asyncio.Lock())
        async with lock:
            if out.exists():
                return out
            async with self._slots:
                tmp = out.with_suffix(".part.jpg")
                ok = await verify.thumbnail(ffmpeg, src, tmp, at_s=at_s, width=WIDTH)
            if not ok:
                tmp.unlink(missing_ok=True)
                return None
            library.replace(tmp, out)
            return out
