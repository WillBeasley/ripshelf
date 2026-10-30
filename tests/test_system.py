"""Storage per folder and hardware encoder detection."""

from __future__ import annotations

import asyncio
import shutil
import stat
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from ripshelf import system
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app

from .conftest import posix_only

ENCODERS = """\
Encoders:
 V..... = Video
 ------
 V....D libx265              libx265 H.265 / HEVC (codec hevc)
 V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 V....D hevc_vaapi           H.265/HEVC (VAAPI) (codec hevc)
 V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 V....D hevc_qsv             HEVC (Intel Quick Sync Video acceleration) (codec hevc)
 V....D mjpeg_vaapi          MJPEG (VAAPI) (codec mjpeg)
 A....D aac                  AAC (Advanced Audio Coding)
"""


def test_parse_encoders_keeps_hardware_video_codecs_only():
    assert system.parse_encoders(ENCODERS) == [
        ("h264", "vaapi", "H.264/AVC (VAAPI) (codec h264)"),
        ("hevc", "vaapi", "H.265/HEVC (VAAPI) (codec hevc)"),
        ("hevc", "nvenc", "NVIDIA NVENC hevc encoder (codec hevc)"),
        ("hevc", "qsv", "HEVC (Intel Quick Sync Video acceleration) (codec hevc)"),
    ]


def test_first_error_drops_the_prefix_and_the_consequences():
    err = (b"[hevc_qsv @ 0x6004574] Error creating a MFX session: -9.\n"
           b"[out#0/null @ 0x60045] Nothing was written into output file\n")
    assert system._first_error(err) == "Error creating a MFX session: -9."
    assert system._first_error(b"") is None


def test_storage_groups_folders_on_one_disk_and_reports_missing(settings, tmp_path):
    settings.movies_dir = tmp_path / "library"  # exists
    disks = system.storage(settings)
    assert len(disks) == 1  # everything under tmp_path is one filesystem
    d = disks[0]
    assert d.total and d.free is not None and d.used is not None and d.free <= d.total
    by_name = {f.name: f for f in d.folders}
    assert by_name["Movies"].exists
    assert not by_name["TV"].exists  # library/TV is made on the first TV rip
    assert "Disc backups" not in by_name  # no drive makes backups

    settings.rip_mode = "backup"
    names = [f.name for d in system.storage(settings) for f in d.folders]
    assert "Disc backups" in names


def test_storage_warns_when_scratch_is_short(settings, monkeypatch):
    gb = 1024**3
    usage = shutil._ntuple_diskusage(1000 * gb, 950 * gb, 50 * gb)  # 1000 GB disk, 50 GB free
    monkeypatch.setattr(system.shutil, "disk_usage", lambda p: usage)
    [d] = system.storage(settings)
    assert d.free == 50 * gb and d.warning == "Less than 10% free"

    usage = shutil._ntuple_diskusage(500 * gb, 401 * gb, 99 * gb)  # 20% free, but scratch wants 100 GB for UHD
    [d] = system.storage(settings)
    assert d.warning == "Under 100 GB free, which isn't enough for a UHD rip"


@posix_only
def test_devices_from_sysfs(tmp_path):
    dev, sysfs = tmp_path / "dev", tmp_path / "sys"
    (dev / "dri").mkdir(parents=True)
    node = dev / "dri" / "renderD128"
    node.touch()
    node.chmod(stat.S_IRUSR | stat.S_IWUSR)
    (dev / "nvidia0").touch()
    pci = sysfs / "devices" / "pci0000:00" / "0000:00:02.0"
    drv = sysfs / "bus" / "pci" / "drivers" / "i915"
    pci.mkdir(parents=True)
    drv.mkdir(parents=True)
    (pci / "vendor").write_text("0x8086\n")
    (pci / "driver").symlink_to(drv)
    (sysfs / "class" / "drm" / "renderD128").mkdir(parents=True)
    (sysfs / "class" / "drm" / "renderD128" / "device").symlink_to(pci)

    devs = system.devices(dev, sysfs)
    assert [(d.path, d.vendor, d.driver, d.accessible) for d in devs] == [
        (str(node), "Intel", "i915", True),
        (str(dev / "nvidia0"), "NVIDIA", "nvidia", True),
    ]


def _fake_ffmpeg(tmp_path: Path, working: set[str]) -> str:
    """An 'ffmpeg' that lists ENCODERS and succeeds only for encoders in `working`."""
    script = tmp_path / "ffmpeg"
    script.write_text(f"""#!{sys.executable}
import sys
args = sys.argv[1:]
if "-encoders" in args:
    print({ENCODERS!r}); sys.exit(0)
enc = args[args.index("-c:v") + 1]
if enc in {sorted(working)!r} and not ("main10" in args and enc == "hevc_vaapi"):
    sys.exit(0)
print(f"[{{enc}} @ 0x1] Cannot open {{enc}}", file=sys.stderr); sys.exit(1)
""")
    script.chmod(0o755)
    return str(script)


@posix_only
def test_hardware_tests_each_encoder_and_skips_missing_gpus(settings, tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / "dri").mkdir(parents=True)
    (dev / "dri" / "renderD128").touch()
    settings.ffmpeg = _fake_ffmpeg(tmp_path, {"hevc_vaapi"})
    settings.vaapi_device = str(dev / "dri" / "renderD128")
    monkeypatch.setattr(system, "devices", lambda d, s: [
        system.HwDevice(path=str(dev / "dri" / "renderD128"), vendor="Intel", driver="i915", accessible=True)])

    hw = asyncio.run(system.hardware(settings, dev, tmp_path / "sys"))
    by_name = {e.name: e for e in hw.encoders}
    assert hw.ffmpeg_found and hw.encoder == "vaapi"
    assert by_name["hevc_vaapi"].works and by_name["hevc_vaapi"].used_by_ripshelf
    assert by_name["hevc_vaapi"].ten_bit is False  # Main10 test failed
    assert not by_name["h264_vaapi"].works and "Cannot open h264_vaapi" in by_name["h264_vaapi"].error
    assert by_name["hevc_qsv"].tested and not by_name["hevc_qsv"].works  # Intel GPU, so tried
    assert not by_name["hevc_nvenc"].tested and by_name["hevc_nvenc"].error == "No NVIDIA GPU found"
    assert hw.encoders[0].name == "hevc_vaapi"  # working ones first


def test_hardware_without_ffmpeg(settings, tmp_path):
    settings.ffmpeg = str(tmp_path / "no-ffmpeg")
    hw = asyncio.run(system.hardware(settings, tmp_path, tmp_path))
    assert not hw.ffmpeg_found and hw.encoder == "x265" and hw.encoders == []


@posix_only  # a script as ffmpeg
def test_api(settings, tmp_path):
    settings.ffmpeg = _fake_ffmpeg(tmp_path, set())
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        disks = c.get("/api/system/storage").json()
        assert any(f["name"] == "Scratch (rips in progress)" for d in disks for f in d["folders"])
        first = c.get("/api/system/hardware").json()
        assert first["ffmpeg_found"]
        assert c.get("/api/system/hardware").json()["checked_at"] == first["checked_at"]  # cached
        assert c.get("/api/system/hardware?refresh=true").json()["checked_at"] > first["checked_at"]
