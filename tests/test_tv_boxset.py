"""A Seagull Manor box set across two discs, where collections carry the numbering on."""

from __future__ import annotations

import pytest
import respx
from pydantic import SecretStr

from ripshelf.models import JobState, MediaKind, TitleRole

from .conftest import until
from .tvfix import mock_show

DRIVE = "/dev/fake-sr0"
RUNTIMES = {1: [30, 28, 33], 2: [29, 29, 31]}  # season 1 on TMDB is 30 28 33 29 29 31


def disc_info(runtimes: list[int], with_play_all: bool = True) -> str:
    lines = ['out(\'CINFO:1,6206,"DVD disc"\')', "out('TCOUNT:4')"]
    idx = 0
    if with_play_all:
        total = sum(runtimes) * 60 + 5
        lines += [f"out('TINFO:0,9,0,\"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}\"')",
                  "out('TINFO:0,11,0,\"9000000000\"')", "out('TINFO:0,27,0,\"title_t00.mkv\"')"]
        idx = 1
    for i, r in enumerate(runtimes):
        n = idx + i
        lines += [f"out('TINFO:{n},9,0,\"0:{r:02d}:20\"')", f"out('TINFO:{n},11,0,\"{1000000000 + n}\"')",
                  f"out('TINFO:{n},27,0,\"title_t{n:02d}.mkv\"')"]
    return "\n".join(lines)


RIP_ALL = """
out('PRGT:5024,0,"Saving all titles to MKV files"')
for name in ("title_t00.mkv", "title_t01.mkv", "title_t02.mkv", "title_t03.mkv"):
    Path(args[3], name).write_bytes(os.urandom(4096))
out('MSG:5036,260,1,"Copy complete.","%1","4"')
"""


@pytest.fixture
def keyed(settings):
    settings.tmdb_api_key = SecretStr("k")
    settings.verify_order = False
    settings.tv_auto_confirm = False  # these tests exercise review; auto-confirm has its own below
    return settings


async def run_disc(h, label, monkeypatch, settings, mkv_script, runtimes, play_all=True):
    from ripshelf.dev import fake

    monkeypatch.setattr(fake.DRIVES[DRIVE][1], "label", label)
    settings.makemkvcon = mkv_script(info=disc_info(runtimes, play_all), rip=RIP_ALL)
    h.backend.insert(DRIVE)
    return await until(lambda: next((j for j in h.db.list_jobs() if j.disc.label == label
                                     and j.state == JobState.review and j.rip.state == "done"), None), 40)


