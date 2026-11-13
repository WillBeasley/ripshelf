"""The covers shelf: pairing a stack's pages into sheets, blank sides, which side is the
outside, PDFs, keeping and using covers, reading them, and suggesting them for a job.
Every cover here is made up (drawn boxes and invented titles)."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from ripshelf import shelf as shelf_mod
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import (
    Candidate,
    DiscKind,
    JobState,
    ShelfCover,
    ShelfReading,
    ShelfScanRequest,
    ShelfSide,
    ShelfSideUpdate,
)
from ripshelf.scanner import images, ocr, stack

from .conftest import read_picture, until
from .test_contribute import heron_job, put
from .test_scan_text import FakeReader
from .test_scans import URL, fake_scanner

CODE = "5012345678900"


def png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def outside(n: int = 0, code: bool = False) -> bytes:
    """A sheet's outside: a back, a spine and a front, in colours of its own (``n``), and
    a green patch the fake zbar reads as a barcode (``code``)."""
    img = Image.new("RGB", (600, 300), (20 + n * 20, 40, 200))
    img.paste((240, 220, 0), (285, 0, 315, 300))
    img.paste((200, 10 * n, 0), (315, 0, 600, 300))
    if code:
        img.paste((0, 255, 0), (40, 220, 120, 270))
    return png(img)


def inside(n: int = 0, code: bool = False) -> bytes:
    """A sheet's inside: white, with a few lines of print on the left and right."""
    img = Image.new("RGB", (600, 300), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for i in range(6):
        d.rectangle((40, 30 + i * 35, 140 + n * 5, 45 + i * 35), fill=(20, 20, 20))
        d.rectangle((420, 30 + i * 35, 540, 45 + i * 35), fill=(20, 20, 20))
    if code:
        img.paste((0, 255, 0), (40, 250, 120, 290))
    return png(img)


def blank(n: int = 0) -> bytes:
    img = Image.new("RGB", (600, 300), (250, 250, 248))
    d = ImageDraw.Draw(img)
    d.point([(100 + n, 100), (400, 200 + n)], fill=(90, 90, 90))  # dust
    return png(img)


def pdf(pages: list[bytes]) -> bytes:
    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in pages]
    out = io.BytesIO()
    imgs[0].save(out, "PDF", save_all=True, append_images=imgs[1:], resolution=100)
    return out.getvalue()


@pytest.fixture(autouse=True)
def fake_zbar(monkeypatch):
    """zbar "finds" a barcode on any picture with the green patch."""
    def decode(img):
        rgb = img.convert("RGB")
        return [CODE] if any(c[1] == (0, 255, 0) or (c[1][1] > 230 and c[1][0] < 40 and c[1][2] < 40)
                             for c in rgb.getcolors(1 << 20) or []) else []
    monkeypatch.setattr(ocr, "decode_barcodes", decode)


# --- pairing --------------------------------------------------------------------------------


def test_duplex_pages_pair_in_order():
    assert [(s.a, s.b) for s in stack.pair_duplex(4)] == [(0, 1), (2, 3)]
    assert [(s.a, s.b) for s in stack.pair_duplex(3)] == [(0, 1), (2, None)]


def test_a_second_pass_pairs_in_reverse():
    # Sheets 1, 2, 3: the second pass comes out 3, 2, 1 (indexes 3, 4, 5).
    assert [(s.a, s.b) for s in stack.pair_passes(3, 3)] == [(0, 5), (1, 4), (2, 3)]


def test_passes_that_dont_match_make_nothing():
    with pytest.raises(stack.StackError, match="first pass has 3 pages and the second 4"):
        stack.pair_passes(3, 4)


def test_one_file_holding_both_passes():
    assert stack.split_passes(6) == (3, 3)
    with pytest.raises(stack.StackError, match="odd"):
        stack.split_passes(5)


def test_one_side_is_a_cover_a_page():
    assert [(s.a, s.b) for s in stack.pair_one_side(2)] == [(0, None), (1, None)]


# --- looking at the sides ------------------------------------------------------------------------


