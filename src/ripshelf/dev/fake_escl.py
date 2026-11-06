"""A pretend network scanner (eSCL) for UI development and tests.

With fake hardware it answers at ``/fake-escl/eSCL``. Its glass holds the unfolded
cover sheet of a made-up DVD, lying sideways as it would to fit, and its feeder a few
insert pages. It describes itself as a Brother MFC like the real one ripshelf was
built against, with the same sizes and resolutions.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, Request, Response
from PIL import Image, ImageDraw, ImageFont

NS = ('xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" '
      'xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"')
GLASS = (2550, 3507)  # 300ths of an inch: A4/Letter
FEEDER = (2550, 4200)


def _source(tag: str, size: tuple[int, int]) -> str:
    res = "".join(f"<scan:DiscreteResolution><scan:XResolution>{r}</scan:XResolution>"
                  f"<scan:YResolution>{r}</scan:YResolution></scan:DiscreteResolution>" for r in (100, 200, 300, 600))
    return (f"<scan:{tag}><scan:MinWidth>16</scan:MinWidth><scan:MaxWidth>{size[0]}</scan:MaxWidth>"
            f"<scan:MinHeight>16</scan:MinHeight><scan:MaxHeight>{size[1]}</scan:MaxHeight>"
            "<scan:SettingProfiles><scan:SettingProfile><scan:ColorModes>"
            "<scan:ColorMode>BlackAndWhite1</scan:ColorMode><scan:ColorMode>Grayscale8</scan:ColorMode>"
            "<scan:ColorMode>RGB24</scan:ColorMode></scan:ColorModes><scan:DocumentFormats>"
            "<pwg:DocumentFormat>application/pdf</pwg:DocumentFormat><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>"
            "<scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt></scan:DocumentFormats>"
            f"<scan:SupportedResolutions><scan:DiscreteResolutions>{res}</scan:DiscreteResolutions>"
            f"</scan:SupportedResolutions></scan:SettingProfile></scan:SettingProfiles></scan:{tag}>")


CAPABILITIES = (
    f'<?xml version="1.0" encoding="UTF-8"?><scan:ScannerCapabilities {NS}>'
    "<pwg:Version>2.62</pwg:Version><pwg:MakeAndModel>Brother MFC-L8690CDW series (simulated)</pwg:MakeAndModel>"
    f"<scan:Platen>{_source('PlatenInputCaps', GLASS)}</scan:Platen>"
    f"<scan:Adf>{_source('AdfSimplexInputCaps', FEEDER)}{_source('AdfDuplexInputCaps', FEEDER)}"
    "<scan:FeederCapacity>20</scan:FeederCapacity><scan:AdfOptions><scan:AdfOption>DetectPaperLoaded</scan:AdfOption>"
    "<scan:AdfOption>Duplex</scan:AdfOption></scan:AdfOptions></scan:Adf></scan:ScannerCapabilities>"
)


def _font(size: int):
    try:
        return ImageFont.load_default(size)
    except TypeError:  # pragma: no cover - Pillow before 10.1
        return ImageFont.load_default()


def cover_sheet(dpi: int) -> Image.Image:
    """The outside of a DVD's cover sheet (273 × 184 mm): back, spine, then front."""
    px = dpi / 25.4
    w, h, spine = round(273 * px), round(184 * px), round(14 * px)
    back_end = (w - spine) // 2
    img = Image.new("RGB", (w, h), (24, 32, 64))
    d = ImageDraw.Draw(img)
    d.rectangle((back_end, 0, back_end + spine, h), fill=(200, 160, 60))
    front = back_end + spine
    d.ellipse((front + w // 8, h // 8, front + w // 8 + h // 3, h // 8 + h // 3), fill=(240, 236, 210))
    d.text((front + w // 30, h * 2 // 3), "MOONLIT\nORCHARD", fill=(250, 250, 250), font=_font(round(h / 9)))
    d.text((w // 30, h // 12), "A quiet orchard. A loud summer.", fill=(220, 220, 220), font=_font(round(h / 28)))
    for i in range(8):
        y = h // 4 + i * h // 18
        d.rectangle((w // 30, y, back_end - w // 30, y + h // 60), fill=(120, 130, 160))
    d.rectangle((w // 30, h - h // 6, w // 30 + w // 9, h - h // 20), fill=(250, 250, 250))  # the barcode
    return img


def glass_scan(dpi: int, inside: bool = False) -> bytes:
    """The glass, white where the lid shows, with the sheet turned to fit."""
    gw, gh = round(GLASS[0] * dpi / 300), round(GLASS[1] * dpi / 300)
    glass = Image.new("RGB", (gw, gh), (246, 246, 244))
    sheet = cover_sheet(dpi)
    if inside:
        sheet = Image.new("RGB", sheet.size, (250, 248, 240))
        d = ImageDraw.Draw(sheet)
        d.text((sheet.width // 20, sheet.height // 10), "Chapters\n1. The first blossom\n2. Ladders\n"
               "3. The storm\nSpecial features\nThe making of Moonlit Orchard", fill=(30, 30, 30),
               font=_font(round(sheet.height / 16)))
    sheet = sheet.rotate(90, expand=True)
    off = round(30 * dpi / 300)
    glass.paste(sheet, (off, off))
    out = io.BytesIO()
    glass.save(out, "JPEG", quality=85)
    return out.getvalue()


def insert_page(dpi: int, n: int) -> bytes:
    w, h = round(1500 * dpi / 300), round(1650 * dpi / 300)  # a booklet page, 127 × 140 mm
    img = Image.new("RGB", (w, h), (252, 252, 250))
    d = ImageDraw.Draw(img)
    d.text((w // 12, h // 12), f"Moonlit Orchard\nbooklet page {n}", fill=(20, 20, 20), font=_font(round(h / 14)))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


@dataclass
class FakeJob:
    pages: list[bytes]
    state: str = "Processing"


@dataclass
class FakeScanner:
    feeder_pages: int = 3
    busy_posts: int = 0  # answer this many scans with 503 first
    busy_pages: int = 0  # and this many page requests
    page_delay_s: float = 0.0
    inside: bool = False  # the glass holds the inside of the sheet
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    settings: list[str] = field(default_factory=list)  # every ScanSettings posted
    cancelled: list[str] = field(default_factory=list)

    @property
    def adf_state(self) -> str:
        return "ScannerAdfLoaded" if self.feeder_pages else "ScannerAdfEmpty"

    def router(self) -> APIRouter:
        r = APIRouter()

        @r.get("/eSCL/ScannerCapabilities")
        async def caps():
            return Response(CAPABILITIES, media_type="text/xml")

        @r.get("/eSCL/ScannerStatus")
        async def status():
            state = "Processing" if any(j.state == "Processing" for j in self.jobs.values()) else "Idle"
            jobs = "".join(f"<scan:JobInfo><pwg:JobUri>/eSCL/ScanJobs/{k}</pwg:JobUri>"
                           f"<pwg:JobState>{'Completed' if j.state == 'Completed' else j.state}</pwg:JobState>"
                           "</scan:JobInfo>" for k, j in self.jobs.items())
            return Response(f'<?xml version="1.0" encoding="UTF-8"?><scan:ScannerStatus {NS}>'
                            f"<pwg:Version>2.62</pwg:Version><pwg:State>{state}</pwg:State>"
                            f"<scan:AdfState>{self.adf_state}</scan:AdfState><scan:Jobs>{jobs}</scan:Jobs>"
                            "</scan:ScannerStatus>", media_type="text/xml")

        @r.post("/eSCL/ScanJobs")
        async def start(request: Request):
            body = (await request.body()).decode()
            self.settings.append(body)
            if self.busy_posts:
                self.busy_posts -= 1
                return Response(status_code=503)
            dpi = int(body.split("<scan:XResolution>")[1].split("<")[0])
            if "<pwg:InputSource>Feeder" in body:
                sides = 2 if "<scan:Duplex>true" in body else 1
                pages = [insert_page(dpi, i + 1) for i in range(self.feeder_pages * sides)]
            else:
                pages = [await asyncio.to_thread(glass_scan, dpi, self.inside)]
            job = uuid.uuid4().hex
            self.jobs[job] = FakeJob(pages)
            return Response(status_code=201, headers={"Location": f"/eSCL/ScanJobs/{job}"})

        @r.get("/eSCL/ScanJobs/{job}/NextDocument")
        async def next_page(job: str):
            j = self.jobs.get(job)
            if not j or j.state != "Processing":
                return Response(status_code=404)
            if self.busy_pages:
                self.busy_pages -= 1
                return Response(status_code=503)
            if self.page_delay_s:
                await asyncio.sleep(self.page_delay_s)
            if not j.pages:
                j.state = "Completed"
                return Response(status_code=404)
            return Response(j.pages.pop(0), media_type="image/jpeg")

        @r.delete("/eSCL/ScanJobs/{job}")
        async def cancel(job: str):
            if job in self.jobs:
                self.jobs[job].state = "Canceled"
                self.cancelled.append(job)
            return Response(status_code=200)

        return r
