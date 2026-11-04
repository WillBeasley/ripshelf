"""The release details TheDiscDB asks for when a disc is contributed, found for the user.

TheDiscDB's form wants the release's UPC, ASIN, release date, locale and region code,
plus pictures of the front and back cover. ripshelf works out what it can:

- **Barcode lookups.** UPCitemdb's free API (no key, about 100 lookups a day) turns a
  barcode into the product's title, ASIN and pictures, and searches its listings by
  title and format when there is no barcode yet.
- **ASIN from a web search.** With SearXNG set up (Settings → Identification), the
  barcode, or the title and format, is searched for on Amazon's site for the locale,
  and the ``/dp/<ASIN>`` in the result addresses is used.
- **Release date.** TMDB's release dates of type 5 (physical) for the locale's country,
  preferring one whose note names the disc's format.
- **Region.** A DVD's regions are read from its VIDEO_TS.IFO. For Blu-rays it's a guess
  from the locale (A for the Americas and East Asia, B for Europe, Africa and Oceania),
  and UHD discs are region free.
- **Locale.** From the barcode's GS1 prefix (50 is the UK, 00 to 13 the US and
  Canada), otherwise the TMDB language setting.

Nothing is looked up unless the user asks, on the job's TheDiscDB panel, and only the
barcode, or the title and format, leaves ripshelf.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .. import __version__
from ..models import DiscKind, ReleaseProduct

log = logging.getLogger(__name__)

UPCITEMDB = "https://api.upcitemdb.com/prod/trial"
TIMEOUT_S = 15
MAX_PRODUCTS = 8
MAX_COVER_BYTES = 20 * 1024 * 1024  # TheDiscDB's limit
COVER_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
UPC = re.compile(r"\d{12,13}")
ASIN = re.compile(r"[A-Z0-9]{10}")
_DP = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?#]|$)")
FORMAT_WORDS = {DiscKind.dvd: "DVD", DiscKind.bluray: "Blu-ray", DiscKind.uhd: "4K Ultra HD Blu-ray"}

# GS1 prefixes (the first digits of a 13-digit EAN; a 12-digit UPC is EAN "0" + UPC)
# -> the locale the release is most likely for. Only the common disc markets.
_GS1: list[tuple[int, int, str]] = [
    (0, 19, "en-US"), (30, 39, "en-US"), (60, 139, "en-US"),
    (300, 379, "fr-FR"), (400, 440, "de-DE"), (450, 459, "ja-JP"), (490, 499, "ja-JP"),
    (500, 509, "en-GB"), (539, 539, "en-IE"), (540, 549, "nl-BE"), (570, 579, "da-DK"),
    (590, 590, "pl-PL"), (640, 649, "fi-FI"), (700, 709, "nb-NO"), (730, 739, "sv-SE"),
    (754, 755, "en-CA"), (760, 769, "de-CH"), (800, 839, "it-IT"), (840, 849, "es-ES"),
    (870, 879, "nl-NL"), (880, 880, "ko-KR"), (900, 919, "de-AT"), (930, 939, "en-AU"),
    (940, 949, "en-NZ"),
]
# Blu-ray regions by country: A (Americas, East and South-East Asia), B (Europe, Africa,
# the Middle East, Oceania), C (the rest of Asia).
_BD_A = {"US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "JP", "KR", "TW", "HK", "PH", "SG", "TH", "MY", "ID", "VN"}
_BD_C = {"CN", "IN", "RU", "PK", "BD", "NP", "KZ", "MN", "LK"}
# Amazon's site for a locale's country, for the ASIN search.
_AMAZON = {"US": "amazon.com", "GB": "amazon.co.uk", "CA": "amazon.ca", "DE": "amazon.de", "AT": "amazon.de",
           "FR": "amazon.fr", "IT": "amazon.it", "ES": "amazon.es", "JP": "amazon.co.jp", "AU": "amazon.com.au",
           "NL": "amazon.nl", "SE": "amazon.se", "PL": "amazon.pl", "IE": "amazon.co.uk", "BE": "amazon.com.be",
           "MX": "amazon.com.mx", "BR": "amazon.com.br", "IN": "amazon.in"}


def amazon_site(locale: str | None) -> str:
    return _AMAZON.get(country(locale) or "", "amazon.com")


class ReleaseLookupError(Exception):
    pass


# --- worked out without asking anyone ------------------------------------------------


def valid_upc(code: str | None) -> bool:
    """A 12-digit UPC-A or 13-digit EAN with a correct check digit."""
    if not code or not UPC.fullmatch(code):
        return False
    digits = [int(c) for c in code.zfill(13)]
    total = sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12]))
    return (10 - total % 10) % 10 == digits[12]


def locale_for_barcode(code: str | None) -> str | None:
    if not code or not UPC.fullmatch(code):
        return None
    prefix = int(code.zfill(13)[:3])
    return next((loc for lo, hi, loc in _GS1 if lo <= prefix <= hi), None)


def country(locale: str | None) -> str | None:
    if not locale or "-" not in locale:
        return None
    return locale.split("-", 1)[1].upper() or None


def dvd_regions(vmg_ifo: bytes) -> str | None:
    """The regions a DVD plays in, from VIDEO_TS.IFO: "2", "2,4", or "0" for all of
    them. Byte 0x23 is a mask of the regions it does *not* play in."""
    if len(vmg_ifo) < 0x24 or not vmg_ifo.startswith(b"DVDVIDEO-VMG"):
        return None
    mask = vmg_ifo[0x23]
    regions = [str(n + 1) for n in range(8) if not mask & (1 << n)]
    if not regions:
        return None
    if len(regions) >= 6 and regions[:6] == ["1", "2", "3", "4", "5", "6"]:
        return "0"
    return ",".join(regions)


def region_guess(kind: DiscKind | None, locale: str | None, structure_dir: Path | None) -> tuple[str | None, str]:
    """(region code, where it came from)."""
    if kind == DiscKind.dvd and structure_dir:
        try:
            ifo = (structure_dir / "VIDEO_TS" / "VIDEO_TS.IFO").read_bytes()
        except OSError:
            ifo = b""
        if r := dvd_regions(ifo):
            return r, "read from the disc"
    if kind == DiscKind.uhd:
        return "Region free", "UHD Blu-rays have no regions"
    c = country(locale)
    if kind == DiscKind.bluray and c:
        return ("A" if c in _BD_A else "C" if c in _BD_C else "B"), f"usual Blu-ray region for {c}, check the box"
    return None, "not known"


def physical_release_date(release_dates: dict, locale: str | None, kind: DiscKind | None) -> tuple[date | None, str]:
    """From TMDB's /movie/{id}/release_dates: the physical (type 5) release in the
    locale's country, preferring one whose note names the format. (date, where from)."""
    want = country(locale)
    found: list[tuple[date, str]] = []
    for r in release_dates.get("results") or []:
        if not isinstance(r, dict) or r.get("iso_3166_1") != want:
            continue
        for d in r.get("release_dates") or []:
            if not isinstance(d, dict) or d.get("type") != 5:
                continue
            try:
                day = datetime.fromisoformat(str(d.get("release_date")).replace("Z", "+00:00")).date()
            except ValueError:
                continue
            found.append((day, str(d.get("note") or "")))
    if not found:
        return None, f"TMDB has no physical release for {want}" if want else "no country to look for"
    words = {DiscKind.uhd: ("4k", "uhd", "ultra hd"), DiscKind.bluray: ("blu",), DiscKind.dvd: ("dvd",)}.get(kind, ())
    named = [f for f in found if any(w in f[1].lower() for w in words)]
    if kind == DiscKind.bluray:  # "4K Ultra HD Blu-ray" names Blu-ray too
        named = [f for f in named if not any(w in f[1].lower() for w in ("4k", "uhd", "ultra hd"))]
    if named:
        day, note = min(named)
        return day, f"TMDB's {want} release \"{note}\""
    day, note = min(found)
    return day, f"TMDB's first physical release in {want}" + (f" (\"{note}\")" if note else "") + ", check the format"


