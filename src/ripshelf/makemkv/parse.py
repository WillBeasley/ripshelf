"""Parsers for ``makemkvcon -r`` (robot mode) output.

Line formats (from makemkvcon's apdefs.h / robot docs):
  MSG:code,flags,count,message,format,param0,...
  DRV:index,visibility,enabled,flags,drive name,disc name,device
  TCOUNT:count
  CINFO:id,code,value
  TINFO:title,id,code,value
  SINFO:title,stream,id,code,value
  PRGT:code,id,name      (total progress title)
  PRGC:code,id,name      (current progress title)
  PRGV:current,total,max
"""

from __future__ import annotations

import csv
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

# Attribute ids (subset of apdefs.h ap_ItemAttributeId)
A_TYPE = 1
A_NAME = 2
A_LANG_CODE = 3
A_LANG_NAME = 4
A_CODEC_ID = 5
A_CODEC_LONG = 7
A_CHAPTERS = 8
A_DURATION = 9
A_SIZE_BYTES = 11
A_CHANNELS = 14
A_SOURCE_FILE = 16
A_VIDEO_SIZE = 19
A_ASPECT = 20
A_FRAMERATE = 21
A_SEGMENTS_MAP = 26
A_OUTPUT_FILE = 27
A_TREE_INFO = 30
A_VOLUME_NAME = 32

# Message codes we act on
MSG_VERSION = 1005
MSG_LIBREDRIVE = 1011
MSG_VOLUME_KEY_UNKNOWN = 3303
MSG_AACS_VID_UNREADABLE = 3306  # host certificate revoked / no LibreDrive data
MSG_LIBREDRIVE_REQUIRED = 3346
MSG_FAILED_OPEN = 5010
MSG_KEY_INVALID = 5020
MSG_VERSION_TOO_OLD = 5021
MSG_COPY_COMPLETE = 5036
MSG_JAVA_MISSING = 3345  # "This disc requires Java runtime (JRE), but none was found"

PRG_SAVING = {5017, 5024}  # "Saving to MKV file", "Saving all titles to MKV files"
PRG_SCANNING_DEVICES = 5018


def split_fields(payload: str) -> list[str]:
    try:
        return next(csv.reader([payload], escapechar="\\", doublequote=False), [])
    except csv.Error:  # NUL bytes, unterminated quotes at EOF
        return []


def split_line(line: str) -> tuple[str, list[str]] | None:
    line = line.rstrip("\r\n")
    key, sep, rest = line.partition(":")
    if not sep or not key.isupper():
        return None
    return key, split_fields(rest)


def parse_duration(s: str) -> int:
    parts = [int(p) for p in s.split(":") if p.isdigit()]
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs


class Stream(BaseModel):
    index: int
    kind: str  # video | audio | subtitles | other
    codec_id: str | None = None
    codec: str | None = None
    lang: str | None = None
    lang_name: str | None = None
    resolution: str | None = None
    framerate: str | None = None
    aspect: str | None = None
    channels: int | None = None
    description: str | None = None


class Title(BaseModel):
    index: int
    name: str | None = None
    chapters: int = 0
    duration_s: int = 0
    size_bytes: int = 0
    source_file: str | None = None
    segments_map: str | None = None
    output_file: str | None = None
    streams: list[Stream] = []

    @property
    def video(self) -> Stream | None:
        return next((s for s in self.streams if s.kind == "video"), None)


class Message(BaseModel):
    code: int
    flags: int
    text: str
    params: list[str] = []


class DriveLine(BaseModel):
    index: int
    visibility: int
    flags: int
    drive_name: str
    disc_name: str
    device: str


