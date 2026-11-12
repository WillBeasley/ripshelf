"""Reading a cryptic label with an AI model: the reply, the request (against pretend
servers, never a real one), how its reading is scored, and its use in the pipeline,
in Review and in Settings' Test."""

from __future__ import annotations

import json

import httpx
import httpx2
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf.dev.fake import FakeDrives
from ripshelf.dev.fake_vision import FakeVision
from ripshelf.identify import ai_label
from ripshelf.identify.tmdb import API
from ripshelf.main import create_app
from ripshelf.models import Candidate, DiscFacts, DiscKind, JobState, Show

from .test_jobs_identify import PM

LABEL = "MNLT_ORCH"  # the Moonlit Orchard DVD, mastered with a cryptic label


def fake(**kw) -> tuple[FakeVision, FastAPI]:
    f = FakeVision(**kw)
    app = FastAPI()
    app.include_router(f.router())
    return f, app


def provider(app: FastAPI, kind: str = "local"):
    from ripshelf.scanner.vision import Provider

    if kind == "anthropic":
        return Provider(kind="anthropic", url="http://anthropic.test/fake-anthropic", model="a-model", key="sk",
                        transport=httpx2.ASGITransport(app=app))
    return Provider(kind="local", url="http://ollama.test/fake-vision", model="qwen2.5:3b",
                    transport=httpx.ASGITransport(app=app))


# --- the reply --------------------------------------------------------------------------------


def test_titles_without_edition_or_disc_words():
    titles, kind = ai_label.parse({"titles": ["Moonlit Orchard (Special Edition)", "moonlit orchard",
                                              "Moonlit Orchard Disc 2", "Tin Parade: Director's Cut", "", "A",
                                              "B", "C", "D"], "kind": "movie"})
    assert titles == ["Moonlit Orchard", "Tin Parade", "A", "B", "C"] and kind == "movie"
    assert ai_label.parse({"titles": [], "kind": "unknown"}) == ([], None)
    with pytest.raises(ai_label.LabelError):
        ai_label.parse({"titles": "Moonlit Orchard"})


def test_only_what_the_disc_says_goes_in_the_prompt():
    facts = DiscFacts(label=LABEL, main_duration_s=5983, pal=True, kind=DiscKind.dvd)
    text = ai_label.describe(facts, "Moonlit", [5983, 600], tv=False)
    assert "Volume label: MNLT_ORCH" in text and "Disc title (from the disc): Moonlit" in text
    assert "1:39:43 (PAL" in text and "Titles on the disc (2): 1:39:43, 10:00" in text
    tv = ai_label.describe(DiscFacts(label="PG_S3_D2"), None, [660] * 4, tv=True)
    assert "Main feature" not in tv and "TV series" in tv


# --- the request ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["local", "anthropic"])
async def test_a_text_only_request(kind):
    f, app = fake(label={"titles": ["Moonlit Orchard"], "kind": "movie"})
    reading = await ai_label.read(provider(app, kind), DiscFacts(label=LABEL), None, [], tv=False)
    assert reading.titles == ["Moonlit Orchard"] and reading.kind == "movie"
    body = f.requests[0]["body"]
    if kind == "local":
        assert isinstance(body["messages"][0]["content"], str)
        assert body["response_format"]["json_schema"]["name"] == "label_reading"
        assert body["response_format"]["json_schema"]["schema"] == ai_label.SCHEMA
    else:
        assert [c["type"] for c in body["messages"][0]["content"]] == ["text"]
        assert body["output_config"]["format"]["schema"] == ai_label.SCHEMA
    assert "MNLT_ORCH" in json.dumps(body)


async def test_a_failure_is_a_label_error():
    _, app = fake(status=404)
    with pytest.raises(ai_label.LabelError, match="has no model"):
        await ai_label.read(provider(app), DiscFacts(label=LABEL), None, [], tv=False)


# --- scoring ----------------------------------------------------------------------------------


def films() -> list[Candidate]:
    return [Candidate(tmdb_id=1, title="Moonlit Orchard", year=1998, runtime=104),
            Candidate(tmdb_id=2, title="Mint Orchestra", year=2003, runtime=95)]


