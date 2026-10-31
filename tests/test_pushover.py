"""Pushover notifications against a mocked API."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from pydantic import SecretStr

from ripshelf import integrations
from ripshelf.integrations import PUSHOVER_API, Note
from ripshelf.models import Candidate, JobState

from .conftest import until

PM = "/dev/fake-sr1"


def form(call) -> dict:
    return {k: v[0] for k, v in parse_qs(call.request.content.decode()).items()}


@pytest.fixture
def pushed(settings):
    settings.pushover_user_key = SecretStr("u-key")
    settings.pushover_app_token = SecretStr("a-token")
    settings.public_url = "http://ripshelf.lan:8380"
    return settings


def sent(route, prefix: str) -> list[dict]:
    return [f for f in map(form, route.calls) if f["title"].startswith(prefix)]


async def test_review_done_and_failed_reach_pushover(harness, pushed, mkv_script):
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
            await h.wait_job(lambda j: j.state == JobState.done)
            await until(lambda: sent(po, "Done"))
    [review], [done] = sent(po, "Needs review"), sent(po, "Done")
    assert review["token"] == "a-token" and review["user"] == "u-key"
    assert review["priority"] == "1"
    assert review["url"] == f"http://ripshelf.lan:8380/review?job={job.id}" and review["url_title"] == "Review it"
    assert done["title"] == "Done: Moonlit Orchard" and done["priority"] == "0"
    assert done["url"] == f"http://ripshelf.lan:8380/finished?job={job.id}"
    body, raw_link = done["message"].rsplit("\n", 1)
    assert body.endswith("Moonlit Orchard (1998).mkv") and raw_link == done["url"]
    assert not sent(po, "Disc inserted"), "off by default"


async def test_failure_notification(harness, pushed, settings, mkv_script):
    settings.makemkvcon = mkv_script(info="out('TCOUNT:0')")
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.failed)
            await until(lambda: sent(po, "Failed"))
    [failed] = sent(po, "Failed")
    assert failed["url"] == f"http://ripshelf.lan:8380/?job={job.id}"


async def test_events_can_be_turned_off_or_on(harness, pushed, settings):
    settings.notify_events = {"disc": -1, "review": None, "ripped": None, "done": None, "failed": None,
                              "setup": None}
    settings.drive_names = {"fake-ihas122": "Top bay"}
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        async with harness(PM) as h:
            await h.wait_job(lambda j: j.state == JobState.review)
            await until(lambda: sent(po, "Disc inserted"))
            await asyncio.sleep(0.2)
    assert [f["title"] for f in map(form, po.calls)] == ["Disc inserted: MOONLIT_ORCHARD"]
    assert form(po.calls[0])["priority"] == "-1"
    assert form(po.calls[0])["message"].startswith("In Top bay")


async def test_needs_both_keys(settings):
    settings.pushover_user_key = SecretStr("u")
    assert not integrations.notifications_enabled(settings)
    settings.pushover_app_token = SecretStr("t")
    assert integrations.notifications_enabled(settings)


@respx.mock
async def test_options_and_limits(pushed):
    pushed.pushover_device = "phone"
    pushed.public_url = None
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    assert await integrations.pushover(pushed, Note("done", "t" * 400, "m" * 2000, "/x"), 5) is None
    f = form(route.calls[0])
    assert f["device"] == "phone" and "url" not in f and f["priority"] == "1"
    assert "http" not in f["message"], "no address, no raw link"
    assert len(f["title"]) == 250 and len(f["message"]) == 1024


@respx.mock
async def test_bad_keys_and_network(pushed, caplog):
    respx.post(PUSHOVER_API).mock(return_value=httpx.Response(
        400, json={"status": 0, "errors": ["application token is invalid"]}))
    err = await integrations.pushover(pushed, Note("done", "t", "m"), 0)
    assert "token is invalid" in err and "token is invalid" in caplog.text and "a-token" not in caplog.text
    respx.post(PUSHOVER_API).mock(side_effect=httpx.ConnectError("down"))
    assert "could not reach" in await integrations.pushover(pushed, Note("done", "t", "m"), 0)
    respx.post(PUSHOVER_API).mock(return_value=httpx.Response(500, text="not json"))
    assert "HTTP 500" in await integrations.pushover(pushed, Note("done", "t", "m"), 0)


@respx.mock
async def test_batching_sends_the_first_then_one_summary(pushed):
    pushed.notify_batch_s = 0.3
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    n = integrations.Notifier(pushed)
    for i in range(4):
        n.send(Note("done", f"Done: Film {i}", "", f"/finished?job={i}"))
    n.send(Note("failed", "Failed: Other", "", "/?job=9"))
    await until(lambda: route.call_count >= 2)
    titles = [form(c)["title"] for c in route.calls]
    assert titles == ["Done: Film 0", "Failed: Other"]
    await until(lambda: route.call_count >= 3, timeout=3)
    summary = form(route.calls[2])
    assert summary["title"] == "Done: 3 more"
    assert summary["message"].splitlines() == ["Done: Film 1", "Done: Film 2", "Done: Film 3",
                                               "http://ripshelf.lan:8380/finished"]
    assert summary["url"] == "http://ripshelf.lan:8380/finished"
    # The window is closed, so the next one goes at once.
    n.send(Note("done", "Done: Film 5", ""))
    await until(lambda: route.call_count == 4)
    n.cancel()


@respx.mock
async def test_one_held_note_is_sent_as_it_is(pushed):
    pushed.notify_batch_s = 0.2
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    n = integrations.Notifier(pushed)
    n.send(Note("done", "Done: A", ""))
    n.send(Note("done", "Done: B", ""))
    await until(lambda: route.call_count == 2, timeout=3)
    assert form(route.calls[1])["title"] == "Done: B"


@respx.mock
async def test_no_batching(pushed):
    pushed.notify_batch_s = 0
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    n = integrations.Notifier(pushed)
    for i in range(3):
        n.send(Note("done", f"Done: {i}", ""))
    await until(lambda: route.call_count == 3)


async def test_setup_problems_notify_once_and_again_after_a_fix(harness, pushed, settings):
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        async with harness() as h:
            settings.notify_batch_s = 0
            lib = settings.library_dir
            lib.rename(lib.with_name("gone"))
            h.m.publish_status()
            h.m.publish_status()
            await until(lambda: sent(po, "Setup problem"))
            lib.with_name("gone").rename(lib)
            h.m.publish_status()
            lib.rename(lib.with_name("gone"))
            h.m.publish_status()
            await until(lambda: len(sent(po, "Setup problem")) == 2)
    assert all("does not exist" in f["message"].lower() or "library" in f["message"].lower()
               for f in sent(po, "Setup problem"))


async def test_missing_ui_address_is_a_setup_problem(harness, pushed, settings):
    settings.public_url = None
    async with harness() as h:
        assert "public_url" in [p.code for p in h.m.problems()]
        settings.public_url = "http://x"
        assert "public_url" not in [p.code for p in h.m.problems()]


def test_settings_api_keeps_keys_secret(settings):
    from fastapi.testclient import TestClient

    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        events = {"disc": None, "review": 1, "done": -1, "failed": 1, "setup": 0}
        r = c.put("/api/settings", json={"pushover_user_key": "secret-user", "pushover_app_token": "secret-app",
                                         "notify_events": events, "notify_batch_s": 30, "pushover_device": "phone"})
        assert r.status_code == 200 and "secret-" not in r.text
        body = r.json()
        assert body["pushover_user_key_set"] and body["pushover_app_token_set"]
        assert body["notify_events"] == {**events, "exists": 1, "ripped": 0, "transcoded": None, "copied": None} and body["notify_batch_s"] == 30
        assert c.put("/api/settings", json={"notify_events": {"done": 2}}).status_code == 422
        assert c.put("/api/settings", json={"notify_events": {"nonsense": 0}}).status_code == 422


@respx.mock
async def test_summary_dropped_if_the_event_was_turned_off_meanwhile(pushed):
    pushed.notify_batch_s = 0.2
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    n = integrations.Notifier(pushed)
    n.send(Note("done", "Done: A", ""))
    n.send(Note("done", "Done: B", ""))
    pushed.notify_events = {**pushed.notify_events, "done": None}
    await asyncio.sleep(0.5)
    assert [form(c)["title"] for c in route.calls] == ["Done: A"]
    n.send(Note("done", "Done: C", ""))  # off now, so nothing at all
    await asyncio.sleep(0.1)
    assert route.call_count == 1


@respx.mock
async def test_summary_carries_its_priority_and_link_title(pushed):
    pushed.notify_batch_s = 0.2
    pushed.notify_events = {**pushed.notify_events, "failed": -1}
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    n = integrations.Notifier(pushed)
    for t in ("A", "B", "C"):
        n.send(Note("failed", f"Failed: {t}", "", f"/?job={t}"))
    await until(lambda: route.call_count == 2, timeout=3)
    summary = form(route.calls[1])
    assert summary["title"] == "Failed: 2 more" and summary["priority"] == "-1"
    assert summary["url_title"] == "Open in ripshelf" and summary["url"] == "http://ripshelf.lan:8380/"


async def test_without_keys_nothing_is_sent_or_held(settings):
    n = integrations.Notifier(settings)
    n.send(Note("done", "Done: A", ""))
    assert n._held == {} and n._tasks == set()


def test_link_needs_both_a_path_and_an_address(pushed):
    assert integrations.link(pushed, "/x") == "http://ripshelf.lan:8380/x"
    pushed.public_url = "http://rip.lan:8380/"
    assert integrations.link(pushed, "/x") == "http://rip.lan:8380/x"
    assert integrations.link(pushed, None) is None
    pushed.public_url = None
    assert integrations.link(pushed, "/x") is None


@respx.mock
async def test_raw_link_survives_a_long_message(pushed):
    route = respx.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
    await integrations.pushover(pushed, Note("done", "t", "m" * 2000, "/?job=42"), 0)
    msg = form(route.calls[0])["message"]
    assert len(msg) == 1024 and msg.endswith("\nhttp://ripshelf.lan:8380/?job=42")


# --- the steps after the rip --------------------------------------------------------------


def test_stage_events_default_for_old_settings(settings):
    settings.notify_events = {"review": 1, "done": 0}  # saved before these existed
    n = integrations.Notifier(settings)
    assert n.priority("ripped") == 0, "on by default"
    assert n.priority("transcoded") is None and n.priority("copied") is None, "opt-in"
    assert list(integrations.EVENTS) == ["disc", "review", "exists", "ripped", "transcoded", "copied",
                                         "done", "failed", "setup"]
    assert set(integrations.SUMMARY_PATHS) == set(integrations.EVENTS)


async def stage_notes(harness, settings, title: str = "Moonlit Orchard") -> tuple[int, list[dict]]:
    """Run a disc to done with every stage event on, and return what Pushover got."""
    settings.notify_events = {"ripped": 0, "transcoded": -1, "copied": -1}
    settings.notify_batch_s = 0
    with respx.mock(assert_all_called=False) as router:
        po = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review and j.rip.state == "done")
            await h.m.confirm(job.id, Candidate(source="manual", title=title, year=1998))
            await h.wait_job(lambda j: j.state == JobState.done, timeout=60)
            await until(lambda: sent(po, "Done"))
            await asyncio.sleep(0.1)
    return job.id, [form(c) for c in po.calls]


async def test_rip_and_copy_notes_without_transcoding(harness, pushed, settings):
    settings.transcode_enabled = False
    job_id, notes = await stage_notes(harness, settings)
    titles = [n["title"] for n in notes]
    assert titles == ["Needs review: MOONLIT_ORCHARD", "Rip finished: MOONLIT_ORCHARD",
                      "Copied to library: Moonlit Orchard", "Done: Moonlit Orchard"]
    ripped, copied = notes[1], notes[2]
    assert ripped["priority"] == "0" and ripped["url"] == f"http://ripshelf.lan:8380/?job={job_id}"
    # Confirmed while the drive ejected, or not yet.
    assert ripped["message"].split("\n")[0] in (
        "Waiting for you to review it. Then: copying into the library (transcoding is off)",
        "Next: copying into the library (transcoding is off)")
    assert copied["priority"] == "-1"
    assert copied["message"].split("\n")[0].endswith("Moonlit Orchard (1998)/Moonlit Orchard (1998).mkv")


async def test_transcode_note(harness, pushed, settings):
    from .test_transcode import HAS_FFMPEG

    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    settings.transcode_codecs = ["mpeg2video"]
    settings.transcode_encoder = "x265"
    settings.transcode_crop = "off"
    _, notes = await stage_notes(harness, settings)
    [ripped] = [n for n in notes if n["title"].startswith("Rip finished")]
    assert ": transcoding to H.265 where needed, then copying into the library\n" in ripped["message"]
    [tc] = [n for n in notes if n["title"].startswith("Transcode finished")]
    assert tc["title"] == "Transcode finished: Moonlit Orchard"
    assert tc["message"].startswith("Transcoded to H.265, ") and "% of the original size" in tc["message"]
    titles = [n["title"].split(":")[0] for n in notes]
    assert titles.index("Transcode finished") < titles.index("Copied to library") < titles.index("Done")


@pytest.mark.parametrize("mode, kind, sources, state, identified, want", [
    ("titles", "bluray", {}, JobState.ripping, True,
     "Next: transcoding to H.265 where needed, then copying into the library"),
    ("titles", "uhd", {}, JobState.ripping, True, "Next: copying into the library (4K UHD discs aren't transcoded)"),
    ("titles", "dvd", {"dvd": {"transcode": False}}, JobState.identifying, False,
     "Still identifying it. Then: copying into the library (DVD discs aren't transcoded)"),
    ("backup", "bluray", {}, JobState.clash, True,
     "Already in the library, so waiting for you to decide. Then: copying the full disc backup into the "
     "library (not transcoded)"),
])
async def test_rip_finished_says_what_happens_next(harness, settings, mode, kind, sources, state, identified,
                                                   want):
    from ripshelf.db import JobRecord, now
    from ripshelf.models import DiscInfo

    settings.transcode_sources = sources
    async with harness() as h:
        job = JobRecord(id=1, drive_id="d", state=state, created_at=now(), updated_at=now(), mode=mode,
                        disc=DiscInfo(label="SEAGULL_MANOR", kind=kind),
                        identity=Candidate(source="manual", title="Seagull Manor") if identified else None)
        assert h.m._next_step(job) == want
