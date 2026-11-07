from __future__ import annotations

from ripshelf import discfs
from ripshelf.makemkv.parse import ScanResult, Title

from .conftest import posix_only


async def test_extract_structure_without_7z(tmp_path):
    assert await discfs.extract_structure(str(tmp_path / "no-7z"), "/dev/sr0", tmp_path / "out") == []


@posix_only
async def test_extract_structure_with_fake_7z(tmp_path):
    sz = tmp_path / "7z"
    sz.write_text('#!/bin/sh\nfor a; do case "$a" in -o*) out=${a#-o};; esac; done\n'
                  '[ -n "$out" ] || exit 0\nmkdir -p "$out/VIDEO_TS"\necho x > "$out/VIDEO_TS/VIDEO_TS.IFO"\n')
    sz.chmod(0o755)
    files = await discfs.extract_structure(str(sz), "/dev/sr0", tmp_path / "out")
    assert files == ["VIDEO_TS/VIDEO_TS.IFO"]
    assert discfs.content_fingerprint(tmp_path / "out", files).startswith("content:")


LISTING = "Path = /dev/sr0\nType = Udf\n\n----------\nPath = VIDEO_TS/VTS_01_1.VOB\nSize = 1073709056\n\n"


@posix_only
async def test_a_disc_7z_only_reads_as_udf(tmp_path):
    """A DVD 7-Zip can't open when left to guess its type (an I/O error in its other
    probes) still gives its structure files and sizes read as UDF."""
    sz = tmp_path / "7z"
    sz.write_text('#!/bin/sh\nudf=; for a; do case "$a" in -tudf) udf=1;; -o*) out=${a#-o};; esac; done\n'
                  '[ -n "$udf" ] || { echo "ERROR: /dev/sr0 : opening : errno=5 : Input/output error"; exit 2; }\n'
                  f'if [ "$1" = l ]; then printf "{LISTING}"; exit 0; fi\n'
                  'mkdir -p "$out/VIDEO_TS"\necho x > "$out/VIDEO_TS/VTS_01_0.IFO"\n')
    sz.chmod(0o755)
    files = await discfs.extract_structure(str(sz), "/dev/sr0", tmp_path / "out")
    assert files == ["VIDEO_TS/VTS_01_0.IFO"]
    assert (tmp_path / "out" / discfs.SIZES).read_text() == "VIDEO_TS/VTS_01_1.VOB\t1073709056\n"


@posix_only
async def test_an_iso_only_disc_is_read_without_a_type(tmp_path):
    sz = tmp_path / "7z"
    sz.write_text('#!/bin/sh\nfor a; do case "$a" in -tudf) exit 2;; -o*) out=${a#-o};; esac; done\n'
                  '[ -n "$out" ] || exit 0\nmkdir -p "$out/VIDEO_TS"\necho x > "$out/VIDEO_TS/VIDEO_TS.IFO"\n')
    sz.chmod(0o755)
    assert await discfs.extract_structure(str(sz), "/dev/sr0", tmp_path / "out") == ["VIDEO_TS/VIDEO_TS.IFO"]


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