class ScanResult(BaseModel):
    makemkv_version: str | None = None
    disc_type: str | None = None  # "DVD disc" / "Blu-ray disc"
    disc_name: str | None = None  # CINFO 2 (bdmt title on BD, label on DVD)
    volume_name: str | None = None  # CINFO 32
    libredrive: str | None = None
    # Every drive MakeMKV can see, latest line per device (not just the one scanned).
    drives: dict[str, DriveLine] = {}
    title_count: int | None = None
    titles: list[Title] = []
    messages: list[Message] = []

    @property
    def volume_key_unknown(self) -> bool:
        return any(m.code == MSG_VOLUME_KEY_UNKNOWN for m in self.messages)

    @property
    def cannot_decrypt(self) -> bool:
        """AACS failed in a way the drive's private data (LibreDrive) fixes."""
        return any(m.code in (MSG_LIBREDRIVE_REQUIRED, MSG_AACS_VID_UNREADABLE) for m in self.messages)

    @property
    def key_invalid(self) -> bool:
        return any(m.code in (MSG_KEY_INVALID, MSG_VERSION_TOO_OLD) for m in self.messages)

    @property
    def java_missing(self) -> bool:
        return any(m.code == MSG_JAVA_MISSING for m in self.messages)

    @property
    def failed_open(self) -> bool:
        return any(m.code == MSG_FAILED_OPEN for m in self.messages)

    @property
    def is_uhd(self) -> bool:
        return any(t.video and t.video.resolution == "3840x2160" for t in self.titles)

    @property
    def kind(self) -> str | None:
        if self.disc_type is None:
            return None
        if "Blu-ray" in self.disc_type:
            return "uhd" if self.is_uhd else "bluray"
        if "DVD" in self.disc_type and "HD-DVD" not in self.disc_type.upper():
            return "dvd"
        return "other"

    def drive_for(self, device: str | None = None) -> DriveLine | None:
        """The DRV line for ``device``; without one, only if MakeMKV saw a single drive."""
        if device:
            return self.drives.get(device)
        return next(iter(self.drives.values())) if len(self.drives) == 1 else None

    def disc_index(self, device: str, label: str | None) -> int | None:
        """MakeMKV's number for the drive holding this disc. By device, else the one drive
        whose disc has this label (a worker's device names differ), else the only drive."""
        drive = self.drive_for(device)
        if not drive and label:
            same = [d for d in self.drives.values() if d.disc_name == label]
            drive = same[0] if len(same) == 1 else None
        drive = drive or self.drive_for()
        return drive.index if drive else None

    @property
    def backup_done(self) -> bool:
        """MakeMKV said "Backup done." with no files failing their hash check."""
        return any(m.text.startswith("Backup done") and "failed" not in m.text for m in self.messages)

    def drive_serial(self, device: str | None = None) -> str | None:
        """Serial from the DRV drive name ("BD-RE PIONEER BD-RW  BDR-S11JX 1.53 SN0000000001")."""
        drive = self.drive_for(device)
        if not drive:
            return None
        parts = drive.drive_name.split()
        return parts[-1] if len(parts) >= 3 else None


_STREAM_KIND = {6201: "video", 6202: "audio", 6203: "subtitles"}


def parse_info(text: str) -> ScanResult:
    res = ScanResult()
    titles: dict[int, Title] = {}
    streams: dict[tuple[int, int], Stream] = {}

    def title(i: int) -> Title:
        if i not in titles:
            titles[i] = Title(index=i)
        return titles[i]

    for line in text.splitlines():
        parsed = split_line(line)
        if not parsed:
            continue
        key, f = parsed
        try:
            if key == "MSG":
                code = int(f[0])
                msg = Message(code=code, flags=int(f[1]), text=f[3], params=f[5:])
                res.messages.append(msg)
                if code == MSG_VERSION and msg.params:
                    m = re.search(r"v(\d+\.\d+\.\d+)", msg.params[0])
                    res.makemkv_version = m.group(1) if m else msg.params[0]
                elif code == MSG_LIBREDRIVE:
                    res.libredrive = msg.text
            elif key == "DRV":
                if int(f[1]) != 256 and f[6]:
                    res.drives[f[6]] = DriveLine(
                        index=int(f[0]), visibility=int(f[1]), flags=int(f[3]),
                        drive_name=f[4], disc_name=f[5], device=f[6],
                    )
            elif key == "TCOUNT":
                res.title_count = int(f[0])
            elif key == "CINFO":
                aid, value = int(f[0]), f[2]
                if aid == A_TYPE:
                    res.disc_type = value
                elif aid == A_NAME:
                    res.disc_name = value
                elif aid == A_VOLUME_NAME:
                    res.volume_name = value
            elif key == "TINFO":
                t, aid, value = title(int(f[0])), int(f[1]), f[3]
                if aid == A_NAME:
                    t.name = value
                elif aid == A_CHAPTERS:
                    t.chapters = int(value or 0)
                elif aid == A_DURATION:
                    t.duration_s = parse_duration(value)
                elif aid == A_SIZE_BYTES:
                    t.size_bytes = int(value or 0)
                elif aid == A_SOURCE_FILE:
                    t.source_file = value
                elif aid == A_SEGMENTS_MAP:
                    t.segments_map = value
                elif aid == A_OUTPUT_FILE:
                    t.output_file = value
            elif key == "SINFO":
                ti, si, aid, code, value = int(f[0]), int(f[1]), int(f[2]), int(f[3]), f[4]
                k = (ti, si)
                if k not in streams:
                    streams[k] = Stream(index=si, kind="other")
                    title(ti).streams.append(streams[k])
                s = streams[k]
                if aid == A_TYPE:
                    s.kind = _STREAM_KIND.get(code, "other")
                elif aid == A_CODEC_ID:
                    s.codec_id = value
                elif aid == A_CODEC_LONG:
                    s.codec = value
                elif aid == A_LANG_CODE:
                    s.lang = value
                elif aid == A_LANG_NAME:
                    s.lang_name = value
                elif aid == A_VIDEO_SIZE:
                    s.resolution = value
                elif aid == A_FRAMERATE:
                    s.framerate = value.split(" ")[0]
                elif aid == A_ASPECT:
                    s.aspect = value
                elif aid == A_CHANNELS:
                    s.channels = int(value) if value.isdigit() else None
                elif aid == A_TREE_INFO:
                    s.description = value
        except (IndexError, ValueError):
            continue
    res.titles = [titles[i] for i in sorted(titles)]
    return res


