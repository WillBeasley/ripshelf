"""Reading the text on scans of the box, and the listing made from it.

The parser runs on lines laid out as Tesseract gives them for real boxes (a flatbed
scan of a DVD sheet, a phone photo of a back cover), with made-up titles. The reader
runs Tesseract itself when it's installed (RIPSHELF_TESSERACT names it otherwise),
on the pretend scanner's sheet; without it those tests are skipped. The manager and
API tests use a stand-in reader."""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ripshelf.dev.fake import FakeDrives
from ripshelf.dev.fake_escl import BARCODE, cover_sheet, ean13, glass_scan
from ripshelf.main import create_app
from ripshelf.models import Listing, ListingItem, ScanText, TextLine
from ripshelf.scanner import images, listing, ocr

from .conftest import until
from .test_contribute import heron_job, put
from .test_scans import picture, sheet

# Where the app would find it: on the PATH, or on Windows in the installer's folder.
TESSERACT = os.environ.get("RIPSHELF_TESSERACT") or ocr.default_command("tesseract")
needs_tesseract = pytest.mark.skipif(not (shutil.which(TESSERACT) or os.path.isfile(TESSERACT)),
                                     reason="Tesseract isn't installed")


def lines(*rows: str | tuple[str, str], conf: float = 95, scan_id: str = "s1", barcodes=()) -> ScanText:
    """A reading of these lines, one under the other: "text", or ("region", "text")."""
    out = []
    for i, row in enumerate(rows):
        region, text = row if isinstance(row, tuple) else ("back", row)
        out.append(TextLine(text=text, conf=conf, region=region, box=[0.05, i * 0.02, 0.45, i * 0.02 + 0.015]))
    return ScanText(scan_id=scan_id, key="k", read_at=datetime.now(UTC), engine="test", lines=out,
                    barcodes=list(barcodes))


# The back of a British DVD's outer sheet, as Tesseract read a flatbed scan of one (the
# real box's titles and names swapped for made-up ones, its quirks kept: a missing ©,
# a logo read as letters, the spine's title run together).
BRASS_HERON_SHEET = lines(
    "1951, the river barges stopped at Heron Lock, the last of the old towpaths. In doing so,",
    "however, the canal company unwittingly gathered the finest crew in its history -",
    "brilliantly played here by Ada Quill, Tom Bramble and Ruth Sedge.",
    "THE MARLOW COMPANY, INC. presents",
    "> ADA QUILL + TOM BRAMBLE ~ RUTH SEDGE: is THE BRASS HERON'",
    "special features",
    "In-depth documentary on the making of",
    "The Brass Heron 'Return to The Brass Heron' distribution by",
    "US Theatrical Trailer UA",
    "1951 Lockside Pictures Inc. All Rights Reserved.",
    "Package Design © 2006 Lockside Pictures Inc. All Rights Reserved.",
    "INFORMATION",
    "LANGUAGES | English, French, German, Italian, Spanish",
    "Mono",
    "DIGITAL",
    "SUBTITLES:",
    "English, German (Hard of Hearing) - Movie only",
    "Dutch, English, French, German, Norwegian, Polish,",
    "Portuguese, Spanish RENTAL OR RESALE prohibited to use this DVD video",
    "ASPECT RATIO | WIDESCREEN 16:9 | COLOUR viewing in the home.",
    "FEATURE RUN TIME | 2Hrs 45mins approx",
    "[PAL] U.K.",
    ("spine", "THEBRASSHERON"),
    ("spine", "16179DVD"),
    ("front", "ADA QUILL TOM BRAMBLE"),
    barcodes=["5050070000115"],
)

# A phone photo of a children's DVD's back cover: the episode names in a column, with
# the noise a photo gives around them.
PIPPA_PHOTO = lines(
    "nd, Duck. Together, they embark",
    "e but exciting adventures,",
    "in a fun games arcade! | as",
    "Contains 10 wide-eyed adventures!",
    "Kite Tram", "Puddle Parade", "Hair Cut", "Arcade Dance Off", "Moon as Mars",
    "ff",
    "Goose Jump", "castle Scribbling.", "Button Hunt", "Cloud Jam", "Pillow Fill",
    "sarahandduck Co}",
    "Feature length 70 mins approx",
    "2 & 4 Colour PAL UK",
    "English for the Hard of Hearing",
)


