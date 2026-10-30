"""Optical drives on Windows, with drive letters, SCSI pass-through (SPTI) and volume info.

The same job as ``hw.drives.SysfsDrives`` on Linux, with plain Win32 calls through
ctypes (no extra packages), none of which need administrator rights:

  * drives are the letters whose type is CD-ROM (a mounted ISO image is one too, and it
    starts disabled, like a VM's emulated drive);
  * identity and capabilities come from the same INQUIRY / GET CONFIGURATION
    commands as on Linux (``hw.sgio``), so a drive gets the same id on both;
  * tray and disc state from GET EVENT STATUS NOTIFICATION and TEST UNIT READY;
  * eject and close as SCSI commands. IOCTL_STORAGE_EJECT_MEDIA fails on drives that
    report their medium locked, so the lock is cleared first, as Linux's eject does;
  * the volume label from GetVolumeInformationW. Windows mounts UDF 2.50 itself, so
    the disc's structure files are copied from the drive letter (no 7-Zip).
"""

from __future__ import annotations

import asyncio
import ctypes
import logging
from ctypes import wintypes
from pathlib import Path

from ..models import DriveStatus
from . import sgio
from .drives import DiscIdentity, DriveHw, capabilities_for, drive_id, is_virtual

log = logging.getLogger(__name__)

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ_WRITE = 0x3
OPEN_EXISTING = 3
DRIVE_CDROM = 5

IOCTL_STORAGE_QUERY_PROPERTY = 0x2D1400
IOCTL_DISK_GET_LENGTH_INFO = 0x7405C
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014
SCSI_IOCTL_DATA_IN, SCSI_IOCTL_DATA_UNSPECIFIED = 1, 2
BUS_TYPE_FILE_BACKED_VIRTUAL = 15  # a mounted ISO image, not a drive

# SCSI commands without data
TEST_UNIT_READY = bytes(6)
ALLOW_MEDIUM_REMOVAL = bytes([0x1E, 0, 0, 0, 0, 0])
START_STOP_EJECT = bytes([0x1B, 0, 0, 0, 0x02, 0])
START_STOP_LOAD = bytes([0x1B, 0, 0, 0, 0x03, 0])
# GET EVENT STATUS NOTIFICATION, polled, media class
GESN_MEDIA = bytes([0x4A, 0x01, 0, 0, 0x10, 0, 0, 0, 8, 0])


class ScsiStatusError(OSError):
    """The drive answered with CHECK CONDITION; ``sense`` is its (key, asc, ascq)."""

    def __init__(self, status: int, sense: tuple[int, int, int]):
        super().__init__(f"SCSI status {status}, sense {sense[0]:x}/{sense[1]:02x}/{sense[2]:02x}")
        self.sense = sense


class _SPTD(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_ushort), ("ScsiStatus", ctypes.c_ubyte), ("PathId", ctypes.c_ubyte),
                ("TargetId", ctypes.c_ubyte), ("Lun", ctypes.c_ubyte), ("CdbLength", ctypes.c_ubyte),
                ("SenseInfoLength", ctypes.c_ubyte), ("DataIn", ctypes.c_ubyte),
                ("DataTransferLength", ctypes.c_ulong), ("TimeOutValue", ctypes.c_ulong),
                ("DataBuffer", ctypes.c_void_p), ("SenseInfoOffset", ctypes.c_ulong), ("Cdb", ctypes.c_ubyte * 16)]


class _SPTDWithSense(ctypes.Structure):
    _fields_ = [("sptd", _SPTD), ("filler", ctypes.c_ulong), ("sense", ctypes.c_ubyte * 32)]


def parse_device_descriptor(d: bytes) -> tuple[str, str, str, int]:
    """(vendor, product, revision, bus type) from a STORAGE_DEVICE_DESCRIPTOR."""

    def text(offset: int) -> str:
        if not offset or offset >= len(d):
            return ""
        end = d.find(b"\0", offset)
        return d[offset: end if end >= 0 else len(d)].decode("ascii", "replace").strip()

    def u32(o: int) -> int:
        return int.from_bytes(d[o:o + 4], "little") if len(d) >= o + 4 else 0

    return text(u32(12)), text(u32(16)), text(u32(20)), u32(28)


