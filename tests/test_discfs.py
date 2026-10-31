from __future__ import annotations

from ripshelf import discfs
from ripshelf.makemkv.parse import ScanResult, Title

from .conftest import posix_only


async def test_extract_structure_without_7z(tmp_path):
    assert await discfs.extract_structure(str(tmp_path / "no-7z"), "/dev/sr0", tmp_path / "out") == []


@posix_only
async def test_extract_structure_with_fake_7z(tmp_path):
    sz = tmp_path / "7z"
    sz.write_text('#!/bin/sh\nout=$(echo "$4" | sed s/^-o//)\nmkdir -p "$out/VIDEO_TS"\necho x > "$out/VIDEO_TS/VIDEO_TS.IFO"\n')
    sz.chmod(0o755)
    files = await discfs.extract_structure(str(sz), "/dev/sr0", tmp_path / "out")
    assert files == ["VIDEO_TS/VIDEO_TS.IFO"]
    assert discfs.content_fingerprint(tmp_path / "out", files).startswith("content:")


async def test_extract_structure_timeout(tmp_path):
    sz = tmp_path / "7z"
    sz.write_text("#!/bin/sh\nexec sleep 30\n")
    sz.chmod(0o755)
    assert await discfs.extract_structure(str(sz), "/dev/sr0", tmp_path / "out", timeout=0.5) == []


def test_bdmt_title(tmp_path):
    assert discfs.bdmt_title(tmp_path) is None
    dl = tmp_path / "BDMV/META/DL"
    dl.mkdir(parents=True)
    (dl / "bdmt_fra.xml").write_text("<di:name>Le Héron de Laiton</di:name>")
    assert discfs.bdmt_title(tmp_path) == "Le Héron de Laiton"
    (dl / "bdmt_eng.xml").write_text("<di:name> Swift &amp; Fierce </di:name>")
    assert discfs.bdmt_title(tmp_path) == "Swift & Fierce"
    (dl / "bdmt_eng.xml").write_bytes(b"<di:name>  </di:name>\xff")
    assert discfs.bdmt_title(tmp_path) == "Le Héron de Laiton"


def test_fingerprints():
    assert discfs.content_fingerprint(None, []) is None
    assert discfs.quick_fingerprint(None, None, 5) is None
    assert discfs.quick_fingerprint("A", None, None) != discfs.quick_fingerprint("A", None, 1)
    assert discfs.scan_fingerprint(ScanResult()) is None
    a = ScanResult(titles=[Title(index=0, duration_s=100, size_bytes=5)])
    b = ScanResult(titles=[Title(index=0, duration_s=101, size_bytes=5)])
    assert discfs.scan_fingerprint(a) != discfs.scan_fingerprint(b)
