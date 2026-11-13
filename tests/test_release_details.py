"""The release details sent to (or shown for) TheDiscDB: UPC, ASIN, date, locale, region
and covers. Lookups run against mocked services only."""

from __future__ import annotations

import time
from datetime import date

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import ContributionExtra, DiscKind, ReleaseDetails, ReleaseProduct
from ripshelf.providers import contribute, release_info

from .test_contribute import BASE, FIXTURES, RELEASE, contributor, heron_job, named, pippa_job, put

JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
UPC_GB = "5053083123451"  # EAN, UK prefix, valid check digit
UPC_US = "012345678905"


# --- worked out locally ---------------------------------------------------------------


@pytest.mark.parametrize("code, ok", [(UPC_US, True), (UPC_GB, True), ("012345678901", False),
                                      ("12345", False), (None, False), ("01234567890a", False)])
def test_valid_upc(code, ok):
    assert release_info.valid_upc(code) is ok


@pytest.mark.parametrize("code, locale", [(UPC_US, "en-US"), (UPC_GB, "en-GB"), ("4006381333931", "de-DE"),
                                          ("9312345678907", "en-AU"), ("2000000000008", None), ("x", None)])
def test_locale_from_barcode(code, locale):
    assert release_info.locale_for_barcode(code) == locale


def ifo(mask: int) -> bytes:
    b = bytearray(b"DVDVIDEO-VMG" + b"\0" * 64)
    b[0x23] = mask
    return bytes(b)


@pytest.mark.parametrize("data, regions", [
    (ifo(0b11111101), "2"), (ifo(0b11110101), "2,4"), (ifo(0), "0"), (ifo(0b11000000), "0"),
    (ifo(0xFF), None), (b"not an ifo", None),
])
def test_dvd_regions(data, regions):
    assert release_info.dvd_regions(data) == regions


def test_region_guess(tmp_path):
    (tmp_path / "VIDEO_TS").mkdir()
    (tmp_path / "VIDEO_TS" / "VIDEO_TS.IFO").write_bytes(ifo(0b11111101))
    assert release_info.region_guess(DiscKind.dvd, "en-US", tmp_path) == ("2", "read from the disc")
    assert release_info.region_guess(DiscKind.uhd, "en-GB", None)[0] == "Region free"
    assert release_info.region_guess(DiscKind.bluray, "en-GB", None)[0] == "B"
    assert release_info.region_guess(DiscKind.bluray, "en-US", None)[0] == "A"
    assert release_info.region_guess(DiscKind.bluray, "hi-IN", None)[0] == "C"
    assert release_info.region_guess(DiscKind.dvd, "en-GB", tmp_path / "missing")[0] is None


DATES = {"results": [
    {"iso_3166_1": "GB", "release_dates": [
        {"type": 3, "release_date": "2024-03-01T00:00:00.000Z", "note": ""},
        {"type": 5, "release_date": "2024-07-08T00:00:00.000Z", "note": "DVD & Blu-ray"},
        {"type": 5, "release_date": "2024-11-18T00:00:00.000Z", "note": "4K Ultra HD Blu-ray"},
    ]},
    {"iso_3166_1": "US", "release_dates": [{"type": 5, "release_date": "2024-06-01T00:00:00.000Z"}]},
]}


def test_physical_release_date():
    assert release_info.physical_release_date(DATES, "en-GB", DiscKind.uhd)[0] == date(2024, 11, 18)
    assert release_info.physical_release_date(DATES, "en-GB", DiscKind.bluray)[0] == date(2024, 7, 8)
    day, why = release_info.physical_release_date(DATES, "en-US", DiscKind.uhd)
    assert day == date(2024, 6, 1) and "check the format" in why
    assert release_info.physical_release_date(DATES, "fr-FR", DiscKind.dvd)[0] is None


def test_asin_from_results():
    results = [{"url": "https://www.amazon.co.uk/Brass-Heron-4K/dp/B0D1234567/ref=sr_1"},
               {"url": "https://www.amazon.co.uk/gp/product/B0D1234567"},
               {"url": "https://www.amazon.com/dp/B0XXXXXXXX"},
               {"url": "https://example.com/dp/B0YYYYYYYY"}]
    assert release_info.asin_from_results(results, "amazon.co.uk") == "B0D1234567"
    assert release_info.asin_from_results(results[2:], "amazon.co.uk") is None


def test_rank_prefers_title_and_format():
    ps = [ReleaseProduct(title="The Brass Heron [Blu-ray]", upc="1"), ReleaseProduct(title="Unrelated Thing"),
          ReleaseProduct(title="The Brass Heron 4K Ultra HD + Blu-ray", upc="2", asin="B0D1234567")]
    assert [p.upc for p in release_info.rank(ps, "The Brass Heron", DiscKind.uhd)] == ["2", "1"]
    assert [p.upc for p in release_info.rank(ps, "The Brass Heron", DiscKind.bluray)][0] == "1"


