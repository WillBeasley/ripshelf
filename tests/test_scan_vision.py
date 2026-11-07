"""Reading the box with a vision model: the two kinds of provider (against pretend
servers, never a real one), tidying their replies, when it runs, and merging what it
read with Tesseract's reading."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import httpx2
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ripshelf.dev.fake import FakeDrives
from ripshelf.dev.fake_vision import READING, FakeVision
from ripshelf.main import create_app
from ripshelf.models import CoverScan, ListingItem, ScanText, TextLine
from ripshelf.scanner import images, listing, vision
from ripshelf.visionjobs import struggles

from .conftest import until
from .test_contribute import heron_job, put
from .test_scan_text import FakeReader
from .test_scans import picture


def img(w: int = 300, h: int = 200):
    return images.open_image(picture(w, h))


def fake(**kw) -> tuple[FakeVision, FastAPI]:
    f = FakeVision(**kw)
    app = FastAPI()
    app.include_router(f.router())
    return f, app


def openai(app: FastAPI, **kw) -> vision.Provider:
    return vision.Provider(kind="local", url="http://ollama.test/fake-vision", model="qwen3-vl:4b-instruct",
                           transport=httpx.ASGITransport(app=app), **kw)


def anthropic_provider(app: FastAPI) -> vision.Provider:
    return vision.Provider(kind="anthropic", url="http://anthropic.test/fake-anthropic", model="a-vision-model",
                           key="sk-test", transport=httpx2.ASGITransport(app=app))


# --- tidying a reply ------------------------------------------------------------------------


def test_a_reply_in_ripshelfs_terms():
    raw = json.loads(json.dumps(READING))
    raw["items"].insert(0, {"kind": "episode", "title": "Moonlit Orchard", "number": None, "runtime_min": None,
                            "extra_type": None, "disc": None})  # the film, listed as its own episode
    raw["items"].append({"kind": "extra", "title": "“Blossom reel”", "number": None, "runtime_min": 0,
                         "extra_type": "Blooper", "disc": None})
    raw["items"].append({"kind": "extra", "title": "In-depth documentary on the making of 'Return to the Orchard",
                         "number": None, "runtime_min": None, "extra_type": "Featurette", "disc": None})
    raw["details"]["audio"].append({"language": "French Stereo", "format": "AC3", "channels": None})
    out = vision.tidy(raw)
    assert [i.title for i in out.items] == ["Growing the Orchard", "Deleted scenes", "Theatrical trailer",
                                            "Orchard photo gallery", "Blossom reel", "Return to the Orchard"]
    assert out.items[-2].extra_type == "Extra" and out.items[-2].runtime_min is None
    assert {i.source for i in out.items} == {"vision"}
    d = out.details
    assert (d.region, d.video_system, d.aspect_ratio, d.barcode) == ("2", "PAL", "16:9", "5012345678900")
    assert [(a.language, a.format, a.channels) for a in d.audio] == [
        ("English", "Dolby Digital", "5.1"), ("French", "Dolby Digital", "2.0")]
    assert d.subtitles[0].hard_of_hearing


@pytest.mark.parametrize("region, tidied", [("Region 2", "2"), ("2 & 4", "2 & 4"), ("B", "B"), ("2,4", "2 & 4"),
                                            ("Europe", None), (None, None)])
def test_regions(region, tidied):
    raw = {"items": [], "details": {**READING["details"], "region": region}}
    assert vision.tidy(raw).details.region == tidied


def test_a_reply_that_isnt_a_listing():
    with pytest.raises(vision.VisionError, match="doesn't fit"):
        vision.tidy({"items": "lots", "details": {}})


# --- the providers ---------------------------------------------------------------------------


async def test_an_openai_compatible_server():
    f, app = fake()
    listing_, secs = await vision.read_listing(openai(app, key="local-key"), img(2400, 1600))
    assert [i.title for i in listing_.items][:3] == ["Growing the Orchard", "Deleted scenes", "Theatrical trailer"]
    req = f.requests[0]
    body = req["body"]
    assert req["auth"] == "Bearer local-key" and body["model"] == "qwen3-vl:4b-instruct"
    assert body["response_format"]["json_schema"]["strict"] is True
    image = body["messages"][0]["content"][1]["image_url"]["url"]
    assert image.startswith("data:image/jpeg;base64,")
    assert secs >= 0


@pytest.mark.parametrize("status, raw, error", [
    (401, None, "refused the key"), (404, None, "has no model"), (500, None, "HTTP 500"),
    (200, "not json at all", "isn't JSON"), (200, "```json\n" + json.dumps(READING) + "\n```", None),
])
async def test_openai_failures(status, raw, error):
    _, app = fake(status=status, raw=raw)
    if error:
        with pytest.raises(vision.VisionError, match=error):
            await vision.read_listing(openai(app), img())
    else:
        assert (await vision.read_listing(openai(app), img()))[0].items


async def test_a_thinking_model_that_runs_out():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": ""}}]})

    p = vision.Provider(kind="local", url="http://x.test", model="m", transport=httpx.MockTransport(handler))
    with pytest.raises(vision.VisionError, match="ran out of room"):
        await vision.read_listing(p, img())


async def test_anthropic():
    f, app = fake()
    listing_, _ = await vision.read_listing(anthropic_provider(app), img())
    assert listing_.details.region == "2"
    req = f.requests[0]
    body = req["body"]
    assert "sk-test" in req["auth"] and body["model"] == "a-vision-model"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["messages"][0]["content"][0]["type"] == "image"


@pytest.mark.parametrize("status, error", [(401, "refused the key"), (404, "has no model")])
async def test_anthropic_failures(status, error):
    _, app = fake(status=status)
    with pytest.raises(vision.VisionError, match=error):
        await vision.read_listing(anthropic_provider(app), img())


async def test_the_test_button():
    _, app = fake()
    ok, message, details = await vision.check(openai(app))
    assert ok and "read the test card" in message and any("Theatrical trailer" in d for d in details)
    _, app = fake(raw=json.dumps({"items": [], "details": READING["details"]}))
    ok, message, _ = await vision.check(openai(app))
    assert not ok and "didn't read" in message


# --- when it runs ---------------------------------------------------------------------------


def text(*rows: str, conf: float = 95) -> ScanText:
    return ScanText(scan_id="s1", key="k", read_at=datetime.now(UTC), engine="t",
                    lines=[TextLine(text=r, conf=conf, region="back", box=[0.1, i / 20, 0.5, i / 20 + 0.04])
                           for i, r in enumerate(rows)])


GOOD = ["Special features", "Growing the Orchard (22 mins)", "Theatrical trailer", "Region 2 PAL 16:9",
        "LANGUAGES English", "SUBTITLES English", "FEATURE RUN TIME 1hr 52mins", "© 1998 Moonlit Pictures",
        "A quiet orchard. A loud summer."]


def scan(kind="back", source="scanner") -> CoverScan:
    return CoverScan(id="s1", kind=kind, source=source, created_at=datetime.now(UTC), content_type="image/jpeg",
                     width=10, height=10, sha256="x", size_bytes=1)


def test_struggles():
    assert struggles(scan(), text(*GOOD)) == []
    assert struggles(scan(source="camera"), text(*GOOD)) == ["it's a photo"]
    assert struggles(scan(kind="front"), text("x")) == []
    few = struggles(scan(), text(*GOOD[:3]))
    assert any("only 3 lines" in w for w in few)
    assert any("weren't read" in w for w in struggles(scan(), text(*[g for g in GOOD if "Region" not in g])))
    assert "no episodes or extras were found" in struggles(scan(), text(*GOOD[3:], "more", "lines", "here"))
    assert any("wasn't confident" in w for w in struggles(scan(), text(*GOOD, conf=72)))


# --- merging --------------------------------------------------------------------------------


def test_merging_the_two_readings():
    tess = text("Special features", "Growing the Orchard (22 mins)", "Deleted scenes", "Theatrical trailer",
                "Orchard photo gallery and more", "FEATURE RUN TIME 1hr 52mins", "Region 2 PAL")
    tess.barcodes = ["5012345678900"]
    own = listing.build([tess])
    own.items = own.items[:3]  # Tesseract missed the gallery line as an item
    own.details.region, own.details.region_guessed = "2", True
    theirs = vision.tidy({**READING, "details": {**READING["details"], "feature_runtime_min": 152,
                                                   "barcode": "5000000000000", "region": "2 & 4"}})
    theirs.items.append(ListingItem(kind="extra", title="Director's commentary", extra_type="Other", source="vision"))
    out = listing.merge(own, [("s1", theirs)], [tess])
    by = {i.title: i for i in out.items}
    assert by["Growing the Orchard"].source == "both" and by["Growing the Orchard"].runtime_min == 22
    assert by["Deleted scenes"].extra_type == "DeletedScene"  # the model's type over a guess? same here
    assert by["Orchard photo gallery"].source == "both" and by["Orchard photo gallery"].box  # found in a line
    assert by["Director's commentary"].source == "vision" and by["Director's commentary"].box is None
    d = out.details
    assert d.feature_runtime_min == 112 and d.sources["feature_runtime_min"] == "tesseract"
    assert d.barcode == "5012345678900" and d.sources["barcode"] == "barcode"
    assert (d.region, d.region_guessed, d.sources["region"]) == ("2 & 4", False, "vision")
    assert d.aspect_ratio == "16:9" and d.sources["aspect_ratio"] == "vision"


# --- the manager ------------------------------------------------------------------------------


@pytest.fixture
def vision_settings(settings):
    settings.vision_mode, settings.vision_url, settings.vision_model = "struggles", "http://v.test/fake-vision", "m"
    return settings


async def wait_vision(h, job_id, state="done"):
    return await until(lambda: (j := h.db.get_job(job_id)) and j.scans and j.scans[0].vision == state and j,
                       10, f"vision {state}")


async def test_a_struggling_scan_is_read_again(harness, vision_settings):
    f, app = fake()
    async with harness() as h:
        h.m.text_reader = FakeReader(["Special features", "Growing the Orchard (22 mins)"])  # little read
        h.m.vision_transport = httpx.ASGITransport(app=app)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "camera")
        j = await wait_vision(h, job.id)
        await until(lambda: (x := h.db.get_job(job.id)) and x.listing.found.details.region, 5, "merged")
        found = h.db.get_job(job.id).listing.found
        by = {i.title: i.source for i in found.items}
        assert by["Growing the Orchard"] == "both" and by["Theatrical trailer"] == "vision"
        assert found.details.sources["region"] == "vision"
        assert "read 4 items" in j.scans[0].vision_message
        n = len(f.requests)
        h.m.read_scans(job.id)  # nothing changed: neither reader runs again
        await asyncio.sleep(0.3)
        assert len(f.requests) == n


async def test_a_well_read_scan_is_left_alone(harness, vision_settings):
    f, app = fake()
    async with harness() as h:
        h.m.text_reader = FakeReader(GOOD)
        h.m.vision_transport = httpx.ASGITransport(app=app)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "upload")
        await until(lambda: (j := h.db.get_job(job.id)) and j.listing and j.listing.state == "done", 5, "read")
        await asyncio.sleep(0.3)
        assert f.requests == [] and h.db.get_job(job.id).scans[0].vision is None
        h.m.ask_vision(job.id, job_scan := h.db.get_job(job.id).scans[0].id)  # the button
        await wait_vision(h, job.id)
        assert len(f.requests) == 1 and job_scan


async def test_manual_and_off(harness, vision_settings):
    vision_settings.vision_mode = "manual"
    f, app = fake()
    async with harness() as h:
        h.m.text_reader = FakeReader(["x"])
        h.m.vision_transport = httpx.ASGITransport(app=app)
        job = put(h, heron_job())
        sc = await h.m.add_scan(job.id, picture(), "back", "camera")
        await asyncio.sleep(0.3)
        assert f.requests == []
        h.m.ask_vision(job.id, sc.id)
        await wait_vision(h, job.id)
        vision_settings.vision_mode = "off"
        with pytest.raises(ValueError, match="Settings"):
            h.m.ask_vision(job.id, sc.id)


async def test_a_failed_reading_says_why(harness, vision_settings):
    _, app = fake(status=500)
    async with harness() as h:
        h.m.text_reader = FakeReader(["x"])
        h.m.vision_transport = httpx.ASGITransport(app=app)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "camera")
        j = await wait_vision(h, job.id, "failed")
        assert "HTTP 500" in j.scans[0].vision_message
        assert j.listing.state == "done"  # Tesseract's reading stands


async def test_it_waits_for_a_transcode(harness, vision_settings):
    import ripshelf.visionjobs as vj

    _, app = fake()
    async with harness() as h:
        h.m.text_reader = FakeReader(["x"])
        h.m.vision_transport = httpx.ASGITransport(app=app)
        h.m._transcoders[999] = object()  # a transcode under way
        old, vj.TRANSCODE_WAIT_S = vj.TRANSCODE_WAIT_S, 0.05
        try:
            job = put(h, heron_job())
            await h.m.add_scan(job.id, picture(), "back", "camera")
            j = await wait_vision(h, job.id, "queued")
            assert "transcode" in j.scans[0].vision_message
            del h.m._transcoders[999]
            await wait_vision(h, job.id)
        finally:
            vj.TRANSCODE_WAIT_S = old


async def test_edits_are_the_users(harness, vision_settings):
    _, app = fake()
    async with harness() as h:
        h.m.text_reader = FakeReader(["Special features", "Growing the Orchard (22 mins)"])
        h.m.vision_transport = httpx.ASGITransport(app=app)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "camera")
        await wait_vision(h, job.id)
        await until(lambda: (x := h.db.get_job(job.id)) and len(x.listing.found.items) == 4, 5, "merged")
        found = h.db.get_job(job.id).listing.found
        mine = found.model_copy(deep=True)
        mine.items[0].title = "Growing the Orchard (extended)"
        view = h.m.save_listing(job.id, mine)
        assert [i.source for i in view.current.items][:2] == ["you", "vision"]


def test_api(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        m = client.app.state.manager
        rec = m.db.create_job("fake-pioneer", "/dev/sr0")
        m.db.save_job(heron_job(rec.id))
        sc = client.post(f"/api/jobs/{rec.id}/scans", data={"kind": "back"},
                         files={"file": ("p.jpg", picture(), "image/jpeg")}).json()
        assert client.post(f"/api/jobs/{rec.id}/scans/{sc['id']}/vision").status_code == 409  # not set up
        s = client.put("/api/settings", json={"vision_mode": "manual", "vision_provider": "openai",
                                              "vision_url": "https://api.example.test", "vision_model": "vm",
                                              "vision_api_key": "sk-x"}).json()
        assert (s["vision_mode"], s["vision_provider"], s["vision_api_key_set"]) == ("manual", "openai", True)
        assert "sk-x" not in json.dumps(s)
        assert client.post(f"/api/jobs/{rec.id}/scans/{sc['id']}/vision").status_code == 202
        assert client.post(f"/api/jobs/{rec.id}/scans/nope/vision").status_code == 404


@respx.mock
def test_api_test_button(settings):
    respx.post("https://api.example.test/v1/chat/completions").mock(return_value=httpx.Response(200, json={
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(READING)}}]}))
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        r = client.post("/api/settings/test", json={"check": "vision", "values": {
            "vision_provider": "openai", "vision_url": "https://api.example.test", "vision_model": "vm",
            "vision_api_key": "sk-x"}}).json()
        assert r["ok"] and any("outside your network" in d for d in r["details"])
        r = client.post("/api/settings/test", json={"check": "vision", "values": {
            "vision_provider": "anthropic", "vision_model": "vm"}}).json()
        assert not r["ok"] and "key" in r["message"]
