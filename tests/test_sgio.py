"""SG_IO identify against canned SCSI responses (no device needed)."""

from __future__ import annotations

import ctypes

import pytest

from ripshelf.hw import sgio

from .conftest import posix_only

pytestmark = posix_only  # SG_IO


def inquiry(vendor=b"PIONEER ", model=b"BD-RW  BDR-S11JX", rev=b"1.53") -> bytes:
    return bytes(8) + vendor.ljust(8) + model.ljust(16) + rev.ljust(4) + bytes(60)


def features(*codes: int, length_override: int | None = None) -> bytes:
    body = b"".join(c.to_bytes(2, "big") + b"\x00\x04" + bytes(4) for c in codes)
    data_len = length_override if length_override is not None else 4 + len(body)
    return data_len.to_bytes(4, "big") + bytes(4) + body


@pytest.fixture
def fake_device(monkeypatch):
    replies: dict[int, object] = {}
    monkeypatch.setattr(sgio.os, "open", lambda path, flags: 99)
    monkeypatch.setattr(sgio.os, "close", lambda fd: None)

    def command(fd, cdb, length, timeout_ms=5000):
        key = (cdb[0], cdb[1])
        r = replies.get(key)
        if isinstance(r, Exception):
            raise r
        return r or b""

    monkeypatch.setattr(sgio, "_command", command)
    return replies


def test_identify_full(fake_device):
    fake_device[(0x12, 0)] = inquiry()
    serial = b"SN0000000001"
    fake_device[(0x12, 1)] = bytes([0, 0x80, 0, len(serial)]) + serial
    fake_device[(0x46, 0)] = features(0x0010, sgio.FEATURE_DVD_READ, sgio.FEATURE_BD_READ)
    ident = sgio.identify("/dev/sg7")
    assert (ident.vendor, ident.model, ident.revision) == ("PIONEER", "BD-RW  BDR-S11JX", "1.53")
    assert ident.serial == "SN0000000001"
    assert ident.reads_dvd and ident.reads_bd


def test_identify_without_serial_page_or_features(fake_device):
    fake_device[(0x12, 0)] = inquiry(b"ATAPI", b"iHAS122", b"ZL0E")
    fake_device[(0x12, 1)] = sgio.ScsiError("illegal request")
    fake_device[(0x46, 0)] = OSError(5, "I/O error")
    ident = sgio.identify("/dev/sg6")
    assert ident.model == "iHAS122" and ident.serial is None
    assert ident.reads_dvd is None and ident.reads_bd is None


def test_identify_short_and_odd_replies(fake_device):
    fake_device[(0x12, 0)] = b"\x05\x80"  # truncated INQUIRY
    fake_device[(0x12, 1)] = bytes([0, 0x83, 0, 4]) + b"XXXX"  # wrong page
    fake_device[(0x46, 0)] = b"\x00\x00"
    ident = sgio.identify("/dev/sg1")
    assert ident.vendor == "" and ident.serial is None and ident.reads_bd is False


def serial_feature(serial: bytes, code: int = sgio.FEATURE_SERIAL) -> bytes:
    return (8 + len(serial)).to_bytes(4, "big") + bytes(4) + code.to_bytes(2, "big") + bytes([0, len(serial)]) + serial


def test_serial_comes_from_the_mmc_feature_first(fake_device):
    fake_device[(0x12, 0)] = inquiry()
    fake_device[(0x12, 1)] = bytes([0, 0x80, 0, 4]) + b"VPD1"
    fake_device[(0x46, 2)] = serial_feature(b"SN0000000002    ")
    assert sgio.identify("/dev/sg7").serial == "SN0000000002"
    # Not supported (or another feature in reply), so the VPD page's serial.
    fake_device[(0x46, 2)] = sgio.ScsiError("illegal request")
    assert sgio.identify("/dev/sg7").serial == "VPD1"
    fake_device[(0x46, 2)] = serial_feature(b"X", code=0x0001)
    assert sgio.identify("/dev/sg7").serial == "VPD1"


def test_blank_serial_feature_is_none():
    assert sgio.parse_feature_serial(serial_feature(b"\0\0  ")) is None
    assert sgio.parse_feature_serial(b"") is None


def test_feature_list_with_bogus_length_terminates(fake_device):
    fake_device[(0x12, 0)] = inquiry()
    fake_device[(0x46, 0)] = features(sgio.FEATURE_DVD_READ, length_override=10_000)
    assert sgio.identify("/dev/sg7").reads_dvd is True


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
