"""Plain-word causes for a failed MakeMKV scan or rip, read from its messages.

MakeMKV reports drive and disc trouble as free text (``Error 'Scsi error - MEDIUM ERROR:...' occurred while
reading '/BDMV/STREAM/01064.m2ts' at offset '0'``), and the last line it prints is often only a summary
("0 titles saved, 1 failed"). This finds the line that explains the failure and says it in words, with what
to try.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .parse import Message

MSG_READ_ERROR = 2003  # Error '%1' occurred while reading '%2' at offset '%3'


@dataclass
class Diagnosis:
    code: str
    cause: str  # what went wrong, one sentence
    hint: str  # what to try
    detail: str  # MakeMKV's own words


@dataclass
class _Rule:
    code: str
    pattern: re.Pattern[str]
    cause: str  # {where} is the file, or "part of the disc"
    hint: str


_DISC = "Clean the disc (wipe straight out from the centre) and retry, or try it in another drive."
_RETRIES = (" Raising MakeMKV's read retry count (io_ErrorRetryCount in data/makemkv/settings.conf) sometimes "
            "gets past a marginal spot.")

# Most specific first; the first rule that any message matches wins.
# Wording and fixes partly from the MakeMKV forum's read-error FAQ (forum.makemkv.com, t=15055).
_RULES = [
    _Rule("disc_toc", re.compile(r"UNABLE TO RECOVER TABLE-OF-CONTENTS", re.I),
          "The drive couldn't read the disc's table of contents.",
          "Check the disc for a crack around the centre hole; a cracked disc needs replacing. Otherwise clean it "
          "and retry, or try another drive."),
    _Rule("aacs_too_new", re.compile(r"ILLEGAL MODE FOR THIS TRACK", re.I),
          "The drive refused to read {where}: usually the disc uses a newer AACS version than this MakeMKV "
          "supports.",
          "Update MakeMKV (rebuild the image) and retry."),
    _Rule("disc_format", re.compile(r"INCOMPATIBLE FORMAT", re.I),
          "The drive can't read this kind of disc.",
          "Try a drive that supports this format (a UHD disc needs a UHD-capable drive)."),
    _Rule("disc_unreadable", re.compile(r"MEDIUM ERROR|L-EC|UNRECOVERED READ|UNCORRECTABLE|RETRIES EXHAUSTED|"
                                        r"DEVICE_DATA_ERROR", re.I),
          "The drive couldn't read {where}: the disc is probably scratched, dirty or damaged.",
          _DISC + " If it fails at the same place every time (in any drive), that part of the disc is damaged or "
          "badly pressed and the disc needs replacing." + _RETRIES),
    _Rule("drive_hardware", re.compile(r"HARDWARE ERROR", re.I),
          "The drive reported a hardware fault while reading {where}.",
          "Eject and retry. If it keeps happening, power-cycle the drive and check its cable, or use another "
          "drive."),
    _Rule("drive_timeout", re.compile(r"timed? ?out|timeout", re.I),
          "The drive stopped answering while reading {where}.",
          "Eject and retry. A drive that keeps timing out may need a power-cycle or a different cable/USB port."),
    _Rule("disc_not_ready", re.compile(r"NOT READY|MEDIUM NOT PRESENT|UNIT ATTENTION", re.I),
          "The drive said the disc wasn't ready or had changed while reading {where}.",
          "Make sure the tray is closed and the disc is seated, wait a few seconds, then retry. A USB drive "
          "that does this mid-read may be short of power, so use one with its own power supply."),
    _Rule("hash_failed", re.compile(r"hash check failed", re.I),
          "The data read from {where} didn't match the disc's checksums. That's a bad read or wrong decryption keys.",
          _DISC + " If every disc does this, update MakeMKV and its KEYDB/LibreDrive data."),
    _Rule("disc_corrupt", re.compile(r"is corrupt or invalid", re.I),
          "MakeMKV found corrupt data in {where}.",
          _DISC + " Some DVDs have deliberately broken titles; ripping a different title may work."),
    _Rule("no_space", re.compile(r"no space left", re.I),
          "There is no space left on the scratch disk.",
          "Free space on the scratch disk (Drives page shows free space), then retry."),
    _Rule("io_error", re.compile(r"Input/output error|\bI/O error", re.I),
          "The drive returned a read error on {where}.",
          _DISC),
    _Rule("disc_auth", re.compile(r"SCRAMBLED SECTOR|AUTHENTICATION", re.I),
          "The drive refused to read the encrypted {where}: the disc couldn't be authenticated, often a "
          "region mismatch.",
          "Check the drive's region matches the disc (or that the drive runs LibreDrive firmware), then retry."),
    _Rule("open_failed", re.compile(r"failed to open disc|can't open|cannot open", re.I),
          "MakeMKV couldn't open the disc.",
          "Eject and reinsert it, then retry. If the drive itself isn't listed, check it's connected and passed "
          "into the container."),
]


def _where(m: Message) -> tuple[str, str | None]:
    """The disc file an error was on, for the cause and for grouping (None when it names none)."""
    if m.code == MSG_READ_ERROR and len(m.params) >= 2 and m.params[1].startswith("/"):
        return m.params[1], m.params[1]
    hit = re.search(r"'(/[^']+)'", m.text)
    return (hit.group(1), hit.group(1)) if hit else ("part of the disc", None)


def _relevant(m: Message) -> bool:
    # MakeMKV probes every drive when it starts, and the probe of a DVD drive with no disc authenticated
    # fails with an error "while reading '<drive name>'". Those are about other drives, not this disc.
    if m.code == MSG_READ_ERROR and len(m.params) >= 2 and not m.params[1].startswith("/"):
        return False
    return True


def diagnose(messages: list[Message]) -> Diagnosis | None:
    msgs = [m for m in messages if _relevant(m)]
    for rule in _RULES:
        hits = [m for m in msgs if rule.pattern.search(m.text)]
        if not hits:
            continue
        # The file most of the errors were on, so a burst of retries reads as one place.
        files = Counter(_where(m)[1] for m in hits)
        top = files.most_common(1)[0][0]
        first = next(m for m in hits if _where(m)[1] == top)
        cause = rule.cause.format(where=_where(first)[0])
        if len(hits) > 1:
            cause += f" MakeMKV hit {len(hits)} errors like this."
        return Diagnosis(rule.code, cause, rule.hint, first.text)
    return None
