"""TV discs end to end on the fake drives, TMDB TV mocked with real data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import respx
from pydantic import SecretStr

from ripshelf.models import EpisodeRef, JobState, MediaKind, TitleMapUpdate, TitleRole

from .conftest import until
from .tvfix import mock_show

SD = "/dev/fake-sr0"  # PIPPA_&_GOOSE_KITE_TRAM has a play-all + 10 x 7:01
CASE_ORDER = ("Kite Tram, Puddle Dash, Beard Trim, Seaside Jig Off, Venus as Comet, Pony Hop, "
              "Tower Doodling, Button Maker, Fog Knot, Cushion Stuff")
TMDB_EPISODES = [34, 26, 21, 23, 24, 25, 29, 37, 30, 28]  # all season 3, looked up on TMDB
HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def keyed(settings):
    settings.tmdb_api_key = SecretStr("k")
    return settings


@pytest.mark.slow
async def test_pippa_and_goose_compilation_end_to_end(harness, keyed, settings):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            assert job.media == MediaKind.tv
            plan = job.tv
            assert plan.show.name == "Pippa & Goose" and plan.compilation
            roles = {m.title_index: m.role for m in plan.mapping}
            assert roles[0] == TitleRole.play_all
            assert [i for i, r in roles.items() if r == TitleRole.episode] == list(range(1, 11))
            # Named after its first episode, so title 1 is already Kite Tram.
            first = next(m for m in plan.mapping if m.title_index == 1)
            assert first.episodes == [EpisodeRef(season=3, episode=34)]
            assert not plan.consistent  # the other nine still need episodes
            assert len(job.rip.files) == 11
            if HAS_FFMPEG:
                await until(lambda: h.job(job.id).tv.order_verified is not None)
                assert h.job(job.id).tv.order_verified is True
                assert h.m.thumbnail_path(job.id, 1)

            job = await h.m.tv_match_text(job.id, CASE_ORDER)
            got = [m.episodes[0].episode for m in job.tv.mapping if m.role == TitleRole.episode]
            assert got == TMDB_EPISODES
            assert job.tv.consistent and job.tv.compilation

            await h.m.tv_confirm(job.id, [])
            done = await h.wait_job(lambda j: j.state == JobState.done)
    show = settings.library_dir / "TV" / "Pippa & Goose (2013)"
    files = sorted(p.name for p in (show / "Season 03").iterdir())
    assert files[0] == "Pippa & Goose - S03E21 - Beard Trim.mkv"
    assert "Pippa & Goose - S03E34 - Kite Tram.mkv" in files and len(files) == 10
    meta = json.loads(next((show / ".disc").glob("*/disc.json")).read_text())
    assert meta["compilation"] and meta["titles"]["1"]["episodes"] == ["S03E34"]
    assert meta["titles"]["0"]["role"] == "play_all"
    assert done.destination == str(show)
    assert not (settings.scratch_dir / f"job-{done.id}").exists()


async def test_collection_grid_after_compilation(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            await h.m.tv_match_text(job.id, CASE_ORDER)
            job = await h.m.tv_confirm(job.id, [])
            detail = h.m.collection_detail(job.tv.collection_id)
    s3 = next(g for g in detail.seasons if g.season == 3)
    assert s3.present == 10 and s3.total == 40
    status = {c.episode.episode: c.status for c in s3.cells}
    assert status[34] == "present" and status[1] == "missing"


async def test_confirm_blocked_until_consistent(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
            with pytest.raises(ValueError, match="marked as an episode but has none"):
                await h.m.tv_confirm(job.id, [])
            # Two titles on the same episode.
            ups = [TitleMapUpdate(title_index=i, role=TitleRole.episode,
                                  episodes=[EpisodeRef(season=3, episode=34)]) for i in (1, 2)]
            with pytest.raises(ValueError, match="on both title 1 and title 2"):
                await h.m.tv_confirm(job.id, ups)
            with pytest.raises(ValueError, match="not on this disc"):
                await h.m.tv_confirm(job.id, [TitleMapUpdate(title_index=99, role=TitleRole.skip)])
            bad = [TitleMapUpdate(title_index=1, role=TitleRole.episode, episodes=[EpisodeRef(season=9, episode=1)])]
            with pytest.raises(ValueError, match="not an episode of this show"):
                await h.m.tv_confirm(job.id, bad)


async def test_box_set_mode_maps_a_run(harness, keyed):
    """The same disc treated as season 3 from episode 21, a consecutive run."""
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
            job = await h.m.tv_propose(job.id, 46866, season=3, disc_number=None, start_episode=21,
                                       compilation=False)
            eps = [m.episodes[0].episode for m in job.tv.mapping if m.role == TitleRole.episode]
            assert eps == list(range(21, 31)) and job.tv.consistent
            assert not job.tv.compilation and job.tv.season == 3


async def test_no_tmdb_key_still_reviews(harness):
    async with harness(SD) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
        assert job.media == MediaKind.tv and job.tv.show is None
        assert "pick the show" in job.tv.problems or not job.tv.consistent
        with pytest.raises(ValueError, match="TMDB"):
            await h.m.tv_search("Pippa")


async def test_movie_disc_stays_a_movie(harness, keyed):
    async with harness("/dev/fake-sr2") as h:  # The Brass Heron
        job = await h.wait_job(lambda j: j.main_title is not None and j.state != JobState.scanning, timeout=60)
        assert job.media == MediaKind.movie and job.tv is None
        assert any("feature length" in e for e in job.media_evidence)


async def test_switch_a_tv_disc_to_movie_and_back(harness, keyed):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            job = await h.m.set_media(job.id, MediaKind.movie)
            assert job.media == MediaKind.movie and job.rip.file.endswith("B1_t00.mkv")
            job = await h.wait_job(lambda j: j.state == JobState.review)
            job = await h.m.set_media(job.id, MediaKind.tv)
            assert job.media == MediaKind.tv and job.rip.all_titles  # no re-rip needed
            await h.wait_job(lambda j: j.state == JobState.review and j.tv.show is not None)


async def test_destination_clash_is_checked_before_copying_anything(harness, keyed, settings):
    clash = settings.library_dir / "TV/Pippa & Goose (2013)/Season 03/Pippa & Goose - S03E28 - Cushion Stuff.mkv"
    clash.parent.mkdir(parents=True)
    clash.write_bytes(b"keep")
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            await h.m.tv_match_text(job.id, CASE_ORDER)
            await h.m.tv_confirm(job.id, [])
            held = await h.wait_job(lambda j: j.state == JobState.clash)
    assert [f.path for f in held.clash.files] == [str(clash)] and held.clash.files[0].size_bytes == 4
    assert clash.read_bytes() == b"keep"
    assert sorted(p.name for p in clash.parent.iterdir()) == [clash.name], "nothing else was copied"


@pytest.mark.parametrize("setting, where", [("extras", "extras"), ("specials", "Season 00"), ("skip", None)])
async def test_unmatched_titles(harness, keyed, settings, setting, where):
    settings.tv_unmatched = setting
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        async with harness(SD) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done", timeout=60)
            await h.m.tv_match_text(job.id, CASE_ORDER)
            # Make the last episode an extra instead.
            await h.m.tv_confirm(job.id, [TitleMapUpdate(title_index=10, role=TitleRole.extra)])
            await h.wait_job(lambda j: j.state == JobState.done)
    show = settings.library_dir / "TV/Pippa & Goose (2013)"
    if where is None:
        assert not (show / "extras").exists() and not (show / "Season 00").exists()
    else:
        names = [p.name for p in (show / where).iterdir()]
        assert len(names) == 1
        if where == "Season 00":
            assert names[0].startswith("Pippa & Goose - S00E09")  # after TMDB's 8 specials


@pytest.mark.slow
async def test_tv_api(settings, keyed):
    from fastapi.testclient import TestClient

    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        router.route(host="testserver").pass_through()
        app = create_app(settings, backend=FakeDrives(present={SD}))
        with TestClient(app) as c:
            import time
            for _ in range(300):
                jobs = c.get("/api/jobs").json()
                if jobs and jobs[0]["state"] == "review" and jobs[0]["rip"]["state"] == "done":
                    break
                time.sleep(0.1)
            jid = jobs[0]["id"]
            assert c.get("/api/tv/search?q=Pippa").json()[0]["tmdb_id"] == 46866
            assert c.get("/api/tv/search?q=").status_code == 422
            r = c.post(f"/api/jobs/{jid}/tv/match-text", json={"text": CASE_ORDER})
            assert r.status_code == 200 and r.json()["tv"]["consistent"]
            assert c.post(f"/api/jobs/{jid}/tv/match-text", json={"text": "  "}).status_code == 409
            assert c.post(f"/api/jobs/{jid}/tv/confirm", json={"mapping": [{"title_index": 1, "role": "nope"}]}
                          ).status_code == 422
            assert c.post(f"/api/jobs/{jid}/tv/propose", json={"show_tmdb_id": 46866, "season": -1}
                          ).status_code == 422
            assert c.get(f"/api/jobs/{jid}/thumbs/99.jpg").status_code == 404
            if HAS_FFMPEG:
                for _ in range(100):
                    if c.get(f"/api/jobs/{jid}/thumbs/1.jpg").status_code == 200:
                        break
                    time.sleep(0.1)
                assert c.get(f"/api/jobs/{jid}/thumbs/1.jpg").headers["content-type"] == "image/jpeg"
            assert c.post(f"/api/jobs/{jid}/tv/confirm", json={"mapping": []}).status_code == 200
            for _ in range(300):
                if c.get(f"/api/jobs/{jid}").json()["state"] == "done":
                    break
                time.sleep(0.1)
            cols = c.get("/api/collections").json()
            assert cols[0]["show"]["name"] == "Pippa & Goose"
            detail = c.get(f"/api/collections/{cols[0]['id']}").json()
            assert any(g["season"] == 3 and g["present"] == 10 for g in detail["seasons"])
            assert c.get("/api/collections/999").status_code == 404
            assert c.post(f"/api/jobs/{jid}/media", json={"media": "tv"}).status_code == 409  # done
    _ = Path


async def test_tv_review_notification(harness, keyed, settings):
    from urllib.parse import parse_qs

    import httpx
    from pydantic import SecretStr

    from ripshelf.integrations import PUSHOVER_API

    settings.pushover_user_key, settings.pushover_app_token = SecretStr("u"), SecretStr("t")
    settings.notify_events = {"review": 1}
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "pippa-goose")
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        async with harness(SD) as h:
            await h.wait_job(lambda j: j.state == JobState.review, timeout=60)
            await until(lambda: po.call_count >= 1)
    body = {k: v[0] for k, v in parse_qs(po.calls[0].request.content.decode()).items()}
    assert body["message"] == "TV disc: Pippa & Goose, 10 episode titles to check"
