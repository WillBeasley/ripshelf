"""Identification inside the pipeline (TMDB mocked), notifications, Emby, hung drives."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from ripshelf.identify.tmdb import API
from ripshelf.integrations import PUSHOVER_API
from ripshelf.models import Candidate, DriveStatus, JobState

from .conftest import until

PM = "/dev/fake-sr1"  # MOONLIT_ORCHARD, 1:39:43 PAL DVD
ROBOT = "/dev/fake-sr2"  # THE_BRASS_HERON UHD, bdmt "The Brass Heron"
TMDB_FIX = Path(__file__).parent / "fixtures" / "tmdb"
EMBY = "http://emby.test"


def mock_tmdb(router, results, details):
    router.get(f"{API}/search/movie").mock(return_value=httpx.Response(200, json={"results": results}))
    for tid, d in details.items():
        router.get(f"{API}/movie/{tid}").mock(return_value=httpx.Response(200, json={"id": int(tid), **d}))


def moonlit_orchard(router):
    mock_tmdb(router, [
        {"id": 6435, "title": "Moonlit Orchard", "release_date": "1998-10-16", "vote_count": 2500,
         "poster_path": "/pm.jpg"},
        {"id": 9000401, "title": "The Making of Moonlit Orchard", "release_date": "1998-12-01",
         "genre_ids": [99]},
    ], {"6435": {"runtime": 104}, "9000401": {"runtime": 22}})


@pytest.fixture
def keyed(settings):
    settings.tmdb_api_key = SecretStr("k")
    return settings


async def test_auto_confirm_from_tmdb_with_pal_runtime(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        moonlit_orchard(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.done)
    assert job.identity.tmdb_id == 6435 and job.identity_source == "auto"
    assert any("PAL" in e.text for e in job.candidates[0].evidence)
    excluded = next(c for c in job.candidates if c.tmdb_id == 9000401)
    assert excluded.excluded
    meta = json.loads((Path(job.destination).parent / ".disc/disc.json").read_text())
    assert meta["tmdb_id"] == 6435 and meta["identity_source"] == "auto"


async def test_uhd_uses_bdmt_title_and_names_2160p(harness, keyed):
    f = json.loads((TMDB_FIX / "brass-heron.json").read_text())
    with respx.mock(assert_all_called=False) as router:
        mock_tmdb(router, f["results"], f["details"])
        async with harness(ROBOT) as h:
            job = await h.wait_job(lambda j: j.state == JobState.done)
    assert job.disc.name == "The Brass Heron" and job.identity.tmdb_id == 1184918
    assert job.destination.endswith("The Brass Heron (2024) - 2160p.mkv")


async def test_tmdb_down_goes_to_review_with_a_warning(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/search/movie").mock(side_effect=httpx.ConnectError("down"))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            events = [e.message for e in h.db.events(job.id)]
    assert any("TMDB request failed" in m for m in events)


async def test_generic_label_falls_back_to_makemkv_disc_name(harness, keyed, monkeypatch):
    from ripshelf.dev import fake

    monkeypatch.setattr(fake.DRIVES[PM][1], "label", "DVD_VIDEO")
    with respx.mock(assert_all_called=False) as router:
        search = router.get(f"{API}/search/movie").mock(return_value=httpx.Response(200, json={"results": []}))
        async with harness(PM) as h:
            await h.wait_job(lambda j: j.state == JobState.review)
    assert search.calls[0].request.url.params["query"] == "Moonlit Orchard"


async def test_nothing_to_search_for(harness, keyed, monkeypatch, settings, mkv_script):
    from ripshelf.dev import fake

    monkeypatch.setattr(fake.DRIVES[PM][1], "label", "DVD_VIDEO")
    settings.makemkvcon = mkv_script(info="""
        out('CINFO:1,6209,"DVD disc"')
        out('CINFO:2,0,"DVD_VIDEO"')
        out('TCOUNT:1')
        out('TINFO:0,9,0,"1:40:00"')
        out('TINFO:0,11,0,"4000000000"')
    """)
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        assert any("generic" in e.message for e in h.db.events(job.id))


@pytest.mark.slow
async def test_search_identify_again_and_cache(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        mock_tmdb(router, [], {})
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
            moonlit_orchard(router)
            found = await h.m.search(job.id, "Moonlit Orchard", 1998)
            assert found[0].tmdb_id == 6435
            assert h.job(job.id).candidates[0].tmdb_id == 6435
            await h.m.confirm(job.id, found[0])
            done = await h.wait_job(lambda j: j.state == JobState.done)
            assert done.identity_source == "user"
            # Remembered, so the same disc again is recognised without TMDB.
            Path(done.destination).unlink()
            router.get(f"{API}/search/movie").mock(side_effect=AssertionError("TMDB not needed"))
            h.backend.insert(PM)
            second = await until(lambda: next((j for j in h.db.list_jobs() if j.id != job.id
                                               and j.state == JobState.done), None))
            assert second.identity_source == "cache"


async def test_search_errors_are_user_errors(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/search/movie").mock(return_value=httpx.Response(401))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            with pytest.raises(ValueError, match="rejected"):
                await h.m.search(job.id, "x", None)
            with pytest.raises(ValueError, match="no such job"):
                await h.m.search(999, "x", None)


async def test_identify_again_from_review_and_after_failure(harness, keyed, settings):
    with respx.mock(assert_all_called=False) as router:
        mock_tmdb(router, [], {})
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
            await h.m.identify_again(job.id)
            await h.wait_job(lambda j: j.state == JobState.review)
            # Already in the library -> a decision is needed; confirming a different film resolves it.
            dest = settings.library_dir / "Movies/Moonlit Orchard (1998)/Moonlit Orchard (1998).mkv"
            dest.parent.mkdir(parents=True)
            dest.write_bytes(b"old")
            await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
            held = await h.wait_job(lambda j: j.state == JobState.clash)
            assert [f.path for f in held.clash.files] == [str(dest)]
            await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1999))
            done = await h.wait_job(lambda j: j.state == JobState.done)
            assert "(1999)" in done.destination and dest.read_bytes() == b"old"


async def test_notifications_and_emby(harness, settings):
    settings.pushover_user_key, settings.pushover_app_token = SecretStr("u"), SecretStr("t")
    settings.public_url = "http://rip.lan:8380"
    settings.notify_events = {"review": 1, "ripped": None, "done": 0}
    settings.emby_url, settings.emby_api_key = EMBY, SecretStr("ek")
    settings.emby_movies_path = "/media/Movies"
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        emby = router.post(f"{EMBY}/Library/Media/Updated").mock(return_value=httpx.Response(204))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
            await h.wait_job(lambda j: j.state == JobState.done)
            await until(lambda: po.call_count >= 2)
            # Never twice for the same kind.
            h.m._spawn_notify(h.job(job.id), "done")
            await asyncio.sleep(0.2)
    bodies = [{k: v[0] for k, v in parse_qs(c.request.content.decode()).items()} for c in po.calls]
    assert [b["title"].split(":")[0] for b in bodies] == ["Needs review", "Done"]
    assert bodies[0]["url"] == f"http://rip.lan:8380/review?job={job.id}"
    assert emby.call_count == 1
    path = json.loads(emby.calls[0].request.content)["Updates"][0]["Path"]
    assert path == "/media/Movies/Moonlit Orchard (1998)/Moonlit Orchard (1998).mkv"
    assert emby.calls[0].request.headers["X-Emby-Token"] == "ek"


async def test_failure_notification_and_broken_pushover(harness, settings, mkv_script):
    settings.pushover_user_key, settings.pushover_app_token = SecretStr("u"), SecretStr("t")
    settings.notify_events = {"failed": 1}
    settings.makemkvcon = mkv_script(info="out('TCOUNT:0')")
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(500))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.failed)
            await until(lambda: po.call_count == 1)
            assert h.job(job.id).state == JobState.failed  # a broken Pushover changes nothing
    body = {k: v[0] for k, v in parse_qs(po.calls[0].request.content.decode()).items()}
    assert body["title"].startswith("Failed") and "url" not in body  # no public_url


async def test_emby_failure_is_a_warning_not_a_failure(harness, settings):
    settings.emby_url, settings.emby_api_key = EMBY, SecretStr("ek")
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{EMBY}/Library/Media/Updated").mock(side_effect=httpx.ConnectError("x"))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
            await h.wait_job(lambda j: j.state == JobState.done)
            assert any("Emby refresh failed" in e.message for e in h.db.events(job.id))


async def test_hung_drive_status_does_not_pile_up_threads(harness, monkeypatch):
    monkeypatch.setattr("ripshelf.jobs.STATUS_TIMEOUT_S", 0.2)
    release = threading.Event()
    calls = []

    async with harness() as h:
        def hung(device):
            if device == "/dev/fake-sr3":
                calls.append(device)
                release.wait(10)
            return DriveStatus.empty

        h.backend.status = hung
        d = h.m.drives["fake-ggc-h20n"]
        await until(lambda: d.status == DriveStatus.error, what="error status")
        await asyncio.sleep(1.0)  # several poll intervals
        assert len(calls) == 1
        release.set()
        await until(lambda: d.status == DriveStatus.empty, what="drive answers again")


async def test_status_exception_is_an_error_status(harness):
    async with harness() as h:
        def broken(device):
            raise OSError("gone")

        h.backend.status = broken
        d = h.m.drives["fake-pioneer"]
        await until(lambda: d.status == DriveStatus.error)
        assert not h.m._poller.done()


async def test_problems_report_uhd_without_private_data(harness):
    async with harness() as h:
        codes = {(p.code, p.drive_id) for p in h.m.problems()}
        assert ("private_data_missing", "fake-pioneer") in codes
        assert ("tmdb_key", None) in codes