def search_query(title: str, year: int | None, kind: DiscKind | None, season: int | None = None) -> str:
    parts = [title]
    if season is not None:
        parts.append(f"Season {season}")
    elif year:
        parts.append(str(year))
    if kind in FORMAT_WORDS:
        parts.append(FORMAT_WORDS[kind])
    return " ".join(parts)


def asin_from_results(results: list[dict], domain: str | None = None) -> str | None:
    """The ASIN most result addresses point at (``/dp/<ASIN>``), on ``domain`` if given."""
    counts: dict[str, int] = {}
    for x in results:
        url = str(x.get("url") or "")
        host = urlsplit(url).hostname or ""
        if "amazon." not in host or (domain and not host.endswith(domain)):
            continue
        if m := _DP.search(url):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else None


# --- asking UPCitemdb -----------------------------------------------------------------


def _product(item: dict) -> ReleaseProduct | None:
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    upc = next((str(item[k]) for k in ("upc", "ean") if UPC.fullmatch(str(item.get(k) or ""))), None)
    asin = str(item.get("asin") or "").strip().upper() or None
    if not asin:
        for o in item.get("offers") or []:
            a = str((o or {}).get("asin") or "").strip().upper()
            if ASIN.fullmatch(a):
                asin = a
                break
    images = [u for u in item.get("images") or [] if isinstance(u, str) and u.startswith("https://")][:6]
    return ReleaseProduct(upc=upc, title=title[:300], brand=(str(item.get("brand") or "").strip() or None),
                          asin=asin if asin and ASIN.fullmatch(asin) else None, images=images)


