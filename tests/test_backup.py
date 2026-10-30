"""Full disc backups (rip mode "backup"), a decrypted BDMV folder instead of MKV titles."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ripshelf import library
from ripshelf.config import Settings
from ripshelf.makemkv.parse import ProgressTracker, parse_info
from ripshelf.models import Candidate, JobState

from .conftest import posix_only, until
from .test_pipeline import run_manager

UHD = "/dev/fake-sr2"


async def test_uhd_disc_is_backed_up_whole(settings: Settings):
    settings.rip_mode = "backup"
    m, db, backend = await run_manager(settings, {UHD})
    try:
        job = await until(lambda: next(iter(db.list_jobs([JobState.review.value])), None))
        assert job.mode == "backup" and job.media == "movie"
        assert job.disc_index == 1  # the Pioneer's number in its scan log
        await m.confirm(job.id, Candidate(source="manual", title="The Wild Robot", year=2024))
        done = await until(lambda: (j := db.get_job(job.id)) and j.state == JobState.done and j)
        dest = Path(done.destination)
        assert dest == settings.library_dir / "Disc backups/The Wild Robot (2024) - 2160p"
        assert (dest / "BDMV/index.bdmv").read_bytes().startswith(b"INDX")
        assert (dest / "BDMV/STREAM/00001.m2ts").stat().st_size == 64 * 1024
        assert (dest / "CERTIFICATE/id.bdmv").exists()
        meta = json.loads((dest / ".disc/disc.json").read_text())
        assert meta["title"] == "The Wild Robot" and meta["backup"]["decrypted"]
        assert set(meta["backup"]["files"]) >= {"BDMV/index.bdmv", "BDMV/STREAM/00001.m2ts"}
        assert done.transcode is None, "backups are never transcoded"
        assert not any(settings.scratch_dir.iterdir()), "scratch cleaned up"
        assert not list(dest.parent.glob(".*.partial"))
        rip_log = (settings.jobs_dir / str(job.id) / "rip.log").read_text()
        assert "Backup done." in rip_log
        assert UHD not in backend.present, "auto-ejected"
    finally:
        await m.stop()
        db.close()


async def test_dvd_is_ripped_as_titles_in_backup_mode(settings: Settings):
    settings.rip_mode = "backup"
    m, db, _ = await run_manager(settings, {"/dev/fake-sr1"})
    try:
        job = await until(lambda: next(iter(db.list_jobs([JobState.review.value])), None))
        assert job.mode == "titles"
        assert any("Blu-ray and UHD discs only" in e.message for e in db.events(job.id))
        await m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
        done = await until(lambda: (j := db.get_job(job.id)) and j.state == JobState.done and j)
        assert done.destination.endswith("Movies/Practical Magic (1998)/Practical Magic (1998).mkv")
    finally:
        await m.stop()
        db.close()


async def test_per_drive_mode_overrides_the_default(settings: Settings):
    settings.drive_modes = {"fake-pioneer": "backup"}
    assert settings.rip_mode_for("fake-pioneer") == "backup"
    assert settings.rip_mode_for("fake-ihas122") == "titles"
    m, db, _ = await run_manager(settings, {UHD})
    try:
        job = await until(lambda: next(iter(db.list_jobs([JobState.review.value])), None))
        assert job.mode == "backup"
        # Planned while ripping, then kept, as the backup folder, not an .mkv
        await m.confirm(job.id, Candidate(source="manual", title="The Wild Robot", year=2024))
        assert db.get_job(job.id).destination.endswith("Disc backups/The Wild Robot (2024) - 2160p")
        with pytest.raises(ValueError, match="no episodes"):
            await m.set_media(job.id, "tv")
        await until(lambda: db.get_job(job.id).state == JobState.done)
    finally:
        await m.stop()
        db.close()


@posix_only
async def test_backup_without_done_message_fails(settings: Settings, tmp_path: Path):
    """makemkvcon exiting 0 without "Backup done." (or with hash check failures) is a failed rip."""
    script = tmp_path / "mkv.sh"
    script.write_text(f"""#!/bin/sh