def status_from(gesn: bytes | None, tur_sense: tuple[int, int, int] | None, tur_ok: bool) -> DriveStatus:
    """The drive's status from its media event (if it answered) and TEST UNIT READY."""
    if gesn is not None and len(gesn) >= 6 and gesn[5] & 0x01:
        return DriveStatus.tray_open
    if tur_ok:
        return DriveStatus.disc
    if tur_sense:
        key, asc, ascq = tur_sense
        if asc == 0x3A:  # medium not present (ascq 2 is tray open)
            return DriveStatus.tray_open if ascq == 0x02 else DriveStatus.empty
    if gesn is not None and len(gesn) >= 6 and not gesn[5] & 0x02:
        return DriveStatus.empty
    return DriveStatus.not_ready  # becoming ready, or a state we don't know


class _Win32:
    def __init__(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                        wintypes.LPVOID]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k32.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                              ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
        k32.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        self.k32 = k32

    def letters(self) -> list[str]:
        mask = self.k32.GetLogicalDrives()
        out = []
        for i in range(26):
            letter = f"{chr(ord('A') + i)}:"
            if mask & (1 << i) and self.k32.GetDriveTypeW(letter + "\\") == DRIVE_CDROM:
                out.append(letter)
        return out

    def open(self, device: str) -> int:
        h = self.k32.CreateFileW(f"\\\\.\\{device}", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ_WRITE,
                                 None, OPEN_EXISTING, 0, None)
        if h in (None, wintypes.HANDLE(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        return h

    def close(self, h: int) -> None:
        self.k32.CloseHandle(h)

    def ioctl(self, h: int, code: int, inbuf: bytes = b"", outlen: int = 0) -> bytes:
        inp = ctypes.create_string_buffer(inbuf, len(inbuf)) if inbuf else None
        out = ctypes.create_string_buffer(outlen) if outlen else None
        n = wintypes.DWORD()
        if not self.k32.DeviceIoControl(h, code, inp, len(inbuf), out, outlen, ctypes.byref(n), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return out.raw[:n.value] if out else b""

    def scsi(self, h: int, cdb: bytes, length: int = 0, timeout_s: int = 10) -> bytes:
        """Send a CDB; returns the data read. ScsiStatusError on CHECK CONDITION."""
        buf = ctypes.create_string_buffer(max(length, 1))
        s = _SPTDWithSense()
        s.sptd.Length = ctypes.sizeof(_SPTD)
        s.sptd.CdbLength = len(cdb)
        s.sptd.SenseInfoLength = len(s.sense)
        s.sptd.DataIn = SCSI_IOCTL_DATA_IN if length else SCSI_IOCTL_DATA_UNSPECIFIED
        s.sptd.DataTransferLength = length
        s.sptd.TimeOutValue = timeout_s
        s.sptd.DataBuffer = ctypes.cast(buf, ctypes.c_void_p) if length else None
        s.sptd.SenseInfoOffset = _SPTDWithSense.sense.offset
        for i, b in enumerate(cdb):
            s.sptd.Cdb[i] = b
        n = wintypes.DWORD()
        if not self.k32.DeviceIoControl(h, IOCTL_SCSI_PASS_THROUGH_DIRECT, ctypes.byref(s), ctypes.sizeof(s),
                                        ctypes.byref(s), ctypes.sizeof(s), ctypes.byref(n), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if s.sptd.ScsiStatus:
            sense = bytes(s.sense)
            raise ScsiStatusError(s.sptd.ScsiStatus, (sense[2] & 0x0F, sense[12], sense[13]))
        return buf.raw[:s.sptd.DataTransferLength] if length else b""

    def volume(self, device: str) -> tuple[str | None, str | None, str | None]:
        """(label, volume serial, filesystem name) of the disc in the drive."""
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        serial = wintypes.DWORD()
        if not self.k32.GetVolumeInformationW(device + "\\", label, 261, ctypes.byref(serial), None, None, fs, 261):
            return None, None, None
        return label.value or None, f"{serial.value >> 16:04X}-{serial.value & 0xFFFF:04X}", fs.value or None

    def dos_device(self, device: str) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        return buf.value if self.k32.QueryDosDeviceW(device, buf, 1024) else device


class WindowsDrives:
    """Real hardware on Windows. ``device`` is the drive letter ("D:"), which is also
    what MakeMKV takes (``dev:D:``) and shows in its drive list."""

    def __init__(self) -> None:
        self.w = _Win32()

    def _with(self, device: str, fn):
        h = self.w.open(device)
        try:
            return fn(h)
        finally:
            self.w.close(h)

    def discover(self, uhd_models: list[str]) -> list[DriveHw]:
        out = []
        for letter in self.w.letters():
            try:
                out.append(self._with(letter, lambda h, letter=letter: self._describe(letter, h, uhd_models)))
            except OSError as e:
                log.info("%s: not usable as an optical drive: %s", letter, e)
        return [d for d in out if d]

    def _describe(self, letter: str, h: int, uhd_models: list[str]) -> DriveHw | None:
        vendor, model, rev, bus = parse_device_descriptor(
            self.w.ioctl(h, IOCTL_STORAGE_QUERY_PROPERTY, bytes(12), 1024))
        ident = None
        try:
            ident = sgio.identify_with(lambda cdb, length: self.w.scsi(h, cdb, length))
        except OSError as e:
            log.info("%s: SCSI identify failed: %s", letter, e)
        if ident:
            vendor, model, rev = ident.vendor or vendor, ident.model or model, ident.revision or rev
        serial = ident.serial if ident else None
        # Only for a drive without a serial, the kernel's name for it (\Device\CdRom0).
        port = self.w.dos_device(letter)
        did = drive_id(vendor, model, serial, port)
        if not serial:
            log.warning("%s (%s %s): no serial number readable; its id %s depends on its device number",
                        letter, vendor, model, did)
        return DriveHw(id=did, device=letter, sg_device=None, vendor=vendor, model=model, revision=rev,
                       serial=serial, capabilities=capabilities_for(vendor, model, ident, uhd_models), port=port,
                       virtual=bus == BUS_TYPE_FILE_BACKED_VIRTUAL or is_virtual(vendor, model))

    def status(self, device: str) -> DriveStatus:
        try:
            return self._with(device, self._status)
        except OSError:
            return DriveStatus.error

    def _status(self, h: int) -> DriveStatus:
        try:
            gesn = self.w.scsi(h, GESN_MEDIA, 8)
        except OSError:
            gesn = None
        try:
            self.w.scsi(h, TEST_UNIT_READY)
            return status_from(gesn, None, True)
        except ScsiStatusError as e:
            return status_from(gesn, e.sense, False)

    def eject(self, device: str) -> None:
        def do(h: int) -> None:
            try:
                self.w.scsi(h, ALLOW_MEDIUM_REMOVAL)
            except OSError:
                pass
            self.w.scsi(h, START_STOP_EJECT, timeout_s=30)

        self._with(device, do)

    def close_tray(self, device: str) -> None:
        self._with(device, lambda h: self.w.scsi(h, START_STOP_LOAD, timeout_s=30))

    async def disc_identity(self, device: str) -> DiscIdentity:
        def read() -> DiscIdentity:
            label, serial, fs = self.w.volume(device)
            try:
                size = int.from_bytes(self._with(device, lambda h: self.w.ioctl(h, IOCTL_DISK_GET_LENGTH_INFO, b"", 8)),
                                      "little") or None
            except OSError:
                size = None
            fs_type = {"CDFS": "iso9660"}.get(fs or "", (fs or "").lower() or None)
            return DiscIdentity(label=label, uuid=serial, fs_type=fs_type, fs_version=None, size_bytes=size)

        return await asyncio.to_thread(read)

    async def extract_structure(self, device: str, out_dir: Path) -> list[str]:
        from ..discfs import copy_structure

        return await asyncio.to_thread(copy_structure, Path(device + "\\"), out_dir)
