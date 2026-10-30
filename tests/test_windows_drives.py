"""The Windows drive backend against a simulated Win32 layer (runs anywhere)."""

from __future__ import annotations

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
    fake.w.volumes["G:"] = ("THE_WILD_ROBOT", "9243-854A", "UDF")
    ident = await fake.disc_identity("G:")
    assert (ident.label, ident.uuid, ident.fs_type, ident.size_bytes) == (
        "THE_WILD_ROBOT", "9243-854A", "udf", 100_100_014_080)


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