def test_a_dvd_sheet():
    out = listing.build([BRASS_HERON_SHEET])
    extras = [(i.title, i.extra_type) for i in out.items]
    assert extras == [("Return to The Brass Heron", "Featurette"), ("US Theatrical Trailer", "Trailer")]
    d = out.details
    assert d.title == "The Brass Heron" and d.year == 1951
    assert d.cast == ["Ada Quill", "Tom Bramble", "Ruth Sedge"]
    assert d.feature_runtime_min == 165
    assert (d.barcode, d.barcode_source) == ("5050070000115", "barcode")
    assert d.catalogue_number == "16179DVD"
    assert (d.video_system, d.country, d.locale) == ("PAL", "UK", "en-GB")
    assert (d.region, d.region_guessed) == ("2", True)
    assert d.aspect_ratio == "Widescreen 16:9"
    assert [(a.language, a.format, a.channels) for a in d.audio] == [
        (lang, "Dolby Digital", "mono") for lang in ("English", "French", "German", "Italian", "Spanish")]
    subs = [(s.language, s.hard_of_hearing, s.note) for s in d.subtitles]
    assert subs[:2] == [("English", False, "movie only"), ("German", True, "movie only")]
    assert [s[0] for s in subs[2:]] == ["Dutch", "English", "French", "German", "Norwegian", "Polish",
                                        "Portuguese", "Spanish"]
    assert out.items[0].box and out.items[0].scan_id == "s1"


def test_a_phone_photo_of_a_tv_disc():
    out = listing.build([PIPPA_PHOTO])
    assert [i.title for i in out.items] == ["Kite Tram", "Puddle Parade", "Hair Cut", "Arcade Dance Off",
                                            "Moon as Mars", "Goose Jump", "Castle Scribbling", "Button Hunt",
                                            "Cloud Jam", "Pillow Fill"]
    assert {i.kind for i in out.items} == {"episode"}
    d = out.details
    assert d.episode_count == 10 and d.feature_runtime_min == 70
    assert (d.region, d.region_guessed, d.video_system, d.country) == ("2 & 4", False, "PAL", "UK")
    assert out.notes == []


def test_discs_numbered_episodes_and_runtimes():
    out = listing.build([lines(
        "Seagull Manor: The Complete Series",
        "13 episodes across 2 discs",
        "DISC ONE",
        "1. The Gull Who Stayed (24 mins)",
        "2. Low Tide - 23 min",
        "3. A Storm in a Teacup 25m",
        "Disc 2: Episodes 4-5",
        "4. The Lighthouse",
        "5. Captain Comet Returns",
        "Bonus material",
        "• Deleted and extended scenes",
        "• In conversation with the cast",
        "• Behind the scenes: building",
        "the manor",
        "• Sing-along with the gulls",
        "© 2019 Gullwing Productions",
    )])
    eps = [(i.number, i.title, i.runtime_min, i.disc) for i in out.items if i.kind == "episode"]
    assert eps == [(1, "The Gull Who Stayed", 24, 1), (2, "Low Tide", 23, 1), (3, "A Storm in a Teacup", 25, 1),
                   (4, "The Lighthouse", None, 2), (5, "Captain Comet Returns", None, 2)]
    extras = [(i.title, i.extra_type) for i in out.items if i.kind == "extra"]
    assert extras == [("Deleted and extended scenes", "DeletedScene"), ("In conversation with the cast", "Interview"),
                      ("Behind the scenes: building the manor", "Featurette"),
                      ("Sing-along with the gulls", "Music")]
    assert out.details.episode_count == 13 and out.details.year == 2019
    assert out.notes == ["The box says 13 episodes, and 5 were read"]


