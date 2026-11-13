"""SG_IO identify against canned SCSI responses (no device needed)."""

from __future__ import annotations

import ctypes

import pytest

from ripshelf.hw import sgio

from .conftest import posix_only


def inquiry(vendor=b"PIONEER ", model=b"BD-RW  BDR-S11JX", rev=b"1.53") -> bytes:
    return bytes(8) + vendor.ljust(8) + model.ljust(16) + rev.ljust(4) + bytes(60)


def features(*codes: int, length_override: int | None = None) -> bytes:
    body = b"".join(c.to_bytes(2, "big") + b"\x00\x04" + bytes(4) for c in codes)
    data_len = length_override if length_override is not None else 4 + len(body)
    return data_len.to_bytes(4, "big") + bytes(4) + body


class FakeDevice(dict):
    """Replies (or errors) by command (opcode, second byte). ``identify()`` asks it the way
    both backends do: Linux through SG_IO, Windows through IOCTL_SCSI_PASS_THROUGH."""

    def send(self, cdb, length):
        r = self.get((cdb[0], cdb[1]))
        if isinstance(r, Exception):
            raise r
        return r or b""

    def identify(self):
        return sgio.identify_with(self.send)


@pytest.fixture
def fake_device():
    return FakeDevice()


def test_identify_full(fake_device):
    fake_device[(0x12, 0)] = inquiry()
    serial = b"SN0000000001"
    fake_device[(0x12, 1)] = bytes([0, 0x80, 0, len(serial)]) + serial
    fake_device[(0x46, 0)] = features(0x0010, sgio.FEATURE_DVD_READ, sgio.FEATURE_BD_READ)
    ident = fake_device.identify()
    assert (ident.vendor, ident.model, ident.revision) == ("PIONEER", "BD-RW  BDR-S11JX", "1.53")
    assert ident.serial == "SN0000000001"
    assert ident.reads_dvd and ident.reads_bd


def test_identify_without_serial_page_or_features(fake_device):
    fake_device[(0x12, 0)] = inquiry(b"ATAPI", b"iHAS122", b"ZL0E")
    fake_device[(0x12, 1)] = sgio.ScsiError("illegal request")
    fake_device[(0x46, 0)] = OSError(5, "I/O error")
    ident = fake_device.identify()
    assert ident.model == "iHAS122" and ident.serial is None
    assert ident.reads_dvd is None and ident.reads_bd is None


def test_identify_short_and_odd_replies(fake_device):
    fake_device[(0x12, 0)] = b"\x05\x80"  # truncated INQUIRY
    fake_device[(0x12, 1)] = bytes([0, 0x83, 0, 4]) + b"XXXX"  # wrong page
    fake_device[(0x46, 0)] = b"\x00\x00"
    ident = fake_device.identify()
    assert ident.vendor == "" and ident.serial is None and ident.reads_bd is False


def serial_feature(serial: bytes, code: int = sgio.FEATURE_SERIAL) -> bytes:
    return (8 + len(serial)).to_bytes(4, "big") + bytes(4) + code.to_bytes(2, "big") + bytes([0, len(serial)]) + serial


def test_serial_comes_from_the_mmc_feature_first(fake_device):
    fake_device[(0x12, 0)] = inquiry()
    fake_device[(0x12, 1)] = bytes([0, 0x80, 0, 4]) + b"VPD1"
    fake_device[(0x46, 2)] = serial_feature(b"SN0000000002    ")
    assert fake_device.identify().serial == "SN0000000002"
    # Not supported (or another feature in reply), so the VPD page's serial.
    fake_device[(0x46, 2)] = sgio.ScsiError("illegal request")
    assert fake_device.identify().serial == "VPD1"
    fake_device[(0x46, 2)] = serial_feature(b"X", code=0x0001)
    assert fake_device.identify().serial == "VPD1"


def test_blank_serial_feature_is_none():
    assert sgio.parse_feature_serial(serial_feature(b"\0\0  ")) is None
    assert sgio.parse_feature_serial(b"") is None


def test_feature_list_with_bogus_length_terminates(fake_device):
    fake_device[(0x12, 0)] = inquiry()
    fake_device[(0x46, 0)] = features(sgio.FEATURE_DVD_READ, length_override=10_000)
    assert fake_device.identify().reads_dvd is True


@posix_only  # O_NONBLOCK
def test_identify_opens_and_closes_the_sg_device(fake_device, monkeypatch):
    opened = []
    monkeypatch.setattr(sgio.os, "open", lambda path, flags: opened.append(path) or 99)
    monkeypatch.setattr(sgio.os, "close", lambda fd: opened.remove("/dev/sg7"))
    monkeypatch.setattr(sgio, "_command", lambda fd, cdb, length: fake_device.send(cdb, length))
    fake_device[(0x12, 0)] = inquiry()
    assert sgio.identify("/dev/sg7").model == "BD-RW  BDR-S11JX"
    assert opened == []


@posix_only  # SG_IO
def test_command_status_checks(monkeypatch):
    def ioctl_ok(fd, req, hdr):
        ctypes.memmove(hdr.dxferp, b"OK", 2)
        hdr.resid = 2

    monkeypatch.setattr(sgio.fcntl, "ioctl", ioctl_ok)
    assert sgio._command(3, b"\x12\x00\x00\x00\x04\x00", 4) == b"OK"

    def ioctl_check_condition(fd, req, hdr):
        hdr.status = 2

    monkeypatch.setattr(sgio.fcntl, "ioctl", ioctl_check_condition)
    with pytest.raises(sgio.ScsiError):
        sgio._command(3, b"\x12\x00\x00\x00\x04\x00", 4)


def toc(*tracks: tuple[int, int]) -> bytes:
    """A READ TOC reply for (track number, control) pairs, with the lead-out last."""
    body = b"".join(bytes([0, 0x10 | control, n, 0]) + bytes(4) for n, control in (*tracks, (0xAA, 0x04)))
    return (2 + len(body)).to_bytes(2, "big") + bytes([1, len(tracks)]) + body


@pytest.mark.parametrize(("tracks", "want"), [
    ([(1, 0), (2, 0), (3, 0x02)], (3, 0)),  # an audio CD (one track with copying allowed)
    ([(1, 0), (2, 0), (3, 0x04)], (2, 1)),  # an enhanced CD
    ([(1, 0x04)], (0, 1)),  # a DVD or Blu-ray: drives report one data track
])
def test_toc_tracks(tracks, want):
    assert sgio.toc_tracks(toc(*tracks)) == want


def test_toc_tracks_of_a_short_reply():
    assert sgio.toc_tracks(b"") == (0, 0)
    assert sgio.toc_tracks(toc((1, 0))[:10]) == (0, 0)
