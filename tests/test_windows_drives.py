"""The Windows drive backend against a simulated Win32 layer (runs anywhere)."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

import pytest

from ripshelf import discfs
from ripshelf.hw import drives, windows
from ripshelf.models import Capability, DriveStatus
from tests.test_sgio import features, inquiry


def descriptor(vendor=b"PIONEER", product=b"BD-RW  BDR-S11JX", rev=b"1.53", bus=10) -> bytes:
    head = bytearray(36)
    strings = b""
    offsets = []
    for s in (vendor, product, rev):
        offsets.append(36 + len(strings))
        strings += s + b"\0"
    for o, v in zip((12, 16, 20), offsets, strict=True):
        head[o:o + 4] = v.to_bytes(4, "little")
    head[28:32] = bus.to_bytes(4, "little")
    return bytes(head) + strings


def serial_feature(serial: bytes) -> bytes:
    return bytes(8) + windows.sgio.FEATURE_SERIAL.to_bytes(2, "big") + bytes([3, len(serial)]) + serial


class FakeWin32:
    """Drive letters with canned SCSI replies; records the commands sent."""

    def __init__(self):
        self.drives: dict[str, dict] = {}
        self.sent: list[tuple[str, bytes]] = []
        self.volumes: dict[str, tuple] = {}

    def letters(self):
        return list(self.drives)

    def open(self, device):
        if device not in self.drives:
            raise OSError(2, "not found")
        return device

    def close(self, h):
        pass

    def ioctl(self, h, code, inbuf=b"", outlen=0):
        if code == windows.IOCTL_STORAGE_QUERY_PROPERTY:
            return self.drives[h]["descriptor"]
        if code == windows.IOCTL_DISK_GET_LENGTH_INFO:
            return (100_100_014_080).to_bytes(8, "little")
        raise OSError(1, "unsupported")

    def scsi(self, h, cdb, length=0, timeout_s=10):
        self.sent.append((h, bytes(cdb)))
        r = self.drives[h]["replies"].get((cdb[0], cdb[1]))
        if isinstance(r, Exception):
            raise r
        if r is None:
            raise OSError(1, "no reply")
        return r

    def volume(self, device):
        return self.volumes.get(device, (None, None, None))

    def dos_device(self, device):
        return "\\Device\\CdRom0"


@pytest.fixture
def fake():
    w = FakeWin32()
    w.drives["G:"] = {"descriptor": descriptor(), "replies": {
        (0x12, 0): inquiry(),
        (0x46, 2): serial_feature(b"SN0000000001"),
        (0x46, 0): features(windows.sgio.FEATURE_DVD_READ, windows.sgio.FEATURE_BD_READ),
    }}
    d = windows.WindowsDrives.__new__(windows.WindowsDrives)
    d.w = w
    return d


def test_discover_matches_the_linux_drive_id(fake):
    [hw] = fake.discover(["PIONEER BD-RW  BDR-S11JX"])
    assert hw.device == "G:" and hw.serial == "SN0000000001"
    # Same vendor, model and serial as on Linux, so the same id, so names and settings carry over.
    assert hw.id == drives.drive_id("PIONEER", "BD-RW  BDR-S11JX", "SN0000000001", "/sys/devices/whatever")
    assert hw.capabilities == [Capability.dvd, Capability.bluray, Capability.uhd]


def test_discover_marks_virtual_drives_and_survives_scsi_failure(fake):
    fake.w.drives["E:"] = {"descriptor": descriptor(b"Msft", b"Virtual DVD-ROM", b"1.0", bus=15), "replies": {}}
    fake.w.drives["F:"] = {"descriptor": descriptor(b"HL-DT-ST", b"BD-RE BH16NS40", b"1.02"), "replies": {}}
    fake.w.drives["H:"] = {"descriptor": descriptor(b"QEMU", b"QEMU DVD-ROM", b"2.5+"), "replies": {}}
    found = {d.device: d for d in fake.discover([])}
    assert set(found) == {"G:", "F:", "E:", "H:"}
    assert found["E:"].virtual and found["H:"].virtual and not found["F:"].virtual and not found["G:"].virtual
    assert found["F:"].serial is None and found["F:"].model == "BD-RE BH16NS40"
    assert found["F:"].capabilities == [Capability.dvd, Capability.bluray]  # "BD" in the name


@pytest.mark.parametrize(("gesn", "sense", "ok", "want"), [
    ("0006045e00020000", None, True, DriveStatus.disc),
    ("0006045e00010000", (2, 0x3A, 0x02), False, DriveStatus.tray_open),  # door open
    (None, (2, 0x3A, 0x02), False, DriveStatus.tray_open),
    ("0006045e00000000", (2, 0x3A, 0x01), False, DriveStatus.empty),
    ("0006045e00000000", (2, 0x04, 0x01), False, DriveStatus.empty),  # nothing in it
    ("0006045e00020000", (2, 0x04, 0x01), False, DriveStatus.not_ready),  # disc spinning up
    (None, None, False, DriveStatus.not_ready),
])
def test_status_from(gesn, sense, ok, want):
    assert windows.status_from(bytes.fromhex(gesn) if gesn else None, sense, ok) == want


def test_status_and_tray_commands(fake):
    replies = fake.w.drives["G:"]["replies"]
    replies[(0x4A, 1)] = bytes.fromhex("0006045e00020000")
    replies[(0x00, 0)] = b""
    assert fake.status("G:") == DriveStatus.disc
    replies[(0x00, 0)] = windows.ScsiStatusError(2, (2, 0x3A, 0x02))
    replies[(0x4A, 1)] = OSError(1, "no GESN")
    assert fake.status("G:") == DriveStatus.tray_open
    assert fake.status("Z:") == DriveStatus.error

    replies[(0x1E, 0)] = b""
    replies[(0x1B, 0)] = b""
    fake.w.sent.clear()
    fake.eject("G:")
    assert [c for _, c in fake.w.sent] == [windows.ALLOW_MEDIUM_REMOVAL, windows.START_STOP_EJECT]
    fake.w.sent.clear()
    fake.close_tray("G:")
    assert [c for _, c in fake.w.sent] == [windows.START_STOP_LOAD]


async def test_disc_identity(fake):
    fake.w.volumes["G:"] = ("THE_BRASS_HERON", "9243-854A", "UDF")
    ident = await fake.disc_identity("G:")
    assert (ident.label, ident.uuid, ident.fs_type, ident.size_bytes) == (
        "THE_BRASS_HERON", "9243-854A", "udf", 100_100_014_080)


def test_parse_device_descriptor():
    assert windows.parse_device_descriptor(descriptor()) == ("PIONEER", "BD-RW  BDR-S11JX", "1.53", 10)
    assert windows.parse_device_descriptor(b"") == ("", "", "", 0)


def test_copy_structure_from_a_mounted_disc(tmp_path):
    disc = tmp_path / "disc"
    for rel, data in {"BDMV/index.bdmv": b"INDX", "BDMV/PLAYLIST/00800.mpls": b"MPLS",
                      "BDMV/STREAM/00001.m2ts": b"x" * 10, "AACS/Unit_Key_RO.inf": b"key",
                      "BDMV/JAR/00000.jar": b"no"}.items():
        (disc / rel).parent.mkdir(parents=True, exist_ok=True)
        (disc / rel).write_bytes(data)
    out = tmp_path / "out"
    files = discfs.copy_structure(disc, out)
    assert files == ["AACS/Unit_Key_RO.inf", "BDMV/PLAYLIST/00800.mpls", "BDMV/index.bdmv"]
    assert discfs.read_sizes(out) == [("BDMV/STREAM/00001.m2ts", 10)]


async def test_disc_identity_of_an_audio_cd(fake):
    from tests.test_sgio import toc

    assert not (await fake.disc_identity("G:")).audio_cd  # no TOC reply
    fake.w.drives["G:"]["replies"][(0x43, 0)] = toc((1, 0), (2, 0))
    assert (await fake.disc_identity("G:")).audio_cd
    fake.w.drives["G:"]["replies"][(0x43, 0)] = toc((1, 0x04))
    assert not (await fake.disc_identity("G:")).audio_cd


def test_a_letter_that_cannot_be_opened_is_skipped(fake, caplog):
    caplog.set_level(logging.INFO, "ripshelf.hw.windows")
    fake.w.letters = lambda: ["E:", "G:"]  # E: is listed as a CD-ROM but won't open
    assert [d.device for d in fake.discover([])] == ["G:"]
    assert "E:: not usable as an optical drive" in caplog.text


def test_a_drive_without_a_serial_gets_an_id_from_its_device_name(fake, caplog):
    fake.w.drives["F:"] = {"descriptor": descriptor(b"HL-DT-ST", b"BD-RE BH16NS40", b"1.02"), "replies": {}}
    found = {d.device: d for d in fake.discover([])}
    assert found["F:"].port == "\\Device\\CdRom0"
    assert found["F:"].id == drives.drive_id("HL-DT-ST", "BD-RE BH16NS40", None, "\\Device\\CdRom0")
    assert "no serial number readable" in caplog.text


def test_eject_goes_ahead_when_the_lock_cannot_be_cleared(fake):
    replies = fake.w.drives["G:"]["replies"]
    replies[(0x1E, 0)] = windows.ScsiStatusError(2, (5, 0x20, 0))  # ALLOW MEDIUM REMOVAL refused
    replies[(0x1B, 0)] = b""
    fake.eject("G:")
    assert fake.w.sent[-1] == ("G:", windows.START_STOP_EJECT)
    with pytest.raises(OSError):
        fake.eject("Z:")


async def test_disc_identity_of_a_cd_and_of_a_drive_that_will_not_say_its_size(fake, monkeypatch):
    fake.w.volumes["G:"] = ("PAPER_KITES", "0000-0001", "CDFS")
    ident = await fake.disc_identity("G:")
    assert ident.fs_type == "iso9660"

    def no_length(h, code, inbuf=b"", outlen=0):
        raise OSError(1, "incorrect function")

    monkeypatch.setattr(fake.w, "ioctl", no_length)
    fake.w.volumes["G:"] = (None, None, None)  # nothing mounted
    ident = await fake.disc_identity("G:")
    assert (ident.label, ident.uuid, ident.fs_type, ident.size_bytes) == (None, None, None, None)


async def test_extract_structure_copies_from_the_drive_letter(fake, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(discfs, "copy_structure", lambda src, out: seen.append((str(src), out)) or ["BDMV/index.bdmv"])
    assert await fake.extract_structure("G:", tmp_path) == ["BDMV/index.bdmv"]
    assert seen == [("G:\\", tmp_path)]


# --- the Win32 layer itself, against a stand-in kernel32 -------------------------------
# The ctypes structures and buffers are real; only the calls into kernel32 are not.

class FakeKernel32:
    def __init__(self):
        self.mask = 0
        self.types: dict[str, int] = {}
        self.handles: dict[str, int] = {}
        self.error = 0
        self.ioctl_reply: bytes | None = b""
        self.scsi = None  # fn(sptd, cdb) -> (status, data, sense)
        self.volume = None  # (label, serial, fs) or None
        self.dos = None
        self.opened: list[tuple] = []
        self.closed: list[int] = []

    def GetLogicalDrives(self):
        return self.mask

    def GetDriveTypeW(self, root):
        return self.types.get(root, 3)

    def CreateFileW(self, path, access, share, sec, disposition, flags, template):
        self.opened.append((path, access, share, disposition))
        return self.handles.get(path, wintypes.HANDLE(-1).value)

    def CloseHandle(self, h):
        self.closed.append(h)
        return 1

    def DeviceIoControl(self, h, code, inp, inlen, out, outlen, n, overlapped):
        if code == windows.IOCTL_SCSI_PASS_THROUGH_DIRECT:
            if self.scsi is None:
                return 0  # the driver refused the request itself
            s = inp._obj
            cdb = bytes(s.sptd.Cdb[:s.sptd.CdbLength])
            status, data, sense = self.scsi(s.sptd, cdb)
            if data:
                ctypes.memmove(s.sptd.DataBuffer, data, len(data))
                s.sptd.DataTransferLength = len(data)
            s.sptd.ScsiStatus = status
            for i, b in enumerate(sense):
                s.sense[i] = b
            return 1
        if self.ioctl_reply is None:
            return 0
        ctypes.memmove(out, self.ioctl_reply, len(self.ioctl_reply))
        n._obj.value = len(self.ioctl_reply)
        return 1

    def GetVolumeInformationW(self, root, label, label_len, serial, max_len, flags, fs, fs_len):
        if self.volume is None:
            return 0
        label.value, serial._obj.value, fs.value = self.volume
        return 1

    def QueryDosDeviceW(self, device, buf, size):
        if self.dos is None:
            return 0
        buf.value = self.dos
        return len(self.dos) + 2


@pytest.fixture
def k32(monkeypatch):
    # Windows-only parts of ctypes, for running these anywhere.
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "Access is denied"), raising=False)
    w = windows._Win32.__new__(windows._Win32)
    w.k32 = FakeKernel32()
    return w


def test_win32_letters_are_the_cd_rom_drives(k32):
    k32.k32.mask = 0b1011100  # C: D: E: G:
    k32.k32.types = {"D:\\": windows.DRIVE_CDROM, "G:\\": windows.DRIVE_CDROM, "H:\\": windows.DRIVE_CDROM}
    assert k32.letters() == ["D:", "G:"], "H: is a CD-ROM type but not a present drive"


def test_win32_open_and_close(k32):
    k32.k32.handles = {"\\\\.\\G:": 0x44}
    assert k32.open("G:") == 0x44
    assert k32.k32.opened == [("\\\\.\\G:", windows.GENERIC_READ | windows.GENERIC_WRITE,
                               windows.FILE_SHARE_READ_WRITE, windows.OPEN_EXISTING)]
    k32.close(0x44)
    assert k32.k32.closed == [0x44]
    with pytest.raises(OSError) as e:
        k32.open("Q:")
    assert e.value.errno == 5


def test_win32_ioctl_returns_what_was_written(k32):
    k32.k32.ioctl_reply = (100_100_014_080).to_bytes(8, "little")
    assert k32.ioctl(1, windows.IOCTL_DISK_GET_LENGTH_INFO, b"", 8) == k32.k32.ioctl_reply
    k32.k32.ioctl_reply = b"abc"  # shorter than the buffer
    assert k32.ioctl(1, windows.IOCTL_STORAGE_QUERY_PROPERTY, bytes(12), 1024) == b"abc"
    k32.k32.ioctl_reply = None
    with pytest.raises(OSError):
        k32.ioctl(1, windows.IOCTL_DISK_GET_LENGTH_INFO, b"", 8)


def test_win32_scsi_sends_the_cdb_and_reads_the_reply(k32):
    seen = []

    def drive(sptd, cdb):
        seen.append((cdb, sptd.DataIn, sptd.DataTransferLength, sptd.TimeOutValue, sptd.CdbLength,
                     sptd.SenseInfoOffset, sptd.Length))
        return 0, b"PIONEER " if cdb[0] == 0x12 else b"", b""

    k32.k32.scsi = drive
    inquiry_cdb = bytes([0x12, 0, 0, 0, 96, 0])
    # The drive sends 8 bytes of the 96 asked for; only those come back.
    assert k32.scsi(1, inquiry_cdb, 96) == b"PIONEER "
    assert k32.scsi(1, windows.START_STOP_EJECT, timeout_s=30) == b""
    (cdb, data_in, length, timeout, cdb_len, sense_at, size), (cdb2, data_in2, length2, timeout2, *_) = seen
    assert (cdb, data_in, length, timeout, cdb_len) == (inquiry_cdb, windows.SCSI_IOCTL_DATA_IN, 96, 10, 6)
    assert sense_at == windows._SPTDWithSense.sense.offset and size == ctypes.sizeof(windows._SPTD)
    assert (cdb2, data_in2, length2, timeout2) == (windows.START_STOP_EJECT, windows.SCSI_IOCTL_DATA_UNSPECIFIED, 0, 30)


def test_win32_scsi_check_condition_carries_the_sense(k32):
    sense = bytearray(18)
    sense[0], sense[2], sense[12], sense[13] = 0x70, 0x22, 0x3A, 0x02  # (flags in the high bits) not ready, tray open
    k32.k32.scsi = lambda sptd, cdb: (2, b"", bytes(sense))
    with pytest.raises(windows.ScsiStatusError) as e:
        k32.scsi(1, windows.TEST_UNIT_READY)
    assert e.value.sense == (0x2, 0x3A, 0x02)
    assert "sense 2/3a/02" in str(e.value)
    # So the drive reads as tray open.
    d = windows.WindowsDrives.__new__(windows.WindowsDrives)
    d.w = k32
    assert d._status(1) == DriveStatus.tray_open
    k32.k32.scsi = None
    with pytest.raises(OSError) as e:
        k32.scsi(1, windows.TEST_UNIT_READY)
    assert not isinstance(e.value, windows.ScsiStatusError)


def test_win32_volume(k32):
    k32.k32.volume = ("THE_BRASS_HERON", 0x9243854A, "UDF")
    assert k32.volume("G:") == ("THE_BRASS_HERON", "9243-854A", "UDF")
    k32.k32.volume = ("", 0x1, "")
    assert k32.volume("G:") == (None, "0000-0001", None)
    k32.k32.volume = None  # no disc
    assert k32.volume("G:") == (None, None, None)


def test_win32_dos_device(k32):
    k32.k32.dos = "\\Device\\CdRom1"
    assert k32.dos_device("G:") == "\\Device\\CdRom1"
    k32.k32.dos = None
    assert k32.dos_device("G:") == "G:"


@pytest.mark.skipif(sys.platform != "win32", reason="the real kernel32")
def test_real_kernel32_answers():
    """The ctypes signatures against the real kernel32. A CI runner has no optical
    drive, so this checks the calls work, not what a drive says."""
    w = windows._Win32()
    letters = w.letters()
    assert all(len(x) == 2 and x.endswith(":") for x in letters)
    assert w.dos_device("C:").startswith("\\Device\\")
    label, serial, fs = w.volume("C:")
    assert fs == "NTFS" and serial and len(serial) == 9
    assert w.volume("B:") == (None, None, None)
    with pytest.raises(OSError):
        w.open("B:")
    assert windows.WindowsDrives().status("B:") == DriveStatus.error