def test_no_heading_and_no_column_is_no_listing():
    out = listing.build([lines("A quiet orchard. A loud summer. When the last tree in the valley blossoms,",
                               "everyone has a theory.", "© 1998 Moonlit Pictures")])
    assert out.items == [] and out.details.year == 1998


def test_details_a_blu_ray_prints():
    d = listing.build([lines(
        "4K ULTRA HD + BLU-RAY", "Dolby Vision HDR10+ 2160p",
        "AUDIO: English Dolby Atmos, English DTS-HD MA 5.1, French 5.1 Dolby Digital, English Commentary 2.0",
        "SUBTITLES: English SDH, Spanish", "Region B 2.39:1 Running time: approx. 128 minutes")]).details
    assert d.video == ["4K UHD", "HDR10+", "Dolby Vision"]
    assert [(a.language, a.format, a.channels, a.note) for a in d.audio] == [
        ("English", "Dolby Atmos", None, None), ("English", "DTS-HD MA", "5.1", None),
        ("French", "Dolby Digital", "5.1", None), ("English", None, "2.0", "commentary")]
    assert [(s.language, s.hard_of_hearing) for s in d.subtitles] == [("English", True), ("Spanish", False)]
    assert (d.region, d.region_guessed, d.aspect_ratio, d.feature_runtime_min) == ("B", False, "2.39:1", 128)


@pytest.mark.parametrize("text, minutes", [
    ("2hrs 45mins approx", 165), ("165 mins", 165), ("2h 45m", 165), ("Approx. 22 mins", 22),
    ("70 mins approx", 70), ("Running time 1 hour 52 minutes", 112), ("thr 52mins", 112), ("Feature running time; Thr approx", 60),
    ("no time here", None),
])
def test_runtimes(text, minutes):
    assert listing.runtime_min(text) == minutes


@pytest.mark.parametrize("printed, found", [
    ("5 050070 000115 >", "5050070000115"),  # the digits under the bars
    ("5 050070 000116", None),  # a misread digit fails the check
    ("0 12345 67890 5", "012345678905"),  # UPC-A
])
def test_barcode_from_its_digits(printed, found):
    d = listing.build([lines("special features", printed)]).details
    assert d.barcode == found and d.barcode_source == ("text" if found else None)


def test_a_decoded_barcode_must_check_out():
    d = listing.build([lines("x", barcodes=["5050070000116", "5050070000115"])]).details
    assert d.barcode == "5050070000115"


@pytest.mark.parametrize("name, cased", [("STEVE", "Steve"), ("McQUEEN", "McQueen"), ("O'BRIEN", "O'Brien"),
                                         ("JEAN-LUC", "Jean-Luc")])
def test_names(name, cased):
    assert listing.name_case(name) == cased


# --- the reader --------------------------------------------------------------------------

TSV = "\n".join(["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
                 "5\t1\t1\t1\t1\t1\t10\t10\t40\t20\t96\tBrolly",
                 "5\t1\t1\t1\t1\t2\t55\t10\t30\t20\t20\tBus",
                 "5\t1\t1\t1\t2\t1\t10\t40\t20\t20\t30\tUw",
                 "5\t1\t2\t1\t1\t1\t10\t80\t60\t20\t91\tSpecial",
                 "5\t1\t2\t1\t1\t2\t75\t80\t70\t20\t92\tfeatures",
                 "4\t1\t2\t1\t1\t0\t10\t80\t135\t20\t-1\t"])


def test_tsv_lines_and_noise():
    got = ocr.parse_tsv(TSV)
    assert [ln.text for ln in got] == ["Brolly Bus", "Uw", "Special features"]
    tidied = [t.text if (t := ocr.tidy(ln)) else None for ln in got]
    assert tidied == ["Brolly", None, "Special features"]  # a doubtful last word, a lone mark
    assert got[2].box == (10, 80, 145, 100)


