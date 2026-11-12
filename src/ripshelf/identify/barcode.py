"""Naming the release a cover's barcode belongs to.

The barcode read off a box (zbar, or the digits printed under the bars) is asked of
these, in order, until one names it:

1. **TheDiscDB**, whose search indexes each release's UPC. A hit names the film or
   show (or box set) as TheDiscDB knows it, so it is the cleanest name there is.
   Only when TheDiscDB is ticked in Settings → Identify.
2. **UPCitemdb**'s free API (no key, about 100 lookups a day). Strong on US UPCs,
   thin on UK and European EANs.
3. **A paid barcode service**, when one is chosen with its key: Barcode Lookup
   (barcodelookup.com) or EAN-Search (ean-search.org).
4. **A web search** of the self-hosted SearXNG, when catalogue lookup is on. Shop
   listings print the EAN, and the title enough of them agree on is used, as for
   catalogue-number labels.

A name found is a search hint, never an identity: it is searched on TMDB like the
title read off the box, and the runtime still has to fit.

The answer is kept on the job. "Nothing found" is only kept when every source
answered; a source that failed (offline, rate limited, the daily allowance used up)
leaves it to be asked again next time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from .. import __version__
from ..providers import release_info
from . import catalogue

log = logging.getLogger(__name__)

TIMEOUT_S = 15
BARCODELOOKUP = "https://api.barcodelookup.com/v3/products"
EANSEARCH = "https://api.ean-search.org/api"
THEDISCDB_SEARCH = "https://thediscdb.com/api/search"

SOURCE_NAMES = {"thediscdb": "TheDiscDB", "upcitemdb": "UPCitemdb", "barcodelookup": "Barcode Lookup",
                "eansearch": "EAN-Search", "web": "a web search"}
# Sources whose titles are already just the film or show; the others are shop listings.
CLEAN_SOURCES = {"thediscdb", "web"}


class SourceFailed(Exception):
    """A source couldn't answer (as opposed to answering that it doesn't know the code)."""


@dataclass
class Found:
    title: str  # as the source gives it
    source: str  # a SOURCE_NAMES key


@dataclass
class Answer:
    found: Found | None
    failed: list[str]  # "UPCitemdb: its free lookups are used up for today", ...

    @property
    def final(self) -> bool:
        """Worth keeping: something was found, or every source said it doesn't know."""
        return self.found is not None or not self.failed


def same_code(a: str, b: str) -> bool:
    """A 12-digit UPC-A is the 13-digit EAN with a leading 0."""
    a, b = re.sub(r"\D", "", a), re.sub(r"\D", "", b)
    return bool(a) and a.lstrip("0") == b.lstrip("0")


def ean13(code: str) -> str:
    return code.zfill(13) if len(code) == 12 else code


# --- the sources ----------------------------------------------------------------------


async def from_thediscdb(code: str, client: httpx.AsyncClient) -> str | None:
    """The film, show or box set whose release has this UPC."""
    try:
        r = await client.get(THEDISCDB_SEARCH, params={"q": code, "limit": 5})
    except httpx.HTTPError as e:
        raise SourceFailed(f"could not reach it ({type(e).__name__})") from None
    if r.status_code in (400, 404):
        return None
    if r.status_code >= 400:
        raise SourceFailed(f"HTTP {r.status_code}")
    try:
        got = r.json()
    except ValueError:
        raise SourceFailed("a reply that isn't JSON") from None
    for entry in got if isinstance(got, list) else []:
        if not isinstance(entry, dict):
            continue
        ids = entry.get("identifiers") or entry.get("Identifiers") or []
        title = str(entry.get("title") or entry.get("Title") or "").strip()
        # Search is fuzzy: only an entry that really lists the code counts.
        if title and isinstance(ids, list) and any(same_code(str(i), code) for i in ids):
            return title[:300]
    return None


async def from_upcitemdb(code: str, client: httpx.AsyncClient) -> str | None:
    products = release_info.Products(client)
    try:
        found = await products.lookup(code)
    except release_info.ReleaseLookupError as e:
        raise SourceFailed(str(e)) from None
    return found[0].title if found else None