def test_a_match_only_the_reading_supports_is_weighted_down_and_marked():
    facts = DiscFacts(label="MO_D1", names=["Mo"], main_duration_s=5983, pal=True)
    ranked = ai_label.weigh_films(films(), facts, ["Moonlit Orchard"], [])
    best = ranked[0]
    assert best.tmdb_id == 1 and best.from_ai and best.score <= ai_label.GUESS_WEIGHT
    assert "read the label as “Moonlit Orchard”" in best.evidence[0].text


def test_the_labels_own_reading_wins_when_its_better():
    facts = DiscFacts(label="MOONLIT_ORCHARD", names=["Moonlit Orchard"], main_duration_s=5983, pal=True)
    ranked = ai_label.weigh_films(films(), facts, ["Mint Orchestra"], [])
    assert ranked[0].tmdb_id == 1 and not ranked[0].from_ai and ranked[0].score > 0.9


def test_a_film_only_the_readings_search_found_is_marked_however_it_scores():
    """"Mnlt Orch" is close enough to Moonlit Orchard to score, but only the search for the
    model's reading found it."""
    facts = DiscFacts(label=LABEL, names=["Mnlt Orch"], main_duration_s=5983, pal=True)
    found = films()[:1]
    ranked = ai_label.weigh_films(found, facts, ["Moonlit Orchard"], found)
    assert ranked[0].from_ai and "read the label" in ranked[0].evidence[0].text


def test_shows_from_the_reading():
    shows = ai_label.weigh_shows([Show(tmdb_id=5, name="Pippa & Goose", score=1.0)], ["Pippa and Goose"])
    assert shows[0].from_ai and shows[0].score == ai_label.GUESS_WEIGHT


# --- in the pipeline --------------------------------------------------------------------------


def cryptic_scan(mkv_script) -> str:
    return mkv_script(info=f"""
        for line in (FIX / 'scan-dvd-moonlit-orchard.log').read_text().splitlines():
            out(line.replace('MOONLIT_ORCHARD', {LABEL!r}))
    """)


def tmdb_knows_only_the_full_title(router):
    def search(request):
        q = request.url.params.get("query", "")
        return httpx.Response(200, json={"results": [
            {"id": 6435, "title": "Moonlit Orchard", "release_date": "1998-10-16", "vote_count": 2500},
        ] if q.lower() == "moonlit orchard" else []})

    router.get(f"{API}/search/movie").mock(side_effect=search)
    router.get(f"{API}/movie/6435").mock(return_value=httpx.Response(200, json={"id": 6435, "runtime": 104}))


@pytest.fixture
def ai_settings(settings, mkv_script):
    settings.tmdb_api_key = SecretStr("k")
    settings.makemkvcon = cryptic_scan(mkv_script)
    settings.vision_provider, settings.vision_url = "local", "http://ollama.test/fake-vision"
    settings.label_ai_model = "qwen2.5:3b"
    settings.auto_confirm_threshold = 0.5  # even this low, the model's word alone never confirms
    return settings


async def test_auto_finds_the_film_but_waits_for_review(harness, ai_settings):
    ai_settings.label_ai = "auto"
    f, app = fake(label={"titles": ["Moonlit Orchard"], "kind": "movie"})
    with respx.mock(assert_all_called=False) as router:
        tmdb_knows_only_the_full_title(router)
        async with harness(PM) as h:
            h.m.backend.swapped[PM] = LABEL
            h.m.vision_transport = httpx.ASGITransport(app=app)
            job = await h.wait_job(lambda j: j.state == JobState.review and j.candidates)
            events = [e.message for e in h.db.events(job.id)]
    assert job.disc.label == LABEL
    best = job.candidates[0]
    assert best.tmdb_id == 6435 and best.from_ai and job.identity is None
    assert any("qwen2.5:3b read the label as “Moonlit Orchard”" in m for m in events)
    assert f.requests[0]["body"]["model"] == "qwen2.5:3b"


async def test_off_never_asks(harness, ai_settings):
    f, app = fake()
    with respx.mock(assert_all_called=False) as router:
        tmdb_knows_only_the_full_title(router)
        async with harness(PM) as h:
            h.m.backend.swapped[PM] = LABEL
            h.m.vision_transport = httpx.ASGITransport(app=app)
            job = await h.wait_job(lambda j: j.state == JobState.review)
            assert h.m.status().label_ai is False
            with pytest.raises(ValueError, match="off"):
                await h.m.read_label(job.id)
    assert not job.candidates and not f.requests


