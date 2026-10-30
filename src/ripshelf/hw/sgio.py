"""Minimal SCSI pass-through (SG_IO) for drive identity and capabilities.

Only read-only commands are used, INQUIRY (standard + unit serial VPD page) and
GET CONFIGURATION (whether the drive reads DVD / BD, and its serial number).
The commands and their parsing are shared with Windows (``hw.windows``), which
sends them through its own pass-through instead of SG_IO.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from dataclasses import dataclass

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

SG_IO = 0x2285
SG_DXFER_FROM_DEV = -3

# MMC feature codes
FEATURE_DVD_READ = 0x001F
FEATURE_BD_READ = 0x0040
FEATURE_SERIAL = 0x0108  # Logical Unit Serial Number


class SgIoHdr(ctypes.Structure):
    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


class ScsiError(OSError):
    pass


def _command(fd: int, cdb: bytes, length: int, timeout_ms: int = 5000) -> bytes:
    buf = ctypes.create_string_buffer(length)
    cmd = ctypes.create_string_buffer(cdb, len(cdb))
    sense = ctypes.create_string_buffer(32)
    hdr = SgIoHdr(
        interface_id=ord("S"),
        dxfer_direction=SG_DXFER_FROM_DEV,
        cmd_len=len(cdb),
        mx_sb_len=len(sense),
        dxfer_len=length,
        dxferp=ctypes.cast(buf, ctypes.c_void_p),
        cmdp=ctypes.cast(cmd, ctypes.c_void_p),
        sbp=ctypes.cast(sense, ctypes.c_void_p),
        timeout=timeout_ms,
    )
    fcntl.ioctl(fd, SG_IO, hdr)
    if hdr.status != 0 or hdr.host_status != 0 or (hdr.driver_status & 0x0F) != 0:
        raise ScsiError(f"SCSI command {cdb[0]:#04x} failed (status={hdr.status})")
    return buf.raw[: length - hdr.resid]


@dataclass
class DriveIdentity:
    vendor: str
    model: str
    revision: str
    serial: str | None
    reads_dvd: bool | None
    reads_bd: bool | None


# Sends a CDB and returns the data read (``length`` bytes at most); OSError on failure.
Send = Callable[[bytes, int], bytes]


def _features(send: Send) -> set[int]:
    # GET CONFIGURATION, RT=0 (all features), allocation 4 KiB
    cdb = bytes([0x46, 0x00, 0x00, 0x00, 0, 0, 0, 0x10, 0x00, 0x00])
    data = send(cdb, 4096)
    if len(data) < 8:
        return set()
    end = min(len(data), 4 + int.from_bytes(data[0:4], "big"))
    pos, out = 8, set()
    while pos + 4 <= end:
        code = int.from_bytes(data[pos : pos + 2], "big")
        out.add(code)
        pos += 4 + data[pos + 3]
    return out


def _feature_serial(send: Send) -> str | None:
    """The serial number from MMC's Logical Unit Serial Number feature. Optical drives
    usually leave INQUIRY's serial page empty but report this (it's what MakeMKV shows)."""
    # GET CONFIGURATION, RT=2 (just this feature)
    cdb = bytes([0x46, 0x02, FEATURE_SERIAL >> 8, FEATURE_SERIAL & 0xFF, 0, 0, 0, 0x01, 0x00, 0x00])
    return parse_feature_serial(send(cdb, 256))


def parse_feature_serial(data: bytes) -> str | None:
    if len(data) < 12 or int.from_bytes(data[8:10], "big") != FEATURE_SERIAL:
        return None
    return data[12 : 12 + data[11]].decode("ascii", "replace").strip("\0 ") or None


def identify(sg_path: str) -> DriveIdentity:
    fd = os.open(sg_path, os.O_RDWR | os.O_NONBLOCK)
    try:
        return identify_with(lambda cdb, length: _command(fd, cdb, length))
    finally:
        os.close(fd)


def identify_with(send: Send) -> DriveIdentity:
    std = send(bytes([0x12, 0, 0, 0, 96, 0]), 96)
    vendor = std[8:16].decode("ascii", "replace").strip()
    model = std[16:32].decode("ascii", "replace").strip()
    rev = std[32:36].decode("ascii", "replace").strip()
    serial = None
    try:
        serial = _feature_serial(send)
    except OSError:
        pass
    try:
        vpd = send(bytes([0x12, 0x01, 0x80, 0, 255, 0]), 255)
        if not serial and len(vpd) >= 4 and vpd[1] == 0x80:
            serial = vpd[4 : 4 + vpd[3]].decode("ascii", "replace").strip() or None
    except OSError:
        pass
    reads_dvd = reads_bd = None
    try:
        feats = _features(send)
        reads_dvd = FEATURE_DVD_READ in feats
        reads_bd = FEATURE_BD_READ in feats
    except OSError:
        pass
    return DriveIdentity(vendor, model, rev, serial, reads_dvd, reads_bd)
