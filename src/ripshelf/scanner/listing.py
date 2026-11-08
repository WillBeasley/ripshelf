"""Turning the text read on the box into a listing of what's on the disc.

Rule-based, for the layouts boxes use:

- **Sections.** A heading ("Special features", "Bonus material", "Episodes",
  "Disc 2") starts a list, and the list runs until the next heading or a line that
  is clearly something else (the copyright line, the technical details).
- **Items.** One per line, with bullets and stray marks taken off. A line ending
  in a word such as "of" or "the", or followed by one starting in lower case, runs
  on to the next. A quoted title in an item is its name ("In-depth documentary
  'Return to the Orchard'" is "Return to the Orchard"), and a running time in it is
  its runtime.
- **Lists without a heading.** Four or more short Title Case lines in a row (a
  column of episode names) are taken as episodes.
- **Release details.** Running time, episode count ("Contains 10 wide-eyed
  adventures"), barcode (decoded, else the printed digits with their check digit
  verified), catalogue number, region, PAL or NTSC, country, aspect ratio, languages,
  subtitles, copyright year, title and cast.

The extras' types are TheDiscDB's, guessed from their words.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from ..models import (
    AudioTrack,
    ExtraKind,
    Listing,
    ListingDetails,
    ListingItem,
    ScanText,
    SubtitleTrack,
    TextLine,
)
from ..providers import release_info

# The order parts of a box are read in: listings are on the back and inside.
REGION_ORDER = {"back": 0, "left": 1, "right": 2, "page": 3, "spine": 4, "front": 5}
LIST_REGIONS = {"back", "left", "right", "page"}

NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())}
NUMBERS.update({"thirty": 30, "forty": 40, "fifty": 50})
NUM = r"(\d{1,3}|" + "|".join(NUMBERS) + r")"

HEADING = re.compile(
    r"^(?P<extras>special\s*features?|special|bonus\s*features?|bonus\s*material|special\s*extras|extras|"
    r"bonus|extra\s*features)"
    r"|^(?P<episodes>episodes|episode\s*guide|contents|on\s*this\s*disc|programmes|stories)"
    r"|^(?P<disc>dis[ck]\s*" + NUM + r")\b\s*[:\-–]?\s*(?P<rest>.*)$", re.I)
# Lines that end a list: the copyright line, the technical panel, a web address.
STOP = re.compile(
    r"©|\(c\)\s*\d{4}|all rights reserved|^information\b|^languages?\b|^audio\b|^subtitles?\b|^aspect|"
    r"run(?:ning)?\s*time|^feature\s*length|^certificate|^region\b|www\.|https?://|^technical|"
    r"^\W*(?:dolby|dts|pal|ntsc)\b|package design|^rated\b"
    r"|^\W*(?:19|20)\d{2}\s+\S.*\b(?:pictures|studios|films|productions|entertainment|inc|ltd|llc)\b", re.I)
JOIN_ENDS = {"of", "the", "a", "an", "and", "on", "with", "to", "from", "in", "for", "by", "at", "&", "-",
             "–", "—", ","}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}

RUNTIME = re.compile(
    r"(?:(?P<h>\d{1,2})\s*(?:hours|hour|hrs|hr|h)(?![a-z])\.?\s*(?:(?P<hm>\d{1,2})\s*(?:minutes|mins|min|m)(?![a-z])\.?)?"
    r"|(?P<m>\d{1,3})\s*(?:minutes|mins|min|m)(?![a-z])\.?)", re.I)
EPISODE_COUNT = re.compile(
    NUM + r"\s+(?:[a-z][\w'-]*\s+){0,3}?(episodes|adventures|stories|programmes|programs|cartoons|tales|"
    r"instalments|installments|shorts|specials)\b", re.I)
BULLET = re.compile(r"^(?:[•·*>~«»■□◆◇○●▪▶►\-–—=_|:;,.'\"“”‘’]+|[eoc]\s(?=[A-Z0-9\"“‘']))\s*")
NUMBERED = re.compile(r"^(?:ep(?:isode)?\.?\s*)?(\d{1,3})\s*[.):\-–]\s*(?=\S)", re.I)
# A quoted phrase. An apostrophe inside a word ("Where's") is not a quote mark.
_IN_WORD = r"(?<=[A-Za-z])[‘’'](?=[A-Za-z])"
QUOTED = re.compile(r"(?:[\"“”`]|(?<![A-Za-z])[‘’'])\s*((?:[^\"“”‘’'`]|" + _IN_WORD + r"){3,}?)\s*"
                    r"(?:[\"“”`]|[‘’'](?![A-Za-z]))")
CATALOGUE = [re.compile(r"\b(\d{3,7}[A-Z]{2,4}\d{0,2})\b"), re.compile(r"\b([A-Z]{2,5}-?\d{4,7}[A-Z]{0,2})\b")]
CATALOGUE_NOT = re.compile(r"FSC|ISBN|BBFC|ISAN", re.I)
YEAR_COPYRIGHT = re.compile(r"(?:©|\(c\)|copyright)\s*((?:19|20)\d{2})", re.I)
REGION = re.compile(r"\bregion\s*(?:code)?\s*[:\-]?\s*((?:[0-8]|[ABC])(?:\s*(?:&|and|,|/)\s*(?:[0-8]|[ABC]))*)\b",
                    re.I)
REGION_ALONE = re.compile(r"^\s*((?:[0-8])(?:\s*(?:&|and|,|/)\s*[0-8])*)\b")
COUNTRY = [(re.compile(r"\bU\.?\s?K\.?(?![a-z])"), "UK", "en-GB"), (re.compile(r"\bU\.S\.A?\.?|\bUSA\b"), "US", "en-US"),
           (re.compile(r"\bAustralia\b"), "AU", "en-AU"), (re.compile(r"\bIreland\b|\bEire\b"), "IE", "en-IE"),
           (re.compile(r"\bCanada\b"), "CA", "en-CA")]
ASPECT = re.compile(r"\b(\d\.\d{2}\s*:\s*1|16\s*:\s*9|4\s*:\s*3)\b")
LANGUAGES = ["English", "French", "German", "Italian", "Spanish", "Castilian", "Catalan", "Portuguese", "Dutch",
             "Flemish", "Danish", "Swedish", "Norwegian", "Finnish", "Icelandic", "Polish", "Czech", "Slovak",
             "Hungarian", "Romanian", "Bulgarian", "Greek", "Turkish", "Russian", "Ukrainian", "Croatian",
             "Serbian", "Slovenian", "Hebrew", "Arabic", "Hindi", "Tamil", "Thai", "Japanese", "Korean",
             "Mandarin", "Cantonese", "Chinese", "Welsh", "Irish", "Latvian", "Lithuanian", "Estonian"]
LANGUAGE = re.compile(r"\b(" + "|".join(LANGUAGES) + r")\b(\s*\((?:for the )?hard of hearing\)|\s*for the hard of hearing"
                      r"|\s*sdh\b)?", re.I)
EXTRAS_TYPES = ["Extra", "Featurette", "DeletedScene", "Trailer", "Interview", "Scene", "Music", "Short", "Other"]
# Extras' types, by the words in them, first match wins. TheDiscDB's types.
TYPES: list[tuple[re.Pattern, ExtraKind]] = [
    (re.compile(r"trailer|teaser|tv spots?\b|promo\b", re.I), "Trailer"),
    (re.compile(r"deleted|extended scenes?|alternate (?:scene|ending|opening)|unused scenes?", re.I), "DeletedScene"),
    (re.compile(r"interview|in conversation|q\s*&\s*a\b|talking to", re.I), "Interview"),
    (re.compile(r"featurette|making of|making-of|behind the scenes|documentary|the making|look at|"
                r"inside the|production|on set", re.I), "Featurette"),
    (re.compile(r"music video|sing-?along|songs?\b|karaoke", re.I), "Music"),
    (re.compile(r"short film|animated short|\bshorts?\b", re.I), "Short"),
    (re.compile(r"\bscenes?\b|\bclips?\b", re.I), "Scene"),
    (re.compile(r"gag reel|blooper|outtakes?|commentary|gallery|storyboard|easter egg", re.I), "Other"),
]


@dataclass
class Src:
    """A line of text, with where it came from."""

    text: str
    line: TextLine
    scan_id: str


def clean(text: str) -> str:
    t = text.replace("’", "'").replace("‘", "'").replace("`", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\s+", " ", t).strip()
    return t


def number(word: str) -> int | None:
    w = word.lower()
    return int(w) if w.isdigit() else NUMBERS.get(w)


def runtime_min(text: str) -> float | None:
    text = re.sub(r"(?<![A-Za-z0-9])[tTlI|](?=\s?(?:hrs?|hours?)\b)", "1", text)  # "1hr" read as "thr"
    m = RUNTIME.search(text)
    if not m:
        return None
    if m.group("h"):
        return int(m.group("h")) * 60 + int(m.group("hm") or 0)
    return float(m.group("m"))


def ean_ok(code: str) -> bool:
    return release_info.valid_upc(code)


def name_case(word: str) -> str:
    """STEVE → Steve, McQUEEN → McQueen, O'BRIEN → O'Brien."""
    m = re.match(r"^(Mc|Mac|O')(.+)$", word, re.I)
    if m and len(m.group(2)) > 1:
        return m.group(1).capitalize() + m.group(2).capitalize()
    return "-".join(p.capitalize() for p in word.split("-"))


def title_case(text: str) -> str:
    words = text.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        out.append(lw if i and lw in SMALL else name_case(w) if w.isupper() or w.islower() else w)
    return " ".join(out)


def _strip_bullet(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = BULLET.sub("", text).strip()
    return text


def _tidy_title(text: str) -> str:
    t = _strip_bullet(text)
    t = re.sub(r"\s*[\(\[]?\s*(?:approx\.?|approximately|c\.)?\s*" + RUNTIME.pattern + r"\s*(?:approx\.?)?\s*[\)\]]?",
               " ", t, flags=re.I)
    t = t.strip(" -–—:;,.'\"")
    # "Growing the Orchard: a featurette" is called "Growing the Orchard".
    t = re.sub(r"\s*[:\-–—,]\s*(?:an?\s+)?(?:featurette|documentary|short film|interview)s?$", "", t, flags=re.I)
    toks = t.split()
    # A short capitalised token stuck on the end is usually a logo beside the line.
    if len(toks) > 1 and re.fullmatch(r"[A-Z]{1,2}", toks[-1]) and len(toks[-2]) > 2:
        toks.pop()
    return " ".join(toks)


def extra_type(text: str) -> ExtraKind:
    for pat, kind in TYPES:
        if pat.search(text):
            return kind
    return "Extra"


def _union(boxes: Iterable[list[float]]) -> list[float]:
    bs = list(boxes)
    return [min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]


def _item(kind: str, srcs: list[Src], disc: int | None) -> ListingItem | None:
    text = clean(" ".join(s.text for s in srcs))
    raw = text
    num = None
    if kind == "episode" and (m := NUMBERED.match(_strip_bullet(text))):
        num = int(m.group(1))
        text = _strip_bullet(text)[m.end():]
    quoted = [q for q in QUOTED.findall(text) if len(q.split()) >= 1 and re.search(r"[A-Za-z]{3}", q)]
    title = _tidy_title(quoted[-1] if quoted and kind == "extra" else text)
    # A scrap misread off a picture ("ae", "GE\\ woth" at 49%) isn't a title.
    if not re.search(r"[A-Za-z]{3}", title) or max(s.line.conf for s in srcs) < 55:
        return None
    same_scan = [s for s in srcs if s.scan_id == srcs[0].scan_id]
    return ListingItem(kind=kind, title=title, number=num, runtime_min=runtime_min(raw),
                       extra_type=extra_type(raw) if kind == "extra" else None, disc=disc,
                       text=raw, scan_id=srcs[0].scan_id, box=_union(s.line.box for s in same_scan),
                       source="tesseract")


def _joins(prev: Src, nxt: Src) -> bool:
    """The item on ``prev`` runs on to ``nxt``: no bullet or number starts ``nxt``, it
    sits right under ``prev``, and ``prev`` stops mid-phrase or ``nxt`` goes on in lower
    case. An item that has given its quoted title is complete."""
    p, n_raw = clean(prev.text), clean(nxt.text)
    n = _strip_bullet(n_raw)
    if NUMBERED.match(n_raw) or BULLET.match(n_raw) and n != n_raw:
        return False
    a, b = prev.line.box, nxt.line.box
    height = max(a[3] - a[1], 1e-6)
    if prev.scan_id != nxt.scan_id or prev.line.region != nxt.line.region or b[1] - a[3] > 0.8 * height:
        return False
    if re.search(r"'[^']{3,}'(?:\s+\S+){0,3}\s*$", p):
        return False
    last = p.split()[-1].lower() if p else ""
    return last in JOIN_ENDS or p.endswith((",", "-", "–", "&")) or bool(n[:1].islower())


def _title_like(text: str) -> bool:
    """A short Title Case line, like an episode name in a column of them."""
    t = _strip_bullet(text)
    words = t.split()
    if not 1 <= len(words) <= 6 or len(t) > 45 or not re.search(r"[A-Za-z]{3}", t):
        return False
    if t.isupper() or re.search(r"[©@/\\|{}<>\[\]]|www|\d{3,}|\b(?:PAL|NTSC|colour|color)\b", t, re.I):
        return False
    return all(w[:1].isupper() or w.lower() in SMALL or w[:1].isdigit() for w in words if w[:1].isalpha())


def _sections(srcs: list[Src]) -> list[ListingItem]:
    items: list[ListingItem] = []
    section: str | None = None
    disc: int | None = None
    current: list[Src] = []

    def flush():
        nonlocal current
        if current and section:
            if (it := _item(section, current, disc)):
                items.append(it)
        current = []

    for s in srcs:
        t = clean(_strip_bullet(s.text))
        h = HEADING.match(t)
        if h and len(t) <= 40 and (h.group("disc") or len(t.split()) <= 4):
            flush()
            if h.group("disc"):
                disc = number(re.sub(r"^dis[ck]\s*", "", h.group("disc"), flags=re.I))
                rest = h.group("rest") or ""
                section = "episode" if re.search(r"episode", rest, re.I) or section == "episode" else section
                if section is None and not rest:
                    section = "extra" if any(i.kind == "extra" for i in items) else "episode"
            else:
                section = "extra" if h.group("extras") else "episode"
            continue
        if not section:
            continue
        if STOP.search(t) or len(t) > 110:
            flush()
            section = None
            continue
        if current and _joins(current[-1], s):
            current.append(s)
        else:
            flush()
            current = [s]
    flush()
    return items


def _columns(srcs: list[Src], taken: set[int]) -> list[ListingItem]:
    """Runs of four or more title-like lines (noise in between allowed) as episodes."""
    out: list[ListingItem] = []
    run: list[Src] = []

    def end():
        nonlocal run
        if len(run) >= 4:
            out.extend(it for s in run if (it := _item("episode", [s], None)))
        run = []

    for s in srcs:
        if id(s) in taken:
            end()
            continue
        t = _strip_bullet(clean(s.text)).rstrip(".,;")
        # A symbol misread before a title ("©& Pat's Special Delivery: A Giant Cake"), not
        # a copyright line ("© 1998 Moonlit Pictures").
        t = re.sub(r"^[©®@&%#*]+\s*(?=[A-Z])", "", t)
        if run and t[:1].islower():
            t = t[:1].upper() + t[1:]  # "castle Scribbling." in a column of titles
        if run:
            heights = sorted(x.line.box[3] - x.line.box[1] for x in run)
            if s.line.box[1] - run[-1].line.box[3] > 2.5 * heights[len(heights) // 2]:
                end()  # well below the column: something else
        if _title_like(t) and s.line.conf >= 60:
            run.append(Src(t, s.line, s.scan_id))
        elif len(clean(s.text)) > 12 and s.line.conf >= 70:
            end()  # a real line of something else
    end()
    return out


def _details(srcs: list[Src], more: list[Src], barcodes: list[str]) -> ListingDetails:
    d = ListingDetails()
    every = srcs + more
    texts = [clean(s.text) for s in every]

    for code in barcodes:
        if ean_ok(code):
            d.barcode, d.barcode_source = code, "barcode"
            break
    if not d.barcode:
        for t in texts:
            for m in re.finditer(r"(?<![\d])(\d[\d ]{10,17}\d)(?![\d])", t):
                digits = m.group(1).replace(" ", "")
                if len(digits) in (12, 13) and ean_ok(digits):
                    d.barcode, d.barcode_source = digits, "text"
                    break
            if d.barcode:
                break

    for t in texts:
        low = t.lower()
        if d.feature_runtime_min is None and re.search(r"run(?:ning)?\s*time|feature\s*length|duration|"
                                                       r"approx", low):
            if (rt := runtime_min(t)) and rt >= 20:
                d.feature_runtime_min = rt
        if d.episode_count is None and (m := EPISODE_COUNT.search(t)):
            d.episode_count = number(m.group(1))
        if d.region is None and (m := REGION.search(t)):
            d.region = re.sub(r"\s*(?:and|,|/)\s*", " & ", m.group(1).upper()).replace("  ", " ")
        if d.video_system is None and (m := re.search(r"\b(PAL|NTSC)\b", t)):
            d.video_system = m.group(1)
        if d.aspect_ratio is None and (m := ASPECT.search(t)):
            d.aspect_ratio = re.sub(r"\s", "", m.group(1))
        if d.country is None and (len(t) <= 14 or re.search(r"\b(PAL|NTSC|region|colour|color)\b", t, re.I)):
            for pat, country, locale in COUNTRY:
                if pat.search(t):
                    d.country, d.locale = country, locale
                    break
    # "REGION" on one line and "2 & 4 Colour PAL UK" under it.
    if d.region is None:
        for t in texts:
            if m := re.match(r"^\W*([1-8](?:\s*&\s*[1-8])+)\s+(?:colour|color|pal|ntsc)\b", t, re.I):
                d.region = re.sub(r"\s*&\s*", " & ", m.group(1))
                break
    if d.region is None:
        for i, t in enumerate(texts[:-1]):
            if re.fullmatch(r"\W*region\W*", t, re.I) and (m := REGION_ALONE.match(texts[i + 1])):
                d.region = re.sub(r"\s*(?:and|,|/|&)\s*", " & ", m.group(1))
                break
    if any(re.search(r"\bwidescreen\b", t, re.I) for t in texts):
        d.aspect_ratio = f"Widescreen {d.aspect_ratio}" if d.aspect_ratio else "Widescreen"
    if d.region is None and d.video_system and d.country:
        guess = {("PAL", "UK"): "2", ("PAL", "IE"): "2", ("PAL", "AU"): "4", ("NTSC", "US"): "1",
                 ("NTSC", "CA"): "1"}.get((d.video_system, d.country))
        if guess:
            d.region, d.region_guessed = guess, True

    d.catalogue_number = _catalogue(srcs, d.barcode)
    years = [int(m.group(1)) for t in texts if not re.search(r"design|artwork|packaging", t, re.I)
             for m in YEAR_COPYRIGHT.finditer(t)]
    # The © is often lost: "1963 Metro-Goldwyn-Mayer Studios Inc. All Rights Reserved."
    years += [int(m.group(1)) for t in texts if re.search(r"rights reserved", t, re.I)
              and not re.search(r"design|artwork|packaging", t, re.I)
              for m in re.finditer(r"\b((?:19|20)\d{2})\b", t)]
    d.year = min(years) if years else None
    d.audio = _audio(texts)
    d.subtitles = _subtitles(texts)
    d.video = _video(texts)
    d.title = _title(srcs)
    d.cast = _cast(srcs)
    return d


def _catalogue(srcs: list[Src], barcode: str | None) -> str | None:
    """A catalogue number, preferring the spine (where it usually is)."""
    for s in sorted(srcs, key=lambda s: s.line.region != "spine"):
        t = clean(s.text)
        if CATALOGUE_NOT.search(t):
            continue
        for pat in CATALOGUE:
            for m in pat.finditer(t):
                tok = m.group(1)
                if re.search(r"\d", tok) and re.search(r"[A-Z]", tok) and tok not in (barcode or "") \
                        and not re.fullmatch(r"\d+(?:MINS?|HRS?|MM|CM|GB|MB|KB|P|I)", tok):
                    return tok
    return None


AUDIO_FORMATS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"atmos", re.I), "Dolby Atmos"),
    (re.compile(r"true\s*hd", re.I), "Dolby TrueHD"),
    (re.compile(r"dts[\s-]*hd\s*(?:ma|master)", re.I), "DTS-HD MA"),
    (re.compile(r"dts[\s-]*hd\s*(?:hr|high)", re.I), "DTS-HD HR"),
    (re.compile(r"dts\s*:\s*x", re.I), "DTS:X"),
    (re.compile(r"\bdts\b", re.I), "DTS"),
    (re.compile(r"\blpcm\b|linear pcm", re.I), "LPCM"),
    (re.compile(r"dolby\s*digital\s*(?:plus|\+)|\bdd\+", re.I), "Dolby Digital Plus"),
    (re.compile(r"dolby|\bdigital\b|\bdd\b|\bac-?3\b", re.I), "Dolby Digital"),
]
CHANNELS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bmono\b|\b1\.0\b", re.I), "mono"),
    (re.compile(r"\b7\.1\b", re.I), "7.1"),
    (re.compile(r"\b6\.1\b", re.I), "6.1"),
    (re.compile(r"\b5\.1\b|surround", re.I), "5.1"),
    (re.compile(r"\b2\.0\b|stereo", re.I), "2.0"),
]
SECTION_START = r"^\W*(?:aspect|feature|region|languages?|audio|sound|subtitles?|running|ratio)\b"


def _block(texts: list[str], start: str) -> list[str]:
    """A labelled list ("LANGUAGES English, French" or "Subtitles:" and the lines
    under it), cut where something else sits beside it on the line."""
    for i, t in enumerate(texts):
        if not re.search(start, t, re.I):
            continue
        chunk = [re.sub(start, "", t, flags=re.I)]
        for nxt in texts[i + 1:i + 6]:
            if re.search(SECTION_START, nxt, re.I) or not LANGUAGE.search(nxt):
                break
            chunk.append(nxt)
        return [re.split(r"\s{2,}|\|| all rights", part, maxsplit=1, flags=re.I)[0] for part in chunk]
    return []


def _first(pats: list[tuple[re.Pattern, str]], text: str) -> str | None:
    return next((v for p, v in pats if p.search(text)), None)


def _audio(texts: list[str]) -> list[AudioTrack]:
    """Audio tracks: each language, with the format and channels printed beside it, or
    those printed for the whole list (the logo and "Mono" next to it)."""
    block = _block(texts, r"^\W*(?:languages?|audio(?:\s*tracks?)?|sound)\b\W*")
    if not block:
        return []
    near = [t for t in texts if re.search(r"dolby|dts|\bdigital\b|pcm|mono|stereo|surround|\d\.1\b", t, re.I)
            and not re.search(r"subtitle", t, re.I)]
    fmt_all = next((f for t in block + near if (f := _first(AUDIO_FORMATS, t))), None)
    ch_all = next((c for t in block + near if (c := _first(CHANNELS, t))), None)
    pieces = [(m, piece) for part in block for piece in re.split(r",|/|;|\band\b", part)
              if (m := LANGUAGE.search(piece))]
    # "English 5.1 DTS, French 2.0" names each track's own; the list's (a logo and
    # "Mono" beside it) are for lists that don't.
    own_fmt = any(_first(AUDIO_FORMATS, p[m.end():]) for m, p in pieces)
    own_ch = any(_first(CHANNELS, p[m.end():]) for m, p in pieces)
    out: list[AudioTrack] = []
    for m, piece in pieces:
        lang = m.group(1).capitalize()
        rest = piece[m.end():]
        note = "commentary" if re.search(r"commentary", piece, re.I) else \
            "audio description" if re.search(r"audio description|descriptive", piece, re.I) else None
        track = AudioTrack(language=lang, format=_first(AUDIO_FORMATS, rest) or (None if own_fmt else fmt_all),
                           channels=_first(CHANNELS, rest) or (None if own_ch else ch_all), note=note)
        if track not in out:
            out.append(track)
    return out


def _subtitles(texts: list[str]) -> list[SubtitleTrack]:
    """Subtitles as printed. A qualifier in brackets ("German (Hard of Hearing)") is for
    the language before it; one after a dash ("- Movie only") for the whole line."""
    out: list[SubtitleTrack] = []
    for part in _block(texts, r"^\W*subtitles?\b\W*"):
        line_note = None
        if m := re.search(r"\s[-–]\s*([a-z][a-z ]{2,30})$", part, re.I):
            line_note = m.group(1).strip().lower()
            part = part[:m.start()]
        for m in LANGUAGE.finditer(part):
            track = SubtitleTrack(language=m.group(1).capitalize(), hard_of_hearing=bool(m.group(2)), note=line_note)
            if track not in out:
                out.append(track)
    return out


VIDEO = [(re.compile(r"\b(?:4K|ultra\s*hd|uhd|2160p)\b", re.I), "4K UHD"), (re.compile(r"\b1080[pi]\b", re.I), "1080p"),
         (re.compile(r"hdr\s*10\s*\+|hdr10\+", re.I), "HDR10+"), (re.compile(r"\bhdr(?:\s*10)?(?![\d+])", re.I), "HDR10"),
         (re.compile(r"dolby\s*vision", re.I), "Dolby Vision")]


def _video(texts: list[str]) -> list[str]:
    """What the box claims, in the order of VIDEO."""
    return [name for pat, name in VIDEO if any(pat.search(t) for t in texts)]


def _squash(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


def _title(srcs: list[Src]) -> str | None:
    """The title as the spine and the credits give it: the version found most often,
    with its words spaced."""
    cands: list[str] = []
    for s in srcs:
        t = clean(s.text)
        if s.line.region == "spine" and s.line.conf >= 60 and re.fullmatch(r"[A-Za-z][A-Za-z '&:-]{3,60}", t):
            cands.append(t)
        for q in QUOTED.findall(t):
            letters = re.sub(r"[^A-Za-z]", "", q)
            if len(letters) >= 4 and letters.isupper():
                cands.append(q.strip())
    if not cands:
        return None
    groups: dict[str, list[str]] = {}
    for c in cands:
        groups.setdefault(_squash(c), []).append(c)
    best = max(groups.values(), key=lambda g: (len(g), max(x.count(" ") for x in g)))
    title = max(best, key=lambda x: x.count(" "))
    if " " not in title:
        # Run together on the spine ("THEGREATESCAPE"): the words as the rest of the
        # box spells them, if it does.
        letters = _squash(title)
        pat = re.compile(r"\b" + r"[\s\-]*".join(map(re.escape, letters)) + r"\b", re.I)
        for s in srcs:
            if (m := pat.search(clean(s.text))) and " " in m.group(0):
                title = m.group(0)
                break
    return title_case(title)


def _cast(srcs: list[Src]) -> list[str]:
    """The top billing: the first line naming two or more people between + • ·."""
    for s in srcs:
        t = clean(s.text)
        parts = re.split(r"\s*[+•·~*]\s*", t)
        names = []
        for p in parts:
            p = re.split(r"[:;\"“]|\bin\b|\bis\b", p)[0]
            # Billing is in capitals; a word that isn't is a misread "Co-starring".
            words = [w for w in re.findall(r"[A-Za-z][A-Za-z'.-]+", p)
                     if sum(c.isupper() for c in w) > len(w) * 0.6]
            if 2 <= len(words) <= 3:
                names.append(" ".join(name_case(w) for w in words))
        if len(names) >= 2:
            return names
    return []


DETAIL_FIELDS = ["title", "year", "cast", "feature_runtime_min", "episode_count", "barcode", "catalogue_number",
                 "region", "video_system", "country", "locale", "aspect_ratio", "audio", "subtitles", "video"]


def _sim(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    na, nb = re.sub(r"[^a-z0-9 ]", "", a.lower()), re.sub(r"[^a-z0-9 ]", "", b.lower())
    return fuzz.ratio(na, nb) / 100 if na and nb else 0.0


def _plausible(reading: Listing) -> Listing:
    """A model's reading without what it copied onto every item: the box's total running
    time ("61 mins" against each of eleven 5-minute episodes), or a disc number on a box
    that isn't split by disc (every item "disc 1")."""
    times = [i.runtime_min for i in reading.items if i.runtime_min]
    total = reading.details.feature_runtime_min
    same_time = len(times) >= 3 and len(set(times)) == 1
    discs = [i.disc for i in reading.items]
    one_disc = len(discs) >= 2 and None not in discs and len(set(discs)) == 1
    if not any(t == total for t in times) and not same_time and not one_disc:
        return reading
    out = reading.model_copy(deep=True)
    for i in out.items:
        if i.runtime_min and (i.runtime_min == total or same_time):
            i.runtime_min = None
        if one_disc:
            i.disc = None
    return out


