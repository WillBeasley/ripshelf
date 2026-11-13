"""The Tesseract runner and the picture reader, without Tesseract: a stand-in program
plays Tesseract (its arguments, stdin and replies), and a stand-in reader that looks at
the picture it is given checks the spine is read along and across."""

from __future__ import annotations

import json
import shutil
import sys

import pytest
from PIL import Image, ImageDraw

from ripshelf import proc
from ripshelf.models import ScanSplit
from ripshelf.scanner import ocr

from .conftest import fake_program

HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def word(block, line, n, left, text, conf=96.0):
    return f"5\t1\t{block}\t1\t{line}\t{n}\t{left}\t10\t40\t20\t{conf}\t{text}"


TSV = "\n".join([HEADER, word(1, 1, 1, 10, "Seagull"), word(1, 1, 2, 60, "Manor"),
                 word(1, 2, 1, 10, "Series", conf=91), word(1, 2, 2, 60, "One", conf=93)]) + "\n"


@pytest.fixture
def tesseract(tmp_path):
    """A stand-in tesseract: ``mode`` picks how it behaves, ``calls`` what it was asked."""
    calls = tmp_path / "calls.jsonl"
    mode = tmp_path / "mode"
    exe = fake_program(tmp_path / "tesseract", f"""\
        import json, sys, time
        args = sys.argv[1:]
        data = sys.stdin.buffer.read()
        with open({str(calls)!r}, "a") as f:
            f.write(json.dumps({{"args": args, "stdin": data[:8].hex(), "size": len(data)}}) + "\\n")
        mode = open({str(mode)!r}).read().strip()
        if mode == "hang":
            time.sleep(60)
        if mode == "fail":
            sys.stderr.write("Tesseract Open Source OCR Engine\\nError opening data file eng.traineddata\\n")
            sys.exit(1)
        if mode == "quiet-fail":
            sys.exit(3)
        if "--version" in args:
            print("tesseract 5.3.4\\n leptonica-1.82.0")
        elif "0" in args and "--psm" in args:
            print("Page number: 0\\nOrientation in degrees: 270\\nRotate: 90\\nOrientation confidence: 7.25")
        else:
            sys.stdout.write({TSV!r})
    """)
    mode.write_text("ok")

    class T:
        command = exe

        @staticmethod
        def set(m: str) -> None:
            mode.write_text(m)

        @staticmethod
        def calls() -> list[dict]:
            return [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []

    return T


async def test_version_is_the_first_line_and_asked_once(tesseract):
    t = ocr.Tesseract(tesseract.command)
    assert await t.version() == "tesseract 5.3.4"
    assert await t.version() == "tesseract 5.3.4"
    assert [c["args"] for c in tesseract.calls()] == [["--version"]]


async def test_read_sends_a_png_and_parses_the_words(tesseract):
    t = ocr.Tesseract(tesseract.command, languages="eng+deu")
    lines = await t.read(Image.new("L", (120, 60), 255), psm=11)
    assert [ln.text for ln in lines] == ["Seagull Manor", "Series One"]
    assert lines[0].box == (10, 10, 100, 30)
    [call] = tesseract.calls()
    assert call["args"] == ["stdin", "stdout", "--psm", "11", "-l", "eng+deu", "tsv"]
    assert call["stdin"] == b"\x89PNG\r\n\x1a\n".hex(), "the picture goes in as a PNG"


async def test_no_languages_means_english(tesseract):
    await ocr.Tesseract(tesseract.command, languages="").read(Image.new("L", (40, 40), 255))
    assert tesseract.calls()[0]["args"][-2] == "eng"


async def test_orientation(tesseract):
    t = ocr.Tesseract(tesseract.command)
    assert await t.orientation(Image.new("L", (40, 40), 255)) == (90, 7.25)
    assert tesseract.calls()[0]["args"] == ["stdin", "stdout", "--psm", "0"]
    # Too little text to tell (or no osd.traineddata): Tesseract fails, and the picture stays as it is.
    tesseract.set("fail")
    assert await t.orientation(Image.new("L", (40, 40), 255)) == (0, 0.0)


async def test_orientation_reply_without_numbers(tmp_path):
    script = fake_program(tmp_path / "tesseract", "import sys; sys.stdin.buffer.read(); print('Page number: 0')")
    assert await ocr.Tesseract(script).orientation(Image.new("L", (40, 40), 255)) == (0, 0.0)


@pytest.mark.parametrize(("mode", "message"), [
    ("fail", "Tesseract failed: Error opening data file eng.traineddata"),
    ("quiet-fail", "Tesseract failed: exit 3"),
])
async def test_tesseract_failing_says_why(tesseract, mode, message):
    tesseract.set(mode)
    with pytest.raises(ocr.OcrUnavailable, match=message):
        await ocr.Tesseract(tesseract.command).read(Image.new("L", (40, 40), 255))


async def test_tesseract_that_hangs_is_stopped(tesseract, monkeypatch):
    monkeypatch.setattr(ocr, "TIMEOUT_S", 0.5)
    tesseract.set("hang")
    with pytest.raises(ocr.OcrUnavailable, match="took too long"):
        await ocr.Tesseract(tesseract.command).version()


async def test_tesseract_that_cannot_start(tmp_path):
    not_a_program = tmp_path / "tesseract"
    not_a_program.write_text("just a file")  # found, but not executable
    with pytest.raises(ocr.OcrUnavailable, match="couldn't be started"):
        await ocr.Tesseract(str(not_a_program)).version()


def test_windows_finds_tesseract_where_its_installer_puts_it(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", True)
    monkeypatch.setattr(shutil, "which", lambda c: None)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    assert ocr.default_command("tesseract") == "tesseract", "not installed: left as it is"
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    per_user = tmp_path / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"
    per_user.parent.mkdir(parents=True)
    per_user.touch()
    assert ocr.default_command("tesseract") == str(per_user)
    machine = tmp_path / "Program Files" / "Tesseract-OCR" / "tesseract.exe"
    machine.parent.mkdir(parents=True)
    machine.touch()
    assert ocr.default_command("tesseract") == str(machine), "Program Files first"
    assert ocr.default_command(r"D:\tools\tesseract.exe") == r"D:\tools\tesseract.exe"
    monkeypatch.setattr(shutil, "which", lambda c: r"C:\scoop\shims\tesseract.exe")
    assert ocr.default_command("tesseract") == "tesseract", "on the PATH"


@pytest.mark.parametrize(("size", "want"), [
    ((6800, 4600), (3400, 2300)),  # a 600 dpi scan, halved
    ((2400, 1600), (2400, 1600)),  # about right already
    ((900, 600), (1800, 1200)),  # a small photo, doubled
    ((300, 200), (900, 600)),  # but never more than three times
])
def test_pictures_are_brought_to_about_300_dpi(size, want):
    assert ocr.to_ocr_size(Image.new("L", size)).size == want


def test_tsv_rows_that_are_not_words_are_skipped():
    tsv = "\n".join([HEADER, word(1, 1, 1, 10, "Seagull"), word(1, 1, 2, 60, "Manor", conf="x"),
                     word(1, 1, 3, 60, "   "), "5\t1\t1\t1\t1", word(1, 1, 4, 60, "Brass", conf=-1)])
    assert [ln.text for ln in ocr.parse_tsv(tsv)] == ["Seagull"]


def test_a_line_of_confident_short_marks_is_noise():
    line = ocr.Line([ocr.Word("~", 96, (0, 0, 5, 5)), ocr.Word("a", 96, (6, 0, 9, 5)), ocr.Word("b", 96, (10, 0, 15, 5))])
    assert ocr.tidy(line) is None, "no two letters together"
    assert ocr.tidy(ocr.Line([ocr.Word("2024", 50, (0, 0, 40, 10))])) is not None, "a lone number is kept"


# --- reading a whole sheet ------------------------------------------------------------

def dark(img: Image.Image, box: tuple[float, float, float, float]) -> bool:
    """Whether that part of the picture (as fractions) has anything printed in it."""
    w, h = img.size
    part = img.convert("L").crop((round(box[0] * w), round(box[1] * h), round(box[2] * w), round(box[3] * h)))
    return part.getextrema()[0] < 128


class LookingReader:
    """Reads what is printed where: the spine's title is near its top, so along the spine
    it reads only when the picture is turned so that end is at the left."""

    def __init__(self, turn=0, conf=0.0):
        self.turn, self.conf = turn, conf
        self.calls: list[tuple[tuple[int, int], int]] = []

    async def orientation(self, img):
        return self.turn, self.conf

    async def read(self, img, psm=3):
        self.calls.append((img.size, psm))
        w, h = img.size
        if w > h * 4:  # along the spine
            if dark(img, (0, 0, 0.3, 1)):
                return [ocr.Line([ocr.Word("SEAGULL", 95, (round(w * 0.02), 2, round(w * 0.2), h - 2)),
                                  ocr.Word("MANOR", 95, (round(w * 0.22), 2, round(w * 0.3), h - 2))])]
            return [ocr.Line([ocr.Word("ꟽꟽꟽ", 30, (0, 0, w, h))])]
        if h > w * 4:  # across the spine, as it is
            return [ocr.Line([ocr.Word("SM1975", 92, (2, round(h * 0.9), w - 2, round(h * 0.95)))])]
        return [ocr.Line([ocr.Word("Special", 96, (10, 10, 90, 30)), ocr.Word("features", 96, (100, 10, 200, 30))])]


def sheet(title_at_top: bool) -> Image.Image:
    img = Image.new("RGB", (3000, 2000), "white")
    # The spine's title block, at one end of the spine in the middle.
    top = 40 if title_at_top else 1500
    ImageDraw.Draw(img).rectangle((1470, top, 1530, top + 460), fill="black")
    return img


@pytest.mark.parametrize("title_at_top", [True, False], ids=["reads-up", "reads-down"])
async def test_the_spine_is_read_along_the_better_way_and_across(title_at_top):
    """Spines read bottom to top (UK) or top to bottom (US): whichever way reads wins."""
    reader = LookingReader()
    split = ScanSplit(position=0.5, spine=0.04)
    out = await ocr.read_picture(reader, sheet(title_at_top), "outside_full", split, barcodes=False)
    spine = [ln for ln in out.lines if ln.region == "spine"]
    assert [ln.text for ln in spine] == ["SEAGULL MANOR", "SM1975"]
    title = spine[0].box
    assert 0.465 <= title[0] < title[2] <= 0.535, "inside the spine strip, in the picture as it is"
    if title_at_top:
        assert title[1] < 0.05 and title[3] < 0.35, "at the top, where it is printed"
    else:
        assert title[1] > 0.65 and title[3] > 0.95, "at the bottom, where it is printed"
    assert [ln.text for ln in out.more if ln.region == "spine"] == [], "the losing way along is noise"
    assert {ln.region for ln in out.lines} == {"back", "spine", "front"}
    assert out.split is None, "the spine was set by hand, so it isn't looked for"
    assert sum(1 for _, psm in reader.calls if psm == 3) == 2, "back and front read as they are once each"


async def test_a_doubtful_orientation_is_not_trusted():
    out = await ocr.read_picture(LookingReader(turn=90, conf=1.0), Image.new("RGB", (800, 600), "white"), "back",
                                 barcodes=False)
    assert out.turned == 0
    out = await ocr.read_picture(LookingReader(turn=90, conf=5.0), Image.new("RGB", (800, 600), "white"), "back",
                                 barcodes=False)
    assert out.turned == 90


class UprightReader(LookingReader):
    """Reads confident text only when the picture's black bar is along its top."""

    def __init__(self, turn=0, conf=0.0):
        super().__init__(turn, conf)
        self.sizes: list[tuple[int, int]] = []

    async def read(self, img, psm=3):
        self.sizes.append(img.size)
        upright = dark(img, (0.2, 0, 0.8, 0.1))
        return [ocr.Line([ocr.Word("Lighthouse" if upright else "ǝsnoɥ", 95 if upright else 30, (5, 5, 90, 20))])]


def wrap(w: int, h: int, bar: str) -> Image.Image:
    """A made-up outer sheet with a black bar along one side (where its text is upright)."""
    img = Image.new("RGB", (w, h), "white")
    box = {"top": (0.3 * w, 0, 0.7 * w, 0.05 * h), "bottom": (0.3 * w, 0.95 * h, 0.7 * w, h),
           "left": (0, 0.3 * h, 0.05 * w, 0.7 * h), "right": (0.95 * w, 0.3 * h, w, 0.7 * h)}[bar]
    ImageDraw.Draw(img).rectangle(box, fill="black")
    return img


@pytest.mark.parametrize(("bar", "osd", "turned"), [
    ("left", (0, 0.0), 90),  # lying on its side, and the orientation check can't tell
    ("right", (0, 0.0), 270),
    ("left", (180, 9.0), 90),  # sure, but upright would stand it up tall: never
    ("right", (270, 9.0), 270),  # sure, and lying flat: taken
    ("left", (0, 9.0), 90),  # sure it's upright, but that's tall: never
], ids=["side-unsure-cw", "side-unsure-ccw", "side-upside-down-osd", "side-sure", "side-osd-upright"])
async def test_a_whole_sheet_on_its_side_is_only_ever_turned_flat(bar, osd, turned):
    out = await ocr.read_picture(UprightReader(*osd), wrap(1500, 2700, bar), "outside_full", barcodes=False)
    assert out.turned == turned


@pytest.mark.parametrize(("bar", "osd", "turned"), [
    ("top", (0, 0.0), 0),
    ("bottom", (0, 0.0), 180),  # upside down: the text reads only turned over
    ("top", (90, 9.0), 0),  # sure it's on its side, but that would stand it up tall
    ("bottom", (270, 9.0), 180),
    ("top", (180, 9.0), 180),  # sure and flat: taken
], ids=["flat", "upside-down", "osd-says-side", "osd-says-side-upside-down", "osd-sure-flat"])
async def test_a_whole_sheet_lying_flat_stays_flat(bar, osd, turned):
    out = await ocr.read_picture(UprightReader(*osd), wrap(2700, 1500, bar), "outside_full", barcodes=False)
    assert out.turned == turned


async def test_a_front_alone_is_turned_as_the_orientation_check_says():
    """A DVD's front on its own (taller than wide, no spine) isn't a whole sheet."""
    front = Image.new("RGB", (1300, 1840), "white")
    assert not ocr.is_wrap(front)
    out = await ocr.read_picture(UprightReader(), front, "outside_full", barcodes=False)
    assert out.turned == 0
    out = await ocr.read_picture(UprightReader(90, 9.0), front, "outside_full", barcodes=False)
    assert out.turned == 90, "turned on its side as sure, though that leaves it wider"


def test_a_squarish_sheet_is_whole_when_its_spine_shows():
    img = Image.new("RGB", (1840, 2730), "white")  # a DVD sheet on its side, about 1.48 long
    assert not ocr.is_wrap(img)
    ImageDraw.Draw(img).rectangle((0, 1300, 1840, 1302), fill="black")  # the spine's two folds
    ImageDraw.Draw(img).rectangle((0, 1430, 1840, 1432), fill="black")
    assert ocr.is_wrap(img)


# --- barcodes -------------------------------------------------------------------------

def test_no_zbar_means_no_barcodes(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyzbar", None)  # import fails, as without libzbar
    assert ocr.decode_barcodes(Image.new("L", (100, 100))) == []
    assert not ocr.barcodes_available()


def test_a_broken_zbar_does_not_stop_the_reading(monkeypatch, caplog):
    import types

    calls = []

    def decode(img, symbols):
        calls.append(img.mode)
        raise RuntimeError("zbar crashed")

    fake = types.ModuleType("pyzbar.pyzbar")
    fake.decode = decode
    fake.ZBarSymbol = types.SimpleNamespace(EAN13="EAN13", UPCA="UPCA")
    pkg = types.ModuleType("pyzbar")
    pkg.pyzbar = fake
    monkeypatch.setitem(sys.modules, "pyzbar", pkg)
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", fake)
    caplog.set_level("INFO", "ripshelf.scanner.ocr")
    assert ocr.decode_barcodes(Image.new("RGB", (100, 100))) == []
    assert calls == ["L"] and "zbar failed: zbar crashed" in caplog.text
    assert ocr.barcodes_available()


def test_barcodes_are_tried_again_with_more_contrast(monkeypatch):
    import types

    seen = []

    def decode(img, symbols):
        seen.append(img.getextrema())
        if len(seen) == 1:
            return []  # too faint as it is
        return [types.SimpleNamespace(data=b"5012345678900"), types.SimpleNamespace(data=b"5012345678900")]

    fake = types.ModuleType("pyzbar.pyzbar")
    fake.decode = decode
    fake.ZBarSymbol = types.SimpleNamespace(EAN13="EAN13", UPCA="UPCA")
    pkg = types.ModuleType("pyzbar")
    pkg.pyzbar = fake
    monkeypatch.setitem(sys.modules, "pyzbar", pkg)
    monkeypatch.setitem(sys.modules, "pyzbar.pyzbar", fake)
    faint = Image.new("L", (100, 100), 140)
    faint.paste(120, (10, 10, 50, 50))
    assert ocr.decode_barcodes(faint) == ["5012345678900"], "once, though found twice"
    assert seen == [(120, 140), (0, 255)]

