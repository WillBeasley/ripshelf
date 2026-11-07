"""Finding the spine of a whole outer sheet, so it's cut into its covers without the cut
editor: two edges down the sheet near its middle, as far apart as a case's spine is."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw

from ripshelf.models import JobState, ScanSplit
from ripshelf.scanner import images

from .test_contribute import heron_job, put


def busy(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rng: random.Random) -> None:
    """Pictures and lines of text, like the back and front of a box."""
    x0, y0, x1, y1 = box
    for _ in range(40):
        w, h = rng.randint(5, (x1 - x0) // 2), rng.randint(3, (y1 - y0) // 5)
        x, y = rng.randint(x0, x1 - w), rng.randint(y0, y1 - h)
        draw.rectangle((x, y, x + w, y + h), fill=tuple(rng.randint(0, 255) for _ in range(3)))
    for y in range(y0 + 10, y1 - 10, 14):
        x = rng.randint(x0 + 5, x0 + 40)
        while x < x1 - 30:
            n = rng.randint(4, 25)
            draw.rectangle((x, y, min(x1 - 5, x + n), y + 6), fill=(20, 20, 20))
            x += n + rng.randint(4, 10)


def make_sheet(ratio: float = 273 / 183, position: float = 0.505, spine: float = 14 / 273, w: int = 1400,
               seed: int = 1, plain_spine: bool = False) -> Image.Image:
    rng = random.Random(seed)
    h = round(w / ratio)
    img = Image.new("RGB", (w, h), (230, 210, 40))
    d = ImageDraw.Draw(img)
    a, b = round(w * (position - spine / 2)), round(w * (position + spine / 2))
    busy(d, (0, 0, a, h), rng)
    busy(d, (b, 0, w, h), rng)
    if not plain_spine:
        d.rectangle((a, 0, b, h), fill=(150, 20, 30))
        for y in range(40, h - 40, 30):  # the title along the spine
            d.rectangle((a + 4, y, b - 4, y + 18), fill=(250, 250, 250))
    return img


@pytest.mark.parametrize("ratio, position, spine", [
    (273 / 183, 0.505, 14 / 273),  # a DVD
    (275 / 150, 0.5, 11.5 / 275),  # a Blu-ray
    (273 / 183, 0.47, 0.06),  # off the middle, and a wider spine (a two-disc case)
])
def test_the_spine_is_found(ratio, position, spine):
    for seed in range(3):
        got = images.find_spine(make_sheet(ratio, position, spine, seed=seed))
        assert got and got.auto, seed
        assert abs(got.position - position) < 0.01 and abs(got.spine - spine) < 0.01, (seed, got)


def test_no_spine_no_cut():
    rng = random.Random(4)
    img = Image.new("RGB", (1400, 940), (230, 210, 40))
    busy(ImageDraw.Draw(img), (0, 0, 1400, 940), rng)  # one picture right across: no spine
    assert images.find_spine(img) is None
    assert images.find_spine(make_sheet().rotate(90, expand=True)) is None  # sideways: not a sheet's shape
    assert images.find_spine(Image.new("RGB", (1400, 940), (200, 200, 200))) is None


def png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


async def test_a_sheet_is_cut_at_its_spine_when_added(harness, settings):
    settings.thediscdb_contribute = "ask"
    async with harness() as h:
        job = put(h, heron_job(identity=None, identity_source=None, state=JobState.review))
        sc = await h.m.add_scan(job.id, png(make_sheet(position=0.47)), "outside_full", "upload")
        assert sc.split and sc.split.auto and abs(sc.split.position - 0.47) < 0.01
        # Turned sideways it has no spine to find; turned back, it's found again.
        sc = await h.m.update_scan(job.id, sc.id, _update(rotate=90))
        assert sc.split is None
        sc = await h.m.update_scan(job.id, sc.id, _update(rotate=0))
        assert sc.split and sc.split.auto
        # Confirmed: the covers are cut there (the back is left of the spine).
        h.db.save_job(heron_job(job.id, scans=h.db.get_job(job.id).scans))
        view = h.m.contribution_view(job.id)
        assert view.contribution.covers == ["back", "front"]
        back = Image.open(h.m.cover_file(job.id, "back"))
        assert abs(back.width / (make_sheet().width * (0.47 - sc.split.spine / 2)) - 1) < 0.02
        # Moved by hand, the cut is the user's from then on.
        await h.m.scan_covers(job.id, sc.id, ScanSplit(position=0.48, spine=0.05, auto=True))
        assert h.db.get_job(job.id).scans[0].split.auto is False


async def test_a_sheet_with_no_clear_spine_waits_for_a_cut(harness, settings):
    rng = random.Random(5)
    img = Image.new("RGB", (1400, 940), (230, 210, 40))
    busy(ImageDraw.Draw(img), (0, 0, 1400, 940), rng)
    async with harness() as h:
        job = put(h, heron_job(identity=None, identity_source=None, state=JobState.review))
        sc = await h.m.add_scan(job.id, png(img), "outside_full", "upload")
        assert sc.split is None
        settings.thediscdb_contribute = "ask"
        h.db.save_job(heron_job(job.id, scans=h.db.get_job(job.id).scans))
        assert h.m.contribution_view(job.id).contribution.covers == []  # not cut on a guess
        view = await h.m.scan_covers(job.id, sc.id, ScanSplit(position=0.5))  # "Cut into front and back covers"
        assert view.contribution.covers == ["back", "front"]


def _update(**kw):
    from ripshelf.models import ScanUpdate

    return ScanUpdate(**kw)
