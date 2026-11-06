"""A network scanner over eSCL (Apple's AirScan), which most network printers speak.

A scan is a job: ``POST <base>/ScanJobs`` with the settings as XML answers 201 with
the job's address in ``Location``, then each ``GET <job>/NextDocument`` returns one
page until a 404 says there are no more (the glass gives one page, the feeder one per
side). 503 means busy: the scanner is still warming up or scanning. ``DELETE <job>``
cancels it.

Sizes are in 300ths of an inch. Plain HTTP is used where it works: Brother's MFCs
(and others) accept TLS on 443 and then close the connection without answering.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

log = logging.getLogger(__name__)

NS = {
    "scan": "http://schemas.hp.com/imaging/escl/2011/05/03",
    "pwg": "http://www.pwg.org/schemas/2010/12/sm",
}
TIMEOUT_S = 10
PAGE_TIMEOUT_S = 180  # a 600 dpi colour page from a slow scanner
BUSY_RETRIES = 10
BUSY_WAIT_S = 2.0
A4_HEIGHT = 3508  # 297 mm; the feeder's longest page is legal size, much longer than an insert

# What the feeder's states mean, for the messages when a scan fails.
ADF_PROBLEMS = {
    "ScannerAdfEmpty": "The feeder is empty: put the pages in it face up",
    "ScannerAdfJam": "The paper is jammed in the feeder",
    "ScannerAdfDoorOpen": "The feeder's cover is open",
    "ScannerAdfHatchOpen": "The feeder's cover is open",
    "ScannerAdfMispick": "The feeder didn't pick up the page",
    "ScannerAdfMultipickDetected": "The feeder took more than one page at once",
    "ScannerAdfInputTrayFailed": "The feeder failed",
}


class ScannerError(Exception):
    """A scan that couldn't be done, with a message for the user. ``code`` is busy,
    offline, empty, problem or bad_reply."""

    def __init__(self, message: str, code: str = "problem"):
        super().__init__(message)
        self.code = code


@dataclass
class Source:
    max_width: int
    max_height: int
    resolutions: list[int]
    color_modes: list[str]
    formats: list[str]


@dataclass
class Capabilities:
    model: str | None = None
    version: str = "2.0"
    platen: Source | None = None
    feeder: Source | None = None
    duplex: bool = False
    admin_url: str | None = None

    @property
    def sources(self) -> list[str]:
        return [n for n, s in (("platen", self.platen), ("feeder", self.feeder)) if s]

    def source(self, name: str) -> Source | None:
        return self.platen if name == "platen" else self.feeder


@dataclass
class Status:
    state: str | None = None
    adf_state: str | None = None
    jobs: dict[str, str] = field(default_factory=dict)  # job uuid -> state


def base_url(url: str) -> str:
    """``http://printer/eSCL`` from what the user typed: a bare address, the printer's
    page, or the eSCL address with or without a trailing slash."""
    url = url.strip().rstrip("/")
    if not url:
        raise ValueError("the scanner's address is empty")
    if "://" not in url:
        url = "http://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{url} is not a scanner's address")
    path = parts.path.rstrip("/")
    if not path.lower().endswith("/escl"):
        path += "/eSCL"
    return f"{parts.scheme}://{parts.netloc}{path}"


def _text(el: ET.Element | None, path: str) -> str | None:
    found = el.find(path, NS) if el is not None else None
    return found.text.strip() if found is not None and found.text else None


def _int(el: ET.Element | None, path: str, default: int = 0) -> int:
    try:
        return int(_text(el, path) or default)
    except ValueError:
        return default


def _source(caps: ET.Element | None) -> Source | None:
    if caps is None:
        return None
    res = sorted({_int(r, "scan:XResolution") for r in caps.iterfind(".//scan:DiscreteResolution", NS)} - {0})
    if not res:  # a range instead of a list
        lo = _int(caps, ".//scan:XResolutionRange/scan:Min", 75)
        hi = _int(caps, ".//scan:XResolutionRange/scan:Max", 600)
        res = [r for r in (75, 100, 150, 200, 300, 400, 600, 1200) if lo <= r <= hi]
    return Source(
        max_width=_int(caps, "scan:MaxWidth", 2550),
        max_height=_int(caps, "scan:MaxHeight", 3508),
        resolutions=res,
        color_modes=[e.text for e in caps.iterfind(".//scan:ColorMode", NS) if e.text],
        formats=sorted({e.text for e in caps.iterfind(".//pwg:DocumentFormat", NS) if e.text}
                       | {e.text for e in caps.iterfind(".//scan:DocumentFormatExt", NS) if e.text}),
    )


def parse_capabilities(xml: bytes) -> Capabilities:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ScannerError(f"The scanner's capabilities aren't readable XML ({e})", "bad_reply") from None
    adf = root.find("scan:Adf", NS)
    return Capabilities(
        model=_text(root, "pwg:MakeAndModel"),
        version=_text(root, "pwg:Version") or "2.0",
        platen=_source(root.find("scan:Platen/scan:PlatenInputCaps", NS)),
        feeder=_source(adf.find("scan:AdfSimplexInputCaps", NS)) if adf is not None else None,
        duplex=adf is not None and (adf.find("scan:AdfDuplexInputCaps", NS) is not None
                                    or any(e.text == "Duplex" for e in adf.iterfind(".//scan:AdfOption", NS))),
        admin_url=_text(root, "scan:AdminURI"),
    )


def parse_status(xml: bytes) -> Status:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ScannerError(f"The scanner's status isn't readable XML ({e})", "bad_reply") from None
    jobs = {}
    for j in root.iterfind(".//scan:JobInfo", NS):
        uri = _text(j, "pwg:JobUri") or ""
        jobs[uri.rstrip("/").rsplit("/", 1)[-1]] = _text(j, "pwg:JobState") or ""
    return Status(state=_text(root, "pwg:State"), adf_state=_text(root, "scan:AdfState"), jobs=jobs)


def scan_settings(caps: Capabilities, source: str, dpi: int, duplex: bool = False,
                  color_mode: str = "RGB24", fmt: str = "image/jpeg") -> str:
    """The ScanSettings document for one job, scanning the whole of the glass (or the
    feeder's page) at ``dpi``, which must be one the source offers."""
    src = caps.source(source)
    if not src:
        raise ScannerError(f"The scanner has no {'glass' if source == 'platen' else 'feeder'}")
    if src.resolutions and dpi not in src.resolutions:
        dpi = min(src.resolutions, key=lambda r: (abs(r - dpi), -r))
    if src.color_modes and color_mode not in src.color_modes:
        color_mode = src.color_modes[-1]
    height = src.max_height if source == "platen" else min(src.max_height, A4_HEIGHT)
    duplex_el = f"<scan:Duplex>{'true' if duplex and caps.duplex else 'false'}</scan:Duplex>" \
        if source == "feeder" else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<scan:ScanSettings xmlns:scan="{NS["scan"]}" xmlns:pwg="{NS["pwg"]}">'
        f"<pwg:Version>{caps.version}</pwg:Version>"
        "<scan:Intent>Photo</scan:Intent>"
        "<pwg:ScanRegions><pwg:ScanRegion>"
        f"<pwg:Height>{height}</pwg:Height><pwg:Width>{src.max_width}</pwg:Width>"
        "<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset>"
        "<pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>"
        "</pwg:ScanRegion></pwg:ScanRegions>"
        f"<pwg:InputSource>{'Platen' if source == 'platen' else 'Feeder'}</pwg:InputSource>"
        f"{duplex_el}"
        f"<scan:ColorMode>{color_mode}</scan:ColorMode>"
        f"<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>"
        f"<pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>"
        f"<scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>"
        "</scan:ScanSettings>"
    )


