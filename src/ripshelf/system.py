"""What the machine has to work with. That's the free space in each folder ripshelf writes
to, and the GPUs and ffmpeg hardware encoders it could transcode with.

Storage is cheap to read and read on every request. Hardware is found by test
encodes (a fraction of a second each) and kept until asked for again.
"""

from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
import time
from pathlib import Path

from . import transcode
from .models import Filesystem, Hardware, HwDevice, HwEncoder, StorageFolder

# Fewer than this many bytes free on the scratch disk is too little for a UHD rip.
SCRATCH_MIN_FREE = 100 * 1024**3
LOW_FREE_FRACTION = 0.1
TEST_TIMEOUT_S = 30

PCI_VENDORS = {"0x8086": "Intel", "0x1002": "AMD", "0x10de": "NVIDIA"}
HW_METHODS = {"vaapi": "VAAPI", "qsv": "Intel Quick Sync", "nvenc": "NVIDIA NVENC", "amf": "AMD AMF"}
# A method is only tried when a GPU from its vendor is passed in.
METHOD_VENDOR = {"qsv": "Intel", "nvenc": "NVIDIA", "amf": "AMD"}
TESTED_CODECS = {"hevc", "h264", "av1"}
# ripshelf itself encodes with these; others are shown but not used yet.
USED_METHODS = {"vaapi"}


def folders(s) -> list[tuple[str, Path]]:
    """Every folder ripshelf writes to, with a name for the UI."""
    out = [("Movies", s.movies), ("TV", s.tv), ("Scratch (rips in progress)", s.scratch_dir)]
    if s.backups_used or s.disc_backups_dir:
        out.append(("Disc backups", s.disc_backups))
    out += [("Data (database, settings)", s.data_dir), ("Database backups", s.backups)]
    return out


def _existing(p: Path) -> Path | None:
    """p, or the nearest folder above it that exists (where it would be made)."""
    for q in (p, *p.parents):
        if q.exists():
            return q
    return None


def storage(s) -> list[Filesystem]:
    """Folders grouped by the filesystem they are on, with its size and free space."""
    groups: dict[int, Filesystem] = {}
    missing: list[StorageFolder] = []
    for name, path in folders(s):
        here = _existing(path)
        folder = StorageFolder(name=name, path=str(path), exists=path.exists())
        if here is None:
            missing.append(folder)
            continue
        try:
            dev = here.stat().st_dev
            usage = shutil.disk_usage(here)
        except OSError:
            missing.append(folder)
            continue
        fs = groups.get(dev)
        if fs is None:
            fs = groups[dev] = Filesystem(total=usage.total, used=usage.used, free=usage.free, folders=[])
        fs.folders.append(folder)
    for fs in groups.values():
        fs.warning = _warning(fs)
    out = list(groups.values())
    if missing:
        out.append(Filesystem(folders=missing, warning="Not found"))
    return out


def _warning(fs: Filesystem) -> str | None:
    if fs.total and fs.free is not None and fs.free < fs.total * LOW_FREE_FRACTION:
        return "Less than 10% free"
    if fs.free is not None and fs.free < SCRATCH_MIN_FREE and any(f.name.startswith("Scratch") for f in fs.folders):
        return "Under 100 GB free, which isn't enough for a UHD rip"
    return None


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def devices(dev_root: Path = Path("/dev"), sys_root: Path = Path("/sys")) -> list[HwDevice]:
    """GPU render nodes (Intel, AMD, NVIDIA with nouveau) and NVIDIA's own device files."""
    out = []
    for node in sorted(glob.glob(str(dev_root / "dri" / "renderD*"))):
        name = Path(node).name
        sysdev = sys_root / "class" / "drm" / name / "device"
        vendor_id = _read(sysdev / "vendor")
        driver = None
        try:
            driver = (sysdev / "driver").resolve(strict=True).name
        except OSError:
            pass
        out.append(HwDevice(path=node, vendor=PCI_VENDORS.get(vendor_id or "", vendor_id), driver=driver,
                            accessible=os.access(node, os.R_OK | os.W_OK)))
    models = []
    for info in sorted(glob.glob("/proc/driver/nvidia/gpus/*/information")):
        m = re.search(r"^Model:\s*(.+)$", _read(Path(info)) or "", re.M)
        models.append(m.group(1).strip() if m else None)
    for i, node in enumerate(sorted(glob.glob(str(dev_root / "nvidia[0-9]*")))):
        out.append(HwDevice(path=node, vendor="NVIDIA", driver="nvidia",
                            model=models[i] if i < len(models) else None,
                            accessible=os.access(node, os.R_OK | os.W_OK)))
    return out


ENCODER_LINE = re.compile(r"^\s*V\S*\s+(\w+?)_(\w+)\s+(.*)$")