class Products:
    """UPCitemdb's free trial API."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(timeout=TIMEOUT_S)
        self._own = client is None

    async def _get(self, path: str, **params) -> list[ReleaseProduct]:
        try:
            r = await self._client.get(f"{UPCITEMDB}/{path}", params=params,
                                       headers={"User-Agent": f"ripshelf/{__version__}", "Accept": "application/json"})
        except httpx.TimeoutException:
            raise ReleaseLookupError("UPCitemdb did not answer in time") from None
        except httpx.HTTPError as e:
            raise ReleaseLookupError(f"could not reach UPCitemdb ({type(e).__name__})") from None
        if r.status_code == 429:
            raise ReleaseLookupError("UPCitemdb's free lookups are used up for today (about 100 a day)")
        if r.status_code == 404:
            return []
        if r.status_code >= 400:
            raise ReleaseLookupError(f"UPCitemdb returned HTTP {r.status_code}")
        try:
            items = r.json().get("items") or []
        except (ValueError, AttributeError):
            raise ReleaseLookupError("UPCitemdb sent a reply that isn't JSON") from None
        return [p for p in (_product(i) for i in items if isinstance(i, dict)) if p][:MAX_PRODUCTS]

    async def lookup(self, upc: str) -> list[ReleaseProduct]:
        return await self._get("lookup", upc=upc)

    async def search(self, query: str) -> list[ReleaseProduct]:
        return await self._get("search", s=query, match_mode=0, type="product")

    async def aclose(self) -> None:
        if self._own:
            await self._client.aclose()


def rank(products: list[ReleaseProduct], title: str, kind: DiscKind | None) -> list[ReleaseProduct]:
    """Listings of the right title and format first; ones with neither are dropped."""
    from ..identify.scoring import normalise

    want = set(normalise(title).split()) - {"the", "a", "an", "of", "and"}
    fmt = {DiscKind.uhd: ("4k", "uhd", "ultra hd"), DiscKind.bluray: ("blu-ray", "bluray", "blu ray"),
           DiscKind.dvd: ("dvd",)}.get(kind, ())

    def score(p: ReleaseProduct) -> float:
        words = set(normalise(p.title).split())
        s = len(want & words) / len(want) if want else 0.0
        low = p.title.lower()
        if any(f in low for f in fmt):
            s += 0.5
        if kind == DiscKind.bluray and any(f in low for f in ("4k", "uhd", "ultra hd")):
            s -= 0.4
        return s + (0.1 if p.upc else 0) + (0.05 if p.asin else 0)

    scored = sorted(((score(p), i, p) for i, p in enumerate(products)), key=lambda t: (-t[0], t[1]))
    return [p for s, _, p in scored if s >= 0.5]


# --- downloading a cover ------------------------------------------------------------


@dataclass
class Picture:
    data: bytes
    content_type: str

    @property
    def ext(self) -> str:
        return COVER_TYPES[self.content_type]


def sniff(data: bytes) -> str | None:
    """The picture's type from its first bytes (JPEG, PNG or WebP), else None."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _public_host(host: str) -> bool:
    """The host resolves only to public addresses, so a product picture's address can't
    be used to reach something on the LAN."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addrs = {ipaddress.ip_address(i[4][0]) for i in infos}
    return bool(addrs) and all(a.is_global for a in addrs)


async def download_cover(url: str, client: httpx.AsyncClient | None = None,
                         public_host=_public_host) -> Picture:
    """A product picture, for use as a cover. Only https addresses on public hosts, no
    redirects, at most 20 MB, and only JPEG, PNG or WebP (checked from the bytes)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ReleaseLookupError("only https picture addresses can be used")
    if not public_host(parts.hostname):
        raise ReleaseLookupError("that picture's address isn't on the public internet")
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)
    try:
        async with client.stream("GET", url, headers={"User-Agent": f"ripshelf/{__version__}"}) as r:
            if r.is_redirect:
                raise ReleaseLookupError("the picture's address redirects elsewhere")
            if r.status_code >= 400:
                raise ReleaseLookupError(f"the picture's server returned HTTP {r.status_code}")
            buf = bytearray()
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) > MAX_COVER_BYTES:
                    raise ReleaseLookupError("the picture is larger than 20 MB")
    except httpx.HTTPError as e:
        raise ReleaseLookupError(f"could not download the picture ({type(e).__name__})") from None
    finally:
        if own:
            await client.aclose()
    kind = sniff(bytes(buf))
    if not kind:
        raise ReleaseLookupError("that address isn't a JPEG, PNG or WebP picture")
    return Picture(bytes(buf), kind)
