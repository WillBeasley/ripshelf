"""Catalogue-number labels ("EO51710") looked up on the web before the TMDB search."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from ripshelf import checks
from ripshelf.dev import fake
from ripshelf.identify import catalogue
from ripshelf.models import JobState, SettingsUpdate

from .test_jobs_identify import PM, moonlit_orchard

SEARX = "http://searx.test"


@pytest.mark.parametrize("label, expected", [
    ("EO51710", "EO51710"),
    ("EO51710_DVD", "EO51710"),
    ("OPTD2345_D1", "OPTD2345"),
    ("CDR-30123", "CDR-30123"),
    ("8299901", "8299901"),
    ("5030305517106", "5030305517106"),
    ("BTTF2", None),  # an abbreviation, not a catalogue number
    ("TPCR_DISC1", None),
    ("COMET_LANES", None),
    ("1747", None),  # a year (and a title)
    ("DVD12345", None),
    ("DVD_VIDEO", None),
    ("", None),
    (None, None),
])
def test_number(label, expected):
    assert catalogue.number(label) == expected


@pytest.mark.parametrize("page, expected", [
    ("The Hollow Compass: City of Ash", "The Hollow Compass: City of Ash"),
    ("Hollow Compass, The: City of Ash (DVD) - Filmbutikken-eksempel.dk - salg af DVD og Blu-ray film.",
     "The Hollow Compass: City of Ash"),
    ("The Hollow Compass: City of Ash [Blu-ray] [Region B] | Example-discs.co.uk", "The Hollow Compass: City of Ash"),
    ("Moonlit Orchard DVD Region 2 Special Edition EO51710", "Moonlit Orchard"),
    ("Lodgers: The Complete Series 3 [DVD] [1995]", "Lodgers"),
    ("Spooky Picture 3 (DVD)", "Spooky Picture 3"),  # "Movie" and a sequel number are the title
    ("Ours (2019) - DVD", "Ours"),
    ("EO51710 | base.com", None),
    ("example-discs.co.uk", None),
])
def test_clean_title(page, expected):
    assert catalogue.clean_title(page, "EO51710") == expected


def test_rank_titles_counts_pages_that_add_the_cast():
    ranked = catalogue.rank_titles(["The Hollow Compass: City of Ash", "Hollow Compass: City of Ash",
                                    "The Hollow Compass: City Of Ash Ada Pellow", "Something Else"])
    assert ranked[0] == ("The Hollow Compass: City of Ash", 3)
    assert ("Something Else", 1) in ranked


def searx_pages(router, pages, status=200):
    return router.get(f"{SEARX}/search").mock(return_value=httpx.Response(status, json={"results": pages}))


def page(title, catno="EO51710", url="https://shop.example/x"):
    return {"title": title, "url": url, "content": f"Catalogue No: {catno}. Release date 2014."}


@pytest.fixture
def searx(settings):
    settings.catalogue_search = "searxng"
    settings.searxng_url = SEARX + "/"
    return settings


async def test_lookup_uses_only_pages_that_mention_the_number(searx):
    with respx.mock() as router:
        route = searx_pages(router, [
            page("The Hollow Compass: City of Ash (DVD) - Filmbutikken-eksempel.dk"),
            page("Hollow Compass, The: City of Ash | base.com"),
            page("EO: a film about a donkey", catno="something else"),
        ])
        found = await catalogue.lookup(searx, "EO51710", "dvd")
    assert route.calls[0].request.url.params["q"] == '"EO51710" dvd'
    assert route.calls[0].request.url.params["format"] == "json"
    assert found.pages == 2 and found.titles == [("The Hollow Compass: City of Ash", 2)]


async def test_lookup_retries_unquoted_when_nothing_mentions_the_number(searx):
    with respx.mock() as router:
        route = router.get(f"{SEARX}/search").mock(side_effect=[
            httpx.Response(200, json={"results": []}),
            httpx.Response(200, json={"results": [page("City of Ash")]}),
        ])
        found = await catalogue.lookup(searx, "EO51710", "bd")
    assert [c.request.url.params["q"] for c in route.calls] == ['"EO51710" blu-ray', "EO51710"]
    assert found.names == ["City of Ash"]


async def test_searxng_without_json_says_how_to_fix_it(searx):
    with respx.mock() as router:
        searx_pages(router, [], status=403)
        with pytest.raises(catalogue.CatalogueError, match="search.formats"):
            await catalogue.lookup(searx, "EO51710")


# The pipeline, with the Moonlit Orchard DVD, its label replaced by a catalogue number.

@pytest.fixture
def catno_disc(searx, monkeypatch, mkv_script):
    searx.tmdb_api_key = SecretStr("k")
    monkeypatch.setattr(fake.DRIVES[PM][1], "label", "EO51710")
    searx.makemkvcon = mkv_script(info="""
        for line in (FIX / 'scan-dvd-moonlit-orchard.log').read_text().splitlines():
            out(line.replace('MOONLIT_ORCHARD', 'EO51710'))
    """)
    return searx


async def test_catalogue_number_auto_confirms_when_pages_agree(harness, catno_disc):
    with respx.mock(assert_all_called=False) as router:
        moonlit_orchard(router)
        searx_pages(router, [page("Moonlit Orchard (DVD) - shop.dk"), page("Moonlit Orchard [DVD] | Example-discs.co.uk")])
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state in (JobState.done, JobState.review))
            events = [e.message for e in h.db.events(job.id)]
    assert job.identity and job.identity.tmdb_id == 6435 and job.identity_source == "auto"
    assert any("2 web pages listing catalogue number EO51710" in e.text for e in job.identity.evidence)
    assert any("Catalogue number EO51710: 2 web page(s)" in m for m in events)


async def test_one_page_alone_needs_review(harness, catno_disc):
    with respx.mock(assert_all_called=False) as router:
        moonlit_orchard(router)
        searx_pages(router, [page("Moonlit Orchard (DVD)")])
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
    best = job.candidates[0]
    assert best.tmdb_id == 6435 and best.score <= catalogue.SINGLE_PAGE_CAP
    assert any("Only one web page" in e.text for e in best.evidence)


async def test_lookup_off_says_so(harness, catno_disc):
    catno_disc.catalogue_search = "off"
    with respx.mock(assert_all_called=False) as router:
        moonlit_orchard(router)
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            events = [e.message for e in h.db.events(job.id)]
    assert any("looks like a catalogue number (EO51710)" in m for m in events)


async def test_search_failure_is_a_warning_not_a_failure(harness, catno_disc):
    with respx.mock(assert_all_called=False) as router:
        moonlit_orchard(router)
        router.get(f"{SEARX}/search").mock(side_effect=httpx.ConnectError("down"))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            events = [e.message for e in h.db.events(job.id)]
    assert any("Catalogue number EO51710: web search failed" in m for m in events)


async def test_settings_check_uses_the_form_values(settings):
    form = SettingsUpdate(catalogue_search="searxng", searxng_url=SEARX)
    with respx.mock() as router:
        searx_pages(router, [page("The Hollow Compass: City of Ash (DVD)"),
                             page("Hollow Compass, The: City of Ash - shop.dk")])
        ok = await checks.catalogue(settings, form)
        searx_pages(router, [])
        empty = await checks.catalogue(settings, form)
    assert ok.ok and "The Hollow Compass: City of Ash" in ok.message
    assert not empty.ok and "no page naming EO51710" in empty.message
    assert not (await checks.catalogue(settings, SettingsUpdate())).ok  # off