@pytest.mark.slow
async def test_two_discs_continue_the_season(harness, keyed, settings, mkv_script, monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            d1 = await run_disc(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
            assert d1.media == MediaKind.tv and d1.tv.season == 1 and d1.tv.disc_number == 1
            eps = [m.episodes[0].episode for m in d1.tv.mapping if m.role == TitleRole.episode]
            assert eps == [1, 2, 3] and d1.tv.consistent
            await h.m.tv_confirm(d1.id, [])
            await until(lambda: h.job(d1.id).state == JobState.done)
            await h.m.eject("fake-ghc0n")
            # Disc 2 has no disc number in its label, so the collection says what comes next.
            d2 = await run_disc(h, "SEAGULL_MANOR_SERIES_1", monkeypatch, settings, mkv_script, RUNTIMES[2])
            assert d2.tv.season == 1 and d2.tv.disc_number == 2 and d2.tv.start_episode == 4
            eps = [m.episodes[0].episode for m in d2.tv.mapping if m.role == TitleRole.episode]
            assert eps == [4, 5, 6]
            await h.m.tv_confirm(d2.id, [])
            await until(lambda: h.job(d2.id).state == JobState.done)
            detail = h.m.collection_detail(h.job(d2.id).tv.collection_id)
    s1 = next(g for g in detail.seasons if g.season == 1)
    assert s1.present == 6 and s1.total == 6
    show = settings.library_dir / "TV/Seagull Manor (1975)/Season 01"
    assert sorted(p.name for p in show.iterdir())[0] == "Seagull Manor - S01E01 - A Hint of Style.mkv"
    assert len(list(show.iterdir())) == 6


@pytest.mark.slow
async def test_same_disc_twice_shows_duplicates(harness, keyed, settings, mkv_script, monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            d1 = await run_disc(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
            await h.m.tv_confirm(d1.id, [])
            await h.m.eject("fake-ghc0n")
            d2 = await run_disc(h, "SEAGULL_MANOR_S1_D2", monkeypatch, settings, mkv_script, RUNTIMES[1])
            # Label says disc 2 but force the same run, and the grid shows the clash.
            await h.m.tv_propose(d2.id, 2207, season=1, disc_number=2, start_episode=1, compilation=False)
            await h.m.tv_confirm(d2.id, [])
            detail = h.m.collection_detail(h.job(d2.id).tv.collection_id)
    s1 = next(g for g in detail.seasons if g.season == 1)
    assert [c.status for c in s1.cells[:3]] == ["duplicate"] * 3 and s1.cells[3].status == "missing"


async def test_disc_number_guess_without_collection(harness, keyed, settings, mkv_script, monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            d2 = await run_disc(h, "SEAGULL_MANOR_S1_D2", monkeypatch, settings, mkv_script, RUNTIMES[2],
                                play_all=False)
            assert d2.tv.start_episode == 4
            assert [m.episodes[0].episode for m in d2.tv.mapping if m.role == TitleRole.episode] == [4, 5, 6]
            assert any("a guess" in e.message for e in h.db.events(d2.id))


async def test_tmdb_down_during_tv_identify(harness, keyed, settings, mkv_script, monkeypatch):
    import httpx


    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.themoviedb.org").mock(side_effect=httpx.ConnectError("down"))
        async with harness() as h:
            d = await run_disc(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
            assert d.tv.show is None and "pick the show" in d.tv.problems
            assert any("TMDB request failed" in e.message for e in h.db.events(d.id))
            with pytest.raises(ValueError, match="pick the show"):
                await h.m.tv_match_text(d.id, "A Hint of Style")
            with pytest.raises(ValueError, match="pick the show"):
                await h.m.tv_confirm(d.id, [])
            with pytest.raises(ValueError, match="TMDB request failed"):
                await h.m.tv_propose(d.id, 2207, 1, 1, None, False)


async def test_generic_label_tv_disc(harness, keyed, settings, mkv_script, monkeypatch):
    async with harness() as h:
        d = await run_disc(h, "DVD_VIDEO", monkeypatch, settings, mkv_script, RUNTIMES[1])
        assert d.media == MediaKind.tv and d.tv.show is None
        assert any("generic" in e.message for e in h.db.events(d.id))


async def test_tv_actions_in_wrong_states(harness, keyed):
    async with harness() as h:
        job = h.db.create_job("fake-ghc0n", DRIVE)
        job.state = JobState.done
        h.db.save_job(job)
        for coro in (h.m.tv_confirm(job.id, []), h.m.tv_match_text(job.id, "x"),
                     h.m.tv_propose(job.id, 1, 1, 1, None, False), h.m.set_media(job.id, MediaKind.tv)):
            with pytest.raises(ValueError, match="only change while"):
                await coro
        with pytest.raises(ValueError, match="no such job"):
            await h.m.tv_confirm(999, [])
        assert h.m.thumbnail_path(999, 1) is None
        assert h.m.collection_detail(999) is None


async def test_ripped_files_missing_at_finalise(harness, keyed, settings, mkv_script, monkeypatch):
    from pathlib import Path

    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            d = await run_disc(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
            Path(d.rip.files[2]).unlink()
            await h.m.tv_confirm(d.id, [])
            failed = await until(lambda: (j := h.job(d.id)).state == JobState.failed and j)
    assert failed.problem.code == "rip_incomplete" and "2" in failed.problem.message


# --- auto-confirm ------------------------------------------------------------------------------


@pytest.fixture
def auto(keyed):
    keyed.tv_auto_confirm = True
    return keyed


async def wait_settled(h, label):
    return await until(lambda: next((j for j in h.db.list_jobs() if j.disc.label == label
                                     and j.rip.state == "done" and j.state in (JobState.review, JobState.done,
                                                                                JobState.failed)), None), 40)


async def run_auto(h, label, monkeypatch, settings, mkv_script, runtimes, play_all=True):
    from ripshelf.dev import fake

    monkeypatch.setattr(fake.DRIVES[DRIVE][1], "label", label)
    settings.makemkvcon = mkv_script(info=disc_info(runtimes, play_all), rip=RIP_ALL)
    h.backend.insert(DRIVE)
    return await wait_settled(h, label)


async def test_label_placed_disc_waits_for_the_order_check(harness, auto, settings, mkv_script, monkeypatch):
    # Play-all present but the order check is off, so not verified, so a human checks it.
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            j = await run_auto(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
    assert j.state == JobState.review and not j.tv.auto_confirmed
    assert "waiting for the order check" in j.tv.auto_evidence


async def test_label_placed_disc_with_verified_order_confirms_itself(harness, auto, settings, mkv_script,
                                                                     monkeypatch):
    from ripshelf.tv import verify

    async def verified(*a, **k):
        return verify.OrderCheck(True, "all 3 episodes match the play-all frame for frame", {})

    monkeypatch.setattr(verify, "verify_order", verified)
    settings.verify_order = True
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            j = await run_auto(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
            done = await until(lambda: (x := h.job(j.id)).state == JobState.done and x)
    assert done.identity_source == "auto" and done.tv.auto_confirmed
    assert any("order verified" in e for e in done.tv.auto_evidence)
    assert any("label says season 1, disc 1" in e for e in done.tv.auto_evidence)


@pytest.mark.slow
async def test_continuing_disc_confirms_itself(harness, auto, settings, mkv_script, monkeypatch):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            d1 = await run_auto(h, "SEAGULL_MANOR_S1_D1", monkeypatch, settings, mkv_script, RUNTIMES[1])
            assert d1.state == JobState.review
            await h.m.tv_confirm(d1.id, [])
            await until(lambda: h.job(d1.id).state == JobState.done)
            await h.m.eject("fake-ghc0n")
            d2 = await run_auto(h, "SEAGULL_MANOR_SERIES_1", monkeypatch, settings, mkv_script, RUNTIMES[2],
                                play_all=False)
            done = await until(lambda: (x := h.job(d2.id)).state == JobState.done and x)
    assert done.identity_source == "auto"
    assert any("continues the collection at episode 4" in e for e in done.tv.auto_evidence)


@pytest.mark.parametrize("label, runtimes, expect", [
    ("SEAGULL_MANOR", [30, 28, 33], "nothing says which season"),  # label gives no season/disc
    ("SEAGULL_MANOR_S1_D1", [30, 45, 33], "runtimes disagree"),  # a title of the wrong length
])
async def test_weak_discs_go_to_review(harness, auto, settings, mkv_script, monkeypatch, label, runtimes, expect):
    with respx.mock(assert_all_called=False) as router:
        mock_show(router, "seagull-manor")
        async with harness() as h:
            j = await run_auto(h, label, monkeypatch, settings, mkv_script, runtimes, play_all=False)
    assert j.state == JobState.review and j.identity is None
    assert any(expect in e for e in j.tv.auto_evidence), j.tv.auto_evidence


async def test_auto_confirm_off(harness, auto, settings, mkv_script, monkeypatch):
    settings.tv_auto_confirm = False
    async with harness() as h:
        job = h.db.create_job("fake-ghc0n", DRIVE)
        from ripshelf.models import Show, TvPlan

        job.media, job.tv = "tv", TvPlan(show=Show(tmdb_id=1, name="X", score=1.0), label_placed=True)
        assert h.m._auto_check(job) == (False, ["auto-confirm is off"])


async def test_auto_check_rules(harness, auto):
    from ripshelf.models import Show, TitleMap, TvPlan

    async with harness() as h:
        job = h.db.create_job("fake-ghc0n", DRIVE)
        job.media = "tv"
        ep = TitleMap(title_index=1, duration_s=1800, role="episode", episodes=[{"season": 1, "episode": 1}],
                      match_cost=0.0)
        base = dict(show=Show(tmdb_id=1, name="X", score=0.95), label_placed=True, season=1, disc_number=1,
                    mapping=[ep], consistent=True, order_verified=True)
        cases = [
            ({}, True, "order verified"),
            ({"show": None}, False, "no show"),
            ({"user_edited": True}, False, "changed by hand"),
            ({"compilation": True}, False, "compilation"),
            ({"show": Show(tmdb_id=1, name="X", score=0.8)}, False, "below 0.90"),
            ({"show": Show(tmdb_id=1, name="X", score=0.99, excluded=True)}, False, "below"),
            ({"consistent": False, "problems": ["title 2 is marked as an episode but has none"]}, False, "has none"),
            ({"order_verified": False}, False, "did not match the play-all"),
        ]
        for change, ok, text in cases:
            job.tv = TvPlan(**{**base, **change})
            got, why = h.m._auto_check(job)
            assert got is ok and any(text in w for w in why), (change, why)
