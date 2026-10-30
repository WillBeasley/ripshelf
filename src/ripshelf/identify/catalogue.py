"""Catalogue-number labels, looked up on the web to find the title.

Some publishers master DVDs with their catalogue number as the volume label
("EO51710" is Entertainment One's UK DVD of The Mortal Instruments: City of
Bones). TMDB knows nothing about those numbers, and no free database maps
them. Retailer listings do ("Catalogue No: EO51710"), so a web search for the
number finds the title.

The search goes to a self-hosted SearXNG (``/search?format=json``; JSON
output must be enabled in its settings.yml).

Only results that mention the number are used. Their page titles are cleaned
of shop names and format words ("(DVD)", "[Blu-ray]", "Region 2"), and the
titles several pages agree on go to the usual TMDB search and scoring, where
the runtime still has to fit. One page alone never auto-confirms.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

import httpx

from ..models import Candidate, Evidence
from .label import DISC_NUMBER, NOISE_TOKENS, YEAR
from .scoring import _order, normalise, title_similarity

MAX_TITLES = 3
# A title only one page names is a lead, not a fact.
MIN_SUPPORT = 2
SINGLE_PAGE_CAP = 0.85

# Letters then digits ("EO51710", "OPTD2345", "CDR-30123", "8299901S"), or a bare
# number of 6+ digits (a barcode or a numeric catalogue number).
_CATNO = re.compile(r"^(?:[A-Z]{1,6}-?\d{4,8}[A-Z]{0,2}|\d{6,13})$")
# Shop names and taglines after these separators.
_SEPARATORS = re.compile(r"\s+(?:\||-|–|—|::|»|·)\s+")
_BRACKETS = re.compile(r"\s*[(\[{][^)\]}]*[)\]}]")
_FORMAT_WORDS = re.compile(
    r"\b(?:4k\s*ultra\s*hd|ultra\s*hd|uhd|blu[- ]?ray|bluray|dvd|bd|region\s*[0-9a-c](?:\s*/\s*[0-9a-c])*"
    r"|ntsc|import|widescreen|box\s*set|boxset|steelbook|brand\s*new|sealed|"
    r"(?:special|collector'?s|limited|ultimate|anniversary|deluxe)\s+edition|"
    r"\d+[- ]?(?:disc|disk)s?(?:\s+set)?|(?:disc|disk)\s*\d+|cert(?:ificate)?\s*(?:u|pg|12a?|15|18))\b",
    re.I,
)
# "Series 3" / "Season 1-4" / "The Complete Series" are dropped, because the show name is what TMDB knows.
_TV_WORDS = re.compile(r"\b(?:(?:the\s+)?complete\s+(?:series|seasons?|collection)|(?:series|seasons?)(?=\s*\d))"
                       r"(?:\s*\d+(?:\s*-\s*\d+)?)?\b", re.I)
_ARTICLE_AFTER = re.compile(r"^(.+?),\s*(The|A|An)\b\s*(.*)$", re.I)


class CatalogueError(Exception):
    pass


@dataclass
class Lookup:
    number: str
    query: str
    pages: int = 0  # results that mention the number
    titles: list[tuple[str, int]] = field(default_factory=list)  # (title, pages naming it), best first

    @property
    def names(self) -> list[str]:
        return [t for t, _ in self.titles]

    def support(self, title: str) -> int:
        return max((n for t, n in self.titles if title_similarity(t, title) >= 0.9), default=0)


def number(label: str | None) -> str | None:
    """The catalogue number a label consists of, or None for a label with words in it."""
    tokens = [t for t in re.split(r"[_\s.]+", (label or "").upper()) if t]
    rest = [t for i, t in enumerate(tokens)
            if not (i and (t in NOISE_TOKENS or DISC_NUMBER.match(t)))]
    if len(rest) != 1:
        return None
    tok = rest[0]
    if not _CATNO.match(tok) or YEAR.match(tok):
        return None
    letters = re.sub(r"[^A-Z]", "", tok)
    if letters in NOISE_TOKENS or letters in {"DISC", "DISK", "DVD", "VOLUME", "VOL"}:
        return None  # "DVD12345"-style generic labels still say nothing
    return tok


def _mentions(text: str, catno: str) -> bool:
    flat = re.sub(r"[^A-Z0-9]", "", text.upper())
    return re.sub(r"[^A-Z0-9]", "", catno) in flat


def clean_title(page_title: str, catno: str) -> str | None:
    """The film or show name in a shop's page title, or None when there is none."""
    s = page_title.replace(catno, " ")
    parts = [p for p in _SEPARATORS.split(s) if p.strip()]
    # The first part with words that is not a domain ("Laserdisken.dk").
    part = next((p for p in parts if re.search(r"[A-Za-z]{2}", p)
                 and not re.search(r"\b[\w-]+\.(?:com|co\.uk|uk|de|dk|fi|cz|fr|nl|se|net|org)\b", p, re.I)), None)
    if not part:
        return None
    part = _BRACKETS.sub(" ", part)
    part = _TV_WORDS.sub(" ", part)
    part = _FORMAT_WORDS.sub(" ", part)
    part = re.sub(r"\s+", " ", part).strip(" -–:,.;/+&")
    part = re.sub(r"\s*:\s*$", "", part)
    m = _ARTICLE_AFTER.match(part)
    if m:  # "Mortal Instruments, The: City of Bones"
        part = f"{m.group(2).title()} {m.group(1)}{(' ' + m.group(3)) if m.group(3) else ''}"
        part = re.sub(r"\s+:", ":", part)
    if not re.search(r"[A-Za-z]{2}", part) or number(part.replace(" ", "_")) or len(part) > 100:
        return None
    return part