# --- main feature selection ---------------------------------------------------

DUPLICATE_TOLERANCE_S = 1


@dataclass
class MainFeature:
    title: Title
    duplicates: list[int]  # identical playlists of the same length (skipped)
    rivals: list[int]  # same length but different content, worth a human look


def choose_main_feature(scan: ScanResult) -> MainFeature | None:
    if not scan.titles:
        return None
    longest = max(t.duration_s for t in scan.titles)
    same = [t for t in scan.titles if longest - t.duration_s <= DUPLICATE_TOLERANCE_S]
    # Prefer real playlists over raw m2ts, then lowest index.
    same.sort(key=lambda t: ((t.source_file or "").endswith(".m2ts"), t.index))
    main = same[0]
    dups, rivals = [], []
    for t in same[1:]:
        if t.size_bytes == main.size_bytes or _same_content(t, main, scan.kind in ("bluray", "uhd")):
            dups.append(t.index)
        else:
            rivals.append(t.index)
    return MainFeature(main, dups, rivals)


def _same_content(a: Title, b: Title, by_segments: bool = True) -> bool:
    # Blu-ray segment maps list clip numbers, shared across playlists. DVD maps
    # list cells local to each title ("1,2" for every episode), so they are not comparable.
    if by_segments and a.segments_map and a.segments_map == b.segments_map:
        return True
    return bool(a.size_bytes and abs(a.size_bytes - b.size_bytes) / a.size_bytes < 0.001)


# --- progress -------------------------------------------------------------------


class RipProgress(BaseModel):
    phase: str = "starting"
    fraction: float = 0.0
    bytes_done: int = 0
    bytes_total: int = 0
    bytes_per_s: float | None = None
    eta_s: float | None = None


@dataclass
class ProgressTracker:
    """Turns PRGT/PRGC/PRGV lines into a percentage, bytes, speed and ETA.

    PRGV is ``current,total,max``, where ``total/max`` is overall progress of the
    current top-level operation. Only the "Saving" operation counts as the rip;
    the earlier disc open/scan also emits PRGV lines. For a backup, whatever comes
    after them ("Copying all files") does, because its code isn't in our logs.
    """

    bytes_total: int
    backup: bool = False
    window_s: float = 30.0
    clock: Callable[[], float] = time.monotonic
    phase: str = "starting"
    saving: bool = False
    fraction: float = 0.0
    samples: deque = field(default_factory=deque)

    def feed(self, line: str) -> bool:
        """Returns True when the visible progress changed."""
        parsed = split_line(line)
        if not parsed:
            return False
        key, f = parsed
        try:
            if key == "PRGT":
                code = int(f[0])
                if self.backup:  # 3xxx is opening the disc
                    self.saving = not (3000 <= code < 4000 or code == PRG_SCANNING_DEVICES)
                else:
                    self.saving = code in PRG_SAVING
                self.phase = f[2]
                return True
            if key == "PRGC":
                if self.saving:
                    self.phase = f[2]
                return False
            if key == "PRGV":
                total, mx = int(f[1]), int(f[2])
                if not self.saving or mx <= 0:
                    return False
                frac = min(1.0, total / mx)
                if frac == self.fraction:
                    return False
                self.fraction = frac
                now = self.clock()
                self.samples.append((now, frac))
                while self.samples and now - self.samples[0][0] > self.window_s:
                    self.samples.popleft()
                return True
        except (IndexError, ValueError):
            return False
        return False

    def snapshot(self) -> RipProgress:
        rate = None
        eta = None
        if len(self.samples) >= 2:
            (t0, f0), (t1, f1) = self.samples[0], self.samples[-1]
            if t1 > t0 and f1 > f0:
                rate = (f1 - f0) * self.bytes_total / (t1 - t0)
                eta = (1 - f1) * self.bytes_total / rate if rate else None
        return RipProgress(
            phase=self.phase,
            fraction=self.fraction,
            bytes_done=int(self.fraction * self.bytes_total),
            bytes_total=self.bytes_total,
            bytes_per_s=rate,
            eta_s=eta,
        )
