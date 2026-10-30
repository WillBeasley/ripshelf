"""Library naming and verified copies."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from ripshelf import library
from ripshelf.library import (
    LibraryError,
    VerifyError,
    check_library,
    copy_verified,
    movie_paths,
    safe_name,
    write_disc_folder,
)


@pytest.mark.parametrize("raw, want", [
    ("Mission: Impossible", "Mission - Impossible"),
    ('Who? "Me" <You> | Them * /path\\x', "Who Me You Them path x"),
    ("..", "Unknown"),
    ("../../etc/passwd", "etc passwd"),
    ("/absolute", "absolute"),
    (".hidden", "hidden"),
    ("Trailing dots...", "Trailing dots"),
    ("  spaces  ", "spaces"),
    ("", "Unknown"),
    ("\x00\x01\x1f", "Unknown"),
    ("CON", "CON_"),
    ("nul", "nul_"),
    ("Com1.txt", "Com1.txt_"),
    ("Console", "Console"),
    ("Amélie", "Amélie"),
    ("千と千尋の神隠し", "千と千尋の神隠し"),
])
def test_safe_name(raw, want):
    assert safe_name(raw) == want


def test_safe_name_length_is_bounded_in_bytes():
    name = safe_name("千" * 200)
    assert len(name.encode()) <= library.MAX_NAME_BYTES
    assert name and "�" not in name


def test_movie_paths_cannot_escape_the_library(tmp_path):
    for title in ("../../outside", "..", "/etc", "a/../../b", "Movies/../.."):
        folder, dest = movie_paths(tmp_path / "Movies", title, 2000, uhd=False)
        assert tmp_path / "Movies" in folder.resolve().parents or folder.resolve().parent == (tmp_path / "Movies")
        assert dest.parent == folder


def test_movie_paths_naming(tmp_path):
    folder, dest = movie_paths(tmp_path / "Movies", "The Wild Robot", 2024, uhd=True)
    assert dest == tmp_path / "Movies/The Wild Robot (2024)/The Wild Robot (2024) - 2160p.mkv"
    _, dest = movie_paths(tmp_path / "Movies", "Untitled", None, uhd=False)
    assert dest.name == "Untitled.mkv"


def src_file(tmp_path: Path, size: int = 3 * library.CHUNK // 2) -> Path:
    p = tmp_path / "scratch.mkv"
    p.write_bytes(b"\x01" * size)
    return p


def test_copy_verified(tmp_path):
    src = src_file(tmp_path)
    seen = []
    sha = copy_verified(src, tmp_path / "lib/a/b.mkv", lambda d, t, v: seen.append((d, t)))
    assert (tmp_path / "lib/a/b.mkv").read_bytes() == src.read_bytes()
    assert len(sha) == 64 and seen[-1] == (2 * src.stat().st_size, 2 * src.stat().st_size)
    assert not list((tmp_path / "lib/a").glob(".*partial"))


def test_copy_refuses_existing_destination(tmp_path):
    src = src_file(tmp_path)
    dst = tmp_path / "dst.mkv"
    dst.write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        copy_verified(src, dst)
    assert dst.read_bytes() == b"keep"


def test_copy_refuses_destination_that_appears_during_copy(tmp_path):
    src = src_file(tmp_path)
    dst = tmp_path / "dst.mkv"

    def sneak(done, total, _verifying):
        if done == total:
            dst.write_bytes(b"other")

    with pytest.raises(FileExistsError):
        copy_verified(src, dst, sneak)
    assert dst.read_bytes() == b"other"
    assert not list(tmp_path.glob(".*partial"))


def test_checksum_mismatch(tmp_path, monkeypatch):
    src = src_file(tmp_path)
    dst = tmp_path / "dst.mkv"
    real_open = Path.open

    def corrupting_open(self, mode="r", *a, **kw):
        f = real_open(self, mode, *a, **kw)
        if self.name.endswith(".partial") and mode == "rb":
            data = bytearray(f.read())
            data[0] ^= 0xFF
            f.close()
            import io
            return io.BytesIO(bytes(data))
        return f

    monkeypatch.setattr(Path, "open", corrupting_open)
    with pytest.raises(VerifyError, match="checksum"):
        copy_verified(src, dst)
    assert not dst.exists() and not list(tmp_path.glob(".*partial"))


def test_size_mismatch(tmp_path, monkeypatch):
    src = src_file(tmp_path, 1000)
    dst = tmp_path / "dst.mkv"
    real_stat = Path.stat

    def short_stat(self, *a, **kw):
        st = real_stat(self, *a, **kw)
        if self.name.endswith(".partial"):
            import os
            return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid, st.st_gid,
                                   st.st_size - 1, st.st_atime, st.st_mtime, st.st_ctime))
        return st

    monkeypatch.setattr(Path, "stat", short_stat)
    with pytest.raises(VerifyError, match="size mismatch"):
        copy_verified(src, dst)
    assert not dst.exists()


def test_write_error_removes_partial(tmp_path):
    src = src_file(tmp_path)

    def full(done, total, _verifying):
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        copy_verified(src, tmp_path / "dst.mkv", full)
    assert not list(tmp_path.glob(".*partial"))


def test_check_library(tmp_path):
    check_library(tmp_path)
    with pytest.raises(LibraryError, match="does not exist"):
        check_library(tmp_path / "missing")
    f = tmp_path / "file"
    f.write_text("")
    with pytest.raises(LibraryError):
        check_library(f)


def test_write_disc_folder(tmp_path):
    scan = tmp_path / "scan.log"
    scan.write_text("MSG:...")
    structure = tmp_path / "structure"
    (structure / "BDMV/META/DL").mkdir(parents=True)
    (structure / "BDMV/index.bdmv").write_bytes(b"INDX")
    (structure / "BDMV/META/DL/bdmt_eng.xml").write_text("<x/>")
    meta = write_disc_folder(tmp_path / "Movie", {"title": "X", "when": object()}, scan, structure)
    assert json.loads((meta / "disc.json").read_text())["title"] == "X"
    assert (meta / "makemkv-scan.log").exists() and (meta / "BDMV/index.bdmv").exists()
    # Missing inputs are fine.
    write_disc_folder(tmp_path / "Other", {}, tmp_path / "nope.log", None)


def test_copy_verified_across_filesystems(tmp_path):
    # Scratch and library on different filesystems, so no rename across them, only
    # a copy, then a rename within the destination folder.
    import tempfile

    shm = Path("/dev/shm")
    if not shm.is_dir() or os.stat(shm).st_dev == os.stat(tmp_path).st_dev:
        pytest.skip("needs /dev/shm on another filesystem")
    with tempfile.TemporaryDirectory(dir=shm) as d:
        src = Path(d) / "rip.mkv"
        src.write_bytes(os.urandom(3 * 1024 * 1024 + 7))
        dest = tmp_path / "Movies" / "Film (2000)" / "Film (2000).mkv"
        sha = copy_verified(src, dest)
        assert dest.read_bytes() == src.read_bytes()
        assert sha == hashlib.sha256(src.read_bytes()).hexdigest()
        assert not list(dest.parent.glob(".*.partial"))


def test_check_library_messages_and_read_only_folder(tmp_path):
    with pytest.raises(LibraryError, match=r"^movies folder .*missing does not exist \(is the share mounted\?\)$"):
        check_library(tmp_path / "missing", "movies folder")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)  # readable and searchable, not writable
    try:
        if os.access(ro, os.W_OK):
            pytest.skip("running as root, so permissions are not enforced")
        with pytest.raises(LibraryError, match=r"^library folder .*ro is not writable$"):
            check_library(ro)
    finally:
        ro.chmod(0o700)
    no_x = tmp_path / "nox"
    no_x.mkdir()
    no_x.chmod(0o600)  # writable but not searchable, so files can't be created in it
    try:
        if not os.access(no_x, os.X_OK):
            with pytest.raises(LibraryError, match="not writable"):
                check_library(no_x)
    finally:
        no_x.chmod(0o700)


def test_copy_verified_cleans_up_after_a_failed_check(tmp_path, monkeypatch):
    src = tmp_path / "rip.mkv"
    src.write_bytes(os.urandom(256 * 1024))
    dest = tmp_path / "lib" / "Film.mkv"

    # The read-back hash differs from the one taken while writing, from bad media or RAM.
    real = hashlib.sha256
    calls = []

    def flaky(*a):
        calls.append(1)
        return real(b"corrupt") if len(calls) == 2 else real(*a)

    monkeypatch.setattr(library.hashlib, "sha256", flaky)
    with pytest.raises(VerifyError, match="^checksum mismatch after copy$"):
        copy_verified(src, dest)
    assert not dest.exists() and list(dest.parent.iterdir()) == []
    monkeypatch.setattr(library.hashlib, "sha256", real)

    # A short write (the share filled up) is a size mismatch, and also cleaned up.
    real_open = Path.open

    def short_open(self, mode="r", *a, **kw):
        f = real_open(self, mode, *a, **kw)
        if "w" in mode:
            write = f.write
            f.write = lambda b: write(b[: len(b) // 2])
        return f

    monkeypatch.setattr(Path, "open", short_open)
    with pytest.raises(VerifyError, match=r"^size mismatch after copy \(\d+ != 262144\)$"):
        copy_verified(src, dest)
    assert list(dest.parent.iterdir()) == []


def test_copy_verified_refuses_an_existing_file_with_its_name(tmp_path):
    src = tmp_path / "a.mkv"
    src.write_bytes(b"x")
    dest = tmp_path / "b.mkv"
    dest.write_bytes(b"old")
    with pytest.raises(FileExistsError, match=r"b\.mkv already exists"):
        copy_verified(src, dest)
    assert dest.read_bytes() == b"old"


def test_copy_verified_reports_the_copy_then_the_check(tmp_path):
    src = tmp_path / "a.mkv"
    src.write_bytes(os.urandom(library.CHUNK * 2 + 10))
    seen = []
    copy_verified(src, tmp_path / "out" / "a.mkv", lambda *a: seen.append(a))
    size = src.stat().st_size
    assert all(t == 2 * size for _, t, _ in seen) and len(seen) == 6
    assert [v for _, _, v in seen] == [False] * 3 + [True] * 3, "the copy, then reading it back"
    assert seen[2][0] == size and seen[-1][0] == 2 * size
    assert [d for d, _, _ in seen] == sorted(d for d, _, _ in seen)


async def test_finalise_shows_the_copy_then_the_check(harness, monkeypatch):
    from ripshelf import jobs
    from ripshelf.models import Candidate, JobState

    monkeypatch.setattr(jobs, "PROGRESS_PUBLISH_S", 0)  # the fake film copies in a blink
    async with harness("/dev/fake-sr1") as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        with h.bus.subscribe() as q:
            await h.m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
            await h.wait_job(lambda j: j.state == JobState.done)
            seen = []
            while not q.empty():
                seen.append(json.loads(q.get_nowait()))
        bars = [e["progress"] for e in seen if e.get("type") == "progress" and e["job_id"] == job.id]
        phases = [p["phase"] for p in bars]
        assert "Copying to library" in phases and phases[-1] == "Verifying the copy"
        assert bars[-1]["fraction"] == 1 and bars[-1]["bytes_done"] == bars[-1]["bytes_total"]