def test_merging_readings():
    best = [TextLine(text="Brolly", conf=90, box=[0.1, 0.1, 0.2, 0.12]),
            TextLine(text="Hair Cut", conf=95, box=[0.1, 0.2, 0.25, 0.22])]
    others = [TextLine(text="Brolly Bus", conf=92, box=[0.1, 0.1, 0.3, 0.12]),
              TextLine(text="Rainbow Race", conf=90, box=[0.1, 0.15, 0.3, 0.17]),
              TextLine(text="Cut", conf=95, box=[0.18, 0.19, 0.25, 0.23])]
    assert [ln.text for ln in ocr.merge(best, others)] == ["Brolly Bus", "Rainbow Race", "Hair Cut"]


@pytest.mark.parametrize("turned", [90, 180, 270])
def test_boxes_turned_back(turned):
    img = Image.new("L", (300, 200), 255)
    img.paste(0, (20, 30, 60, 50))
    turned_img = img.rotate(-turned, expand=True)
    box = turned_img.point(lambda v: 255 - v).getbbox()
    assert ocr._unturn(box, turned_img.size, turned) == (20, 30, 60, 50)


def test_parts_of_a_sheet():
    names = [p.name for p in ocr.parts("outside_full", (3000, 2000), None)]
    assert names == ["back", "spine", "front"]
    assert [p.name for p in ocr.parts("inside_full", (3000, 2000), None)] == ["left", "right"]
    assert [p.name for p in ocr.parts("outside_full", (2000, 3000), None)] == ["page"]  # not unfolded


def test_the_pretend_barcode_decodes():
    if not ocr.barcodes_available():
        pytest.skip("zbar isn't installed")
    img = Image.new("RGB", (600, 300), "white")
    img.paste(ean13(BARCODE, 3, 120), (60, 80))
    assert ocr.decode_barcodes(img) == [BARCODE]


@needs_tesseract
async def test_reading_the_pretend_scanners_sheet():
    """The glass holds the sheet on its side; the reader turns it and finds everything."""
    data = glass_scan(300)
    img = images.open_image(data)
    reading = await ocr.read_picture(ocr.Tesseract(TESSERACT), images.render(data, images.trim_box(img), 0),
                                     "outside_full")
    assert reading.turned in (90, 270)
    out = listing.build([ScanText(scan_id="s", key="k", read_at=datetime.now(UTC), engine="t",
                                  lines=reading.lines, more=reading.more, barcodes=reading.barcodes)])
    assert [(i.title, i.extra_type, i.runtime_min) for i in out.items] == [
        ("Growing the Orchard", "Featurette", 22), ("Deleted scenes", "DeletedScene", None),
        ("Theatrical trailer", "Trailer", None)]
    d = out.details
    assert (d.title, d.year, d.catalogue_number, d.feature_runtime_min) == ("Moonlit Orchard", 1998, "40112DVD", 112)
    assert (d.region, d.video_system, d.aspect_ratio) == ("2", "PAL", "Widescreen 16:9")
    if ocr.barcodes_available():
        assert d.barcode == BARCODE and d.barcode_source == "barcode"
    back = next(ln for ln in reading.lines if ln.text.startswith("Special"))
    assert back.region == "back" and back.box[2] < 0.5


@needs_tesseract
async def test_tesseract_version():
    assert (await ocr.Tesseract(TESSERACT).version()).startswith("tesseract")


async def test_no_tesseract():
    with pytest.raises(ocr.OcrUnavailable, match="isn't installed"):
        await ocr.Tesseract("no-such-tesseract").version()


def test_sheet_is_drawn_with_its_spine_text():
    assert cover_sheet(100).size == (1075, 724)


# --- the manager and the API --------------------------------------------------------------