def test_blank_sides():
    def img(data):
        return Image.open(io.BytesIO(data))
    assert stack.is_blank(img(blank()))
    assert stack.is_blank(Image.new("RGB", (400, 300), (10, 10, 10)))  # all one colour
    assert not stack.is_blank(img(inside()))
    assert not stack.is_blank(img(outside()))
    # A short chapter list in small print, on a big scan, is printed.
    sparse = Image.new("RGB", (1638, 900), (250, 250, 250))
    d = ImageDraw.Draw(sparse)
    for k in range(6):
        d.text((80, 80 + k * 50), f"Chapter {k + 1}", fill=(20, 20, 20))
    assert not stack.is_blank(sparse)
    # So is white print on black, and paper's grain isn't print.
    dark = Image.new("RGB", (800, 400), (12, 12, 12))
    ImageDraw.Draw(dark).text((40, 40), "Scene selection\n1. The Lighthouse\n2. The Storm", fill=(240, 240, 240))
    assert not stack.is_blank(dark)
    grain = Image.effect_noise((800, 400), 8).point(lambda v: 200 + v // 8).convert("RGB")
    assert stack.is_blank(grain)


def test_the_outside_is_the_side_with_the_barcode_or_spine():
    yes, no = stack.Look(False, True, False), stack.Look(False, False, False)
    spine = stack.Look(False, False, True)
    assert stack.outside_first(yes, no) and not stack.outside_first(no, yes)
    assert not stack.outside_first(no, spine)
    assert stack.outside_first(no, no)  # nothing tells them apart: as scanned
    assert not stack.outside_first(None, no) and stack.outside_first(no, None)
    assert stack.look(Image.open(io.BytesIO(outside()))).spine
    assert not stack.look(Image.open(io.BytesIO(inside()))).spine


def test_pdf_pages():
    pages = stack.pdf_pages(pdf([outside(), inside(), blank()]), dpi=72)
    assert len(pages) == 3 and all(p.startswith(b"\xff\xd8") for p in pages)
    assert Image.open(io.BytesIO(pages[0])).size == (432, 216)  # 6 x 3 inches at 72 dpi
    with pytest.raises(stack.StackError, match="can't be read"):
        stack.pdf_pages(b"%PDF-1.4 nonsense")


# --- the shelf ---------------------------------------------------------------------------------------


async def test_upload_pairs_sides_drops_blanks_and_finds_the_outside(harness, settings):
    async with harness() as h:
        # Sheet 1 scanned inside first (its barcode is on the outside); sheet 2 printed on one side.
        out = await h.m.add_shelf_upload([inside(1), outside(1, code=True), outside(2), blank()], [])
        assert len(out.covers) == 2 and not out.notes
        first, second = out.covers
        assert first.outside.sha256 == shelf_mod.hashlib.sha256(outside(1, code=True)).hexdigest()
        assert first.inside and "swapped" in first.note
        assert first.outside.split  # the spine was found
        assert second.inside is None and "blank" in second.note
        view = h.m.shelf()
        assert [c.id for c in view.covers] == [second.id, first.id] and view.limit == 500
        assert h.m.shelf_file(first.id, "inside")[0].read_bytes() == inside(1)
        # The same sheet again is skipped.
        again = await h.m.add_shelf_upload([outside(2)], [], both_sides=False)
        assert not again.covers and "on the shelf already" in again.notes[0]


async def test_odd_uploads_arent_paired(harness):
    async with harness() as h:
        with pytest.raises(ValueError, match="3 pages can't be paired"):
            await h.m.add_shelf_upload([outside(), inside(), outside(1)], [])
        out = await h.m.add_shelf_upload([outside(), inside(), outside(1)], [], both_sides=False)
        assert len(out.covers) == 3 and all(c.inside is None for c in out.covers)


async def test_upload_two_passes(harness):
    async with harness() as h:
        # One PDF: the outsides in order, then the turned stack's insides in reverse.
        out = await h.m.add_shelf_upload([pdf([outside(1), outside(2), inside(2), inside(1)])], [],
                                         layout="passes")
        assert len(out.covers) == 2 and all(c.inside for c in out.covers)
        # Two files: outsides, then insides as they came out.
        out = await h.m.add_shelf_upload([outside(3), outside(4)], [inside(4), inside(3)])
        assert len(out.covers) == 2
        for c, n in zip(out.covers, (3, 4), strict=True):
            assert h.m.shelf_file(c.id, "inside")[0].read_bytes() == inside(n)
        # Insides in sheet order.
        out = await h.m.add_shelf_upload([outside(5), outside(6)], [inside(5), inside(6)], reverse_insides=False)
        assert h.m.shelf_file(out.covers[0].id, "inside")[0].read_bytes() == inside(5)


async def test_passes_that_dont_pair_are_held(harness):
    async with harness() as h:
        out = await h.m.add_shelf_upload([outside(1), outside(2), outside(3)], [inside(3), inside(3), inside(2),
                                                                               inside(1)])
        assert not out.covers and out.stack.state == "mismatch"
        assert (len(out.stack.first), len(out.stack.second)) == (3, 4)
        assert "3 pages and the second 4" in out.stack.message
        with pytest.raises(ValueError, match="don't pair up"):
            await h.m.pair_stack()
        with pytest.raises(ValueError, match="waiting already"):
            await h.m.add_shelf_upload([outside(7)], [inside(7), inside(8)])
        st = await h.m.drop_stack_page(out.stack.second[0].id)  # the sheet fed twice
        assert "pair up now" in st.message
        assert h.m.stack_page_file(st.first[0].id)
        made = await h.m.pair_stack()
        assert len(made.covers) == 3 and h.m.shelf().stack is None
        for c, n in zip(made.covers, (1, 2, 3), strict=True):
            assert h.m.shelf_file(c.id, "inside")[0].read_bytes() == inside(n)
        assert not list((h.m._shelf_dir / "stack").iterdir())


async def test_swap_delete_and_the_cap(harness, monkeypatch):
    async with harness() as h:
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        swapped = await h.m.swap_shelf_sides(c.id)
        assert swapped.outside.sha256 == c.inside.sha256 and swapped.inside.sha256 == c.outside.sha256
        assert h.m.shelf_file(c.id, "outside")[0].read_bytes() == inside()
        assert swapped.inside.split is None
        assert (await h.m.shelf_thumb(c.id, "outside")).is_file()
        await h.m.delete_shelf_cover(c.id)
        assert not h.m.shelf().covers and not list(h.m._shelf_dir.glob(f"{c.id}.*"))
        with pytest.raises(KeyError):
            await h.m.delete_shelf_cover(c.id)
        monkeypatch.setattr(shelf_mod, "MAX_COVERS", 1)
        out = await h.m.add_shelf_upload([outside(1), outside(2)], [], both_sides=False)
        assert len(out.covers) == 1 and "shelf is full" in out.notes[0]


async def test_search(harness):
    async with harness() as h:
        a = (await h.m.add_shelf_upload([outside(1)], [], both_sides=False)).covers[0]
        b = (await h.m.add_shelf_upload([outside(2)], [], both_sides=False)).covers[0]
        await h.m._update_cover(a.id, lambda c: setattr(c, "reading", ShelfReading(
            state="done", title="The Lantern Keeper", year=2011, barcode=CODE, format=DiscKind.bluray)))
        await h.m._update_cover(b.id, lambda c: setattr(c, "reading", ShelfReading(
            state="done", barcode_name="Seagull Manor Series 2 [DVD]")))
        assert [c.id for c in h.m.shelf("lantern 2011").covers] == [a.id]
        assert [c.id for c in h.m.shelf(CODE[:6]).covers] == [a.id]
        assert [c.id for c in h.m.shelf("bluray").covers] == [a.id]
        assert [c.id for c in h.m.shelf("seagull").covers] == [b.id]
        assert len(h.m.shelf(" ").covers) == 2


# --- editing by hand ----------------------------------------------------------------------------------


def test_render_flips_after_the_turn_and_straightens_in_place():
    img = Image.new("RGB", (40, 20), (0, 0, 255))
    img.paste((255, 0, 0), (0, 0, 10, 20))  # red down the left
    data = png(img)
    assert images.render(data, None, 0, flip=True).getpixel((35, 10)) == (255, 0, 0)
    turned = images.render(data, None, 90, flip=True)  # red along the top, then mirrored: still the top
    assert turned.size == (20, 40) and turned.getpixel((10, 2)) == (255, 0, 0)
    level = images.render(data, None, 0, straighten=10)
    assert level.size == (40, 20) and level.getpixel((0, 0)) == (255, 255, 255)  # a corner turned out of it


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_a_crop_drawn_on_a_flipped_side(rotate):
    img = Image.new("RGB", (200, 100), (0, 0, 0))
    img.paste((255, 255, 255), (20, 10, 60, 40))  # a white patch near the top left
    data = png(img)
    shown = images.render(data, None, rotate, flip=True)
    bbox = shown.convert("L").point(lambda v: 255 if v > 128 else 0).getbbox()  # where the patch shows
    frac = [bbox[0] / shown.width, bbox[1] / shown.height, bbox[2] / shown.width, bbox[3] / shown.height]
    assert images.crop_from_shown(frac, 200, 100, rotate, flip=True) == [20, 10, 60, 40]


async def test_edit_a_side_by_hand_and_undo_it(harness, settings):
    async with harness() as h:
        h.m.text_reader = FakeReader(["THE LANTERN KEEPER"], turn=90)
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        await until(lambda: h.m.shelf_cover(c.id).reading.state == "done", 10, "reading")
        assert h.m.shelf_cover(c.id).inside.rotate == 90  # the reading turned it
        reads = h.m.text_reader.reads
        await h.m.shelf_thumb(c.id, "inside", large=True)
        e = await h.m.edit_shelf_side(c.id, "inside", ShelfSideUpdate(
            rotate=0, flip=True, straighten=4.5, crop=[0.1, 0.2, 0.9, 0.8]))
        s = e.inside
        assert (s.rotate, s.flip, s.straighten, s.by_hand) == (0, True, -4.5, True)  # kept as on the original
        assert s.crop == [60, 60, 540, 240] and e.reading.state == "waiting"
        assert h.m.shelf_file(c.id, "inside")[0].read_bytes() == inside()  # the original stays
        assert not (h.m._shelf_dir / f"{c.id}.inside.view.jpg").exists()
        whole = await h.m.shelf_thumb(c.id, "inside", whole=True)
        assert read_picture(whole).size == (600, 300)
        done = await until(lambda: (x := h.m.shelf_cover(c.id)).reading.state == "done" and x, 10, "read again")
        assert h.m.text_reader.reads > reads  # read again, as it shows now
        assert done.inside.rotate == 0  # not turned back over what was set by hand
        back = await h.m.edit_shelf_side(c.id, "inside", ShelfSideUpdate(reset=True))
        assert (back.inside.rotate, back.inside.flip, back.inside.straighten, back.inside.by_hand) == (0, False, 0, False)
        assert back.inside.crop == c.inside.crop
        with pytest.raises(KeyError):
            await h.m.edit_shelf_side("nope", "outside", ShelfSideUpdate(rotate=90))


async def test_an_edited_side_goes_to_the_job_as_shown(harness, settings):
    async with harness("fake-pioneer") as h:
        job = put(h, heron_job(state=JobState.review))
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        await h.m.edit_shelf_side(c.id, "inside", ShelfSideUpdate(rotate=90))
        done = await h.m.use_shelf_cover(c.id, job.id)
        out, ins = done.scans
        assert out.sha256 == c.outside.sha256  # not edited: the original
        assert ins.content_type == "image/jpeg" and ins.height > ins.width  # trimmed, and turned


# --- reading -----------------------------------------------------------------------------------------


async def test_covers_are_read_as_they_arrive(harness, settings, monkeypatch):
    async def name(s, code, kind=None, *, thediscdb=True, client=None):
        from ripshelf.identify.barcode import Answer, Found
        return Answer(Found("The Lantern Keeper (Blu-ray) [2011]", "upcitemdb"), [])
    monkeypatch.setattr(shelf_mod.barcode, "name", name)
    async with harness() as h:
        h.m.text_reader = FakeReader(["THE LANTERN KEEPER", "Blu-ray", "© 2011 Lamplight Films"])
        c = (await h.m.add_shelf_upload([outside(code=True), inside()], [])).covers[0]
        done = await until(lambda: (x := h.m.shelf_cover(c.id)).reading.state == "done" and x, 10, "reading")
        r = done.reading
        assert r.barcode == CODE and r.barcode_name.startswith("The Lantern Keeper")
        assert r.format == DiscKind.bluray
        assert h.m._saved_texts(done)
        reads = h.m.text_reader.reads
        # Read again only when asked.
        await h.m.read_shelf_again(c.id)
        await until(lambda: h.m.shelf_cover(c.id).reading.state == "done", 10, "read again")
        assert h.m.text_reader.reads == reads  # the text was kept


async def test_no_tesseract(harness, settings):
    async with harness() as h:
        class Missing(FakeReader):
            async def version(self):
                raise ocr.OcrUnavailable("Tesseract isn't installed")
        h.m.text_reader = Missing([])
        c = (await h.m.add_shelf_upload([outside()], [], both_sides=False)).covers[0]
        r = (await until(lambda: (x := h.m.shelf_cover(c.id)).reading.state == "unavailable" and x, 10, "")).reading
        assert "isn't installed" in r.message


async def test_vision_now_needs_a_model(harness):
    async with harness() as h:
        c = (await h.m.add_shelf_upload([outside()], [], both_sides=False)).covers[0]
        with pytest.raises(ValueError, match="vision model"):
            await h.m.ask_shelf_vision(c.id)


async def test_vision_now_reads_with_the_model(harness, settings, monkeypatch):
    from ripshelf.models import Listing, ListingDetails
    seen = []

    async def read_listing(provider, img):
        seen.append(img.size)
        return Listing(details=ListingDetails(title="The Lantern Keeper", year=2011)), 1.0
    monkeypatch.setattr(shelf_mod.vision, "read_listing", read_listing)
    settings.vision_mode, settings.vision_provider, settings.vision_url = "manual", "local", "http://vision.test"
    async with harness() as h:
        h.m.text_reader = FakeReader(["x"])
        c = (await h.m.add_shelf_upload([outside()], [], both_sides=False)).covers[0]
        await until(lambda: h.m.shelf_cover(c.id).reading.state == "done", 10, "reading")
        assert not seen and not h.m.shelf_cover(c.id).reading.vision  # manual: only when asked
        reads = h.m.text_reader.reads
        assert (await h.m.ask_shelf_vision(c.id)).reading.vision_state == "queued"
        r = (await until(lambda: (x := h.m.shelf_cover(c.id)).reading.vision_state == "done" and x, 10,
                         "vision")).reading
        assert r.vision and r.title == "The Lantern Keeper" and r.year == 2011 and len(seen) == 1
        assert h.m.text_reader.reads == reads


# --- the scanner ---------------------------------------------------------------------------------------


async def test_scan_a_stack_both_sides(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        fake, h.m.escl_transport = fake_scanner(feeder_images=[[outside(1), inside(1), outside(2), blank(),
                                                                inside(3), outside(3, code=True)]])
        h.m.escl_busy_wait_s = 0
        st = await h.m.scan_stack(ShelfScanRequest(mode="duplex", dpi=100))
        assert st.state == "scanning" and st.mode == "duplex"
        with pytest.raises(ValueError, match="busy"):
            await h.m.scan_stack(ShelfScanRequest())
        assert (await h.m.scanner_info()).busy_shelf
        await until(lambda: not h.m._stack_scanning() and h.m.shelf().stack is None, 20, "the stack")
        covers = sorted(h.m.shelf().covers, key=lambda c: c.created_at)
        assert len(covers) == 3
        assert covers[1].inside is None
        assert h.m.shelf_file(covers[2].id, "outside")[0].read_bytes() == outside(3, code=True)
        assert "<scan:Duplex>true" in fake.settings[-1] and "Feeder" in fake.settings[-1]


async def test_scan_a_stack_in_two_passes(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        _, h.m.escl_transport = fake_scanner(feeder_images=[
            [outside(1), outside(2)],
            [inside(2), inside(2), inside(1)],  # sheet 2 went through twice
            [inside(2), inside(1)],  # scanned again
        ])
        h.m.escl_busy_wait_s = 0
        with pytest.raises(ValueError, match="no first pass"):
            await h.m.scan_second_pass()
        await h.m.scan_stack(ShelfScanRequest(mode="two_pass", dpi=100))
        st = await until(lambda: (s := h.m.shelf().stack) and s.state == "flip" and s, 20, "the first pass")
        assert len(st.first) == 2 and "turn the stack over" in st.message and not h.m.shelf().covers
        with pytest.raises(ValueError, match="waiting for its other side"):
            await h.m.scan_stack(ShelfScanRequest(mode="duplex"))
        await h.m.scan_second_pass(dpi=100)
        st = await until(lambda: (s := h.m.shelf().stack) and s.state == "mismatch" and s, 20, "the second pass")
        assert (len(st.first), len(st.second)) == (2, 3) and not h.m.shelf().covers
        await h.m.scan_second_pass(dpi=100)  # scan the other side again
        await until(lambda: h.m.shelf().stack is None and len(h.m.shelf().covers) == 2, 20, "the covers")
        for c in h.m.shelf().covers:
            n = 1 if h.m.shelf_file(c.id, "outside")[0].read_bytes() == outside(1) else 2
            assert h.m.shelf_file(c.id, "inside")[0].read_bytes() == inside(n)


async def test_cancel_a_held_stack(harness, settings):
    settings.scanner_enabled, settings.scanner_url = True, URL
    async with harness() as h:
        _, h.m.escl_transport = fake_scanner(feeder_images=[[outside(1), outside(2)]])
        h.m.escl_busy_wait_s = 0
        await h.m.scan_stack(ShelfScanRequest(mode="two_pass", dpi=100))
        await until(lambda: (s := h.m.shelf().stack) and s.state == "flip", 20, "the first pass")
        await h.m.cancel_stack()
        assert h.m.shelf().stack is None and not list((h.m._shelf_dir / "stack").iterdir())


async def test_a_stack_needs_the_scanner(harness):
    async with harness() as h:
        with pytest.raises(ValueError, match="Settings"):
            await h.m.scan_stack(ShelfScanRequest())


# --- using a cover -----------------------------------------------------------------------------------------


async def test_a_cover_becomes_the_jobs_scans(harness, settings):
    async with harness("fake-pioneer") as h:
        job = put(h, heron_job(state=JobState.review))
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        done = await h.m.use_shelf_cover(c.id, job.id)
        assert [s.kind for s in done.scans] == ["outside_full", "inside_full"]
        assert done.scans[0].sha256 == c.outside.sha256
        assert not h.m.shelf().covers and not list(h.m._shelf_dir.glob(f"{c.id}.*"))
        with pytest.raises(KeyError):
            await h.m.use_shelf_cover(c.id, job.id)


async def test_a_cover_for_a_drives_next_disc(harness, settings):
    async with harness("fake-pioneer") as h:
        drive = next(iter(h.m.drives))
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        pending = await h.m.shelf_cover_for_drive(c.id, drive)
        assert [p.kind for p in pending.scans] == ["outside_full", "inside_full"]
        assert not h.m.shelf().covers
        with pytest.raises(KeyError):
            await h.m.shelf_cover_for_drive("nope", drive)


# --- suggestions ---------------------------------------------------------------------------------------------


def cover(**reading) -> ShelfCover:
    side = ShelfSide(content_type="image/png", width=1, height=1, sha256="x", size_bytes=1)
    return ShelfCover(id="c1", created_at=shelf_mod.now(), source="upload", outside=side,
                      reading=ShelfReading(state="done", **reading))


def test_the_same_barcode_scores_highest():
    score, why = shelf_mod.score_cover(cover(barcode=CODE, title="Something Else"), ["012345678900"[0:0] + CODE],
                                       [("The Lantern Keeper", 2011, "the disc's title")])
    assert score == 1.0 and CODE in why
    upc = shelf_mod.score_cover(cover(barcode="012345678905"), ["0012345678905"], [])
    assert upc[0] == 1.0  # a UPC-A is its EAN-13 with a leading 0


def test_titles_score_by_likeness_and_year():
    names = [("The Lantern Keeper", 2011, "the disc's title")]
    same, why = shelf_mod.score_cover(cover(title="LANTERN KEEPER", year=2011), [], names)
    assert same >= 0.9 and "like the disc's title" in why
    wrong_year, _ = shelf_mod.score_cover(cover(title="The Lantern Keeper", year=1987), [], names)
    no_year, _ = shelf_mod.score_cover(cover(title="The Lantern Keeper"), [], names)
    assert wrong_year < shelf_mod.SUGGEST_AT <= no_year < same
    other, _ = shelf_mod.score_cover(cover(title="Captain Comet"), [], names)
    assert other < shelf_mod.SUGGEST_AT
    by_code_name, why = shelf_mod.score_cover(cover(barcode_name="The Lantern Keeper [Blu-ray] (2011)"), [], names)
    assert by_code_name >= shelf_mod.SUGGEST_AT and "Lantern Keeper" in why
    assert shelf_mod.score_cover(cover(), [], names)[0] == 0  # not read yet


async def test_suggestions_for_a_job(harness, settings):
    async with harness() as h:
        job = put(h, heron_job(state=JobState.review, identity=None, candidates=[
            Candidate(title="The Brass Heron", year=2024, score=0.8), Candidate(title="Copper Kite", year=2019)]))
        a = (await h.m.add_shelf_upload([outside(1)], [], both_sides=False)).covers[0]
        b = (await h.m.add_shelf_upload([outside(2)], [], both_sides=False)).covers[0]
        await h.m._update_cover(a.id, lambda c: setattr(c, "reading", ShelfReading(
            state="done", title="The Brass Heron", year=2024)))
        await h.m._update_cover(b.id, lambda c: setattr(c, "reading", ShelfReading(
            state="done", title="Seagull Manor")))
        got = h.m.shelf_suggestions(job.id)
        assert [s.cover.id for s in got] == [a.id] and got[0].score > 0.9
        assert [s.cover.id for s in h.m.shelf_picks(job.id)] == [a.id, b.id]
        with pytest.raises(KeyError):
            h.m.shelf_suggestions(9999)


# --- the API ------------------------------------------------------------------------------------------------


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c:
        yield c


def test_api(client):
    r = client.post("/api/covers/upload", files=[("files", ("a.png", outside(1), "image/png")),
                                                  ("files", ("b.png", inside(1), "image/png"))])
    assert r.status_code == 200, r.text
    cid = r.json()["covers"][0]["id"]
    r = client.post("/api/covers/upload", data={"both_sides": "false"},
                    files=[("files", ("s.pdf", pdf([outside(2), outside(3)]), "application/pdf"))])
    assert len(r.json()["covers"]) == 2
    view = client.get("/api/covers").json()
    assert len(view["covers"]) == 3 and view["stack"] is None and view["limit"] == 500
    assert client.get(f"/api/covers/{cid}/outside").content == outside(1)
    assert client.get(f"/api/covers/{cid}/inside", params={"thumb": True}).headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/covers/{cid}/outside", params={"large": True}).status_code == 200
    assert client.get(f"/api/covers/{cid}/sideways").status_code == 422
    r = client.patch(f"/api/covers/{cid}/outside", json={"rotate": 180, "flip": True})
    assert r.status_code == 200 and r.json()["outside"]["flip"] and r.json()["outside"]["by_hand"]
    assert client.get(f"/api/covers/{cid}/outside", params={"whole": True}).headers["content-type"] == "image/jpeg"
    assert client.patch(f"/api/covers/{cid}/outside", json={"straighten": 20}).status_code == 422
    assert client.patch(f"/api/covers/{cid}/outside", json={"crop": [0.5, 0, 0.52, 1]}).status_code == 422
    assert client.patch(f"/api/covers/{cid}/outside", json={"reset": True}).json()["outside"]["by_hand"] is False
    assert client.patch("/api/covers/nope/outside", json={"rotate": 90}).status_code == 404
    assert client.post(f"/api/covers/{cid}/swap").json()["outside"]["sha256"] == view["covers"][-1]["inside"]["sha256"]
    assert client.post(f"/api/covers/{cid}/read").status_code == 200
    assert client.post(f"/api/covers/{cid}/vision").status_code == 409  # no vision model
    assert client.post("/api/covers/upload", files=[("files", ("x.txt", b"hello", "text/plain"))]).status_code == 409
    r = client.post("/api/covers/upload", data={"layout": "passes"},
                    files=[("files", ("a.png", outside(4), "image/png")), ("insides", ("b.png", inside(4), "image/png")),
                           ("insides", ("c.png", inside(5), "image/png"))])
    st = r.json()["stack"]
    assert st["state"] == "mismatch"
    assert client.get(f"/api/covers/stack/pages/{st['first'][0]['id']}", params={"thumb": True}).status_code == 200
    assert client.post("/api/covers/stack/pair").status_code == 409
    assert client.delete(f"/api/covers/stack/pages/{st['second'][0]['id']}").json()["second"].__len__() == 1
    assert len(client.post("/api/covers/stack/pair").json()["covers"]) == 1
    assert client.delete("/api/covers/stack").status_code == 204
    assert client.post("/api/covers/scanner", json={"mode": "two_pass"}).status_code == 409  # no scanner
    assert client.post("/api/covers/scanner/second-pass").status_code == 409
    m = client.app.state.manager
    rec = m.db.create_job("fake-pioneer", "/dev/sr0")
    job = heron_job(rec.id, state=JobState.review)
    m.db.save_job(job)
    assert client.get(f"/api/jobs/{job.id}/shelf-suggestions").json() == []
    assert len(client.get(f"/api/jobs/{job.id}/shelf-suggestions", params={"all": True}).json()) == 4
    r = client.post(f"/api/covers/{cid}/use/job/{job.id}")
    assert r.status_code == 200, r.text
    assert [s["kind"] for s in r.json()["scans"]] == ["outside_full", "inside_full"]
    assert client.post(f"/api/covers/{cid}/use/job/{job.id}").status_code == 404
    assert client.post(f"/api/covers/{cid}/use/drive/nope").status_code == 404
    other = client.get("/api/covers").json()["covers"][0]["id"]
    assert client.delete(f"/api/covers/{other}").status_code == 204
    assert client.delete(f"/api/covers/{other}").status_code == 404
    assert client.get("/api/covers", params={"q": "nothing like it"}).json()["covers"] == []


# --- when the shelf can't do it --------------------------------------------------------------------------------


async def test_uploads_that_cant_be_taken(harness, monkeypatch):
    from ripshelf.providers import release_info

    async with harness() as h:
        with pytest.raises(ValueError, match="no pages were uploaded"):
            await h.m.add_shelf_upload([], [])
        monkeypatch.setattr(release_info, "MAX_COVER_BYTES", 100)
        with pytest.raises(ValueError, match="larger than 20 MB"):
            await h.m.add_shelf_upload([outside()], [])
        assert not h.m.shelf().covers


async def test_a_cover_that_cant_go_to_the_job_stays_on_the_shelf(harness, monkeypatch):
    from ripshelf import scanjobs

    async with harness("fake-pioneer") as h:
        job = put(h, heron_job(state=JobState.review))
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        monkeypatch.setattr(scanjobs, "MAX_SCANS", 1)
        with pytest.raises(ValueError, match="at most 1 pictures"):
            await h.m.use_shelf_cover(c.id, job.id)
        monkeypatch.setattr(scanjobs, "MAX_SCANS", 60)

        real, calls = h.m.add_scan, []

        async def second_fails(*a, **kw):
            calls.append(a)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return await real(*a, **kw)

        monkeypatch.setattr(h.m, "add_scan", second_fails)
        with pytest.raises(OSError):
            await h.m.use_shelf_cover(c.id, job.id)
        assert [x.id for x in h.m.shelf().covers] == [c.id], "put back, not lost"
        assert h.db.get_job(job.id).scans == [], "and the side already added taken off, not left twice"
        assert h.m.shelf_file(c.id, "outside")[0].read_bytes() == outside()


async def test_a_cover_that_cant_wait_for_the_drive_stays_on_the_shelf(harness, monkeypatch):
    async with harness("fake-pioneer") as h:
        drive = next(iter(h.m.drives))
        c = (await h.m.add_shelf_upload([outside(), inside()], [])).covers[0]
        real, calls = h.m.add_pending, []

        async def second_fails(*a, **kw):
            calls.append(a)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return await real(*a, **kw)

        monkeypatch.setattr(h.m, "add_pending", second_fails)
        with pytest.raises(OSError):
            await h.m.shelf_cover_for_drive(c.id, drive)
        assert h.m.pending_scans(drive).scans == [], "the side already kept for the drive is taken back"
        assert [x.id for x in h.m.shelf().covers] == [c.id]


async def test_the_stack_buttons_with_nothing_to_act_on(harness):
    async with harness() as h:
        with pytest.raises(ValueError, match="no stack waiting"):
            await h.m.pair_stack()
        with pytest.raises(KeyError):
            await h.m.drop_stack_page("nope")
        with pytest.raises(KeyError):
            await h.m.swap_shelf_sides("nope")
        await h.m.cancel_stack()  # nothing held: harmless
        assert h.m.shelf().stack is None
