"""Working with the pictures of a box: their size, trimming the empty glass around a
scan, turning them, cutting a whole sheet into its covers, and thumbnails.

The original file is never changed. A scan's trim and turn are kept as numbers and
applied to the copies made from it (the thumbnail and the covers), in this order: the
camera's own orientation (EXIF), the straightening (a shelf cover's), the trim, the
turn, then the mirroring (a shelf cover's).
"""

from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageFilter, ImageOps

from ..models import ScanSplit

THUMB_PX = 480
VIEW_PX = 1600
COVER_QUALITY = 92
TRIM_SAMPLE_PX = 600  # trimming looks at a copy this wide
TRIM_DIFF = 40  # how far from the glass's colour a pixel must be to count as the picture
TRIM_EDGE = (0.01, 0.04)  # the glass's colour is taken this far in from a side, past its very edge (where
# there's often a line or shadow, which is never taken as the picture)
TRIM_FILL = 0.02  # a row or column this much picture is the picture's...
TRIM_RUN = 0.01  # ...when this many (of the scan's width or height) are together; a thinner line is a shadow
TRIM_FAINT = 0.005  # less than this of a row or column is a speck, not the picture's small print
TRIM_MARGIN = 0.004


class ImageError(ValueError):
    pass


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise ImageError(f"the picture can't be read ({e})") from None
    return ImageOps.exif_transpose(img)


def size(data: bytes) -> tuple[int, int]:
    """Width and height as shown, after the camera's orientation."""
    return open_image(data).size


def trim_box(img: Image.Image) -> list[int] | None:
    """Where the picture is on the glass, as [left, top, right, bottom], or None when it
    fills the scan (or nothing stands out). The glass is a side of the scan in one even
    near-white or near-black colour, so a white lid and an open one both work.

    Each side is cut on its own, to where solid rows or columns of the picture start:
    specks of dust, and the thin shadow along the glass's edge, aren't the picture. From
    there it goes out again over anything fainter short of the glass's very edge, so a
    pale cover's small print, or a corner of a sheet laid a little askew, isn't cut."""
    w, h = img.size
    scale = min(1.0, TRIM_SAMPLE_PX / max(w, h))
    small = img.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))))
    sw, sh = small.size
    glass = _glass(small)
    if glass is None:
        return None
    # How far each pixel is from the glass's colour, in whichever channel differs most.
    r, g, b = ImageChops.difference(small, Image.new("RGB", small.size, glass)).split()
    diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
    faint = diff.point(lambda v: 255 if v > TRIM_DIFF else 0)
    mask = faint.filter(ImageFilter.MedianFilter(5))  # without the specks and lines
    cols, rows = _fills(mask, 0), _fills(mask, 1)
    left, right, top, bottom = _core(cols), _core(cols[::-1]), _core(rows), _core(rows[::-1])
    if None in (left, right, top, bottom):
        return None
    right, bottom = sw - right, sh - bottom
    # Out again over the picture's faint parts (its small print went with the specks),
    # looking only across the picture, so a shadow down one side doesn't count along
    # the other.
    cols = _fills(faint.crop((0, top, sw, bottom)), 0)
    rows = _fills(faint.crop((left, 0, right, sh)), 1)
    left, right = _out(cols, left), sw - _out(cols[::-1], sw - right)
    top, bottom = _out(rows, top), sh - _out(rows[::-1], sh - bottom)
    bw, bh = right - left, bottom - top
    if bw * bh < 0.05 * sw * sh or (bw > 0.97 * sw and bh > 0.97 * sh):
        return None
    m = round(TRIM_MARGIN * max(sw, sh))
    left, top = max(0, left - m), max(0, top - m)
    right, bottom = min(sw, right + m), min(sh, bottom + m)
    return [round(left / scale), round(top / scale), min(w, round(right / scale)), min(h, round(bottom / scale))]