case " $* " in
  *" backup "*) for a; do out=$a; done; mkdir -p "$out/BDMV"
     echo 'MSG:5080,0,1,"Backup done but 2 files failed hash check.","x","2"'; exit 0;;
  *) exec {settings.makemkvcon} "$@";;
esac
""")
    script.chmod(0o755)
    settings.makemkvcon = str(script)
    settings.rip_mode = "backup"
    m, db, _ = await run_manager(settings, {UHD})
    try:
        job = await until(lambda: next((j for j in db.list_jobs() if j.rip.state == "failed"), None))
        assert job.problem.code == "rip_failed"
        assert "failed hash check" in job.problem.message and "backup failed" in job.problem.message
    finally:
        await m.stop()
        db.close()


def test_disc_index_from_scan(fixture_text):
    scan = parse_info(fixture_text("scan-uhd-wild-robot.log"))
    assert scan.disc_index("/dev/sr2", None) == 1
    # A device the scan doesn't name (a worker's, or a fake one) is matched by the disc's label.
    assert scan.disc_index("/dev/fake-sr2", "THE_WILD_ROBOT") == 1
    assert scan.disc_index("/dev/fake-sr2", "SOMETHING_ELSE") is None


def test_backup_progress_counts_the_copy_not_the_disc_open():
    t = ProgressTracker(bytes_total=1000, backup=True)
    for line in ['PRGT:5018,0,"Scanning CD-ROM devices"', "PRGV:0,65536,65536",
                 'PRGT:3404,0,"Opening Blu-ray disc"', "PRGV:0,65536,65536"]:
        t.feed(line)
    assert t.fraction == 0
    t.feed('PRGT:5026,0,"Copying all files"')
    assert t.feed("PRGV:100,32768,65536") and t.fraction == 0.5 and t.phase == "Copying all files"
    # Titles mode ignores the same lines, because only "Saving" counts there.
    plain = ProgressTracker(bytes_total=1000)
    plain.feed('PRGT:5026,0,"Copying all files"')
    assert not plain.feed("PRGV:100,32768,65536")


def test_backup_done_message():
    def done(text: str) -> bool:
        return parse_info(f'MSG:5080,0,0,"{text}","{text}"\n').backup_done

    assert done("Backup done.")
    assert not done("Backup done but 3 files failed hash check.")
    assert not done("Backup failed.")


def test_copy_tree_verified(tmp_path: Path):
    src = tmp_path / "src"
    (src / "BDMV/STREAM").mkdir(parents=True)
    (src / "BDMV/index.bdmv").write_bytes(b"INDX")
    (src / "BDMV/STREAM/00001.m2ts").write_bytes(b"x" * 1000)
    seen = []
    hashes = library.copy_tree_verified(src, tmp_path / "out/Film (2000)", lambda d, t, v: seen.append((d, t)))
    assert set(hashes) == {"BDMV/index.bdmv", "BDMV/STREAM/00001.m2ts"}
    assert (tmp_path / "out/Film (2000)/BDMV/STREAM/00001.m2ts").read_bytes() == b"x" * 1000
    assert seen[-1] == (2008, 2008)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert library.tree_size(tmp_path / "out/Film (2000)") == 1004
    with pytest.raises(FileExistsError):
        library.copy_tree_verified(src, tmp_path / "out/Film (2000)")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["Film (2000)"], "no .partial left"


def test_backup_path():
    root = Path("/b")
    assert library.backup_path(root, "Alien: Romulus", 2024, False) == root / "Alien - Romulus (2024)"
    assert library.backup_path(root, "Dune", None, True) == root / "Dune - 2160p"


def test_backup_folder_is_checked_only_when_used(tmp_path: Path):
    s = Settings(_env_file=None, library_dir=tmp_path, movies_dir=tmp_path / "m", tv_dir=tmp_path / "t",
                 disc_backups_dir=tmp_path / "b")
    assert (tmp_path / "b", "disc backups folder") not in s.output_roots()
    s.drive_modes = {"x": "backup"}
    assert (tmp_path / "b", "disc backups folder") in s.output_roots()
