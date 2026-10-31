"""Drive discovery from sysfs and drive control, without touching real hardware."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ripshelf.hw import drives, sgio
from ripshelf.hw.drives import SysfsDrives, capabilities_for, sbin_fallback
from ripshelf.models import Capability, DriveStatus

from .conftest import posix_only

UHD = ["PIONEER BD-RW  BDR-S11JX"]


def fake_sysfs(root: Path, drives_spec: dict[str, dict]) -> Path:
    block = root / "class/block"
    for name, spec in drives_spec.items():
        dev = root / "devices" / spec.get("port", name) / "device"
        dev.mkdir(parents=True)
        for k in ("vendor", "model", "rev"):
            if k in spec:
                (dev / k).write_text(spec[k] + "\n")
        if spec.get("sg"):
            (dev / "scsi_generic" / spec["sg"]).mkdir(parents=True)
        (block / name).mkdir(parents=True)
        (block / name / "device").symlink_to(dev)
        (block / name / "size").write_text(spec.get("size", "0"))
    return block


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    def make(spec, dev_nodes=True):
        block = fake_sysfs(tmp_path, spec)
        monkeypatch.setattr(drives, "SYS_BLOCK", block)
        dev = tmp_path / "dev"
        dev.mkdir(exist_ok=True)
        for name in spec if dev_nodes else ():
            (dev / name).touch()
        monkeypatch.setattr(drives.SysfsDrives, "__init__", _with_dev(dev))
        return block
    return make


def _with_dev(dev):
    real = drives.SysfsDrives.__init__

    def init(self, blkid="blkid", sevenzip="7z", dev_root=dev):
        real(self, blkid, sevenzip, dev_root)
    return init


def test_discover_pairs_sr_with_sg_and_identifies(sysfs, monkeypatch):
    sysfs({
        "sr0": {"vendor": "HL-DT-ST", "model": "DVD-RAM GHC0N", "rev": "MA00", "sg": "sg5"},
        "sr2": {"vendor": "PIONEER", "model": "BD-RW  BDR-S11JX", "rev": "1.53", "sg": "sg7"},
    })
    monkeypatch.setattr(sgio, "identify", lambda sg: sgio.DriveIdentity(
        "PIONEER", "BD-RW  BDR-S11JX", "1.53", "SER123" if sg == "/dev/sg7" else None,
        True, sg == "/dev/sg7"))
    found = {d.device: d for d in SysfsDrives().discover(UHD)}
    assert found["/dev/sr0"].sg_device == "/dev/sg5"
    assert found["/dev/sr0"].capabilities == [Capability.dvd]
    p = found["/dev/sr2"]
    assert p.sg_device == "/dev/sg7" and p.serial == "SER123"
    assert p.capabilities == [Capability.dvd, Capability.bluray, Capability.uhd]
    assert p.id.startswith("bd-rw-bdr-s11jx-")


def test_drive_ids_are_stable_and_distinct(sysfs, monkeypatch):
    sysfs({"sr0": {"model": "iHAS122", "port": "a"}, "sr1": {"model": "iHAS122", "port": "b"}})
    monkeypatch.setattr(sgio, "identify", lambda sg: None)
    a = [d.id for d in SysfsDrives().discover(UHD)]
    b = [d.id for d in SysfsDrives().discover(UHD)]
    assert a == b and len(set(a)) == 2


def test_missing_sg_device_and_sysfs_fields(sysfs, monkeypatch):
    sysfs({"sr0": {}})  # no vendor/model/rev, no scsi_generic
    called = []
    monkeypatch.setattr(sgio, "identify", lambda sg: called.append(sg))
    [d] = SysfsDrives().discover(UHD)
    assert d.sg_device is None and d.vendor == "" and d.serial is None
    assert d.capabilities == [Capability.dvd]
    assert called == []


def test_sg_identify_failure_falls_back_to_model_name(sysfs, monkeypatch):
    sysfs({"sr3": {"vendor": "HL-DT-ST", "model": "BDDVDRW GGC-H20N", "sg": "sg1"}})

    def boom(sg):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(sgio, "identify", boom)
    [d] = SysfsDrives().discover(UHD)
    assert d.capabilities == [Capability.dvd, Capability.bluray]


def test_no_drives(sysfs):
    sysfs({})
    assert SysfsDrives().discover(UHD) == []


def test_drives_without_a_device_node_are_skipped(sysfs, monkeypatch):
    # A controller container sees the host's sysfs but was given no /dev/srN.
    sysfs({"sr0": {"model": "iHAS122"}, "sr1": {"model": "GHC0N"}}, dev_nodes=False)
    monkeypatch.setattr(sgio, "identify", lambda sg: None)
    assert SysfsDrives().discover(UHD) == []


def test_uhd_only_for_listed_models():
    ident = sgio.DriveIdentity("LG", "BD", "1", None, True, True)
    assert Capability.uhd not in capabilities_for("HL-DT-ST", "BDDVDRW GGC-H20N", ident, UHD)
    assert Capability.uhd in capabilities_for("pioneer", "bd-rw bdr-s11jx", ident, UHD)
    no_bd = sgio.DriveIdentity("P", "BD-RW BDR-S11JX", "1", None, True, False)
    assert capabilities_for("PIONEER", "BD-RW  BDR-S11JX", no_bd, UHD) == [Capability.dvd]


@posix_only
def test_status_of_missing_or_wrong_device(tmp_path):
    b = SysfsDrives()
    assert b.status(str(tmp_path / "sr9")) == DriveStatus.error
    f = tmp_path / "not-a-drive"
    f.write_text("")
    assert b.status(str(f)) == DriveStatus.error


@posix_only
def test_eject_missing_device_raises(tmp_path):
    with pytest.raises(OSError):
        SysfsDrives().eject(str(tmp_path / "sr9"))


async def test_disc_identity_without_blkid(sysfs, tmp_path):
    sysfs({"sr0": {"size": "8388608"}})
    ident = await SysfsDrives(blkid=str(tmp_path / "no-blkid")).disc_identity("/dev/sr0")
    assert ident.label is None and ident.size_bytes == 8388608 * 512


@posix_only
def test_sbin_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    sbin = tmp_path / "sbin"
    sbin.mkdir()
    (sbin / "blkid").write_text("#!/bin/sh\n")
    (sbin / "blkid").chmod(0o755)
    assert sbin_fallback("blkid", (str(tmp_path / "nope"), str(sbin))) == str(sbin / "blkid")
    assert sbin_fallback("missing", (str(sbin),)) == "missing"
    assert sbin_fallback("/opt/blkid", (str(sbin),)) == "/opt/blkid"


@posix_only
async def test_disc_identity_parses_blkid(sysfs, tmp_path):
    sysfs({"sr0": {"size": "10"}})
    blkid = tmp_path / "blkid"
    blkid.write_text("#!/bin/sh\necho LABEL=THE_BRASS_HERON\necho UUID=abc\necho TYPE=udf\necho junk\n")
    blkid.chmod(0o755)
    ident = await SysfsDrives(blkid=str(blkid)).disc_identity("/dev/sr0")
    assert (ident.label, ident.uuid, ident.fs_type) == ("THE_BRASS_HERON", "abc", "udf")


@pytest.mark.skipif(not os.environ.get("RIPSHELF_REAL_DRIVES"), reason="set RIPSHELF_REAL_DRIVES=1 to read real sysfs")
def test_real_drives_read_only():
    found = SysfsDrives().discover(UHD)
    assert found, "no optical drives"
    for d in found:
        assert d.device.startswith("/dev/sr")
        assert SysfsDrives().status(d.device) in DriveStatus


def test_id_follows_the_serial_not_the_port(sysfs, monkeypatch, tmp_path):
    monkeypatch.setattr(sgio, "identify", lambda sg: sgio.DriveIdentity(
        "PIONEER", "BD-RW  BDR-S11JX", "1.53", "SN0000000001", True, True))
    spec = {"vendor": "PIONEER", "model": "BD-RW  BDR-S11JX", "sg": "sg7"}
    sysfs({"sr0": {**spec, "port": "pci-a"}})
    [a] = SysfsDrives().discover(UHD)
    (tmp_path / "class").rename(tmp_path / "old-class")
    sysfs({"sr4": {**spec, "port": "pci-b"}})
    [b] = SysfsDrives().discover(UHD)
    assert a.id == b.id and a.id.startswith("bd-rw-bdr-s11jx-") and a.device != b.device
    assert a.legacy_id == drives.port_id(spec["model"], a.port) != b.legacy_id


def test_without_a_serial_the_port_id_stays(sysfs, monkeypatch):
    sysfs({"sr0": {"model": "iHAS122", "port": "a"}})
    monkeypatch.setattr(sgio, "identify", lambda sg: None)
    [d] = SysfsDrives().discover(UHD)
    assert d.serial is None and d.legacy_id is None and d.id == drives.port_id("iHAS122", d.port)

