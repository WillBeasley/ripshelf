"""Moving finished rips into the library.

Layout (Emby movie naming)::

    Movies/Title (Year)/Title (Year).mkv
    Movies/Title (Year)/Title (Year) - 2160p.mkv      (UHD)
    Movies/Title (Year)/.disc/disc.json, makemkv-scan.log, VIDEO_TS/ or BDMV/...

Full disc backups (a folder Emby plays as the film)::

    Disc backups/Title (Year)/BDMV/..., CERTIFICATE/..., .disc/disc.json
    Disc backups/Title (Year) - 2160p/...             (UHD)

The copy is written to a ``.partial`` name with a running SHA-256, then read
back and hashed again before it is renamed into place and the scratch copy is
deleted.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

CHUNK = 8 * 1024 * 1024
# Characters SMB/Windows refuse in names, plus control characters.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$", re.I)
# Leaves room for " - 2160p.mkv" and ".partial" within the usual 255-byte limit.
MAX_NAME_BYTES = 200


def safe_name(s: str) -> str:
    """A single path component that is safe on ext4 and SMB shares.

    No separators, no "..", no leading dots (hidden files), no trailing dots or
    spaces (Windows strips them), no reserved device names, bounded length.
    """
    s = _ILLEGAL.sub(" ", s.replace(":", " -"))
    s = " ".join(w for w in s.split() if w.strip("."))  # drop ".." left from paths
    s = s.strip(".").strip()
    if _RESERVED.match(s):
        s = f"{s}_"
    while len(s.encode()) > MAX_NAME_BYTES:
        s = s[:-1]
    s = s.rstrip(" .")
    return s or "Unknown"


# Windows refuses to rename a file another program has open, and virus scanners,
# the search indexer or a media server scanning the folder open new files briefly.
REPLACE_TRIES = 8


def replace(src: Path, dst: Path) -> None:
    """``os.replace``, retried for a few seconds on Windows while something else has the file open."""
    delay = 0.1
    for attempt in range(REPLACE_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == REPLACE_TRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2


# Emby's extras folders, in a film's or show's folder (and its seasons'), as its naming
# guides list them: https://emby.media/support/articles/Movie-Naming.html and
# https://emby.media/support/articles/TV-Naming.html. Jellyfin and Plex read these too.
EXTRAS_FOLDERS = {"Featurette": "featurettes", "Extra": "extras", "DeletedScene": "deleted scenes",
                  "Trailer": "trailers", "Interview": "interviews", "Scene": "scenes", "Short": "shorts",
                  "Music": "extras", "Other": "extras"}
BEHIND_THE_SCENES = re.compile(r"behind the scenes|\bmaking\b|on set", re.I)


def extras_folder(extra_type: str | None, name: str) -> str:
    """The Emby extras folder for a named extra of TheDiscDB's type. A featurette about
    the making of the film goes in "behind the scenes"."""
    if extra_type == "Featurette" and BEHIND_THE_SCENES.search(name):
        return "behind the scenes"
    return EXTRAS_FOLDERS.get(extra_type or "Extra", "extras")


def free_path(path: Path, taken: set[Path] = frozenset()) -> Path:
    """``path``, or with " (2)", " (3)"... before the extension if a file has it (or
    another file being placed now)."""
    n = 1
    out = path
    while out.exists() or out in taken:
        n += 1
        out = path.with_name(f"{path.stem} ({n}){path.suffix}")
    return out


class LibraryError(Exception):
    pass


def check_library(library: Path, what: str = "library folder") -> None:
    """An output root must already exist, so that an unmounted share must not be
    silently replaced by a folder on the local disk."""
    if not library.is_dir():
        raise LibraryError(f"{what} {library} does not exist (is the share mounted?)")
    if not writable(library):
        raise LibraryError(f"{what} {library} is not writable")


def writable(folder: Path) -> bool:
    """Whether files can be made in ``folder``. On Windows ``os.access`` only reads the
    read-only attribute, not the folder's permissions (or a share's), so a file is made
    and removed there instead."""
    if sys.platform != "win32":
        return os.access(folder, os.W_OK | os.X_OK)
    try:
        with tempfile.TemporaryFile(dir=folder, prefix=".ripshelf-check-"):
            return True
    except OSError:
        return False


# A copy needs the files' size plus this much, for the filesystem's own overhead and the
# .disc folder written beside them.
COPY_MARGIN = 1.02


def gib(n: int) -> str:
    """Sizes as MakeMKV prints them (binary units, labelled GB/MB)."""
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    return f"{n / 1024**2:.0f} MB"


def space_shortfall(root: Path, sources: list[Path], what: str = "library folder") -> str | None:
    """Why copying ``sources`` (files or folders) into ``root`` won't fit, or None.

    A copy is written in full before anything is replaced, so even an overwrite needs the
    whole size free. When the free space can't be read (an odd share), the copy is tried.
    """
    try:
        free = shutil.disk_usage(root).free
        size = sum(tree_size(p) if p.is_dir() else p.stat().st_size for p in sources)
    except OSError:
        return None
    need = int(size * COPY_MARGIN)
    if free >= need:
        return None
    return (f"Not enough space in the {what} {root} to copy this rip. It needs about {gib(need)}, and "
            f"{gib(free)} is free. Free some space, then retry.")


def movie_paths(movies: Path, title: str, year: int | None, uhd: bool) -> tuple[Path, Path]:
    """``movies`` is the films root (``<library>/Movies`` or a separate folder)."""
    base = safe_name(f"{title} ({year})" if year else title)
    folder = movies / base
    name = f"{base} - 2160p.mkv" if uhd else f"{base}.mkv"
    return folder, folder / name


def backup_path(root: Path, title: str, year: int | None, uhd: bool) -> Path:
    base = safe_name(f"{title} ({year})" if year else title)
    return root / (f"{base} - 2160p" if uhd else base)


def tree_size(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())


class VerifyError(Exception):
    pass


# Called as on_progress(done, total, verifying). Both count the two passes over the data,
# the copy and the read back to check it, so ``total`` is twice the bytes and the copy
# reaches half way before the check starts.
CopyProgress = Callable[[int, int, bool], None]


def copy_verified(src: Path, dst: Path, on_progress: CopyProgress | None = None,
                  overwrite: bool = False) -> str:
    """Copy with checksum verification; returns the SHA-256. Blocking (run in a thread).

    With ``overwrite`` an existing ``dst`` is replaced, but only once the new copy has
    been written and verified next to it. The old file is never removed first.
    """
    total = src.stat().st_size
    if dst.exists() and not overwrite:
        raise FileExistsError(f"{dst} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    partial = dst.with_name(f".{dst.name}.partial")
    h = hashlib.sha256()
    done = 0
    try:
        with src.open("rb") as fi, partial.open("wb") as fo:
            while chunk := fi.read(CHUNK):
                h.update(chunk)
                fo.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, 2 * total, False)
            fo.flush()
            os.fsync(fo.fileno())
    except BaseException:
        # If the disk is full or the share gone, never leave half a film in the library.
        partial.unlink(missing_ok=True)
        raise
    src_hash = h.hexdigest()
    written = partial.stat().st_size
    if written != total:
        partial.unlink(missing_ok=True)
        raise VerifyError(f"size mismatch after copy ({written} != {total})")
    h2 = hashlib.sha256()
    checked = 0
    with partial.open("rb") as f:
        while chunk := f.read(CHUNK):
            h2.update(chunk)
            checked += len(chunk)
            if on_progress:
                on_progress(total + checked, 2 * total, True)
    if h2.hexdigest() != src_hash:
        partial.unlink(missing_ok=True)
        raise VerifyError("checksum mismatch after copy")
    if dst.exists() and not overwrite:  # appeared while we copied, so never overwrite
        partial.unlink(missing_ok=True)
        raise FileExistsError(f"{dst} already exists")
    replace(partial, dst)  # atomic, so the old file (if any) is replaced in one step
    return src_hash


def copy_tree_verified(src: Path, dst: Path, on_progress: CopyProgress | None = None) -> dict[str, str]:
    """Copy a folder file by file with ``copy_verified``; returns relative path -> SHA-256.

    It is built under a hidden ``.partial`` name and renamed into place at the end,
    so a half-copied backup never shows up under its real name. Blocking.
    """
    if dst.exists():
        raise FileExistsError(f"{dst} already exists")
    files = sorted(p for p in src.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files) or 1
    partial = dst.with_name(f".{dst.name}.partial")
    shutil.rmtree(partial, ignore_errors=True)
    hashes: dict[str, str] = {}
    base = 0
    try:
        for f in files:
            rel = f.relative_to(src)
            size = f.stat().st_size
            cb = (lambda done, _t, v, base=base: on_progress(2 * base + done, 2 * total, v)) if on_progress else None
            hashes[rel.as_posix()] = copy_verified(f, partial / rel, cb)
            base += size
        if dst.exists():
            raise FileExistsError(f"{dst} already exists")
        replace(partial, dst)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return hashes


def move_path(src: Path, dst: Path) -> None:
    """Move a file or folder within the library, never over anything already at ``dst``.

    A rename where it can be. Across filesystems (the films on one share and a folder
    mounted from another), it's copied and verified, and only then is the original deleted.
    Blocking.
    """
    if dst.exists():
        raise FileExistsError(f"{dst} already exists")
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
        return
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    if src.is_dir():
        copy_tree_verified(src, dst)
        shutil.rmtree(src)
    else:
        copy_verified(src, dst)
        src.unlink()


# What a media server writes next to a film or show it scanned: artwork and .nfo files of
# the old title. Once that title's files have moved away they describe nothing, so they go.
SERVER_METADATA = re.compile(r"^(.*\.nfo|(poster|folder|fanart|backdrop|banner|logo|clearart|clearlogo|landscape|"
                             r"thumb|disc|discart)(-\d+)?\.(jpg|jpeg|png|webp))$", re.I)
VIDEO_SUFFIXES = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".m2ts", ".iso"}


def prune_folder(folder: Path, root: Path) -> list[Path]:
    """Remove ``folder`` and the folders above it (up to, not including, ``root``) when
    nothing is left in them. A folder with no videos left keeps nothing but a media
    server's artwork and .nfo files of the title that moved away, so they go first.
    Returns what's left in ``folder`` (empty once it's gone). Blocking."""
    try:
        folder.resolve().relative_to(root.resolve())
    except ValueError:
        return []
    if folder.resolve() == root.resolve() or not folder.is_dir():
        return []
    left = [p for p in folder.rglob("*") if p.is_file()]
    if not any(p.suffix.lower() in VIDEO_SUFFIXES for p in left):
        for p in left:
            if p.parent == folder and SERVER_METADATA.match(p.name):
                p.unlink(missing_ok=True)
    for d in sorted((p for p in folder.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    left = [p for p in folder.rglob("*") if p.is_file()]
    if left:
        return left
    d = folder
    while d.resolve() != root.resolve() and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent
    return []


def write_disc_folder(folder: Path, disc_json: dict, scan_log: Path | None, structure_dir: Path | None,
                      meta: Path | None = None) -> Path:
    """Writes ``folder/.disc`` (or ``meta``, for TV's per-disc folders)."""
    meta = meta or folder / ".disc"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "disc.json").write_text(json.dumps(disc_json, indent=2, default=str), encoding="utf-8")
    if scan_log and scan_log.exists():
        shutil.copy2(scan_log, meta / "makemkv-scan.log")
    if structure_dir and structure_dir.is_dir():
        for p in structure_dir.rglob("*"):
            if p.is_file():
                target = meta / p.relative_to(structure_dir)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, target)
    return meta
