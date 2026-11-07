"""Reading the text on a picture of the box (Tesseract), and its barcode (zbar).

The text reader is pluggable: anything with ``version()``, ``read()`` and
``orientation()`` will do. ``Tesseract`` runs the program, giving it the picture as a
PNG on stdin and reading its TSV (each word with its box and confidence).

``read_picture`` does the work for one scan:

1. **Size.** The picture is scaled to about 300 dpi for the text's sake: scanners at
   600 dpi make text too big for Tesseract, and phone photos too small.
2. **Orientation.** Tesseract's orientation check finds a picture lying on its side
   (a whole sheet scanned to fit the glass) or upside down.
3. **Parts.** A whole outer sheet is read as its back, spine and front, so the
   columns don't run into each other. The spine's text runs along it, so it is read
   turned both ways, and once as it is for the catalogue number printed across it.
4. **Two readings.** Each part is read as it is (good for scans) and with each pixel
   compared to its neighbourhood (good for photos, with glare and coloured stripes
   behind the text). The reading with more confident words wins; the other is kept
   for the release details only.
5. **Barcode.** zbar decodes EAN-13 and UPC-A bars, when it's installed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import shutil
from dataclasses import dataclass, field

from PIL import Image, ImageChops, ImageFilter, ImageOps

from .. import proc
from ..models import ScanSplit, TextLine

log = logging.getLogger(__name__)

TARGET_LONG_PX = 3400  # a DVD sheet (273 mm) at about 300 dpi
MIN_LONG_PX = 1800  # smaller pictures (photos of a corner) are scaled up
OSD_PX = 2000
OSD_MIN_CONFIDENCE = 1.5
TIMEOUT_S = 180
WORD_MIN_CONF = 45  # words below this at the ends of a line are dropped as noise
LINE_MIN_CONF = 40
SINGLE_WORD_MIN_CONF = 60
SPINE_HALF_MIN = 0.035  # the spine strip is at least this either side of its middle


class OcrUnavailable(Exception):
    pass


@dataclass
class Word:
    text: str
    conf: float
    box: tuple[int, int, int, int]


@dataclass
class Line:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def conf(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words)

    @property
    def box(self) -> tuple[int, int, int, int]:
        return (min(w.box[0] for w in self.words), min(w.box[1] for w in self.words),
                max(w.box[2] for w in self.words), max(w.box[3] for w in self.words))


@dataclass
class Reading:
    lines: list[TextLine] = field(default_factory=list)
    more: list[TextLine] = field(default_factory=list)  # the losing readings, for the details
    barcodes: list[str] = field(default_factory=list)
    turned: int = 0  # quarter turns clockwise the picture needed, in degrees


def parse_tsv(tsv: str) -> list[Line]:
    """Tesseract's TSV as lines of words, in its reading order."""
    lines: dict[tuple, list[Word]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        left, top, width, height = (int(c) for c in cols[6:10])
        key = (int(cols[2]), int(cols[3]), int(cols[4]))  # block, paragraph, line
        lines.setdefault(key, []).append(Word(text, conf, (left, top, left + width, top + height)))
    return [Line(ws) for ws in lines.values()]


def tidy(line: Line) -> Line | None:
    """Drop doubtful words at the ends (stray marks read as letters), and lines that
    are only noise."""
    ws = list(line.words)
    while ws and (ws[0].conf < WORD_MIN_CONF or not re.search(r"[A-Za-z0-9©]", ws[0].text)):
        ws.pop(0)
    while ws and (ws[-1].conf < WORD_MIN_CONF or not re.search(r"[A-Za-z0-9]", ws[-1].text)):
        ws.pop()
    if not ws:
        return None
    out = Line(ws)
    if out.conf < LINE_MIN_CONF or not re.search(r"[A-Za-z0-9]{2}", out.text):
        return None
    # A lone short word is usually a picture read as letters.
    if len(ws) == 1 and not re.fullmatch(r"\W*\d[\d\W]*", ws[0].text):
        letters = len(re.sub(r"[^A-Za-z0-9]", "", ws[0].text))
        if out.conf < SINGLE_WORD_MIN_CONF or letters < 3 and out.conf < 75:
            return None
    return out


def score(lines: list[Line]) -> int:
    """How much confident text a reading found: words of three letters or more read
    with high confidence (noise is mostly short and doubtful)."""
    return sum(len(w.text) for ln in lines for w in ln.words
               if w.conf >= 85 and len(re.sub(r"[^A-Za-z0-9]", "", w.text)) >= 3)


class Tesseract:
    def __init__(self, command: str = "tesseract", languages: str = "eng"):
        self.command = command
        self.languages = languages or "eng"
        self._version: str | None = None

    def _args(self) -> list[str]:
        return proc.split_command(self.command)

    async def version(self) -> str:
        """``tesseract 5.3.4``; OcrUnavailable if it can't be run."""
        if self._version:
            return self._version
        args = self._args()
        if not args or not (shutil.which(args[0]) or (len(args) == 1 and _is_file(args[0]))):
            raise OcrUnavailable(f"Tesseract isn't installed ({self.command} wasn't found)")
        out = await self._run([*args, "--version"], b"")
        first = (out or "").strip().splitlines()[:1]
        self._version = first[0] if first else "tesseract"
        return self._version

    async def _run(self, args: list[str], data: bytes) -> str:
        try:
            p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            raise OcrUnavailable(f"Tesseract couldn't be started ({e})") from None
        got = await proc.communicate_input(p, data, TIMEOUT_S)
        if got is None:
            raise OcrUnavailable("Tesseract took too long")
        out, err = got
        if p.returncode:
            msg = err.decode(errors="replace").strip().splitlines()[-1:] or [f"exit {p.returncode}"]
            raise OcrUnavailable(f"Tesseract failed: {msg[0]}")
        return out.decode("utf-8", errors="replace")

    async def read(self, img: Image.Image, psm: int = 3) -> list[Line]:
        tsv = await self._run([*self._args(), "stdin", "stdout", "--psm", str(psm), "-l", self.languages, "tsv"],
                              _png(img))
        return parse_tsv(tsv)

    async def orientation(self, img: Image.Image) -> tuple[int, float]:
        """(degrees to turn the picture clockwise, confidence); (0, 0) when it can't tell."""
        try:
            out = await self._run([*self._args(), "stdin", "stdout", "--psm", "0"], _png(img))
        except OcrUnavailable as e:
            log.debug("no orientation: %s", e)  # too little text, or no osd.traineddata
            return 0, 0.0
        turn = re.search(r"Rotate:\s*(\d+)", out)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", out)
        return (int(turn.group(1)) % 360 if turn else 0), (float(conf.group(1)) if conf else 0.0)


def _is_file(p: str) -> bool:
    import os

    return os.path.isfile(p)


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG", compress_level=1)
    return out.getvalue()


def to_ocr_size(img: Image.Image) -> Image.Image:
    long = max(img.size)
    if long > TARGET_LONG_PX:
        f = TARGET_LONG_PX / long
    elif long < MIN_LONG_PX:
        f = min(3.0, MIN_LONG_PX / long)
    else:
        return img
    return img.resize((max(1, round(img.width * f)), max(1, round(img.height * f))), Image.LANCZOS)


def local_contrast(img: Image.Image) -> Image.Image:
    """Black where a pixel is darker than its neighbourhood, white elsewhere: text
    stays readable on stripes, gradients and glare."""
    g = img.convert("L").filter(ImageFilter.GaussianBlur(0.8))
    mean = g.filter(ImageFilter.GaussianBlur(max(4, round(max(g.size) / 400))))
    return ImageChops.subtract(mean, g).point(lambda v: 0 if v > 12 else 255)


Box = tuple[float, float, float, float]


@dataclass
class Part:
    name: str
    box: Box  # fractions of the picture
    spine: bool = False


def parts(kind: str, size: tuple[int, int], split: ScanSplit | None) -> list[Part]:
    """The parts of the picture read separately."""
    w, h = size
    if kind == "outside_full" and w > h:
        s = split or ScanSplit()
        half = max(s.spine / 2, SPINE_HALF_MIN)
        left, right = ("front", "back") if s.swap else ("back", "front")
        return [Part(left, (0, 0, s.position - s.spine / 2, 1)),
                Part("spine", (s.position - half, 0, s.position + half, 1), spine=True),
                Part(right, (s.position + s.spine / 2, 0, 1, 1))]
    if kind == "inside_full" and w > h:
        return [Part("left", (0, 0, 0.5, 1)), Part("right", (0.5, 0, 1, 1))]
    return [Part("page", (0, 0, 1, 1))]


def _unturn(box: tuple[int, int, int, int], size: tuple[int, int], turned: int) -> tuple[int, int, int, int]:
    """A box found in a picture turned by ``turned`` degrees clockwise (from ``rotate(-turned)``),
    in the picture as it was; ``size`` is the turned picture's."""
    w, h = size
    left, top, right, bottom = box
    if turned == 90:  # turned clockwise: x' = H0 - y, y' = x, and the turned width is H0
        pts = [(y, w - x) for x, y in ((left, top), (right, bottom))]
    elif turned == 270:
        pts = [(h - y, x) for x, y in ((left, top), (right, bottom))]
    elif turned == 180:
        pts = [(w - x, h - y) for x, y in ((left, top), (right, bottom))]
    else:
        return box
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


async def read_picture(reader: Tesseract, img: Image.Image, kind: str, split: ScanSplit | None = None,
                       barcodes: bool = True) -> Reading:
    """Read one picture (already trimmed and turned as the scan says)."""
    img = img.convert("RGB")
    small = img.copy()
    small.thumbnail((OSD_PX, OSD_PX))
    turn, conf = await reader.orientation(small)
    out = Reading()
    if turn and conf >= OSD_MIN_CONFIDENCE:
        out.turned = turn
        img = img.rotate(-turn, expand=True)
    page = to_ocr_size(img)
    pw, ph = page.size
    for part in parts(kind, page.size, split):
        x0, y0 = round(part.box[0] * pw), round(part.box[1] * ph)
        crop = page.crop((x0, y0, round(part.box[2] * pw), round(part.box[3] * ph)))
        if crop.width < 20 or crop.height < 20:
            continue
        found = await (_read_spine(reader, crop) if part.spine else _read_part(reader, crop))
        readings: list[tuple[bool, list[TextLine]]] = []
        for winner, lines, turned_by, cs in found:
            got = []
            for ln in lines:
                if not (t := tidy(ln)):
                    continue
                left, top, right, bottom = _unturn(t.box, cs, turned_by)
                got.append(TextLine(text=t.text, conf=round(t.conf, 1), region=part.name,
                                    box=[round((x0 + left) / pw, 4), round((y0 + top) / ph, 4),
                                         round((x0 + right) / pw, 4), round((y0 + bottom) / ph, 4)]))
            readings.append((winner, got))
        best = [lines for winner, lines in readings if winner]
        others = [lines for winner, lines in readings if not winner]
        if part.spine:
            out.lines += [ln for lines in best for ln in lines]
        else:
            out.lines += merge(best[0], [ln for lines in others for ln in lines])
        out.more += [ln for lines in others for ln in lines]
    if barcodes:
        out.barcodes = await asyncio.to_thread(decode_barcodes, page)
    return out


def quality(line: TextLine) -> float:
    """How much of a line was read: its letters and digits, weighted by confidence."""
    return len(re.sub(r"[^A-Za-z0-9]", "", line.text)) * min(1.0, line.conf / 90)


def overlap(a: list[float], b: list[float]) -> float:
    """How much two boxes share, as a fraction of the smaller."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / smaller if smaller > 0 else 0.0


def merge(best: list[TextLine], others: list[TextLine]) -> list[TextLine]:
    """The best reading, in its order, with each line replaced by a fuller reading of
    the same spot ("Brolly" by "Brolly Bus"), and lines only another reading found put
    in after the line above them."""
    out = list(best)
    used: set[int] = set()
    for i, ln in enumerate(out):
        rivals = [(j, o) for j, o in enumerate(others) if j not in used and overlap(ln.box, o.box) > 0.5]
        for j, _ in rivals:
            used.add(j)
        if rivals:
            j, o = max(rivals, key=lambda r: quality(r[1]))
            if quality(o) > quality(ln) * 1.15 and o.conf >= 60:
                out[i] = o
    for j, o in enumerate(others):
        if j in used or o.conf < 70 or quality(o) < 6 or any(overlap(o.box, x.box) > 0.5 for x in out):
            continue
        # A piece of a line already there, read again on its own.
        squashed = re.sub(r"[^a-z0-9]", "", o.text.lower())
        if any(squashed in re.sub(r"[^a-z0-9]", "", x.text.lower()) and x.box[1] < o.box[3] and o.box[1] < x.box[3]
               for x in out):
            continue
        above = [i for i, x in enumerate(out) if x.box[1] <= o.box[1]
                 and min(x.box[2], o.box[2]) > max(x.box[0], o.box[0])]
        at = max(above, key=lambda i: out[i].box[1]) + 1 if above else len(out)
        out.insert(at, o)
    return out


async def _read_part(reader: Tesseract, crop: Image.Image):
    """As it is, and with local contrast at full and half size (text in photos is often
    too big for Tesseract at full size). The best wins; the others are kept too."""
    found = [(await reader.read(crop.convert("L"), 3), crop.size, 1.0)]
    for f in (1.0, 0.5):
        img = crop if f == 1 else crop.resize((max(1, round(crop.width * f)), max(1, round(crop.height * f))),
                                              Image.LANCZOS)
        found.append((await reader.read(local_contrast(img), 11), crop.size, f))
    best = max(range(len(found)), key=lambda i: score(found[i][0]))
    out = []
    for i, (lines, size, f) in enumerate(found):
        if f != 1:
            for ln in lines:
                for w in ln.words:
                    w.box = tuple(round(v / f) for v in w.box)
        out.append((i == best, lines, 0, size))
    return out


async def _read_spine(reader: Tesseract, crop: Image.Image):
    """Along the spine both ways (the better one wins), and across it as it is."""
    gray = crop.convert("L")
    readings = []
    for turned in (270, 90):  # 270: turned anticlockwise, the usual way a spine reads
        turned_img = gray.rotate(-turned, expand=True)
        readings.append((await reader.read(turned_img, 11), turned, turned_img.size))
    readings.sort(key=lambda r: score(r[0]), reverse=True)
    across = await reader.read(gray, 11)
    (best, t, s), (other, t2, s2) = readings
    return [(True, best, t, s), (False, other, t2, s2), (True, across, 0, gray.size)]


def decode_barcodes(img: Image.Image) -> list[str]:
    """EAN-13 and UPC-A barcodes zbar finds (none when zbar isn't installed)."""
    try:
        from pyzbar import pyzbar
    except (ImportError, OSError) as e:
        log.debug("no barcode reading: %s", e)
        return []
    found: list[str] = []
    gray = img.convert("L")
    for candidate in (gray, ImageOps.autocontrast(gray)):
        try:
            results = pyzbar.decode(candidate, symbols=[pyzbar.ZBarSymbol.EAN13, pyzbar.ZBarSymbol.UPCA])
        except Exception as e:  # a broken zbar shouldn't stop the text
            log.info("zbar failed: %s", e)
            return found
        for r in results:
            code = r.data.decode("ascii", errors="ignore")
            if code not in found:
                found.append(code)
        if found:
            break
    return found


def barcodes_available() -> bool:
    try:
        from pyzbar import pyzbar  # noqa: F401
    except (ImportError, OSError):
        return False
    return True