def rank_titles(titles: list[str]) -> list[tuple[str, int]]:
    """Distinct titles by how many pages agree, a page counting for every title it contains."""
    keys = {}
    for t in titles:
        keys.setdefault(normalise(t), t)
    counts = Counter(normalise(t) for t in titles)
    support = {}
    for k in keys:
        # "City of Bones" listings that add the cast still back the bare title.
        support[k] = sum(n for other, n in counts.items() if other == k or other.startswith(k + " "))
    order = sorted(keys, key=lambda k: (-support[k], len(k)))
    return [(keys[k], support[k]) for k in order if k]


def query_for(catno: str, kind: str | None) -> str:
    fmt = "blu-ray" if kind in ("bd", "uhd") else "dvd"
    return f'"{catno}" {fmt}'


async def search(settings, query: str, client: httpx.AsyncClient) -> list[dict]:
    """Results as [{title, url, content}] from the configured backend."""
    backend = settings.catalogue_search
    try:
        if backend == "searxng":
            url = (settings.searxng_url or "").rstrip("/")
            if not url:
                raise CatalogueError("no SearXNG address set")
            r = await client.get(url + "/search", params={"q": query, "format": "json"})
            if r.status_code == 403:
                raise CatalogueError("SearXNG refused JSON, so add json to search.formats in its settings.yml")
        else:
            raise CatalogueError("catalogue lookup is off")
    except httpx.HTTPError as e:
        raise CatalogueError(f"web search failed ({type(e).__name__})") from None
    if r.status_code == 429:
        raise CatalogueError("web search is rate limited; try again later")
    if r.status_code >= 400:
        raise CatalogueError(f"web search returned HTTP {r.status_code}")
    try:
        results = r.json()["results"]
    except (ValueError, KeyError, TypeError):
        raise CatalogueError("web search sent a reply without results") from None
    return [x for x in results if isinstance(x, dict)]


async def lookup(settings, catno: str, kind: str | None = None,
                 client: httpx.AsyncClient | None = None) -> Lookup:
    own = client is None
    client = client or httpx.AsyncClient(timeout=20, headers={"User-Agent": "ripshelf"})
    try:
        query = query_for(catno, kind)
        results = await search(settings, query, client)
        hits = [x for x in results if _mentions(" ".join(str(x.get(k) or "") for k in ("title", "url", "content")),
                                                catno)]
        if not hits:  # quoted queries can come back empty on some engines
            query = catno
            results = await search(settings, query, client)
            hits = [x for x in results if _mentions(" ".join(str(x.get(k) or "") for k in ("title", "url", "content")),
                                                    catno)]
    finally:
        if own:
            await client.aclose()
    titles = [t for t in (clean_title(str(x.get("title") or ""), catno) for x in hits) if t]
    return Lookup(number=catno, query=query, pages=len(hits), titles=rank_titles(titles)[:MAX_TITLES])


def weigh(candidates: list[Candidate], found: Lookup) -> list[Candidate]:
    """Say which candidates the web named, and keep a one-page lead from auto-confirming."""
    for c in candidates:
        n = max(found.support(c.title), found.support(c.original_title or ""))
        if not n:
            continue
        if n < MIN_SUPPORT:
            c.score = round(min(c.score, SINGLE_PAGE_CAP), 3)
            c.evidence.append(Evidence(ok=None, text=f"Only one web page listing {found.number} names it"))
        else:
            c.evidence.append(Evidence(ok=True, text=f"{n} web pages listing catalogue number {found.number} name it"))
    return sorted(candidates, key=_order)