class FakeReader:
    """Stands in for Tesseract: every picture reads as ``rows``."""

    def __init__(self, rows: list[str], turn: int = 0):
        self.rows = rows
        self.turn = turn
        self.reads = 0

    async def version(self):
        return "fake 1.0"

    async def orientation(self, img):
        return self.turn, 10.0 if self.turn else 0.0

    async def read(self, img, psm=3):
        """The rows down the picture, wherever the reader looks (at any size)."""
        self.reads += 1
        w, h = img.size
        return [ocr.Line([ocr.Word(word, 95, (round(w * (0.05 + 0.1 * j)), round(h * (0.05 + 0.08 * i)),
                                              round(w * (0.14 + 0.1 * j)), round(h * (0.1 + 0.08 * i))))
                          for j, word in enumerate(row.split())]) for i, row in enumerate(self.rows)]


ROWS = ["Special features", "Growing the Orchard (22 mins)", "Theatrical trailer", "© 1998 Moonlit Pictures"]


async def wait_listing(h, job_id, state="done"):
    return await until(lambda: (j := h.db.get_job(job_id)) and j.listing and j.listing.state == state and j,
                       10, f"listing {state}")


async def test_scans_are_read_and_listed(harness, settings):
    async with harness() as h:
        h.m.text_reader = FakeReader(ROWS, turn=90)
        job = put(h, heron_job())
        sc = await h.m.add_scan(job.id, picture(), "back", "upload")
        j = await wait_listing(h, job.id)
        found = j.listing.found
        assert [i.title for i in found.items] == ["Growing the Orchard", "Theatrical trailer"]
        assert found.details.year == 1998 and j.listing.engine == "fake 1.0"
        assert j.scans[0].rotate == 90  # turned the way the text reads
        text = h.m.scan_text(job.id, sc.id)
        assert text.turned == 90 and text.lines[0].text == "Special features"
        reads = h.m.text_reader.reads
        h.m.read_scans(job.id)  # nothing changed, so nothing is read
        await asyncio.sleep(0.2)
        assert h.m.text_reader.reads == reads


async def test_edits_survive_reading_again(harness, settings):
    async with harness() as h:
        h.m.text_reader = FakeReader(ROWS)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "upload")
        await wait_listing(h, job.id)
        mine = Listing(items=[ListingItem(kind="extra", title="Growing the Orchard", extra_type="Featurette")])
        view = h.m.save_listing(job.id, mine)
        assert view.current == mine and not view.newer_reading
        h.m.text_reader.rows = [*ROWS[:3], "Deleted scenes"]
        h.m.read_scans(job.id, again=True)
        j = await until(lambda: (j := h.db.get_job(job.id)) and j.listing.state == "done"
                        and len(j.listing.found.items) == 3 and j, 10, "the new reading")
        assert j.listing.edited == mine
        view = h.m.listing_view(job.id)
        assert view.current == mine and view.newer_reading
        view = h.m.drop_listing_edits(job.id)
        assert view.current.items[-1].title == "Deleted scenes" and not view.newer_reading


async def test_removing_a_scan_relists(harness, settings):
    async with harness() as h:
        h.m.text_reader = FakeReader(ROWS)
        job = put(h, heron_job())
        sc = await h.m.add_scan(job.id, picture(), "back", "upload")
        await wait_listing(h, job.id)
        await h.m.delete_scan(job.id, sc.id)
        j = h.db.get_job(job.id)
        assert j.listing.found is None and j.listing.state == "idle"


async def test_without_tesseract(harness, settings):
    settings.tesseract = "no-such-tesseract"
    async with harness() as h:
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "upload")
        j = await wait_listing(h, job.id, "unavailable")
        assert "isn't installed" in j.listing.message
        assert not h.m.listing_view(job.id).available


async def test_reading_switched_off(harness, settings):
    settings.scan_read_text = False
    async with harness() as h:
        h.m.text_reader = FakeReader(ROWS)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "upload")
        await asyncio.sleep(0.2)
        assert h.db.get_job(job.id).listing is None and h.m.text_reader.reads == 0