def _sim_item(a: str, b: str) -> float:
    """How alike two listed titles are, past the words they start with in common: "Pat's
    Special Delivery: Ice Skates" isn't "Pat's Special Delivery: A Treehouse"."""
    wa, wb = re.sub(r"[^a-z0-9 ]", "", a.lower()).split(), re.sub(r"[^a-z0-9 ]", "", b.lower()).split()
    n = 0
    while n < min(len(wa), len(wb)) and wa[n] == wb[n]:
        n += 1
    if n == len(wa) == len(wb):
        return 1.0 if wa else 0.0
    return _sim(" ".join(wa[n:]), " ".join(wb[n:])) if n else _sim(a, b)


def _in_line(title: str, text: str) -> float:
    """How well a title appears within a line of text read on the scan: each of its
    words (of three letters or more) found among the line's, allowing a misread letter."""
    from rapidfuzz import fuzz

    want = [w for w in re.sub(r"[^a-z0-9 ]", " ", title.lower()).split() if len(w) >= 3]
    have = re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()
    if not want or not have:
        return 0.0
    return min(max(fuzz.ratio(w, h) for h in have) for w in want) / 100


def merge(own: Listing, readings: list[tuple[str, Listing]], texts: list[ScanText]) -> Listing:
    """Tesseract's listing with what the vision model read (scan id, listing) added.

    - **Items** both found are one item, marked "both", with Tesseract's place on the
      scan; the model's type is taken where Tesseract only guessed "Extra". An item only
      the model found is added, marked "both" when its title is in a line Tesseract read
      (which gives its place on the scan), else "vision": unconfirmed.
    - **Details**: the barcode always from the bars when decoded; otherwise each field
      from Tesseract when it read it, else from the model. A region Tesseract only
      guessed gives way to one the model read off the box.
    """
    out = own.model_copy(deep=True)
    d = out.details
    for f in DETAIL_FIELDS:
        if getattr(d, f) not in (None, [], ""):
            d.sources[f] = "barcode" if f == "barcode" and d.barcode_source == "barcode" else "tesseract"
    lines = {t.scan_id: t.lines for t in texts}
    for scan_id, theirs in readings:
        theirs = _plausible(theirs)
        for vi in theirs.items:
            # The other show's name, listed by the model as an extra of its own, is the
            # bonus episode Tesseract found.
            if bonus := next((x for x in out.items if x.show and _sim(x.show, vi.title) >= 0.8), None):
                bonus.source = "both" if bonus.source in ("tesseract", "both") else bonus.source
                continue
            same = max((x for x in out.items if x.kind == vi.kind), key=lambda x: _sim_item(x.title, vi.title),
                       default=None)
            if same and _sim_item(same.title, vi.title) >= 0.8:
                same.source = "both" if same.source in ("tesseract", "both") else same.source
                if vi.show and not same.show:
                    same.show, same.extra_type = vi.show, "Other"
                if vi.extra_type and same.extra_type in (None, "Extra"):
                    same.extra_type = vi.extra_type
                if same.runtime_min is None and vi.runtime_min:
                    same.runtime_min = vi.runtime_min
                continue
            add = vi.model_copy(update={"scan_id": scan_id, "source": "vision"})
            hit = max(lines.get(scan_id, []), key=lambda ln: _in_line(vi.title, ln.text), default=None)
            if hit and _in_line(vi.title, hit.text) >= 0.85:
                add.box, add.text, add.source = hit.box, hit.text, "both"
            out.items.append(add)
        vd = theirs.details
        for f in DETAIL_FIELDS:
            mine, other = getattr(d, f), getattr(vd, f)
            if other in (None, [], ""):
                continue
            if f == "barcode" and (d.barcode_source == "barcode" or not release_info.valid_upc(other)):
                continue
            # A title Tesseract read only part of ("Postman" off the spine) gives way to
            # the model's whole one ("Postman Pat: Special Delivery Service").
            fragment = f == "title" and mine and _squash(mine) in _squash(other) and len(other) > len(mine) + 3
            if mine in (None, [], "") or (f == "region" and d.region_guessed) or fragment:
                setattr(d, f, other)
                d.sources[f] = "vision"
                if f == "region":
                    d.region_guessed = False
                if f == "barcode":
                    d.barcode_source = "text"
    return out