def _unreachable(e: Exception) -> ScannerError:
    return ScannerError(f"The scanner didn't answer ({type(e).__name__}): is it on, and is the address right?",
                        "offline")


class Escl:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None,
                 busy_wait_s: float = BUSY_WAIT_S):
        self.base = base_url(url)
        self._transport = transport
        self.busy_wait_s = busy_wait_s

    def _client(self, timeout: float = TIMEOUT_S) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self._transport, follow_redirects=True,
                                 headers={"Accept": "*/*"})

    async def _get(self, path: str) -> bytes:
        try:
            async with self._client() as c:
                r = await c.get(f"{self.base}/{path}")
        except httpx.HTTPError as e:
            raise _unreachable(e) from None
        if r.status_code >= 400:
            raise ScannerError(f"The scanner answered HTTP {r.status_code} to {path}", "bad_reply")
        return r.content

    async def capabilities(self) -> Capabilities:
        return parse_capabilities(await self._get("ScannerCapabilities"))

    async def status(self) -> Status:
        return parse_status(await self._get("ScannerStatus"))

    async def _problem(self, source: str, fallback: str) -> ScannerError:
        """Why the scan stopped, from the scanner's status if it says."""
        try:
            st = await self.status()
        except ScannerError:
            return ScannerError(fallback)
        if source == "feeder" and st.adf_state in ADF_PROBLEMS:
            return ScannerError(ADF_PROBLEMS[st.adf_state], "empty" if st.adf_state == "ScannerAdfEmpty"
                                else "problem")
        if st.state == "Stopped":
            return ScannerError(f"{fallback}; the scanner has stopped (check its screen: a cover may be open)")
        return ScannerError(fallback)

    async def scan(self, caps: Capabilities, source: str, dpi: int, duplex: bool = False,
                   on_page: Callable[[int, bytes], Awaitable[None]] | None = None,
                   on_job: Callable[[str], None] | None = None) -> list[bytes]:
        """Scan from the glass or the feeder and return the pages, as JPEGs. ``on_page``
        is called with each page as it arrives, ``on_job`` with the job's address (to
        cancel it with ``cancel``)."""
        if source == "feeder":
            try:
                st = await self.status()
            except ScannerError:
                st = Status()
            if st.adf_state == "ScannerAdfEmpty":
                raise ScannerError(ADF_PROBLEMS["ScannerAdfEmpty"], "empty")
        body = scan_settings(caps, source, dpi, duplex)
        job = await self._start(body)
        if on_job:
            on_job(job)
        pages: list[bytes] = []
        try:
            async with self._client(PAGE_TIMEOUT_S) as c:
                busy = 0
                while True:
                    try:
                        r = await c.get(f"{job.rstrip('/')}/NextDocument")
                    except httpx.HTTPError as e:
                        raise _unreachable(e) from None
                    if r.status_code == 200 and r.content:
                        pages.append(r.content)
                        busy = 0
                        if on_page:
                            await on_page(len(pages), r.content)
                        continue
                    if r.status_code == 404:
                        break
                    if r.status_code == 503 and busy < BUSY_RETRIES * 5:
                        busy += 1
                        await asyncio.sleep(self.busy_wait_s / 2)
                        continue
                    raise await self._problem(source, f"The scanner answered HTTP {r.status_code} "
                                                      f"while scanning")
        except BaseException:
            await self.cancel(job)
            raise
        if not pages:
            raise await self._problem(source, "The scanner finished without sending a page")
        return pages

    async def _start(self, body: str) -> str:
        for attempt in range(BUSY_RETRIES):
            try:
                async with self._client() as c:
                    r = await c.post(f"{self.base}/ScanJobs", content=body.encode(),
                                     headers={"Content-Type": "text/xml"})
            except httpx.HTTPError as e:
                raise _unreachable(e) from None
            if r.status_code in (200, 201) and r.headers.get("location"):
                return urljoin(f"{self.base}/ScanJobs", r.headers["location"])
            if r.status_code == 503 and attempt < BUSY_RETRIES - 1:
                await asyncio.sleep(self.busy_wait_s)
                continue
            if r.status_code in (409, 503):
                raise ScannerError("The scanner is busy (in use from its panel or another computer)", "busy")
            raise ScannerError(f"The scanner refused the scan (HTTP {r.status_code})", "bad_reply")
        raise ScannerError("The scanner is busy", "busy")  # pragma: no cover

    async def cancel(self, job: str) -> None:
        try:
            async with self._client() as c:
                await c.delete(job)
        except httpx.HTTPError as e:
            log.info("could not cancel scan job %s: %s", job, e)