async def test_a_disc_joining_a_release_takes_the_listing(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        h.m.text_reader = FakeReader(ROWS)
        a = put(h, heron_job())
        h.m.contribution_view(a.id)
        await h.m.add_scan(a.id, sheet(), "back", "upload")
        await wait_listing(h, a.id)
        b = put(h, heron_job())
        h.m.contribution_view(b.id)
        jb = h.db.get_job(b.id)
        assert jb.listing.found == h.db.get_job(a.id).listing.found
        assert h.m.scan_text(b.id, jb.scans[0].id) is not None


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        c.app.state.manager.text_reader = FakeReader(ROWS)
        yield c


def test_api(client):
    m = client.app.state.manager
    rec = m.db.create_job("fake-pioneer", "/dev/sr0")
    m.db.save_job(heron_job(rec.id))
    url = f"/api/jobs/{rec.id}"
    sc = client.post(f"{url}/scans", data={"kind": "back"}, files={"file": ("p.jpg", picture(), "image/jpeg")}).json()
    for _ in range(100):
        view = client.get(f"{url}/listing").json()
        if view["listing"]["state"] == "done":
            break
        import time
        time.sleep(0.05)
    assert [i["title"] for i in view["current"]["items"]] == ["Growing the Orchard", "Theatrical trailer"]
    assert view["available"] and not view["newer_reading"]
    text = client.get(f"{url}/scans/{sc['id']}/text").json()
    assert text["lines"][0]["text"] == "Special features"
    edited = {"items": [{"kind": "extra", "title": "Growing", "extra_type": "Featurette"}]}
    view = client.put(f"{url}/listing", json=edited).json()
    assert view["current"]["items"][0]["title"] == "Growing" and view["listing"]["found"]["items"]
    assert client.put(f"{url}/listing", json={"items": [{"kind": "nope", "title": "x"}]}).status_code == 422
    view = client.delete(f"{url}/listing/edits").json()
    assert view["current"]["items"][0]["title"] == "Growing the Orchard"
    assert client.post(f"{url}/listing/read").status_code == 200
    assert client.get("/api/jobs/9999/listing").status_code == 404
    assert client.get(f"{url}/scans/nope/text").status_code == 404


def test_api_reading_off(client):
    client.put("/api/settings", json={"scan_read_text": False, "ocr_languages": "eng+fra"})
    s = client.get("/api/settings").json()
    assert s["scan_read_text"] is False and s["ocr_languages"] == "eng+fra"
    assert client.put("/api/settings", json={"ocr_languages": "eng; rm -rf"}).status_code == 422
    m = client.app.state.manager
    rec = m.db.create_job("fake-pioneer", "/dev/sr0")
    m.db.save_job(heron_job(rec.id))
    assert client.post(f"/api/jobs/{rec.id}/listing/read").status_code == 409


def test_api_ocr_check(client):
    client.app.state.settings.tesseract = "no-such-tesseract"
    r = client.post("/api/settings/test", json={"check": "ocr", "values": {}}).json()
    assert not r["ok"] and "isn't installed" in r["message"]


class BrokenReader(FakeReader):
    async def read(self, img, psm=3):
        raise RuntimeError("bad picture")


async def test_a_reading_that_crashes_is_shown_failed_not_left_reading(harness, settings):
    async with harness() as h:
        h.m.text_reader = BrokenReader(ROWS)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "upload")
        j = await wait_listing(h, job.id, "failed")
        assert j.listing.message == "Reading failed: bad picture" and j.listing.progress is None
        # Fixed, and asked again: read as usual.
        h.m.text_reader = FakeReader(ROWS)
        h.m.read_scans(job.id, again=True)
        await wait_listing(h, job.id)


async def test_scans_added_while_reading_are_read_in_the_same_go(harness, settings):
    class Slow(FakeReader):
        async def read(self, img, psm=3):
            await asyncio.sleep(0.05)
            return await super().read(img, psm)

    async with harness() as h:
        h.m.text_reader = Slow(ROWS)
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(), "back", "upload")
        await h.m.add_scan(job.id, picture(colour=(9, 9, 9)), "insert", "upload")  # while the first is read
        j = await wait_listing(h, job.id)
        assert all(h.m.scan_text(job.id, sc.id) for sc in j.scans), "both read"
        await h.m.cancel_reading()  # at shutdown: nothing left running
