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
    fills the scan (or nothing stands out). The glass's colour is the edges' median, so
    a white or a black lid both work."""
    w, h = img.size
    scale = min(1.0, TRIM_SAMPLE_PX / max(w, h))
    small = img.convert("L").resize((max(1, round(w * scale)), max(1, round(h * scale))))
    sw, sh = small.size
    px = small.load()
    edge = sorted([px[x, 0] for x in range(sw)] + [px[x, sh - 1] for x in range(sw)]
                  + [px[0, y] for y in range(sh)] + [px[sw - 1, y] for y in range(sh)])
    glass = edge[len(edge) // 2]
    diff = ImageChops.difference(small, Image.new("L", small.size, glass))
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


def thumbnail(img: Image.Image) -> bytes:
    small = img.copy()
    small.thumbnail((THUMB_PX, THUMB_PX))
    return jpeg(small, 85)


def halves(img: Image.Image, split: ScanSplit) -> tuple[Image.Image, Image.Image]:
    """(front, back) from a whole outer sheet: the back on the left, the spine, and the
    front on the right, unless swapped."""
    w = img.width
    left_end = round(w * (split.position - split.spine / 2))
    right_start = round(w * (split.position + split.spine / 2))
    left = img.crop((0, 0, max(1, left_end), img.height))
    right = img.crop((min(w - 1, right_start), 0, w, img.height))
    return (left, right) if split.swap else (right, left)
