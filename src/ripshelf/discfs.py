"""Read a disc's small structure files without mounting it.

7-Zip reads ISO 9660 and UDF (including the 2.50 metadata partition used by
Blu-ray and UHD) straight from the block device, which works in a rootless
container where ``mount`` does not. Only the small navigation files are copied:
DVD IFOs, and Blu-ray index/MovieObject/playlists/clip info/META, plus the AACS
unit key file (its hash is the disc's AACS Disc ID). The sizes of the video files
are listed too, in ``SIZES``, because disc databases fingerprint discs by them.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import shutil
from pathlib import Path

from . import proc as procs
from .makemkv.parse import ScanResult

log = logging.getLogger(__name__)

STRUCTURE_PATTERNS = [
    "VIDEO_TS/*.IFO",
    "BDMV/index.bdmv",
    "BDMV/MovieObject.bdmv",
    "BDMV/PLAYLIST/*.mpls",
    "BDMV/CLIPINF/*.clpi",
    "BDMV/META/DL/*",
    "BDMV/META/TN/*",
    "AACS/Unit_Key_RO.inf",
    "AACS/DUPLICATE/Unit_Key_RO.inf",
]
# Files whose sizes are listed (not copied) into SIZES.
SIZE_PATTERNS = ["BDMV/STREAM/*.m2ts", "VIDEO_TS/*"]
SIZES = "disc-files.tsv"


async def extract_structure(sevenzip: str, device: str, out_dir: Path, timeout: float = 120) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            sevenzip, "x", "-y", "-bd", f"-o{out_dir}", device, *STRUCTURE_PATTERNS,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as e:
        log.warning("7z not available: %s", e)
        return []
    if await procs.communicate(proc, timeout) is None:
        log.warning("7z timed out reading %s", device)
    sizes = await list_sizes(sevenzip, device, timeout)
    if sizes:
        (out_dir / SIZES).write_text("".join(f"{p}\t{n}\n" for p, n in sizes), encoding="utf-8")
    return sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file() and p.name != SIZES)


def copy_structure(root: Path, out_dir: Path) -> list[str]:
    """``extract_structure`` for a disc the system has mounted (Windows reads UDF 2.50
    itself, so the disc is a drive letter). It copies the same files and writes the same SIZES."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for pattern in STRUCTURE_PATTERNS:
        for src in root.glob(pattern):
            dst = out_dir / src.relative_to(root)
            try:
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
            except OSError as e:
                log.warning("could not read %s: %s", src, e)
    sizes = []
    for pattern in SIZE_PATTERNS:
        for p in sorted(root.glob(pattern)):
            try:
                if p.is_file():
                    sizes.append((p.relative_to(root).as_posix(), p.stat().st_size))
            except OSError:
                pass
    if sizes:
        (out_dir / SIZES).write_text("".join(f"{p}\t{n}\n" for p, n in sizes), encoding="utf-8")
    return sorted(p.relative_to(out_dir).as_posix() for p in out_dir.rglob("*") if p.is_file() and p.name != SIZES)


async def list_sizes(sevenzip: str, device: str, timeout: float = 120) -> list[tuple[str, int]]:
    """(path, size) of the disc's video files, from 7-Zip's listing (nothing is read but the directory)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            sevenzip, "l", "-slt", "-bd", device, *SIZE_PATTERNS,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return []
    done = await procs.communicate(proc, timeout)
    if done is None:
        return []
    return parse_listing(done[0].decode(errors="replace"))


def parse_listing(text: str) -> list[tuple[str, int]]:
    """Files and sizes from ``7z l -slt`` output (folders left out). The entries
    follow a "----------" line; the block before it describes the disc itself."""
    lines = text.splitlines()
    if "----------" not in lines:
        return []
    out, path, size, folder = [], None, None, False
    for line in [*lines[lines.index("----------") + 1:], ""]:
        if not line.strip():
            if path and size is not None and not folder:
                out.append((path.replace("\\", "/"), size))
            path, size, folder = None, None, False
        elif line.startswith("Path = "):
            path = line[7:].strip()
        elif line.startswith("Size = "):
            size = int(line[7:]) if line[7:].strip().isdigit() else None
        elif line.startswith("Folder = +") or line.startswith("Attributes = D"):
            folder = True
    return out


def read_sizes(structure_dir: Path) -> list[tuple[str, int]]:
    try:
        text = (structure_dir / SIZES).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        p, _, n = line.rpartition("\t")
        if p and n.isdigit():
            out.append((p, int(n)))
    return out


def clip_sizes(structure_dir: Path) -> list[tuple[str, int]]:
    """Blu-ray video file sizes worked out from the clip info files. Each
    ``BDMV/CLIPINF/NNNNN.clpi`` gives its ``BDMV/STREAM/NNNNN.m2ts``'s number of
    192-byte source packets. For a disc whose SIZES listing is missing."""
    out = []
    for f in sorted((structure_dir / "BDMV" / "CLIPINF").glob("*.clpi")):
        try:
            b = f.read_bytes()
        except OSError:
            return []
        # The ClipInfo block starts at byte 40; its packet count is 16 bytes in.
        if len(b) < 60 or b[:4] != b"HDMV":
            return []
        out.append((f"BDMV/STREAM/{f.stem}.m2ts", int.from_bytes(b[56:60], "big") * 192))
    return out


def bdmt_title(structure_dir: Path) -> str | None:
    """Disc title from BDMV/META/DL/bdmt_eng.xml (or the first language present)."""
    meta = structure_dir / "BDMV" / "META" / "DL"
    if not meta.is_dir():
        return None
    files = sorted(meta.glob("bdmt_*.xml"), key=lambda p: (p.name != "bdmt_eng.xml", p.name))
    for f in files:
        m = re.search(r"<di:name>(.*?)</di:name>", f.read_text(encoding="utf-8", errors="replace"), re.S)
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return None


# --- fingerprints ---------------------------------------------------------------
#
# content: hash of the navigation files themselves (like libdvdread's disc id).
#          Same pressing -> same hash, available seconds after insertion.
# quick:   volume label + UDF volume set UUID + size. Fallback when 7z found nothing.
# scan:    MakeMKV's title list (durations, sizes, sources).


def content_fingerprint(structure_dir: Path, files: list[str]) -> str | None:
    picked = [f for f in files if f.endswith((".IFO", ".bdmv", ".mpls"))]
    if not picked:
        return None
    h = hashlib.sha256()
    for rel in sorted(picked):
        h.update(rel.encode() + b"\0")
        h.update((structure_dir / rel).read_bytes())
    return "content:" + h.hexdigest()[:32]


def quick_fingerprint(label: str | None, uuid: str | None, size: int | None) -> str | None:
    if not (label or uuid):
        return None
    raw = f"{label or ''}|{uuid or ''}|{size or 0}"
    return "quick:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def scan_fingerprint(scan: ScanResult) -> str | None:
    if not scan.titles:
        return None
    parts = [scan.volume_name or "", scan.disc_name or ""]
    for t in scan.titles:
        parts.append(f"{t.duration_s}:{t.size_bytes}:{t.source_file or ''}:{t.segments_map or ''}")
    return "scan:" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]
