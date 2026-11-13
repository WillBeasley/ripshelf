"""A stack of case sheets made into covers: pages from the scanner's feeder or an
upload (pictures and PDFs), paired into each sheet's two sides, blank sides dropped,
and the outside told from the inside.

How the pages pair depends on how the stack went through:

- **Both sides at once** (a duplex feeder): each sheet's two sides arrive one after
  the other, A1 B1 A2 B2...
- **Two passes** (a feeder without duplex): the stack goes through once for one side,
  then is turned over and goes through again for the other. The second pass comes out
  in reverse, so the first sheet's other side is the second pass's last page.
- **One side only**: each page is a cover of its own, with only an outside.

Nothing here keeps anything; ``shelf`` does.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageStat

from ..providers import release_info
from . import images, ocr

log = logging.getLogger(__name__)

PDF_DPI = 300  # PDF pages are drawn at this, about what the text reader wants
MAX_PDF_PAGES = 200
BLANK_SAMPLE_PX = 1000  # a side is looked at in a copy this wide, so small print survives
BLANK_SPREAD = 3  # a side whose greys vary less than this is one even colour...
BLANK_MARKS = 0.0005  # ...or one with less than this of it marked (specks of dust, not a chapter list)
MARK_LEVEL = 60  # a grey this far from the paper's is a mark: print, darker or lighter


class StackError(ValueError):
    pass


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"


def pdf_pages(data: bytes, dpi: int = PDF_DPI) -> list[bytes]:
    """Each page of a PDF as a JPEG (pypdfium2 draws them)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise StackError("PDFs can't be read here (pypdfium2 isn't installed)") from None
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise StackError(f"the PDF can't be read ({e})") from None
    try:
        if len(pdf) > MAX_PDF_PAGES:
            raise StackError(f"the PDF has {len(pdf)} pages; at most {MAX_PDF_PAGES} are taken at once")
        out = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                img = page.render(scale=dpi / 72).to_pil()
            finally:
                page.close()
            out.append(images.jpeg(img))
        return out
    finally:
        pdf.close()


# --- pairing ----------------------------------------------------------------------------


@dataclass
class Sheet:
    """One sheet's sides, in the order they came (``a`` first). Either may be None."""

    a: int | None  # an index into the pages
    b: int | None


def pair_duplex(count: int) -> list[Sheet]:
    """A1 B1 A2 B2...: an odd last page is a sheet with one side."""
    return [Sheet(i, i + 1 if i + 1 < count else None) for i in range(0, count, 2)]


def pair_passes(first: int, second: int) -> list[Sheet]:
    """The first pass's page i with the second pass's page n-1-i (the turned stack comes
    out in reverse). Indexes into the second pass are offset by ``first``, as if the
    passes were one list. The counts must be the same: one page fed twice, or two fed
    together, would shift every pair after it."""
    if first != second:
        raise StackError(f"the first pass has {first} page{_s(first)} and the second {second}")
    return [Sheet(i, first + second - 1 - i) for i in range(first)]


def pair_one_side(count: int) -> list[Sheet]:
    return [Sheet(i, None) for i in range(count)]


def split_passes(count: int) -> tuple[int, int]:
    """One file holding both passes back to back: its first half, then its second."""
    if count % 2:
        raise StackError(f"{count} pages can't be two passes of the same stack (the count is odd)")
    return count // 2, count // 2


def _s(n: int) -> str:
    return "" if n == 1 else "s"


# --- looking at a side ------------------------------------------------------------------


@dataclass
class Look:
    """What a side shows, as far as pairing cares."""

    blank: bool
    barcode: bool
    spine: bool


def is_blank(img: Image.Image) -> bool:
    """Near enough nothing printed: one even colour, or paper with hardly a mark (the
    plain inside of a single-sided sheet, specks and all). The paper is the commonest
    grey, so a black inside with white print counts as printed too."""
    small = img.convert("L")
    small.thumbnail((BLANK_SAMPLE_PX, BLANK_SAMPLE_PX))
    if ImageStat.Stat(small).stddev[0] < BLANK_SPREAD:
        return True
    hist = small.histogram()
    paper = max(range(256), key=lambda v: hist[v])
    marks = sum(n for v, n in enumerate(hist) if abs(v - paper) > MARK_LEVEL)
    return marks < BLANK_MARKS * small.width * small.height


def look(img: Image.Image) -> Look:
    """A side, trimmed: blank, a barcode on it, a spine down it (lying either way)."""
    if is_blank(img):
        return Look(blank=True, barcode=False, spine=False)
    spine = images.find_spine(img) is not None or images.find_spine(img.rotate(90, expand=True)) is not None
    return Look(blank=False, barcode=bool(ocr.decode_barcodes(img)), spine=spine)


def outside_first(a: Look | None, b: Look | None) -> bool:
    """Whether ``a`` is the outside: the side with the barcode, else the one with a
    spine; when that doesn't tell them apart, the first scanned."""
    if a is None or b is None:
        return b is None
    for clue in ("barcode", "spine"):
        if getattr(a, clue) != getattr(b, clue):
            return getattr(a, clue)
    return True


def render_trimmed(data: bytes, trim: bool) -> tuple[Image.Image, list[int] | None, int, int]:
    """The picture, its trim box (None for none), and its size before the trim."""
    img = images.open_image(data)
    box = images.trim_box(img) if trim else None
    return (img.crop(box) if box else img), box, img.width, img.height


def to_jpeg_if_needed(data: bytes) -> bytes:
    """A picture of a type the scans don't keep (TIFF, BMP...), as a JPEG."""
    if release_info.sniff(data):
        return data
    try:
        img = images.open_image(data)
    except images.ImageError:
        raise StackError("a page isn't a picture or a PDF") from None
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=images.COVER_QUALITY)
    return out.getvalue()
