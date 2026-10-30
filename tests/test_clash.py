"""Already in the library. Caught at identification, decided by the user, never overwritten silently."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import respx
from pydantic import SecretStr

from ripshelf.models import Candidate, JobState, RipState, TitleRole

from .conftest import until
from .test_jobs_identify import mock_tmdb, practical_magic
from .test_tv_pipeline import CASE_ORDER, SD
from .tvfix import mock_show

PM = "/dev/fake-sr1"
MANUAL = Candidate(source="manual", title="Practical Magic", year=1998)


def film(settings) -> Path:
    return settings.library_dir / "Movies/Practical Magic (1998)/Practical Magic (1998).mkv"


def put(path: Path, data: bytes = b"old film") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def keyed(settings):
    settings.tmdb_api_key = SecretStr("k")
    return settings


async def test_auto_confirmed_film_already_there_holds_the_rip(harness, keyed, settings):
    old = put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            await asyncio.sleep(0.5)
            job = h.job(job.id)
            assert job.state == JobState.clash and job.rip.state == RipState.pending, "nothing ripped yet"
            assert not any(settings.scratch_dir.rglob("*.mkv"))
            [f] = job.clash.files
            assert f.path == str(old) and f.size_bytes == len(b"old film") and not job.clash.proposed
            assert any("Already in the library: Practical Magic (1998).mkv" in e.message for e in h.db.events(job.id))
            # Skip means cancelled and ejected, with no rip at all.
            await h.m.resolve_clash(job.id, "skip")
            assert h.job(job.id).state == JobState.cancelled
            assert PM not in h.backend.present
    assert old.read_bytes() == b"old film"


async def test_overwrite_rips_and_replaces_only_after_verifying(harness, keyed, settings):
    old = put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            await h.m.resolve_clash(job.id, "overwrite")
            done = await h.wait_job(lambda j: j.state == JobState.done)
    assert done.destination == str(old) and old.read_bytes() != b"old film"
    assert sorted(p.name for p in old.parent.iterdir()) == [".disc", old.name]


async def test_failed_overwrite_keeps_the_old_file(harness, keyed, settings, monkeypatch):
    from ripshelf import library

    old = put(film(settings))

    def broken(src, dst, on_progress=None, overwrite=False):
        raise library.VerifyError("checksum mismatch after copy")

    monkeypatch.setattr(library, "copy_verified", broken)
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            await h.m.resolve_clash(job.id, "overwrite")
            failed = await h.wait_job(lambda j: j.state == JobState.failed)
    assert failed.problem.code == "copy_failed" and old.read_bytes() == b"old film"


async def test_keep_both_saves_an_emby_version(harness, keyed, settings):
    old = put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            await h.m.resolve_clash(job.id, "keep_both")
            done = await h.wait_job(lambda j: j.state == JobState.done)
    new = old.parent / "Practical Magic (1998) - DVD.mkv"
    assert done.destination == str(new) and new.stat().st_size > 0 and old.read_bytes() == b"old film"


async def test_strong_best_guess_in_review_is_checked_before_ripping(harness, settings):
    # No auto-confirm (threshold above the score), but the best guess clearly is the film on disk.
    settings.tmdb_api_key = SecretStr("k")
    settings.auto_confirm_threshold = 1.0
    put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        mock_tmdb(router, [{"id": 6435, "title": "Practical Magic", "release_date": "1998-10-16"}],
                  {"6435": {"runtime": 109}})  # a few minutes off, so scores below 1
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            assert job.clash.proposed and job.identity is None and job.rip.state == RipState.pending
            # It isn't that film after all, so confirming another one starts the rip.
            await h.m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=2001))
            await h.wait_job(lambda j: j.state == JobState.done)


async def test_identity_confirmed_during_the_rip_lets_the_rip_finish_and_waits(harness, settings):
    put(film(settings))
    async with harness(PM) as h:  # no TMDB key, so review, and the rip runs meanwhile
        job = await h.wait_job(lambda j: j.state == JobState.review)
        assert job.rip.state in (RipState.running, RipState.done)
        await h.m.confirm(job.id, MANUAL)
        held = await h.wait_job(lambda j: j.state == JobState.clash and j.rip.state == RipState.done)
        await asyncio.sleep(0.3)
        assert h.job(held.id).state == JobState.clash, "the copy waits for the decision"
        await h.m.resolve_clash(held.id, "overwrite")
        await h.wait_job(lambda j: j.state == JobState.done)


async def test_a_file_appearing_during_the_copy_is_never_overwritten(harness, settings, monkeypatch):
    # The last check is in the copy itself, if something else wrote the file meanwhile.
    from ripshelf import library

    real = library.copy_verified

    def racing(src, dst, on_progress=None, overwrite=False):
        put(dst, b"arrived during the copy")
        return real(src, dst, on_progress, overwrite)

    monkeypatch.setattr(library, "copy_verified", racing)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.done)
        await h.m.confirm(job.id, MANUAL)
        held = await h.wait_job(lambda j: j.state == JobState.clash)
    assert held.clash.files[0].path == str(film(settings))
    assert film(settings).read_bytes() == b"arrived during the copy"
    assert not list(film(settings).parent.glob(".*.partial"))


async def test_tv_partial_clash_skips_only_the_clashing_episode(harness, settings):
    settings.tmdb_api_key = SecretStr("k")
    clash = put(settings.library_dir / "TV/Sarah & Duck (2013)/Season 03/Sarah & Duck - S03E28 - Pillow Fill.mkv",
                b"keep")
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "sarah-duck")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            await h.m.tv_match_text(job.id, CASE_ORDER)
            await h.m.tv_confirm(job.id, [])
            held = await h.wait_job(lambda j: j.state == JobState.clash)
            with pytest.raises(ValueError, match="for films"):
                await h.m.resolve_clash(held.id, "keep_both")
            await h.m.resolve_clash(held.id, "skip_episodes")
            done = await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
    assert clash.read_bytes() == b"keep"
    season = sorted(p.name for p in clash.parent.iterdir())
    assert len(season) == 10 and clash.name in season
    assert any(m.role == TitleRole.skip for m in done.tv.mapping)


async def test_clash_api(harness, settings):
    from fastapi.testclient import TestClient

    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        m = c.app.state.manager
        j = m.db.create_job("fake-ihas122", PM)
        assert c.post(f"/api/jobs/{j.id}/clash", json={"action": "overwrite"}).status_code == 409
        assert c.post(f"/api/jobs/{j.id}/clash", json={"action": "nonsense"}).status_code == 422


def test_exists_notification_defaults_on_even_for_old_settings(settings):
    from ripshelf import integrations

    settings.notify_events = {"review": 1, "done": 0}  # saved before the event existed
    assert integrations.Notifier(settings).priority("exists") == 1
    assert integrations.EVENTS["exists"] == "Already in library"


@pytest.mark.parametrize("label, name, kind, want", [
    ("BLADE_RUNNER_DIRECTORS_CUT", None, "bluray", "Director's Cut"),
    ("ALIEN_DC", None, "dvd", "Director's Cut"),
    ("LOTR_EXT_DISC1", None, "bluray", "Extended"),
    ("FILM", "Film - Theatrical Edition", "bluray", "Theatrical"),
    ("THE_WILD_ROBOT", None, "uhd", "2160p"),
    ("PRACTICAL_MAGIC", None, "bluray", "1080p"),
    ("PRACTICAL_MAGIC", None, "dvd", "DVD"),
    ("ODD_THING", None, None, "Odd Thing"),
])
def test_keep_both_version_label(label, name, kind, want):
    from ripshelf.db import JobRecord, now
    from ripshelf.jobs import version_label
    from ripshelf.models import DiscInfo

    job = JobRecord(id=1, drive_id="d", state=JobState.clash, created_at=now(), updated_at=now(),
                    disc=DiscInfo(label=label, name=name, kind=kind))
    assert version_label(job) == want


async def test_keep_both_name_is_offered_and_numbered_if_taken(harness, keyed, settings):
    put(film(settings))
    put(film(settings).parent / "Practical Magic (1998) - DVD.mkv")
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            assert job.clash.keep_both_name == "Practical Magic (1998) - DVD 2.mkv"


# --- eject to check -------------------------------------------------------------------------


async def test_eject_to_check_and_the_same_disc_carries_on(harness, keyed, settings):
    put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            checking = await h.m.check_disc(job.id)
            assert checking.state == JobState.checking and checking.checking_from == JobState.clash
            assert PM not in h.backend.present
            await asyncio.sleep(0.5)
            assert h.job(job.id).state == JobState.checking, "an empty drive doesn't fail it"
            h.backend.insert(PM)
            back = await h.wait_job(lambda j: j.state == JobState.clash)
            assert back.id == job.id and back.clash.files and back.checking_from is None
            assert len(h.db.list_jobs()) == 1, "no rescan, no new job"
            assert any("Same disc back" in e.message for e in h.db.events(job.id))


async def test_eject_to_check_and_a_different_disc_ends_the_job(harness, keyed, settings):
    put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash)
            await h.m.check_disc(job.id)
            h.backend.insert(PM, label="SOMETHING_ELSE")
            await until(lambda: len(h.db.list_jobs()) == 2)
            old = h.job(job.id)
            assert old.state == JobState.cancelled
            assert any("a different disc went in" in e.message for e in h.db.events(job.id))


async def test_eject_to_check_is_refused_mid_rip_and_when_not_waiting(harness, settings):
    async with harness(PM) as h:  # no TMDB key, so review while the rip runs
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.running)
        with pytest.raises(ValueError, match="MakeMKV is using the drive"):
            await h.m.check_disc(job.id)
        done = await h.wait_job(lambda j: j.rip.state == RipState.done)
        checking = await h.m.check_disc(done.id)  # review with the rip finished is fine
        assert checking.state == JobState.checking
        with pytest.raises(ValueError, match="only a job waiting"):
            await h.m.check_disc(done.id)


async def test_identify_again_from_a_held_clash_then_rips(harness, keyed, settings):
    put(film(settings))
    with respx.mock(assert_all_called=False) as router:
        practical_magic(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.clash and j.rip.state == RipState.pending)
            film(settings).unlink()  # it was a mistake, and removed from the library meanwhile
            await h.m.identify_again(job.id)
            done = await h.wait_job(lambda j: j.state == JobState.done)
    assert done.destination == str(film(settings)) and film(settings).stat().st_size > 0


# --- eject to check during TV review ----------------------------------------------------------


async def test_tv_check_mid_rip_keeps_finished_titles_and_rips_the_rest(harness, settings, monkeypatch):
    from .test_tv_pipeline import SD

    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "6")
    settings.auto_eject = False
    async with harness(SD) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.running)
        out = h.m.scratch_dir(job)
        await until(lambda: len(list(out.glob("*.mkv"))) >= 3, timeout=20)
        with pytest.raises(ValueError, match="rip is running"):
            await h.m.check_disc(job.id)  # needs the confirm (pause_rip)
        paused = await h.m.check_disc(job.id, pause_rip=True)
        assert paused.state == JobState.checking and paused.rip.paused and paused.rip.state == RipState.pending
        kept = dict(paused.rip.files)
        assert 2 <= len(kept) < 11 and all(Path(f).exists() for f in kept.values())
        assert len(list(out.glob("*.mkv"))) == len(kept), "the half-written title is gone"
        mtimes = {i: Path(f).stat().st_mtime for i, f in kept.items()}
        monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.3")  # each remaining title is a run of its own
        h.backend.insert(SD)
        done = await h.wait_job(lambda j: j.rip.state == RipState.done and j.state != JobState.checking, timeout=60)
        assert len(done.rip.files) == 11
        assert all(Path(done.rip.files[i]).stat().st_mtime == m for i, m in mtimes.items()), "kept, not redone"
        assert any("Ripping the" in e.message and "unfinished titles" in e.message for e in h.db.events(job.id))


async def test_tv_confirm_with_the_disc_out_needs_every_mapped_title(harness, settings, monkeypatch):
    from .test_tv_pipeline import CASE_ORDER, SD, keyed  # noqa: F401
    from .tvfix import mock_show

    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.5")
    settings.tmdb_api_key = SecretStr("k")
    settings.auto_eject = False
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "sarah-duck")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            await h.m.tv_match_text(job.id, CASE_ORDER)
            checking = await h.m.check_disc(job.id)  # rip finished, so no pause needed
            assert checking.state == JobState.checking
            await h.m.tv_confirm(job.id, [])  # every title is ripped, so no need for the disc
            await h.wait_job(lambda j: j.state == JobState.done, timeout=60)


async def test_tv_confirm_with_the_disc_out_and_titles_missing_is_refused(harness, settings, monkeypatch):
    from .test_tv_pipeline import CASE_ORDER, SD
    from .tvfix import mock_show

    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "6")
    settings.tmdb_api_key = SecretStr("k")
    settings.auto_eject = False
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "sarah-duck")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == RipState.running
                                   and j.tv and j.tv.show, timeout=60)
            await h.m.tv_match_text(job.id, CASE_ORDER)
            await until(lambda: len(list(h.m.scratch_dir(job).glob("*.mkv"))) >= 2, timeout=20)
            await h.m.check_disc(job.id, pause_rip=True)
            with pytest.raises(ValueError, match="aren't ripped yet"):
                await h.m.tv_confirm(job.id, [])
            assert h.job(job.id).state == JobState.checking
