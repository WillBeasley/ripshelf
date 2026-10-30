"""Optical drive discovery and control (sysfs + cdrom ioctls)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..models import Capability, DriveStatus
from . import sgio

try:
    import fcntl
except ImportError:  # on Windows, hw.windows has the drives
    fcntl = None

log = logging.getLogger(__name__)

SYS_BLOCK = Path("/sys/class/block")

CDROM_DRIVE_STATUS = 0x5326
CDROMEJECT = 0x5309
CDROMCLOSETRAY = 0x5319
CDROM_LOCKDOOR = 0x5329
CDSL_CURRENT = 0x7FFFFFFF
_STATUS = {
    1: DriveStatus.empty,
    2: DriveStatus.tray_open,
    3: DriveStatus.not_ready,
    4: DriveStatus.disc,
}


@dataclass
class DriveHw:
    id: str
    device: str  # /dev/srN
    sg_device: str | None
    vendor: str
    model: str
    revision: str
    serial: str | None
    capabilities: list[Capability]
    port: str  # sysfs device path, which changes when the drive moves to another port
    # The id this drive had when ids came from its port, if that differs (for migrating data).
    legacy_id: str | None = None
    # A VM's emulated drive or a mounted image, not a real one. It starts disabled.
    virtual: bool = False

    @property
    def label(self) -> str:
        """Vendor and model, for when the drive has no name."""
        return " ".join(f"{self.vendor} {self.model}".split()) or self.id


@dataclass
class DiscIdentity:
    label: str | None
    uuid: str | None
    fs_type: str | None
    fs_version: str | None
    size_bytes: int | None


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def normalise_model(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().upper()


VIRTUAL_VENDORS = {"QEMU", "VBOX", "VMWARE", "MSFT"}


def is_virtual(vendor: str, model: str) -> bool:
    """An emulated drive, either a VM's (QEMU, VirtualBox, VMware, Hyper-V) or a server's
    remote-media CD (BMC virtual media)."""
    return vendor.strip().upper() in VIRTUAL_VENDORS or "VIRTUAL" in model.upper()


def drive_id(vendor: str, model: str, serial: str | None, port: str) -> str:
    """The drive's persistent id, from vendor, model and serial, so it stays the same on
    another port or /dev/srN. Only a drive with no readable serial falls back to its port."""
    if serial:
        key = " ".join(f"{vendor} {model} {serial}".split()).upper()
        return f"{slug(model)}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
    return port_id(model, port)


def port_id(model: str, port: str) -> str:
    """The id drives had before serials were used (and still have without one)."""
    return f"{slug(model)}-{hashlib.sha1(port.encode()).hexdigest()[:4]}"


def capabilities_for(vendor: str, model: str, ident: sgio.DriveIdentity | None,
                     uhd_models: list[str]) -> list[Capability]:
    caps = [Capability.dvd]
    reads_bd = ident.reads_bd if ident and ident.reads_bd is not None else "BD" in model.upper()
    if reads_bd:
        caps.append(Capability.bluray)
        full = normalise_model(f"{vendor} {model}")
        if any(normalise_model(m) == full for m in uhd_models):
            caps.append(Capability.uhd)
    return caps


def sbin_fallback(command: str, dirs: tuple[str, ...] = ("/usr/sbin", "/sbin")) -> str:
    """A bare command name that isn't on the PATH, from the sbin folders. Debian keeps blkid
    in /usr/sbin, which isn't on a normal user's PATH, so a native run found no disc labels."""
    if os.sep in command or shutil.which(command):
        return command
    for d in dirs:
        if os.access(p := os.path.join(d, command), os.X_OK):
            return p
    return command


class SysfsDrives:
    """Real hardware."""

    def __init__(self, blkid: str = "blkid", sevenzip: str = "7z", dev_root: Path = Path("/dev")):
        self.blkid = sbin_fallback(blkid)
        self.sevenzip = sevenzip
        self.dev_root = dev_root

    async def extract_structure(self, device: str, out_dir: Path) -> list[str]:
        from ..discfs import extract_structure

        return await extract_structure(self.sevenzip, device, out_dir)

    def discover(self, uhd_models: list[str]) -> list[DriveHw]:
        out = []
        for entry in sorted(SYS_BLOCK.glob("sr*")):
            name = entry.name
            # sysfs shows every drive on the host; a container only has the
            # devices it was given (a controller may have none).
            if not (self.dev_root / name).exists():
                continue
            dev = entry / "device"
            vendor = _read(dev / "vendor")
            model = _read(dev / "model")
            rev = _read(dev / "rev")
            sgs = list((dev / "scsi_generic").glob("sg*")) if (dev / "scsi_generic").exists() else []
            sg = f"/dev/{sgs[0].name}" if sgs else None
            port = os.path.realpath(dev)
            ident = None
            if sg:
                try:
                    ident = sgio.identify(sg)
                except OSError as e:
                    log.info("SG_IO identify %s failed: %s", sg, e)
            # Not udev's ID_SERIAL as a fallback, because it can be a different number from the drive's
            # own, and an id must not depend on which source happened to answer.
            serial = ident.serial if ident else None
            did, legacy = drive_id(vendor, model, serial, port), port_id(model, port)
            if not serial:
                log.warning("%s (%s %s): no serial number readable; its id %s depends on the port "
                            "it is plugged into", name, vendor, model, did)
            out.append(DriveHw(
                id=did, device=f"/dev/{name}", sg_device=sg, vendor=vendor, model=model,
                revision=rev, serial=serial,
                capabilities=capabilities_for(vendor, model, ident, uhd_models), port=port,
                legacy_id=legacy if legacy != did else None, virtual=is_virtual(vendor, model),
            ))
        return out

    def status(self, device: str) -> DriveStatus:
        try:
            fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            return DriveStatus.error
        try:
            return _STATUS.get(fcntl.ioctl(fd, CDROM_DRIVE_STATUS, CDSL_CURRENT), DriveStatus.not_ready)
        except OSError:
            return DriveStatus.error
        finally:
            os.close(fd)

    def eject(self, device: str) -> None:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        try:
            try:
                fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
            except OSError:
                pass
            fcntl.ioctl(fd, CDROMEJECT, 0)
        finally:
            os.close(fd)

    def close_tray(self, device: str) -> None:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, CDROMCLOSETRAY, 0)
        finally:
            os.close(fd)

    async def disc_identity(self, device: str) -> DiscIdentity:
        props: dict[str, str] = {}
        try:
            proc = await asyncio.create_subprocess_exec(
                self.blkid, "-p", "-o", "export", device,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 30)
            for line in out.decode(errors="replace").splitlines():
                k, _, v = line.partition("=")
                props[k] = v
        except (OSError, TimeoutError):
            pass
        size = None
        sectors = _read(SYS_BLOCK / Path(device).name / "size")
        if sectors.isdigit():
            size = int(sectors) * 512
        return DiscIdentity(
            label=props.get("LABEL") or props.get("VOLUME_ID") or None,
            uuid=props.get("UUID"),
            fs_type=props.get("TYPE"),
            fs_version=props.get("VERSION"),
            size_bytes=size,
        )
