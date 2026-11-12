"""A cover's barcode named by TheDiscDB, UPCitemdb, a paid service or a web search."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from ripshelf import jobs as jobs_module
from ripshelf.identify import barcode
from ripshelf.models import Candidate, JobState, Listing, ListingDetails, ScanListing
from ripshelf.providers import release_info

from .test_contribute import heron_job, put

EAN = "5012345678900"  # made up, with a valid check digit
UPC_A = "012345678905"
SEARX = "http://searx.test"


@pytest.fixture
def s(settings):
    settings.catalogue_search = "off"
    settings.barcode_service = "off"
    settings.barcode_service_key = None
    return settings


def thediscdb(*entries):
    return respx.get(barcode.THEDISCDB_SEARCH).mock(return_value=httpx.Response(200, json=list(entries)))


def upcitemdb(status=200, title=None):
    body = {"items": [{"title": title, "ean": EAN}]} if title else {"items": []}
    return respx.get(f"{release_info.UPCITEMDB}/lookup").mock(
        return_value=httpx.Response(status, json=body if status == 200 else {}))


def test_same_code():
    assert barcode.same_code(UPC_A, "0" + UPC_A)
    assert not barcode.same_code(EAN, UPC_A)
    assert not barcode.same_code("", "")


@respx.mock
async def test_thediscdb_first(s):
    thediscdb({"type": "Movie", "title": "The Brass Heron", "identifiers": ["tt0000001", "0" + UPC_A]})
    upc = upcitemdb(title="never asked")
    answer = await barcode.name(s, UPC_A)
    assert answer.found == barcode.Found("The Brass Heron", "thediscdb")
    assert not upc.called


@respx.mock
async def test_thediscdb_fuzzy_hits_dont_count(s):
    thediscdb({"type": "Movie", "title": "Something Else", "identifiers": ["5012345678901"]})
    upcitemdb(title="The Brass Heron [Blu-ray] (2024)")
    answer = await barcode.name(s, EAN)
    assert answer.found == barcode.Found("The Brass Heron [Blu-ray] (2024)", "upcitemdb")


@respx.mock
async def test_thediscdb_left_out_when_unticked(s):
    route = thediscdb()
    upcitemdb(title="The Brass Heron [DVD]")
    assert (await barcode.name(s, EAN, thediscdb=False)).found.source == "upcitemdb"
    assert not route.called


@respx.mock
async def test_nobody_knows_it_is_final(s):
    thediscdb()
    upcitemdb()
    answer = await barcode.name(s, EAN)
    assert answer.found is None and answer.final


@respx.mock
async def test_a_failed_source_is_asked_again(s):
    thediscdb()
    upcitemdb(status=429)
    answer = await barcode.name(s, EAN)
    assert answer.found is None and not answer.final
    assert "UPCitemdb" in answer.failed[0] and "used up" in answer.failed[0]


@respx.mock
async def test_barcode_lookup(s):
    s.barcode_service, s.barcode_service_key = "barcodelookup", SecretStr("k")
    thediscdb()
    upcitemdb()
    route = respx.get(barcode.BARCODELOOKUP, params={"barcode": EAN, "key": "k"}).mock(
        return_value=httpx.Response(200, json={"products": [{"title": "The Brass Heron (Blu-ray)"}]}))
    assert (await barcode.name(s, EAN)).found == barcode.Found("The Brass Heron (Blu-ray)", "barcodelookup")
    assert route.called


@respx.mock
async def test_barcode_lookup_without_a_key_is_skipped(s):
    s.barcode_service = "barcodelookup"
    thediscdb()
    upcitemdb()
    route = respx.get(barcode.BARCODELOOKUP).mock(return_value=httpx.Response(500))
    assert (await barcode.name(s, EAN)).final
    assert not route.called


@respx.mock
async def test_ean_search(s):
    s.barcode_service, s.barcode_service_key = "eansearch", SecretStr("t")
    thediscdb()
    upcitemdb()
    route = respx.get(barcode.EANSEARCH, params={"ean": "0" + UPC_A, "token": "t"}).mock(
        return_value=httpx.Response(200, json=[{"ean": "0" + UPC_A, "name": "The Brass Heron DVD"}]))
    assert (await barcode.name(s, UPC_A)).found == barcode.Found("The Brass Heron DVD", "eansearch")
    assert route.called


@respx.mock
@pytest.mark.parametrize("reply, final", [([{"error": "Barcode not found"}], True),
                                          ([{"error": "Invalid token"}], False)])
async def test_ean_search_errors(s, reply, final):
    s.barcode_service, s.barcode_service_key = "eansearch", SecretStr("t")
    thediscdb()
    upcitemdb()
    respx.get(barcode.EANSEARCH).mock(return_value=httpx.Response(200, json=reply))
    answer = await barcode.name(s, EAN)
    assert answer.found is None and answer.final == final


def searx(*titles):
    return respx.get(f"{SEARX}/search").mock(return_value=httpx.Response(200, json={"results": [
        {"title": t, "url": f"https://shop{i}.example/p", "content": f"EAN: {EAN}"} for i, t in enumerate(titles)]}))


@respx.mock
async def test_web_search_last(s):
    s.catalogue_search, s.searxng_url = "searxng", SEARX
    thediscdb()
    upcitemdb()
    searx("The Brass Heron [Blu-ray] [2024] | Shop", "The Brass Heron (Blu-ray) - Other shop", "Unrelated")
    assert (await barcode.name(s, EAN, "bluray")).found == barcode.Found("The Brass Heron", "web")


@respx.mock
async def test_one_web_page_is_not_enough(s):
    s.catalogue_search, s.searxng_url = "searxng", SEARX
    thediscdb()
    upcitemdb()
    searx("The Brass Heron [Blu-ray] | Shop")
    answer = await barcode.name(s, EAN, "bluray")
    assert answer.found is None and answer.final


# --- on a job ---------------------------------------------------------------------------


def reading(code) -> ScanListing:
    return ScanListing(state="done", found=Listing(details=ListingDetails(barcode=code)))


async def test_the_answer_is_kept_and_asked_again_for_a_new_barcode(harness, s, monkeypatch):
    asked = []
    answers = {EAN: barcode.Answer(barcode.Found("The Brass Heron", "thediscdb"), []),
               UPC_A: barcode.Answer(None, ["UPCitemdb: offline"])}

    async def name(settings, code, kind=None, **kw):
        asked.append(code)
        return answers[code]

    monkeypatch.setattr(barcode, "name", name)
    async with harness() as h:
        job = put(h, heron_job(identity=None, identity_source=None, state=JobState.review))
        job.listing = reading(EAN)
        job = put(h, job)
        hints = await h.m.scan_hints(job)
        assert hints.names == ["The Brass Heron"]  # TheDiscDB's names aren't cleaned like shop listings
        assert "(TheDiscDB)" in hints.evidence[0]
        job = h.db.get_job(job.id)
        await h.m.scan_hints(job)
        assert asked == [EAN]  # kept
        job.listing = reading(UPC_A)
        job = put(h, job)
        assert (await h.m.scan_hints(job)).names == []
        job = h.db.get_job(job.id)
        assert job.listing.upc_title is None  # a source failed: asked again next time
        await h.m.scan_hints(job)
        assert asked == [EAN, UPC_A, UPC_A]


async def test_identify_searches_what_the_barcode_names(harness, s, monkeypatch):
    queries = {}

    async def search(settings, q, year):
        queries["q"] = q
        return [Candidate(tmdb_id=1, title="The Brass Heron", year=2024, runtime=102)], []

    async def name(settings, code, kind=None, **kw):
        return barcode.Answer(barcode.Found("The Brass Heron", "web"), [])

    monkeypatch.setattr(jobs_module.providers, "enabled", lambda settings, kind: ["tmdb"])
    monkeypatch.setattr(jobs_module.films, "search", search)
    monkeypatch.setattr(barcode, "name", name)
    async with harness() as h:
        job = heron_job(identity=None, identity_source=None, state=JobState.review)
        job.disc.label = "DISC_1"
        job.listing = reading(EAN)
        job = put(h, job)
        await h.m._identify(job.id)
        assert queries["q"][0] == "The Brass Heron"
        assert any("a web search" in e.message for e in h.db.events(job.id))