def test_details_validation():
    d = ReleaseDetails(upc=" 5053083123451 ", asin="b0d1234567", locale="", region_code=" ")
    assert (d.upc, d.asin, d.locale, d.region_code) == (UPC_GB, "B0D1234567", None, None)
    for bad in ({"upc": "123"}, {"asin": "short"}, {"locale": "english please"}):
        with pytest.raises(ValueError):
            ReleaseDetails(**bad)
    assert ReleaseDetails().empty() and not ReleaseDetails(asin_unavailable=True).empty()


# --- UPCitemdb ----------------------------------------------------------------------------


ITEM = {"ean": "5053083123451", "title": "The Brass Heron 4K Ultra HD", "brand": "Studio",
        "images": ["https://img.example/heron.jpg", "http://insecure/x.jpg"],
        "offers": [{"asin": "B0D1234567"}]}


@respx.mock
async def test_products_lookup_and_search():
    respx.get(f"{release_info.UPCITEMDB}/lookup", params={"upc": UPC_GB}).mock(
        return_value=httpx.Response(200, json={"items": [ITEM]}))
    respx.get(f"{release_info.UPCITEMDB}/search").mock(return_value=httpx.Response(429))
    p = release_info.Products()
    [found] = await p.lookup(UPC_GB)
    assert (found.upc, found.asin, found.images) == (UPC_GB, "B0D1234567", ["https://img.example/heron.jpg"])
    with pytest.raises(release_info.ReleaseLookupError, match="used up for today"):
        await p.search("The Brass Heron 4K Ultra HD Blu-ray")
    await p.aclose()


# --- covers ---------------------------------------------------------------------------


def test_sniff():
    assert release_info.sniff(JPEG) == "image/jpeg" and release_info.sniff(PNG) == "image/png"
    assert release_info.sniff(b"RIFF\0\0\0\0WEBPVP8 ") == "image/webp"
    assert release_info.sniff(b"<html>") is None


@respx.mock
async def test_download_cover_guards():
    public = lambda host, port: "93.184.215.14" if host == "img.example" else None  # noqa: E731
    respx.get("https://93.184.215.14/ok.jpg").mock(return_value=httpx.Response(200, content=JPEG))
    respx.get("https://93.184.215.14/page").mock(return_value=httpx.Response(200, content=b"<html>"))
    respx.get("https://93.184.215.14/moved").mock(return_value=httpx.Response(302, headers={"location": "/x"}))
    pic = await release_info.download_cover("https://img.example/ok.jpg", public_address=public)
    assert pic.ext == "jpg" and pic.data == JPEG
    for url, why in (("http://img.example/ok.jpg", "only https"), ("https://192.168.1.2/x.jpg", "public internet"),
                     ("https://img.example/page", "isn't a JPEG"), ("https://img.example/moved", "redirects"),
                     ("https://u:p@img.example/ok.jpg", "only https")):
        with pytest.raises(release_info.ReleaseLookupError, match=why):
            await release_info.download_cover(url, public_address=public)


@respx.mock
async def test_download_cover_connects_to_the_checked_address():
    # The name is looked up once. A second lookup could answer with a LAN address (DNS
    # rebinding), so the request goes to the address that passed, named for Host and TLS.
    looked_up = []

    def public(host, port):
        looked_up.append((host, port))
        return "2606:2800:21f:cb07:6820:80da:af6b:8b2c"

    route = respx.get("https://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]:8443/ok.jpg").mock(
        return_value=httpx.Response(200, content=JPEG))
    await release_info.download_cover("https://img.example:8443/ok.jpg", public_address=public)
    assert looked_up == [("img.example", 8443)]
    sent = route.calls.last.request
    assert sent.headers["host"] == "img.example:8443"
    assert sent.extensions["sni_hostname"] == "img.example"


def test_private_hosts_are_not_public():
    assert release_info._public_address("localhost") is None
    assert release_info._public_address("127.0.0.1") is None
    assert release_info._public_address("10.0.0.1") is None
    assert release_info._public_address("8.8.8.8") == "8.8.8.8"


# --- the payload and sending ------------------------------------------------------------


def test_payload_carries_the_release_details():
    job = named(heron_job(), ContributionExtra(title_index=1, kind="Interview", name="Cast Interviews"))
    job.contribution.details = ReleaseDetails(upc=UPC_GB, asin="B0D1234567", release_date=date(2024, 11, 18),
                                              locale="en-GB", region_code="Region free")
    body = contribute.payload(job, RELEASE, 1)
    assert (body["upc"], body["asin"], body["release_date"]) == (UPC_GB, "B0D1234567", "2024-11-18")
    assert "locale" not in body and "region_code" not in body  # TheDiscDB's import has no field for them
    assert body["titles"][1]["title_type"] == "Interview"
    job.contribution.details.asin_unavailable = True
    assert "asin" not in contribute.payload(job, RELEASE, 1)
    assert "upc" not in contribute.payload(named(heron_job()), RELEASE, 1)