async def test_the_button_in_review(harness, ai_settings):
    ai_settings.label_ai = "manual"
    f, app = fake(label={"titles": ["Moonlit Orchard", "Mint Orchestra"], "kind": "movie"})
    with respx.mock(assert_all_called=False) as router:
        tmdb_knows_only_the_full_title(router)
        async with harness(PM) as h:
            h.m.backend.swapped[PM] = LABEL
            h.m.vision_transport = httpx.ASGITransport(app=app)
            job = await h.wait_job(lambda j: j.state == JobState.review)
            assert not job.candidates and not f.requests  # manual: only when asked
            assert h.m.status().label_ai is True
            out = await h.m.read_label(job.id)
            job = h.job(job.id)
    assert out.titles == ["Moonlit Orchard", "Mint Orchestra"] and out.model == "qwen2.5:3b"
    assert out.candidates[0].tmdb_id == 6435 and job.candidates[0].from_ai


# --- Settings' Test and the API ---------------------------------------------------------------


@respx.mock
def test_settings_test_button(settings):
    def answer(titles):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"titles": titles, "kind": "movie"})}}]})

    route = respx.post("https://api.example.test/v1/chat/completions")
    values = {"vision_provider": "openai", "vision_url": "https://api.example.test", "vision_model": "vm",
              "label_ai_model": "lm", "vision_api_key": "sk-x"}
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        route.mock(return_value=answer(["Back to the Future Part II"]))
        r = client.post("/api/settings/test", json={"check": "label_ai", "values": values}).json()
        assert r["ok"] and "lm read the test label" in r["message"]
        assert any("outside your network" in d for d in r["details"])
        assert json.loads(route.calls.last.request.content)["model"] == "lm"
        route.mock(return_value=answer(["Bill & Ted"]))
        r = client.post("/api/settings/test", json={"check": "label_ai", "values": values}).json()
        assert not r["ok"] and "didn't read" in r["message"]
        r = client.post("/api/settings/test", json={"check": "label_ai", "values": {
            "vision_provider": "local", "vision_url": "", "vision_model": "vm"}}).json()
        assert not r["ok"] and "address" in r["message"]


def test_api_read_label(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        assert client.post("/api/jobs/99/read-label").status_code == 404
        s = client.put("/api/settings", json={"label_ai": "manual", "label_ai_model": "lm"}).json()
        assert (s["label_ai"], s["label_ai_model"]) == ("manual", "lm")


async def test_auto_finds_the_show_but_never_confirms_it(harness, ai_settings, mkv_script):
    from .tvfix import mock_show

    ai_settings.label_ai, ai_settings.tv_auto_threshold = "auto", 0.5
    ai_settings.makemkvcon = mkv_script(info="""
        for line in (FIX / 'scan-dvd-pippa-goose.log').read_text().splitlines():
            out(line.replace('PIPPA_&_GOOSE_KITE_TRAM', 'PG_KT'))
    """)
    f, app = fake(label={"titles": ["Pippa & Goose"], "kind": "tv"})
    with respx.mock(assert_all_called=False) as router:
        fx = mock_show(router, "pippa-goose")

        def search(request):
            found = request.url.params.get("query", "").lower() == "pippa & goose"
            return httpx.Response(200, json={"results": fx["search"]["results"] if found else []})

        router.get(f"{API}/search/tv").mock(side_effect=search)
        async with harness("/dev/fake-sr0") as h:
            h.m.backend.swapped["/dev/fake-sr0"] = "PG_KT"
            h.m.vision_transport = httpx.ASGITransport(app=app)
            job = await h.wait_job(lambda j: j.state == JobState.review and j.tv and j.tv.show, timeout=60)
    assert job.tv.show.name == "Pippa & Goose" and job.tv.show.from_ai
    assert "TV series" in f.requests[0]["body"]["messages"][0]["content"]
    assert any("AI model" in w for w in job.tv.auto_evidence)