def parse_encoders(text: str) -> list[tuple[str, str, str]]:
    """(codec, method, description) for the hardware video encoders in `ffmpeg -encoders`."""
    out = []
    for line in text.splitlines():
        m = ENCODER_LINE.match(line)
        if m and m.group(2) in HW_METHODS and m.group(1) in TESTED_CODECS:
            out.append((m.group(1), m.group(2), m.group(3).strip()))
    return out


def _test_cmd(ffmpeg: str, codec: str, method: str, device: str | None, ten_bit: bool = False) -> list[str]:
    src = ["-f", "lavfi", "-i", "color=black:s=320x240:d=0.2"]
    enc = ["-c:v", f"{codec}_{method}", "-f", "null", "-"]
    if method == "vaapi":
        fmt = "p010" if ten_bit else "nv12"
        profile = ["-profile:v", "main10"] if ten_bit else []
        return [ffmpeg, "-v", "error", "-init_hw_device", f"vaapi=va:{device}", "-filter_hw_device", "va",
                *src, "-vf", f"format={fmt},hwupload", *enc[:2], *profile, *enc[2:]]
    return [ffmpeg, "-v", "error", *src, "-vf", "format=nv12", *enc]


def _first_error(err: bytes) -> str | None:
    """ffmpeg's first complaint, without the "[hevc_qsv @ 0x…]" prefix, because the later lines are consequences."""
    for ln in err.decode(errors="replace").splitlines():
        ln = re.sub(r"^\[[^\]]*@ 0x[0-9a-f]+\]\s*", "", ln.strip())
        if ln:
            return ln[:200]
    return None


async def _test(ffmpeg: str, codec: str, method: str, description: str, device: str | None) -> HwEncoder:
    rc, _, err = await transcode._run(_test_cmd(ffmpeg, codec, method, device), timeout=TEST_TIMEOUT_S)
    enc = HwEncoder(name=f"{codec}_{method}", codec=codec, method=HW_METHODS[method], device=device,
                    description=description, works=rc == 0, error=None if rc == 0 else _first_error(err),
                    used_by_ripshelf=method in USED_METHODS and codec == "hevc")
    if rc == 0 and method == "vaapi" and codec == "hevc":
        rc10, _, _ = await transcode._run(_test_cmd(ffmpeg, codec, method, device, ten_bit=True),
                                          timeout=TEST_TIMEOUT_S)
        enc.ten_bit = rc10 == 0
    return enc


def _skipped(codec: str, method: str, description: str, why: str) -> HwEncoder:
    return HwEncoder(name=f"{codec}_{method}", codec=codec, method=HW_METHODS[method], description=description,
                     works=False, error=why, tested=False)


async def hardware(s, dev_root: Path = Path("/dev"), sys_root: Path = Path("/sys")) -> Hardware:
    """Devices, which hardware encoders ffmpeg has and whether each one works here."""
    devs = devices(dev_root, sys_root)
    rc, out, err = await transcode._run([s.ffmpeg, "-hide_banner", "-encoders"], timeout=TEST_TIMEOUT_S)
    if rc != 0:
        return Hardware(devices=devs, encoders=[], ffmpeg_found=False, encoder="x265",
                        encoder_note=f"ffmpeg not usable: {_first_error(err) or 'not found'}",
                        checked_at=time.time())
    nodes = [d.path for d in devs if "renderD" in d.path]
    vendors = {d.vendor for d in devs}
    tests, skipped = [], []
    for codec, method, desc in parse_encoders(out.decode(errors="replace")):
        if method == "vaapi":
            # One test per GPU, because each render node is a separate device.
            tests += [_test(s.ffmpeg, codec, method, desc, n) for n in nodes]
            if not nodes:
                skipped.append(_skipped(codec, method, desc, "No /dev/dri render node passed in"))
        elif METHOD_VENDOR[method] in vendors:
            tests.append(_test(s.ffmpeg, codec, method, desc, None))
        else:
            skipped.append(_skipped(codec, method, desc, f"No {METHOD_VENDOR[method]} GPU found"))
    encoders = sorted([*await asyncio.gather(*tests), *skipped],
                      key=lambda e: (not e.works, e.method, ["hevc", "h264", "av1"].index(e.codec)))
    chosen = await transcode.pick_encoder(s.ffmpeg, s)
    if chosen == "vaapi":
        note = f"H.265 on the GPU (VAAPI, {s.vaapi_device})"
    elif s.transcode_encoder == "x265":
        note = "H.265 on the CPU (x265), chosen in Settings"
    else:
        note = f"H.265 on the CPU (x265), because VAAPI did not work on {s.vaapi_device}"
    return Hardware(devices=devs, encoders=encoders, ffmpeg_found=True, encoder=chosen, encoder_note=note,
                    checked_at=time.time())


class HardwareCache:
    """The last hardware check; checked again only when asked."""

    def __init__(self) -> None:
        self._result: Hardware | None = None
        self._lock = asyncio.Lock()

    async def get(self, s, refresh: bool = False) -> Hardware:
        async with self._lock:
            if self._result is None or refresh:
                self._result = await hardware(s)
            return self._result