@respx.mock
async def test_covers_go_after_the_scan_log(settings, tmp_path):
    respx.post(f"{BASE}/api/engram/disc").mock(return_value=httpx.Response(200, json={}))
    respx.post(url__regex=rf"{BASE}/api/engram/disc/.*/logs/scan").mock(return_value=httpx.Response(200))
    front = respx.post(f"{BASE}/api/engram/release/{RELEASE}/images/front").mock(return_value=httpx.Response(200))
    (tmp_path / "front.png").write_bytes(PNG)
    c = contributor(settings)
    await c.send(contribute.payload(heron_job(), RELEASE, 1), "TINFO:0,2,0,\"x\"\n", {"front": tmp_path / "front.png"})
    await c.aclose()
    assert respx.calls[-1].request.url.path.endswith("/images/front")
    req = front.calls[0].request
    assert req.headers["content-type"].startswith("multipart/form-data")
    assert b'name="file"; filename="front.png"' in req.content and b"image/png" in req.content


# --- the manager and the API ------------------------------------------------------------


async def test_a_disc_joining_a_release_takes_its_details_and_covers(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        a = put(h, heron_job())
        h.m.contribution_view(a.id)
        await h.m.contribution_details(a.id, ReleaseDetails(upc=UPC_GB, locale="en-GB"))
        await h.m.set_cover(a.id, "front", JPEG)
        b = put(h, heron_job())
        vb = h.m.contribution_view(b.id)
        assert vb.contribution.release_id == h.db.get_job(a.id).contribution.release_id
        assert vb.contribution.details.upc == UPC_GB and vb.contribution.covers == ["front"]
        assert h.m.cover_file(b.id, "front").read_bytes() == JPEG
        with pytest.raises(ValueError, match="JPEG, PNG or WebP"):
            await h.m.set_cover(b.id, "back", b"<svg/>")
        await h.m.delete_cover(b.id, "front")
        assert h.m.cover_file(b.id, "front") is None and h.m.cover_file(a.id, "front") is not None


async def test_lookup_suggests_without_saving(harness, settings, monkeypatch):
    settings.thediscdb_contribute = "ask"

    class FakeTmdb:
        async def release_dates(self, tmdb_id):
            return DATES

    async with harness() as h:
        job = put(h, heron_job())
        monkeypatch.setattr(h.m, "_tmdb", lambda: FakeTmdb())
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{release_info.UPCITEMDB}/lookup").mock(return_value=httpx.Response(404))
            router.get(f"{release_info.UPCITEMDB}/search").mock(
                return_value=httpx.Response(200, json={"items": [ITEM, {"title": "Something Else"}]}))
            found = await h.m.release_lookup(job.id)
        assert [p.upc for p in found.products] == [UPC_GB]
        s = found.suggested
        assert s.locale == "en-GB" and s.region_code == "Region free" and s.release_date == date(2024, 11, 18)
        assert any("ASIN B0D1234567 from UPCitemdb" in n for n in found.notes)
        c = h.db.get_job(job.id).contribution
        assert c is None or c.details.empty()  # nothing saved


async def test_tv_lookup_has_no_tmdb_date(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        job = put(h, pippa_job())
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{release_info.UPCITEMDB}/search").mock(side_effect=httpx.ConnectError("down"))
            found = await h.m.release_lookup(job.id)
        assert found.suggested.release_date is None and found.products == []
        assert any("No barcode lookup" in n for n in found.notes)
        assert any("TMDB has no disc release dates for TV" in n for n in found.notes)


SEARX = "http://searx.test"


def searx(router, *urls):
    return router.get(f"{SEARX}/search").mock(return_value=httpx.Response(200, json={"results": [
        {"title": "The Brass Heron", "url": u, "content": ""} for u in urls]}))


async def test_a_label_that_is_a_barcode_and_an_asin_from_a_web_search(harness, settings, monkeypatch):
    settings.thediscdb_contribute = "ask"
    settings.catalogue_search, settings.searxng_url = "searxng", SEARX

    class NoDates:
        async def release_dates(self, tmdb_id):
            from ripshelf.identify.tmdb import TmdbError
            raise TmdbError("HTTP 503")

    async with harness() as h:
        job = heron_job()
        job.disc.label = UPC_GB  # some discs are labelled with their barcode
        job = put(h, job)
        monkeypatch.setattr(h.m, "_tmdb", lambda: NoDates())
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{release_info.UPCITEMDB}/lookup").mock(return_value=httpx.Response(200, json={"items": []}))
            router.get(f"{release_info.UPCITEMDB}/search").mock(return_value=httpx.Response(200, json={"items": []}))
            searx(router, "https://www.amazon.co.uk/dp/B0D7654321", "https://www.amazon.co.uk/x/dp/B0D7654321")
            found = await h.m.release_lookup(job.id)
        notes = " | ".join(found.notes)
        assert found.suggested.upc == UPC_GB and f"The disc's label is a barcode, {UPC_GB}." in notes
        assert f"UPCitemdb doesn't know the barcode {UPC_GB}." in notes
        assert found.suggested.asin == "B0D7654321" and "from a web search of amazon.co.uk" in notes
        assert "No release date from TMDB (HTTP 503)." in notes


@pytest.mark.parametrize(("reply", "note"), [
    (httpx.Response(200, json={"results": []}), "No ASIN found on amazon.co.uk."),
    (httpx.ConnectError("refused"), "No web search for the ASIN:"),
])
async def test_no_asin_from_the_web_search(harness, settings, reply, note):
    settings.thediscdb_contribute = "ask"
    settings.catalogue_search, settings.searxng_url = "searxng", SEARX
    async with harness() as h:
        job = put(h, heron_job())
        with respx.mock(assert_all_called=False) as router:
            router.get(f"{release_info.UPCITEMDB}/lookup").mock(return_value=httpx.Response(404))
            router.get(f"{release_info.UPCITEMDB}/search").mock(return_value=httpx.Response(200, json={"items": []}))
            if isinstance(reply, Exception):
                router.get(f"{SEARX}/search").mock(side_effect=reply)
            else:
                router.get(f"{SEARX}/search").mock(return_value=reply)
            found = await h.m.release_lookup(job.id, upc=UPC_GB)
        assert found.suggested.asin is None and any(n.startswith(note) for n in found.notes)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        yield c


def test_api_details_and_covers(client):
    m = client.app.state.manager
    rec = m.db.create_job("fake-pioneer", "/dev/sr0")
    job = heron_job(rec.id)
    m.db.save_job(job)
    m.work_dir(job).mkdir(parents=True)
    (m.work_dir(job) / "scan.log").write_text((FIXTURES / "scan-uhd-brass-heron.log").read_text())
    client.put("/api/settings", json={"thediscdb_contribute": "ask", "thediscdb_contribute_url": BASE})
    url = f"/api/jobs/{job.id}/thediscdb"
    client.put(f"{url}/extras", json={"extras": [{"title_index": 1, "kind": "Short", "name": "Short Film"}]})

    assert client.put(f"{url}/details", json={"upc": "12"}).status_code == 422
    view = client.put(f"{url}/details", json={"upc": UPC_GB, "asin": "b0d1234567", "release_date": "2024-11-18",
                                              "locale": "en-GB", "region_code": "Region free"}).json()
    assert view["payload"]["upc"] == UPC_GB and view["contribution"]["details"]["asin"] == "B0D1234567"

    assert client.put(f"{url}/cover/front", files={"file": ("f.txt", b"hello", "text/plain")}).status_code == 409
    assert client.put(f"{url}/cover/side", files={"file": ("f.jpg", JPEG, "image/jpeg")}).status_code == 422
    view = client.put(f"{url}/cover/front", files={"file": ("f.jpg", JPEG, "image/jpeg")}).json()
    assert view["contribution"]["covers"] == ["front"]
    r = client.get(f"{url}/cover/front")
    assert r.status_code == 200 and r.content == JPEG and r.headers["content-type"] == "image/jpeg"
    assert client.get(f"{url}/cover/back").status_code == 404
    r = client.post(f"{url}/cover/back/from-url", json={"url": "http://192.168.1.10/x.jpg"})
    assert r.status_code == 409 and "only https" in r.text

    with respx.mock(assert_all_called=False) as router:
        router.post(f"{BASE}/api/engram/disc").mock(return_value=httpx.Response(200, json={"id": 1}))
        router.post(url__regex=rf"{BASE}/api/engram/disc/.*/logs/scan").mock(return_value=httpx.Response(200))
        cover = router.post(url__regex=rf"{BASE}/api/engram/release/.*/images/front").mock(
            return_value=httpx.Response(200))
        client.post(f"{url}/send")
        end = time.monotonic() + 10
        while client.get(f"/api/jobs/{job.id}").json()["contribution"]["status"] == "sending":
            assert time.monotonic() < end
            time.sleep(0.05)
    assert client.get(f"/api/jobs/{job.id}").json()["contribution"]["status"] == "sent" and cover.called
    # Changing the box's details after sending asks for it to be sent again.
    again = client.delete(f"{url}/cover/front").json()
    assert again["contribution"]["status"] == "not_sent" and again["contribution"]["covers"] == []
    assert client.put("/api/jobs/999/thediscdb/details", json={}).status_code == 404
