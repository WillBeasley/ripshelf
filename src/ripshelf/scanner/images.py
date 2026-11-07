"""Working with the pictures of a box: their size, trimming the empty glass around a
scan, turning them, cutting a whole sheet into its covers, and thumbnails.

The original file is never changed. A scan's trim and turn are kept as numbers and
applied to the copies made from it (the thumbnail and the covers), in this order: the
camera's own orientation (EXIF), the trim, then the turn.
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
TRIM_MARGIN = 0.01


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
    near-white or near-black colour, so a white lid and an open one both work."""
    w, h = img.size
    scale = min(1.0, TRIM_SAMPLE_PX / max(w, h))
    small = img.convert("RGB").resize((max(1, round(w * scale)), max(1, round(h * scale))))
    sw, sh = small.size
    px = small.load()
    sides = [[px[x, 0] for x in range(sw)], [px[x, sh - 1] for x in range(sw)],
             [px[0, y] for y in range(sh)], [px[sw - 1, y] for y in range(sh)]]
    glass = None
    # The glass shows along at least one side: one even colour there, near white (the
    # lid) or near black (the lid left open). A picture to its edges has neither.
    for side in sides:
        median = tuple(sorted(p[c] for p in side)[len(side) // 2] for c in range(3))
        even = sum(max(abs(p[c] - median[c]) for c in range(3)) <= TRIM_DIFF for p in side) >= 0.9 * len(side)
        neutral = max(median) - min(median) <= 30 and (min(median) >= 190 or max(median) <= 50)
        if even and neutral:
            glass = median
            break
    if glass is None:
        return None
    # How far each pixel is from the glass's colour, in whichever channel differs most.
    r, g, b = ImageChops.difference(small, Image.new("RGB", small.size, glass)).split()
    diff = ImageChops.lighter(ImageChops.lighter(r, g), b)
    mask = diff.point(lambda v: 255 if v > TRIM_DIFF else 0).filter(ImageFilter.MedianFilter(5))
    box = mask.getbbox()
    if not box:
        return None
    bw, bh = box[2] - box[0], box[3] - box[1]
    if bw * bh < 0.05 * sw * sh or (bw > 0.97 * sw and bh > 0.97 * sh):
        return None
    m = round(TRIM_MARGIN * max(sw, sh))
    left, top = max(0, box[0] - m), max(0, box[1] - m)
    right, bottom = min(sw, box[2] + m), min(sh, box[3] + m)
    return [round(left / scale), round(top / scale), min(w, round(right / scale)), min(h, round(bottom / scale))]


def render(data: bytes, crop: list[int] | None, rotate: int) -> Image.Image:
    img = open_image(data)
    if crop:
        left, top, right, bottom = crop
        if 0 <= left < right <= img.width and 0 <= top < bottom <= img.height:
            img = img.crop((left, top, right, bottom))
    if rotate:
        img = img.rotate(-rotate, expand=True)  # PIL turns anticlockwise
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