# A bonus episode of another show: "Includes Bonus Episode", "Plus meet Jess in an all
# new series" (but not "bonus features"), its title in quotes on a line of its own.
BONUS_CUE = re.compile(r"\bbonus\b(?!\s+(?:features?|material|content|disc|footage|scenes?))|"
                       r"\bp[li1]us,?\s+meet\b|\ball[- ]new series\b", re.I)
WHOLE_QUOTE = re.compile(r"^\W*" + QUOTED.pattern + r"\W*$")
MEET = re.compile(r"\bmeet\s+(?:[\w']+\s+){0,3}?((?:[A-Z][\w']*)(?:\s+(?:with|and|the|of|&|[A-Z][\w']*)){0,4})"
                  r"\s+in\s+an?\s+all[- ]new series", re.I)


def _bonus(srcs: list[Src], listable: list[Src], items: list[ListingItem], title: str | None) -> None:
    """Add the bonus episode of another show the box names, when it gives both the
    episode (quoted) and the show (quoted elsewhere on the box, as in its small print,
    or "meet ... in an all new series")."""
    if not any(BONUS_CUE.search(s.text) for s in srcs):
        return
    quotes = [(s, m.group(1)) for s in listable if (m := WHOLE_QUOTE.match(clean(s.text)))
              and len(m.group(1).split()) >= 2 and re.search(r"[A-Za-z]{3}", m.group(1))]
    if not quotes:
        return
    src, episode = quotes[0]
    film = _squash(title or "")
    shows: list[str] = []
    for s in srcs:
        text = clean(s.text)
        shows += [m.group(1) for m in MEET.finditer(text)]
        shows += [q for q in QUOTED.findall(text) if s is not src and 2 <= len(q.split()) <= 5
                  and _title_like(q) and not q.rstrip().endswith("?")]
    shows = [x.strip() for x in shows if _squash(x) not in (_squash(episode), film) and not (film and film in _squash(x))]
    if not shows:
        return
    show = Counter(shows).most_common(1)[0][0]
    name = title_case(_tidy_title(episode))
    for it in list(items):
        if _squash(it.title) == _squash(show):
            items.remove(it)  # the show's name read as an extra of its own
    if same := next((it for it in items if _squash(it.title) == _squash(name)), None):
        same.kind, same.show, same.extra_type = "extra", show, "Other"
        return
    items.append(ListingItem(kind="extra", title=name, extra_type="Other", show=show, text=clean(src.text),
                             scan_id=src.scan_id, box=src.line.box, source="tesseract"))


def build(texts: list[ScanText]) -> Listing:
    """The listing from the scans' readings, the back's first."""
    srcs: list[Src] = []
    more: list[Src] = []
    barcodes: list[str] = []
    for st in texts:
        for ln in sorted(st.lines, key=lambda ln: REGION_ORDER.get(ln.region, 9)):
            srcs.append(Src(ln.text, ln, st.scan_id))
        more += [Src(ln.text, ln, st.scan_id) for ln in st.more]
        barcodes += st.barcodes
    listable = [s for s in srcs if s.line.region in LIST_REGIONS]
    items = _sections(listable)
    taken = {id(s) for s in listable if any(s.line.box == it.box for it in items)}
    if not any(it.kind == "episode" for it in items):
        items += _columns(listable, taken)
    out = Listing(items=items, details=_details(srcs, more, barcodes))
    _bonus(srcs, listable, out.items, out.details.title)
    eps = [i for i in items if i.kind == "episode"]
    n = out.details.episode_count
    if n and eps and n != len(eps):
        out.notes.append(f"The box says {n} episodes, and {len(eps)} were read")
    return out
