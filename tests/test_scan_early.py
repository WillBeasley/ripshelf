"""Scans before and while a disc is identified: pictures kept for a drive's next disc,
and what the box says used to identify the disc."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ripshelf import jobs as jobs_module
from ripshelf import pendingscans
from ripshelf.dev.fake import FakeDrives
from ripshelf.identify import barcode
from ripshelf.main import create_app
from ripshelf.matchjobs import product_name
from ripshelf.models import Candidate, JobState, Listing, ListingDetails, ScanListing

from .conftest import until
from .test_contribute import heron_job, put
from .test_scans import picture

DRIVE, DEVICE = "fake-ihas122", "/dev/fake-sr1"


async def test_scans_wait_for_the_next_disc(harness, settings):
    async with harness() as h:
        p = await h.m.add_pending(DRIVE, picture(), "back", "camera")
        await h.m.add_pending(DRIVE, picture(), "insert", "upload")
        assert [x.kind for x in h.m.pending_scans(DRIVE).scans] == ["back", "insert"]
        assert h.m.pending_scans("fake-pioneer").scans == []
        assert h.m.pending_file(p.id)[0].read_bytes() == picture()
        h.backend.insert(DEVICE)
        job = await h.wait_job(lambda j: len(j.scans) == 2, what="the scans taken")
        assert [(s.kind, s.source) for s in job.scans] == [("back", "camera"), ("insert", "upload")]
        assert h.m.pending_scans(DRIVE).scans == []
        assert any("added before the disc went in" in e.message for e in h.db.events(job.id))


async def test_waiting_scans_are_forgotten_after_a_day(harness, settings, monkeypatch):
    async with harness() as h:
        p = await h.m.add_pending(DRIVE, picture(), "front", "upload")
        f = h.m.pending_file(p.id)[0]
        later = pendingscans.now() + timedelta(hours=25)
        monkeypatch.setattr(pendingscans, "now", lambda: later)
        assert h.m.pending_scans(DRIVE).scans == []
        assert not f.exists()


async def test_waiting_scans_can_be_removed(harness, settings):
    async with harness() as h:
        p = await h.m.add_pending(DRIVE, picture(), "front", "upload")
        await h.m.delete_pending(DRIVE, p.id)
        assert h.m.pending_scans(DRIVE).scans == []
        with pytest.raises(KeyError):
            await h.m.delete_pending(DRIVE, p.id)
        with pytest.raises(KeyError):
            h.m.pending_scans("no-such-drive")
        with pytest.raises(ValueError, match="JPEG"):
            await h.m.add_pending(DRIVE, b"nope", "front", "upload")


@pytest.mark.parametrize("title, name", [
    ("The Brass Heron (DVD, 2024) [Region 2]", "The Brass Heron"),
    ("Moonlit Orchard DVD Special Edition", "Moonlit Orchard"),
    ("Pippa & Goose: Kite Tram [Blu-ray]", "Pippa & Goose: Kite Tram"),
])
def test_product_names(title, name):
    assert product_name(title) == name


def reading(title=None, year=None, barcode=None) -> ScanListing:
    return ScanListing(state="done", found=Listing(details=ListingDetails(title=title, year=year, barcode=barcode)))


async def test_the_box_names_the_film(harness, settings, monkeypatch):
    """The title read off the box is searched for first, its year counts, and the
    candidate it finds says where it came from."""
    asked = {}

    async def search(s, queries, year):
        asked.update(queries=queries, year=year)
        return [Candidate(tmdb_id=1, title="The Brass Heron", year=2024, runtime=102)], []

    monkeypatch.setattr(jobs_module.providers, "enabled", lambda s, kind: ["tmdb"])
    monkeypatch.setattr(jobs_module.films, "search", search)
    async with harness() as h:
        job = heron_job(identity=None, identity_source=None, state=JobState.review)
        job.disc.label = "DISC_1"
        job.listing = reading("The Brass Heron", 2024, "5012345678900")
        job = put(h, job)

        async def name(s, code, kind=None, **kw):
            return barcode.Answer(barcode.Found("The Brass Heron [Blu-ray] (2024)", "upcitemdb"), [])

        monkeypatch.setattr(barcode, "name", name)
        await h.m._identify(job.id)
        assert asked["queries"][0] == "The Brass Heron" and asked["year"] == 2024
        best = h.db.get_job(job.id).candidates[0]
        assert any(e.text.startswith("From the cover scan") for e in best.evidence)
        assert h.db.get_job(job.id).listing.upc_title == "The Brass Heron [Blu-ray] (2024)"


async def test_a_scan_read_in_review_identifies_again(harness, settings, monkeypatch):
    async with harness() as h:
        again = []

        async def identify(job_id):
            again.append(job_id)

        monkeypatch.setattr(h.m, "_identify_then_rip", identify)
        job = heron_job(identity=None, identity_source=None, state=JobState.review)
        job.listing = reading("The Brass Heron", 2024)
        job = put(h, job)
        h.m.identify_from_scan(job.id)
        await until(lambda: again, 5, "identified again")
        h.m.identify_from_scan(job.id)  # nothing new on the box: not again
        assert again == [job.id]
        confirmed = put(h, heron_job(state=JobState.review))
        confirmed.listing = reading("Something Else")
        h.db.save_job(confirmed)
        h.m.identify_from_scan(confirmed.id)  # already confirmed: left alone
        assert again == [job.id]


def test_api(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as client:
        r = client.post(f"/api/drives/{DRIVE}/pending-scans", data={"kind": "back", "source": "camera"},
                        files={"file": ("p.jpg", picture(), "image/jpeg")})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        got = client.get(f"/api/drives/{DRIVE}/pending-scans").json()
        assert [s["id"] for s in got["scans"]] == [sid] and got["scanner"] is None
        assert client.get(f"/api/pending-scans/{sid}").content == picture()
        assert client.get(f"/api/pending-scans/{sid}?thumb=true").headers["content-type"] == "image/jpeg"
        assert client.post(f"/api/drives/{DRIVE}/pending-scans/scanner", json={"kind": "back"}).status_code == 409
        assert client.delete(f"/api/drives/{DRIVE}/pending-scans/{sid}").status_code == 204
        assert client.get("/api/drives/nope/pending-scans").status_code == 404


async def test_scan_for_the_next_disc(harness, settings):
    from ripshelf.models import ScannerScanRequest

    from .test_scans import URL, fake_scanner

    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        _, h.m.escl_transport = fake_scanner()
        h.m.escl_busy_wait_s = 0
        run = await h.m.scan_pending(DRIVE, ScannerScanRequest(kind="outside_full", dpi=100))
        assert run.state == "scanning" and h.m.pending_scans(DRIVE).scanner
        got = await until(lambda: (p := h.m.pending_scans(DRIVE)).scans and not p.scanner and p, 20, "the scan")
        assert got.scans[0].kind == "outside_full" and got.scans[0].source == "scanner" and got.scans[0].crop


async def test_a_scan_survives_a_save_from_an_older_copy(harness, settings):
    """The pipeline holds a copy of the job across a scan or a rip; saving it after a
    scan arrived must not lose the scan."""
    async with harness() as h:
        job = put(h, heron_job(state=JobState.review))
        stale = h.db.get_job(job.id)  # what the pipeline read earlier
        await h.m.add_scan(job.id, picture(), "back", "upload")
        stale.disc.name = "changed by the pipeline"
        h.m._save(stale)
        now = h.db.get_job(job.id)
        assert len(now.scans) == 1 and now.disc.name == "changed by the pipeline"


async def test_the_waiting_scans_list_is_saved_with_the_windows_retry(harness, settings, monkeypatch):
    from ripshelf import library

    real, replaced = library.replace, []
    monkeypatch.setattr(library, "replace", lambda a, b: (replaced.append(b.name), real(a, b)))
    async with harness() as h:
        await h.m.add_pending(DRIVE, picture(), "back", "camera")
    assert "index.json" in replaced
