"""Scans of the box: the eSCL scanner client (against a fake scanner, never a real
one), the pictures kept with a job, the covers cut from them, and the copies filed in
the library."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from ripshelf import scanjobs
from ripshelf.dev.fake import FakeDrives
from ripshelf.dev.fake_escl import FakeScanner, glass_scan
from ripshelf.main import create_app
from ripshelf.models import JobState, ScannerScanRequest, ScanSplit, ScanUpdate
from ripshelf.scanner import escl, images
from ripshelf.scanner.escl import Escl, ScannerError

from .conftest import read_picture, until
from .test_contribute import BASE, FIXTURES, heron_job, pippa_job, put

URL = "http://scanner.test/eSCL"


def picture(w: int = 300, h: int = 200, colour=(200, 40, 40), fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(out, fmt)
    return out.getvalue()


def sheet(w: int = 600, h: int = 300) -> bytes:
    """A whole outer sheet: a blue back on the left, a yellow spine, a red front on the right."""
    img = Image.new("RGB", (w, h), (0, 0, 200))
    img.paste((240, 220, 0), (w // 2 - 15, 0, w // 2 + 15, h))
    img.paste((200, 0, 0), (w // 2 + 15, 0, w, h))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def fake_scanner(**kw) -> tuple[FakeScanner, httpx.ASGITransport]:
    fake = FakeScanner(**kw)
    app = FastAPI()
    app.include_router(fake.router())
    return fake, httpx.ASGITransport(app=app)


# --- the eSCL client --------------------------------------------------------------------


@pytest.mark.parametrize("typed, base", [
    ("192.0.2.10", "http://192.0.2.10/eSCL"),
    ("http://192.0.2.10/", "http://192.0.2.10/eSCL"),
    ("http://192.0.2.10/eSCL/", "http://192.0.2.10/eSCL"),
    ("https://printer.lan:8443/escl", "https://printer.lan:8443/escl"),
])
def test_base_url(typed, base):
    assert escl.base_url(typed) == base


@pytest.mark.parametrize("bad", ["", "ftp://printer/", "http:///eSCL"])
def test_base_url_refuses(bad):
    with pytest.raises(ValueError):
        escl.base_url(bad)


def test_brother_capabilities():
    caps = escl.parse_capabilities((FIXTURES / "escl-capabilities-mfc-l8690cdw.xml").read_bytes())
    assert caps.model == "Brother MFC-L8690CDW series" and caps.version == "2.62"
    assert caps.sources == ["platen", "feeder"] and caps.duplex
    assert (caps.platen.max_width, caps.platen.max_height) == (2550, 3507)
    assert caps.feeder.max_height == 4200
    assert caps.platen.resolutions == [100, 200, 300, 600]
    assert "RGB24" in caps.platen.color_modes and "image/jpeg" in caps.platen.formats


def test_brother_status():
    st = escl.parse_status((FIXTURES / "escl-status-mfc-l8690cdw.xml").read_bytes())
    assert st.state == "Idle" and st.adf_state == "ScannerAdfEmpty"
    assert len(st.jobs) == 5 and set(st.jobs.values()) == {"Completed"}


def test_unreadable_xml():
    with pytest.raises(ScannerError, match="readable XML"):
        escl.parse_capabilities(b"<html>login</html")


def test_scan_settings():
    caps = escl.parse_capabilities((FIXTURES / "escl-capabilities-mfc-l8690cdw.xml").read_bytes())
    glass = escl.scan_settings(caps, "platen", 300)
    assert "<pwg:InputSource>Platen</pwg:InputSource>" in glass and "Duplex" not in glass
    assert "<pwg:Width>2550</pwg:Width>" in glass and "<pwg:Height>3507</pwg:Height>" in glass
    assert "<scan:ColorMode>RGB24</scan:ColorMode>" in glass and "image/jpeg" in glass
    feeder = escl.scan_settings(caps, "feeder", 250, duplex=True)
    assert "<pwg:InputSource>Feeder</pwg:InputSource>" in feeder and "<scan:Duplex>true</scan:Duplex>" in feeder
    assert "<pwg:Height>3508</pwg:Height>" in feeder  # A4, not the feeder's legal-size maximum
    assert "<scan:XResolution>300</scan:XResolution>" in feeder  # the nearest it offers
    caps.feeder = None
    with pytest.raises(ScannerError, match="no feeder"):
        escl.scan_settings(caps, "feeder", 300)


async def test_scan_the_glass():
    fake, transport = fake_scanner()
    client = Escl(URL, transport=transport, busy_wait_s=0)
    caps = await client.capabilities()
    seen = []

    async def on_page(n, data):
        seen.append(n)

    pages = await client.scan(caps, "platen", 100, on_page=on_page)
    assert len(pages) == 1 and seen == [1] and pages[0].startswith(b"\xff\xd8")
    assert Image.open(io.BytesIO(pages[0])).size == (850, 1169)
    assert "<scan:XResolution>100<" in fake.settings[0]


async def test_scan_the_feeder_both_sides_after_waiting():
    fake, transport = fake_scanner(feeder_pages=2, busy_posts=2, busy_pages=3)
    client = Escl(URL, transport=transport, busy_wait_s=0)
    pages = await client.scan(await client.capabilities(), "feeder", 100, duplex=True)
    assert len(pages) == 4 and len(fake.settings) == 3


async def test_an_empty_feeder_is_refused():
    _, transport = fake_scanner(feeder_pages=0)
    client = Escl(URL, transport=transport, busy_wait_s=0)
    with pytest.raises(ScannerError, match="feeder is empty") as e:
        await client.scan(await client.capabilities(), "feeder", 300)
    assert e.value.code == "empty"


async def test_a_busy_scanner():
    _, transport = fake_scanner(busy_posts=99)
    client = Escl(URL, transport=transport, busy_wait_s=0)
    with pytest.raises(ScannerError, match="busy") as e:
        await client.scan(await client.capabilities(), "platen", 300)
    assert e.value.code == "busy"


async def test_an_unreachable_scanner():
    def down(request):
        raise httpx.ConnectError("no route")

    client = Escl(URL, transport=httpx.MockTransport(down))
    with pytest.raises(ScannerError, match="didn't answer") as e:
        await client.capabilities()
    assert e.value.code == "offline"


async def test_a_stopped_scan_is_cancelled():
    fake, transport = fake_scanner(page_delay_s=5)
    client = Escl(URL, transport=transport, busy_wait_s=0)
    caps = await client.capabilities()
    task = asyncio.create_task(client.scan(caps, "platen", 100))
    await until(lambda: fake.jobs, what="the scan to start")
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.cancelled == list(fake.jobs)


# --- pictures -----------------------------------------------------------------------------


def test_trim_finds_the_sheet_on_the_glass():
    img = images.open_image(glass_scan(100))
    box = images.trim_box(img)
    assert box is not None
    left, top, right, bottom = box
    # The sheet is 184 × 273 mm, turned, 0.1 inch from the corner of the glass.
    assert left <= 10 and top <= 10
    # The box takes in that 0.1 inch and a margin of 1% of the scan.
    assert abs((right - left) - 184 / 25.4 * 100) < 40 and abs((bottom - top) - 273 / 25.4 * 100) < 40


def test_trim_leaves_a_full_picture_alone():
    assert images.trim_box(images.open_image(picture())) is None


def test_render_turns_after_trimming():
    img = images.render(picture(300, 200), [0, 0, 100, 50], 90)
    assert img.size == (50, 100)


def flatbed(w: int = 1200, h: int = 1720, *, edge_lines: bool = True, askew: float = 0.0,
            pale_foot: bool = False, specks: bool = False) -> tuple[bytes, tuple[int, int]]:
    """A made-up sleeve in the top-left corner of an off-white glass, the way a flatbed
    scans it: the sleeve fills 90% of the width and 93% of the height, the empty glass
    is to its right and below it, saved as a JPEG. With ``edge_lines`` the glass's far
    edges have the thin grey shadow a scanner's lid often leaves. Returns the scan and
    the sleeve's size."""
    glass = Image.new("RGB", (w, h), (247, 246, 243))
    cw, ch = round(w * 0.9), round(h * 0.93)
    sleeve = Image.new("RGB", (cw, ch), (40, 70, 140))
    d = ImageDraw.Draw(sleeve)
    d.rectangle((cw * 0.1, ch * 0.5, cw * 0.9, ch * 0.8), fill=(220, 200, 60))
    if pale_foot:  # a white foot to the sleeve with only small grey print on it
        d.rectangle((0, ch * 0.85, cw, ch), fill=(246, 245, 242))
        for i in range(3):
            d.text((cw * 0.1, ch * (0.9 + 0.025 * i)), "Lanternfish Pictures presents a made-up film",
                   fill=(90, 90, 90), font=ImageFont.load_default(ch // 70))
    if askew:
        sleeve = sleeve.convert("RGBA").rotate(askew, expand=True, resample=Image.Resampling.BICUBIC)
        glass.paste(sleeve, (0, 0), sleeve)
    else:
        glass.paste(sleeve, (0, 0))
    d = ImageDraw.Draw(glass)
    if edge_lines:
        d.line([(w - 3, 0), (w - 3, h)], fill=(150, 150, 150), width=2)
        d.line([(0, h - 3), (w, h - 3)], fill=(160, 160, 160), width=2)
    if specks:
        for x, y in ((0.95, 0.3), (0.5, 0.97), (0.98, 0.6)):
            d.ellipse((w * x, h * y, w * x + 4, h * y + 4), fill=(60, 60, 60))
    out = io.BytesIO()
    glass.save(out, "JPEG", quality=80)
    return out.getvalue(), (cw, ch)


@pytest.mark.parametrize("kw", [{}, {"edge_lines": False}, {"specks": True}])
def test_trim_cuts_each_side_to_the_sleeve(kw):
    data, (cw, ch) = flatbed(**kw)
    left, top, right, bottom = images.trim_box(images.open_image(data))
    # The sleeve is flush with the top and left; right and bottom are cut to within a
    # small margin of it, past the shadow along the glass's edges and any dust.
    assert (left, top) == (0, 0)
    assert cw <= right <= cw + 20 and ch <= bottom <= ch + 20


def test_trim_keeps_a_pale_sleeves_small_print():
    data, (cw, ch) = flatbed(pale_foot=True)
    _, _, right, bottom = images.trim_box(images.open_image(data))
    assert cw <= right <= cw + 20
    assert bottom >= ch * (0.9 + 0.05) + ch // 70  # below the last line of print


def test_trim_keeps_the_corners_of_a_sleeve_laid_askew():
    data, (cw, ch) = flatbed(askew=1.0)
    _, _, right, bottom = images.trim_box(images.open_image(data))
    # Turned by a degree, its corners reach out past its sides by sin(1°) of them.
    assert right >= cw + 0.017 * ch and bottom >= ch + 0.017 * cw - 2
    assert right <= cw + 0.0175 * ch + 20 and bottom <= ch + 0.0175 * cw + 20


@pytest.mark.parametrize("rotate, shown, original", [
    (0, [0.1, 0.2, 0.6, 0.9], [30, 40, 180, 180]),
    (90, [0.1, 0.2, 0.6, 0.9], [60, 80, 270, 180]),  # the top of the turned picture is its left
    (180, [0.1, 0.2, 0.6, 0.9], [120, 20, 270, 160]),
    (270, [0.1, 0.2, 0.6, 0.9], [30, 20, 240, 120]),
])
def test_a_crop_drawn_on_the_turned_scan_is_kept_in_the_originals_pixels(rotate, shown, original):
    assert images.crop_from_shown(shown, 300, 200, rotate) == original
    # Cropping then turning gives the part that was drawn on.
    img = Image.new("RGB", (300, 200), (255, 255, 255))
    img.paste((255, 0, 0), tuple(original))
    turned = img.rotate(-rotate, expand=True)
    w, h = turned.size
    box = (round(shown[0] * w), round(shown[1] * h), round(shown[2] * w) - 1, round(shown[3] * h) - 1)
    assert turned.getpixel(box[:2]) == turned.getpixel(box[2:]) == (255, 0, 0)


def test_halves_of_a_sheet():
    img = images.render(sheet(), None, 0)
    front, back = images.halves(img, ScanSplit())
    assert front.getpixel((front.width // 2, 10)) == (200, 0, 0)
    assert back.getpixel((back.width // 2, 10)) == (0, 0, 200)
    assert front.width == back.width == 285  # the spine (5%) left out
    front, back = images.halves(img, ScanSplit(swap=True))
    assert front.getpixel((10, 10)) == (0, 0, 200)


# --- the manager --------------------------------------------------------------------------


async def test_uploads_are_kept_numbered_and_checked(harness, settings):
    async with harness() as h:
        job = put(h, heron_job())
        a = await h.m.add_scan(job.id, picture(), "insert", "upload")
        b = await h.m.add_scan(job.id, picture(fmt="PNG"), "insert", "camera")
        assert (a.page, b.page) == (1, 2) and b.content_type == "image/png"
        assert (a.width, a.height) == (300, 200) and a.crop is None  # uploads aren't trimmed
        path, ctype = h.m.scan_file(job.id, a.id)
        assert path.read_bytes() == picture() and ctype == "image/jpeg"
        thumb = await h.m.scan_thumb(job.id, a.id)
        assert Image.open(thumb).size == (300, 200)
        with pytest.raises(ValueError, match="JPEG, PNG or WebP"):
            await h.m.add_scan(job.id, b"<svg/>", "front", "upload")
        with pytest.raises(ValueError, match="20 MB"):
            await h.m.add_scan(job.id, b"\xff\xd8\xff" + b"\0" * scanjobs.MAX_SCAN_BYTES, "front", "upload")
        sc = await h.m.update_scan(job.id, a.id, ScanUpdate(kind="inside_full", rotate=270))
        assert sc.page is None and sc.rotate == 270
        assert not thumb.exists()  # made again, turned
        assert Image.open(await h.m.scan_thumb(job.id, a.id)).size == (200, 300)
        await h.m.delete_scan(job.id, a.id)
        assert [s.id for s in h.db.get_job(job.id).scans] == [b.id] and not path.exists()
        with pytest.raises(KeyError):
            await h.m.delete_scan(job.id, a.id)


async def test_a_scan_is_cropped_by_hand_and_trimmed_again(harness, settings):
    async with harness() as h:
        job = put(h, heron_job())
        data, (cw, ch) = flatbed(600, 860)
        sc = await h.m.add_scan(job.id, data, "front", "upload")
        auto = sc.crop
        assert auto and not sc.crop_by_hand
        await h.m.update_scan(job.id, sc.id, ScanUpdate(rotate=90))
        # Drawn on the scan as shown, turned: its left half, all the way down.
        sc = await h.m.update_scan(job.id, sc.id, ScanUpdate(crop=[0, 0, 0.5, 1]))
        assert sc.crop == [0, 430, 600, 860] and sc.crop_by_hand  # the original's bottom half
        thumb = read_picture(await h.m.scan_thumb(job.id, sc.id, large=True))
        assert thumb.size == (430, 600)
        whole = read_picture(await h.m.scan_thumb(job.id, sc.id, whole=True))
        assert whole.size == (860, 600)  # turned, not cropped
        sc = await h.m.update_scan(job.id, sc.id, ScanUpdate(rotate=0))
        assert sc.crop == [0, 430, 600, 860]  # turning keeps the crop
        assert not (h.m._scans_dir(h.db.get_job(job.id)) / f"{sc.id}.whole.jpg").exists()
        sc = await h.m.update_scan(job.id, sc.id, ScanUpdate(trim=True))
        assert sc.crop == auto and not sc.crop_by_hand
        sc = await h.m.update_scan(job.id, sc.id, ScanUpdate(trim=False))
        assert sc.crop is None


@pytest.mark.parametrize("crop", [[0.5, 0, 0.4, 1], [0, 0, 1.2, 1], [0, 0, 0.02, 1], [0, 0, 1]])
def test_a_crop_by_hand_must_leave_some_of_the_scan(crop):
    with pytest.raises(ValueError):
        ScanUpdate(crop=crop)


async def test_front_and_back_scans_become_the_covers(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        job = put(h, heron_job())
        await h.m.add_scan(job.id, picture(colour=(0, 200, 0)), "front", "camera")
        c = h.db.get_job(job.id).contribution
        assert c.covers == ["front"]
        cover = read_picture(h.m.cover_file(job.id, "front"))
        assert cover.getpixel((5, 5))[1] > 180


async def test_scans_are_kept_when_the_disc_cant_be_sent(harness, settings):
    async with harness() as h:  # contributing is off
        job = put(h, heron_job())
        sc = await h.m.add_scan(job.id, picture(), "front", "upload")
        assert h.db.get_job(job.id).scans[0].id == sc.id
        with pytest.raises(ValueError):
            await h.m.scan_covers(job.id, sc.id)


async def test_a_whole_sheet_is_cut_into_the_covers(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        job = put(h, heron_job())
        sc = await h.m.add_scan(job.id, sheet(), "outside_full", "upload")
        assert not h.db.get_job(job.id).contribution or not h.db.get_job(job.id).contribution.covers
        view = await h.m.scan_covers(job.id, sc.id)
        assert view.contribution.covers == ["back", "front"]
        assert Image.open(h.m.cover_file(job.id, "front")).getpixel((100, 10))[0] > 180
        assert Image.open(h.m.cover_file(job.id, "back")).getpixel((100, 10))[2] > 180
        await h.m.scan_covers(job.id, sc.id, ScanSplit(swap=True, position=0.4))
        assert Image.open(h.m.cover_file(job.id, "front")).getpixel((100, 10))[2] > 180
        assert h.db.get_job(job.id).scans[0].split == ScanSplit(swap=True, position=0.4)
        inside = await h.m.add_scan(job.id, sheet(), "inside_full", "upload")
        with pytest.raises(ValueError, match="only the front"):
            await h.m.scan_covers(job.id, inside.id)


async def test_scan_from_the_scanner(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        fake, h.m.escl_transport = fake_scanner(feeder_pages=2)
        h.m.escl_busy_wait_s = 0
        job = put(h, heron_job())
        info = await h.m.scanner_info()
        assert info.ok and info.model.startswith("Brother") and info.sources == ["platen", "feeder"]
        run = await h.m.scanner_scan(job.id, ScannerScanRequest(kind="outside_full", dpi=100))
        assert run.state == "scanning" and run.source == "platen"
        with pytest.raises(ValueError, match="scanning for job"):
            await h.m.scanner_scan(job.id, ScannerScanRequest(kind="front"))
        done = await until(lambda: (j := h.db.get_job(job.id)) and not j.scanner and j.scans and j, 20,
                           "the scan")
        sc = done.scans[0]
        assert (sc.kind, sc.source) == ("outside_full", "scanner") and sc.crop  # the empty glass trimmed off
        await h.m.scanner_scan(job.id, ScannerScanRequest(kind="insert", source="feeder", duplex=True, dpi=100))
        done = await until(lambda: (j := h.db.get_job(job.id)) and not j.scanner and len(j.scans) == 5 and j,
                           20, "the feeder")
        assert [s.page for s in done.scans if s.kind == "insert"] == [1, 2, 3, 4]


async def test_a_failed_scan_says_why(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        _, h.m.escl_transport = fake_scanner(feeder_pages=0)
        job = put(h, heron_job())
        await h.m.scanner_scan(job.id, ScannerScanRequest(kind="insert", source="feeder"))
        j = await until(lambda: (j := h.db.get_job(job.id)) and j.scanner.state == "failed" and j, 10, "failure")
        assert "feeder is empty" in j.scanner.message
        await h.m.cancel_scanner(job.id)
        assert h.db.get_job(job.id).scanner is None


async def test_a_scan_can_be_stopped_part_way(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        fake, h.m.escl_transport = fake_scanner(feeder_pages=3, page_delay_s=2)
        h.m.escl_busy_wait_s = 0
        job = put(h, heron_job())
        await h.m.scanner_scan(job.id, ScannerScanRequest(kind="insert", source="feeder", dpi=100))
        await until(lambda: fake.jobs, 10, "the scanner's job")
        await h.m.cancel_scanner(job.id)
        assert h.db.get_job(job.id).scanner is None and not (await h.m.scanner_info()).busy_job
        await until(lambda: fake.cancelled, 5, "the scanner's job cancelled")


async def test_the_scanner_being_off_is_shown_not_raised(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, "http://192.0.2.1"

    def refused(request):
        raise httpx.ConnectError("refused")

    async with harness() as h:
        h.m.escl_transport = httpx.MockTransport(refused)
        info = await h.m.scanner_info()
        assert info.enabled and not info.ok and "didn't answer" in info.message


async def test_a_scan_that_crashes_is_shown_failed_not_left_scanning(harness, settings, monkeypatch):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        _, h.m.escl_transport = fake_scanner()

        async def boom(*a, **kw):
            raise RuntimeError("bad page")

        monkeypatch.setattr(escl.Escl, "scan", boom)
        job = put(h, heron_job())
        await h.m.scanner_scan(job.id, ScannerScanRequest(kind="front"))
        j = await until(lambda: (j := h.db.get_job(job.id)) and j.scanner and j.scanner.state == "failed" and j,
                        10, "failure")
        assert j.scanner.message == "The scan failed: bad page"


async def test_a_job_keeps_a_limited_number_of_pictures(harness, monkeypatch):
    from ripshelf import scanjobs

    monkeypatch.setattr(scanjobs, "MAX_SCANS", 2)
    async with harness() as h:
        job = put(h, heron_job())
        for colour in ((1, 1, 1), (2, 2, 2)):
            await h.m.add_scan(job.id, picture(colour=colour), "insert", "upload")
        with pytest.raises(ValueError, match="at most 2 pictures"):
            await h.m.add_scan(job.id, picture(colour=(3, 3, 3)), "insert", "upload")
        with pytest.raises(KeyError):
            await h.m.scan_covers(job.id, "no-such-scan")


async def test_scanning_needs_the_scanner_set_up(harness):
    async with harness() as h:
        job = put(h, heron_job())
        with pytest.raises(ValueError, match="Settings"):
            await h.m.scanner_scan(job.id, ScannerScanRequest(kind="front"))
        info = await h.m.scanner_info()
        assert not info.enabled and not info.ok


# --- filing in the library ---------------------------------------------------------------


def filed_heron(h, settings) -> tuple:
    folder = settings.movies / "The Brass Heron (2024)"
    folder.mkdir(parents=True)
    (folder / "The Brass Heron (2024) - 2160p.mkv").write_bytes(b"film")
    job = put(h, heron_job(destination=str(folder / "The Brass Heron (2024) - 2160p.mkv")))
    return job, folder


async def test_scans_are_copied_to_the_films_folder(harness, settings):
    async with harness() as h:
        job, folder = filed_heron(h, settings)
        await h.m.add_scan(job.id, sheet(), "outside_full", "upload")
        await h.m.add_scan(job.id, picture(), "inside_full", "camera")
        await h.m.add_scan(job.id, picture(colour=(1, 2, 3)), "insert", "upload")
        names = sorted(p.name for p in (folder / "scans").iterdir())
        assert names == ["insert-01.jpg", "inside-full.jpg", "outside-full.png"]
        assert (folder / "scans" / "outside-full.png").read_bytes() == sheet()  # the original, untouched
        assert all(s.library_path for s in h.db.get_job(job.id).scans)
        assert not any(p.name in ("poster.jpg", "folder.jpg") for p in folder.rglob("*"))


async def test_filing_never_overwrites_a_different_file(harness, settings):
    async with harness() as h:
        job, folder = filed_heron(h, settings)
        (folder / "scans").mkdir()
        (folder / "scans" / "front.jpg").write_bytes(b"someone else's picture")
        same = picture(colour=(9, 9, 9))
        (folder / "scans" / "back.jpg").write_bytes(same)
        await h.m.add_scan(job.id, picture(), "front", "upload")
        await h.m.add_scan(job.id, same, "back", "upload")
        assert (folder / "scans" / "front.jpg").read_bytes() == b"someone else's picture"
        assert (folder / "scans" / "front (2).jpg").read_bytes() == picture()
        assert sorted(p.name for p in (folder / "scans").iterdir()) == ["back.jpg", "front (2).jpg", "front.jpg"]
        assert h.db.get_job(job.id).scans[1].library_path == str(folder / "scans" / "back.jpg")


async def test_scans_are_filed_when_the_job_is(harness, settings):
    async with harness() as h:
        job, folder = filed_heron(h, settings)
        h.db.save_job(job.model_copy(update={"state": JobState.ripping}))
        await h.m.add_scan(job.id, picture(), "front", "upload")
        assert not (folder / "scans").exists()
        settings.scans_to_library = False
        await h.m.file_scans(h.db.get_job(job.id).model_copy(update={"state": JobState.done}))
        assert not (folder / "scans").exists()
        settings.scans_to_library = True
        await h.m.file_scans(h.db.get_job(job.id).model_copy(update={"state": JobState.done}))
        assert (folder / "scans" / "front.jpg").is_file()


async def test_tv_scans_carry_the_discs_label(harness, settings):
    async with harness() as h:
        show = settings.tv / "Pippa & Goose (2013)"
        show.mkdir(parents=True)
        job = pippa_job()
        job.destination = str(show)
        job = put(h, job)
        await h.m.add_scan(job.id, picture(), "front", "upload")
        assert [p.name for p in (show / "scans").iterdir()] == ["PIPPA_&_GOOSE_KITE_TRAM - front.jpg"]


async def test_a_disc_joining_a_release_takes_its_scans(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        a = put(h, heron_job())
        h.m.contribution_view(a.id)
        await h.m.add_scan(a.id, sheet(), "outside_full", "upload")
        b = put(h, heron_job())
        h.m.contribution_view(b.id)
        scans = h.db.get_job(b.id).scans
        assert [s.kind for s in scans] == ["outside_full"]
        assert h.m.scan_file(b.id, scans[0].id)[0].read_bytes() == sheet()


# --- the API --------------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        yield c


def test_api(client):
    m = client.app.state.manager
    rec = m.db.create_job("fake-pioneer", "/dev/sr0")
    job = heron_job(rec.id)
    m.db.save_job(job)
    m.work_dir(job).mkdir(parents=True)
    (m.work_dir(job) / "scan.log").write_text((FIXTURES / "scan-uhd-brass-heron.log").read_text())
    url = f"/api/jobs/{job.id}/scans"
    r = client.post(url, data={"kind": "outside_full", "source": "camera"},
                    files={"file": ("p.png", sheet(), "image/png")})
    assert r.status_code == 200, r.text
    sc = r.json()
    assert sc["kind"] == "outside_full" and sc["source"] == "camera"
    assert client.post(url, data={"kind": "front", "source": "scanner"},
                       files={"file": ("p.jpg", picture(), "image/jpeg")}).status_code == 400
    assert client.post(url, data={"kind": "nope"}, files={"file": ("p.jpg", picture(), "image/jpeg")}).status_code == 422
    assert [s["id"] for s in client.get(url).json()] == [sc["id"]]
    assert client.get(f"/api/jobs/{job.id}").json()["scans"][0]["id"] == sc["id"]
    assert client.get(f"{url}/{sc['id']}").content == sheet()
    thumb = client.get(f"{url}/{sc['id']}/thumb")
    assert thumb.headers["content-type"] == "image/jpeg"
    r = client.patch(f"{url}/{sc['id']}", json={"rotate": 90})
    assert r.json()["rotate"] == 90
    r = client.patch(f"{url}/{sc['id']}", json={"crop": [0, 0.1, 1, 0.9]})
    assert r.json()["crop"] == [60, 0, 540, 300] and r.json()["crop_by_hand"]  # turned: its top is the left
    assert client.patch(f"{url}/{sc['id']}", json={"crop": [0, 0, 2, 1]}).status_code == 422
    assert client.get(f"{url}/{sc['id']}/thumb", params={"whole": True}).status_code == 200
    assert client.post(f"{url}/{sc['id']}/covers", json={}).status_code == 409  # contributing is off
    client.put("/api/settings", json={"thediscdb_contribute": "ask", "thediscdb_contribute_url": BASE})
    view = client.post(f"{url}/{sc['id']}/covers", json={"position": 0.5, "spine": 0.04, "swap": False}).json()
    assert view["contribution"]["covers"] == ["back", "front"]
    assert client.post(f"{url}/scanner", json={"kind": "front"}).status_code == 409  # no scanner set up
    assert client.get("/api/scanner").json()["enabled"] is False
    assert client.delete(f"{url}/{sc['id']}").status_code == 204
    assert client.get(f"{url}/{sc['id']}").status_code == 404
    assert client.get("/api/jobs/9999/scans").status_code == 404


def test_api_scanner_settings(client):
    r = client.put("/api/settings", json={"scanner_enabled": True, "scanner_url": "192.0.2.10/",
                                          "scanner_dpi": 600, "scans_to_library": False})
    s = r.json()
    assert s["scanner_url"] == "http://192.0.2.10/eSCL" and s["scanner_dpi"] == 600
    assert s["scans_to_library"] is False and s["scanner_trim"] is True
    assert client.put("/api/settings", json={"scanner_url": "ftp://x"}).status_code == 409
    assert client.put("/api/settings", json={"scanner_dpi": 250}).status_code == 422


@respx.mock
def test_api_scanner_test_button(client):
    respx.get("http://192.0.2.10/eSCL/ScannerCapabilities").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "escl-capabilities-mfc-l8690cdw.xml").read_bytes()))
    respx.get("http://192.0.2.10/eSCL/ScannerStatus").mock(
        return_value=httpx.Response(200, content=(FIXTURES / "escl-status-mfc-l8690cdw.xml").read_bytes()))
    r = client.post("/api/settings/test", json={"check": "scanner", "values": {"scanner_url": "192.0.2.10"}}).json()
    assert r["ok"] and r["message"] == "Brother MFC-L8690CDW series answered (Idle)"
    assert "Glass: up to 216 × 297 mm, 100, 200, 300, 600 dpi" in r["details"]
    assert any(d.startswith("Feeder (both sides)") for d in r["details"])
    respx.get("http://192.0.2.11/eSCL/ScannerCapabilities").mock(side_effect=httpx.ConnectError("down"))
    r = client.post("/api/settings/test", json={"check": "scanner", "values": {"scanner_url": "192.0.2.11"}}).json()
    assert not r["ok"] and "didn't answer" in r["message"]


def test_fake_scanner_in_the_fake_backend(settings):
    """scripts/dev-fake.sh points the scanner at the backend's own fake one."""
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        r = c.get("/fake-escl/eSCL/ScannerCapabilities")
        assert r.status_code == 200 and b"simulated" in r.content



async def test_a_ripped_film_files_its_scans(harness, settings):
    from ripshelf.models import Candidate

    async with harness("/dev/fake-sr1") as h:
        job = await h.wait_job(lambda j: j.state == JobState.review, what="review")
        await h.m.add_scan(job.id, sheet(), "outside_full", "upload")
        await h.m.confirm(job.id, Candidate(source="manual", title="Moonlit Orchard", year=1998))
        done = await h.wait_job(lambda j: j.state == JobState.done and j.scans[0].library_path, what="filing")
        folder = Path(done.destination).parent / "scans"
        assert [p.name for p in folder.iterdir()] == ["outside-full.png"]


async def test_scans_from_before_the_disc_could_be_sent_become_its_covers(harness, settings):
    async with harness() as h:
        job = put(h, heron_job(identity=None, identity_source=None, state=JobState.review))
        await h.m.add_scan(job.id, sheet(), "outside_full", "upload")
        await h.m.add_scan(job.id, picture(colour=(0, 200, 0)), "front", "camera")
        settings.thediscdb_contribute = "ask"
        h.db.save_job(heron_job(job.id, scans=h.db.get_job(job.id).scans))  # confirmed now
        view = h.m.contribution_view(job.id)
        assert view.contribution.covers == ["back", "front"]
        assert Image.open(h.m.cover_file(job.id, "front")).getpixel((5, 5))[1] > 180  # the front scan
        assert Image.open(h.m.cover_file(job.id, "back")).getpixel((50, 10))[2] > 180  # the sheet's back half
