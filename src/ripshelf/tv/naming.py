"""Emby TV layout.

    TV/Show (Year)/Season 01/Show - S01E03 - Episode Name.mkv
    TV/Show (Year)/Season 01/Show - S01E01-E02 - Part One & Part Two.mkv
    TV/Show (Year)/Season 00/Show - S00E05 - Special.mkv
    TV/Show (Year)/extras/LABEL - title 7.mkv
    TV/Show (Year)/featurettes/Name From The Box.mkv     (an extra the box names)
    TV/Show (Year)/.disc/LABEL (job 12)/disc.json
"""

from __future__ import annotations

from pathlib import Path

from ..library import MAX_NAME_BYTES, extras_folder, safe_name
from ..models import Episode, Show


def show_folder(tv: Path, show: Show) -> Path:
    """``tv`` is the TV root (``<library>/TV`` or a separate folder)."""
    base = f"{show.name} ({show.year})" if show.year else show.name
    return tv / safe_name(base)


def episode_path(tv: Path, show: Show, eps: list[Episode]) -> Path:
    """One title's file. Several episodes in a run of one season get a range
    (S01E04-E05), which media servers read as each of them; any others (a compilation's
    S01E02 with S03E07) can't be said in a range, so the file takes the first one's code
    and names the rest with theirs."""
    eps = sorted(eps, key=lambda e: (e.season, e.episode))
    first = eps[0]
    code = f"S{first.season:02d}E{first.episode:02d}"
    run = all(b.season == first.season and b.episode == a.episode + 1 for a, b in zip(eps, eps[1:], strict=False))
    if len(eps) > 1 and run:
        code += f"-E{eps[-1].episode:02d}"
        names = " & ".join(e.name for e in eps if e.name)
    else:
        names = " & ".join(
            [*([first.name] if first.name else []),
             *(f"S{e.season:02d}E{e.episode:02d}" + (f" {e.name}" if e.name else "") for e in eps[1:])])
    stem = f"{show.name} - {code}" + (f" - {names}" if names else "")
    # Keep the S01E01 code even when a long name has to be cut.
    stem = safe_name(stem)
    if len(stem.encode()) > MAX_NAME_BYTES - 4:
        stem = safe_name(stem.encode()[: MAX_NAME_BYTES - 4].decode(errors="ignore"))
    return show_folder(tv, show) / f"Season {first.season:02d}" / f"{stem}.mkv"


def extra_path(tv: Path, show: Show, label: str | None, title_index: int) -> Path:
    return show_folder(tv, show) / "extras" / f"{safe_name(label or 'Disc')} - title {title_index}.mkv"


def named_extra_path(tv: Path, show: Show, extra_type: str | None, name: str) -> Path:
    """An extra the box names, in Emby's folder for its type (the name made safe)."""
    return show_folder(tv, show) / extras_folder(extra_type, name) / f"{safe_name(name)}.mkv"


def disc_meta_folder(tv: Path, show: Show, label: str | None, job_id: int) -> Path:
    return show_folder(tv, show) / ".disc" / safe_name(f"{label or 'disc'} (job {job_id})")