async def from_barcodelookup(code: str, key: str, client: httpx.AsyncClient) -> str | None:
    try:
        r = await client.get(BARCODELOOKUP, params={"barcode": code, "key": key})
    except httpx.HTTPError as e:
        raise SourceFailed(f"could not reach it ({type(e).__name__})") from None
    if r.status_code == 404:
        return None
    if r.status_code in (401, 403):
        raise SourceFailed("it refused the API key")
    if r.status_code == 429:
        raise SourceFailed("the key's lookups are used up for now")
    if r.status_code >= 400:
        raise SourceFailed(f"HTTP {r.status_code}")
    try:
        products = r.json().get("products") or []
    except (ValueError, AttributeError):
        raise SourceFailed("a reply that isn't JSON") from None
    for p in products:
        if isinstance(p, dict) and (t := str(p.get("title") or p.get("product_name") or "").strip()):
            return t[:300]
    return None


async def from_eansearch(code: str, key: str, client: httpx.AsyncClient) -> str | None:
    try:
        r = await client.get(EANSEARCH, params={"token": key, "op": "barcode-lookup", "format": "json",
                                                "ean": ean13(code)})
    except httpx.HTTPError as e:
        raise SourceFailed(f"could not reach it ({type(e).__name__})") from None
    if r.status_code == 429:
        raise SourceFailed("the key's lookups are used up for now")
    if r.status_code >= 400:
        raise SourceFailed(f"HTTP {r.status_code}")
    try:
        got = r.json()
    except ValueError:
        raise SourceFailed("a reply that isn't JSON") from None
    for item in got if isinstance(got, list) else [got]:
        if not isinstance(item, dict):
            continue
        if err := str(item.get("error") or ""):
            if "not found" in err.lower():
                return None
            raise SourceFailed(err[:200])
        if t := str(item.get("name") or "").strip():
            return t[:300]
    return None


async def from_web(settings, code: str, kind: str | None, client: httpx.AsyncClient) -> str | None:
    """The title enough shop listings that print the code agree on."""
    try:
        found = await catalogue.lookup(settings, code, kind, client)
    except catalogue.CatalogueError as e:
        raise SourceFailed(str(e)) from None
    if found.titles and found.titles[0][1] >= catalogue.MIN_SUPPORT:
        return found.titles[0][0]
    return None


# --- asking them in turn --------------------------------------------------------------


def sources(settings, thediscdb: bool) -> list[str]:
    out = ["thediscdb"] if thediscdb else []
    out.append("upcitemdb")
    if settings.barcode_service != "off" and settings.secret("barcode_service_key"):
        out.append(settings.barcode_service)
    if settings.catalogue_search == "searxng":
        out.append("web")
    return out


async def name(settings, code: str, kind: str | None = None, *, thediscdb: bool = True,
               client: httpx.AsyncClient | None = None) -> Answer:
    """What the barcode is, from the first source that knows."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, headers={"User-Agent": f"ripshelf/{__version__}"})
    failed: list[str] = []
    try:
        for src in sources(settings, thediscdb):
            try:
                if src == "thediscdb":
                    title = await from_thediscdb(code, client)
                elif src == "upcitemdb":
                    title = await from_upcitemdb(code, client)
                elif src == "barcodelookup":
                    title = await from_barcodelookup(code, settings.secret("barcode_service_key"), client)
                elif src == "eansearch":
                    title = await from_eansearch(code, settings.secret("barcode_service_key"), client)
                else:
                    title = await from_web(settings, code, kind, client)
            except SourceFailed as e:
                log.info("barcode %s: %s failed: %s", code, SOURCE_NAMES[src], e)
                failed.append(f"{SOURCE_NAMES[src]}: {e}")
                continue
            if title:
                return Answer(Found(title, src), failed)
    finally:
        if own:
            await client.aclose()
    return Answer(None, failed)