def _glass(small: Image.Image) -> tuple[int, ...] | None:
    """The glass's colour, from a side where it shows: one even colour, near white (the
    lid) or near black (the lid left open). A picture to its edges has neither. It's
    looked for a little way in, past the line or shadow often at the glass's very edge."""
    sw, sh = small.size
    a, b = TRIM_EDGE
    x0, x1 = min(sw - 1, round(sw * a)), max(round(sw * a) + 1, round(sw * b))
    y0, y1 = min(sh - 1, round(sh * a)), max(round(sh * a) + 1, round(sh * b))
    strips = [(0, y0, sw, y1), (0, sh - y1, sw, sh - y0), (x0, 0, x1, sh), (sw - x1, 0, sw - x0, sh)]
    for box in strips:
        bands = [c.tobytes() for c in small.crop(box).split()]
        median = tuple(sorted(c)[len(c) // 2] for c in bands)
        side = list(zip(*bands, strict=True))
        even = sum(max(abs(p[c] - median[c]) for c in range(3)) <= TRIM_DIFF for p in side) >= 0.9 * len(side)
        neutral = max(median) - min(median) <= 30 and (min(median) >= 190 or max(median) <= 50)
        if even and neutral:
            return median
    return None


def _fills(mask: Image.Image, axis: int) -> list[float]:
    """How much of each column (axis 0) or row (axis 1) is the picture's."""
    size = (mask.width, 1) if axis == 0 else (1, mask.height)
    return [v / 255 for v in mask.resize(size, Image.Resampling.BOX).tobytes()]


def _core(fills: list[float]) -> int | None:
    """How far in from a side (the start of ``fills``) the picture's solid part starts;
    None when it doesn't."""
    run, need = 0, max(3, round(TRIM_RUN * len(fills)))
    for i, f in enumerate(fills):
        run = run + 1 if f >= TRIM_FILL else 0
        if run >= need:
            return i - need + 1
    return None


def _out(fills: list[float], core: int) -> int:
    """From the solid part's start back towards the side, to the last of the picture
    there (short of the glass's very edge): its small print, or a corner of a sheet
    laid a little askew."""
    edge = core
    for i in range(core - 1, round(TRIM_EDGE[0] * len(fills)) - 1, -1):
        if fills[i] >= TRIM_FAINT:
            edge = i
    return edge


def crop_from_shown(box: list[float], width: int, height: int, rotate: int, flip: bool = False) -> list[int]:
    """A crop drawn on the whole scan as shown (turned clockwise by ``rotate``, then
    mirrored left to right with ``flip``), as fractions [left, top, right, bottom], in
    the original's pixels (``width`` by ``height``, before the turn)."""
    left, top, right, bottom = box
    if flip:
        left, right = 1 - right, 1 - left
    left, top, right, bottom = {
        0: (left, top, right, bottom),
        90: (top, 1 - right, bottom, 1 - left),
        180: (1 - right, 1 - bottom, 1 - left, 1 - top),
        270: (1 - bottom, left, 1 - top, right),
    }[rotate]
    return [round(left * width), round(top * height), round(right * width), round(bottom * height)]


def render(data: bytes, crop: list[int] | None, rotate: int, flip: bool = False,
           straighten: float = 0) -> Image.Image:
    """The picture as used: straightened by ``straighten`` degrees clockwise (within its
    own frame, the corners it turns out of filled white), trimmed to ``crop``, turned
    clockwise by ``rotate`` and mirrored left to right with ``flip``."""
    img = open_image(data)
    if straighten:
        img = img.convert("RGB").rotate(-straighten, Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
    if crop:
        left, top, right, bottom = crop
        if 0 <= left < right <= img.width and 0 <= top < bottom <= img.height:
            img = img.crop((left, top, right, bottom))
    if rotate:
        img = img.rotate(-rotate, expand=True)  # PIL turns anticlockwise
    if flip:
        img = ImageOps.mirror(img)
    return img.convert("RGB")


def jpeg(img: Image.Image, quality: int = COVER_QUALITY) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def thumbnail(img: Image.Image, px: int = THUMB_PX) -> bytes:
    small = img.copy()
    small.thumbnail((px, px))
    return jpeg(small, 85)


# A case's sheet (its width over its height) and its spine (over its width): a DVD's
# Amaray sheet is about 273 x 183 mm with a 14 mm spine, a Blu-ray's (and a 4K's) about
# 275 x 150 mm with an 11.5 mm one.
SHEETS = [(273 / 183, 14 / 273), (275 / 150, 11.5 / 275)]
SPINE_SAMPLE_PX = 600  # the spine is looked for in a copy this wide
SPINE_EDGE_DIFF = 40  # a change of colour this big, across two pixels, is an edge
SPINE_EDGE_MIN = 0.4  # an edge of the spine runs down at least this much of the sheet
SPINE_EDGE_LIFT = 1.8  # and stands out this much from the sheet's columns around it


def find_spine(img: Image.Image) -> ScanSplit | None:
    """Where a whole outer sheet (turned the right way up) folds: the spine, found as two
    edges running down the sheet near its middle, as far apart as a case's spine is
    (from the sheet's shape). None when the picture isn't a sheet's shape or the edges
    aren't clear, so it isn't cut on a guess."""
    ratio = img.width / max(1, img.height)
    if not 1.3 <= ratio <= 2.05:
        return None
    spine0 = min(SHEETS, key=lambda s: abs(s[0] - ratio))[1]
    w = SPINE_SAMPLE_PX
    h = max(8, round(img.height * w / img.width))
    small = img.convert("RGB").resize((w, h), Image.Resampling.BILINEAR)
    small = small.crop((0, round(h * 0.05), w, round(h * 0.95)))  # not the sheet's own top and bottom
    # Per column, how much of the sheet changes colour sharply there: an edge down the
    # whole sheet scores near 1, a picture's or text's edges much less.
    diff = ImageChops.difference(small.crop((0, 0, w - 2, small.height)), small.crop((2, 0, w, small.height)))
    r, g, b = diff.split()
    strong = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v > SPINE_EDGE_DIFF else 0)
    cols = [v / 255 for v in strong.resize((w - 2, 1), Image.Resampling.BOX).tobytes()]

    def at(x: int) -> float:  # an edge may fall between columns
        return max(cols[max(0, x - 1):x + 2])

    lo, hi = round(w * 0.35), round(w * 0.65)
    narrow, wide = max(3, round(w * spine0 * 0.6)), round(w * spine0 * 1.5)
    best = None
    for a in range(lo, hi):
        for b in range(a + narrow, min(hi + wide, a + wide + 1, w - 3)):
            edge = min(at(a), at(b))
            # The nearer the middle and the nearer a case's spine, the likelier.
            mid = (a + b + 2) / 2 / w
            score = edge * (1 - abs(mid - 0.5)) * (1 - 0.5 * abs((b - a) / w - spine0) / spine0)
            if not best or score > best[0]:
                best = (score, a, b, edge)
    if not best:
        return None
    _, a, b, edge = best
    around = sorted(cols[lo:hi])
    typical = around[len(around) // 2]
    if edge < SPINE_EDGE_MIN or edge < SPINE_EDGE_LIFT * typical:
        return None
    # Column x is the change between pixels x and x + 2, so the edge is at x + 1.
    left, right = (a + 1) / w, (b + 1) / w
    split = ScanSplit(position=round((left + right) / 2, 4), spine=round(right - left, 4), auto=True)
    return split if 0.2 <= split.position <= 0.8 and split.spine <= 0.2 else None


def halves(img: Image.Image, split: ScanSplit) -> tuple[Image.Image, Image.Image]:
    """(front, back) from a whole outer sheet: the back on the left, the spine, and the
    front on the right, unless swapped."""
    w = img.width
    left_end = round(w * (split.position - split.spine / 2))
    right_start = round(w * (split.position + split.spine / 2))
    left = img.crop((0, 0, max(1, left_end), img.height))
    right = img.crop((min(w - 1, right_start), 0, w, img.height))
    return (left, right) if split.swap else (right, left)
