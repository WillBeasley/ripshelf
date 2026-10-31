"""Turn disc labels into search queries.

Volume labels are upper case, underscore separated and often truncated to 32
characters, with production noise appended (disc numbers, region, aspect,
edition). The Blu-ray ``bdmt_*.xml`` title (also reported by MakeMKV as the
disc name) is usually the real title and is preferred when present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Whole tokens that carry no title information.
NOISE_TOKENS = {
    "DISC", "DISK", "DVD", "DVD5", "DVD9", "BD", "BD25", "BD50", "BD66", "BD100", "BLURAY",
    "BLU", "RAY", "UHD", "4K", "2160P", "1080P", "HD", "WS", "FS", "WIDESCREEN", "FULLSCREEN",
    "PAL", "NTSC", "UK", "US", "USA", "EU", "EUR", "GER", "FR", "R1", "R2", "R4", "RA", "RB",
    # Release regions: WW is worldwide, INT/INTL international, SCAN Scandinavian.
    "WW", "INT", "INTL", "NORDIC", "SCAN", "AU", "AUS", "JP", "JPN", "FRA", "ESP", "ITA", "NL", "BENELUX",
    "KOR",
    "16X9", "4X3", "ANA", "ANAMORPHIC", "SE", "CE", "LE", "DTS", "AC3", "EXT", "EXTENDED", "DC",
}
# Multi-token phrases dropped wherever they appear.
NOISE_PHRASES = [
    "SPECIAL EDITION", "COLLECTORS EDITION", "COLLECTOR S EDITION", "DIRECTORS CUT",
    "DIRECTOR S CUT", "EXTENDED EDITION", "EXTENDED CUT", "THEATRICAL CUT", "ULTIMATE EDITION",
    "ANNIVERSARY EDITION", "LIMITED EDITION", "UNRATED", "REMASTERED", "STEELBOOK", "SIDE A",
    "SIDE B",
]
DISC_NUMBER = re.compile(r"^(?:D|DISC|DISK|CD|DVD|BD)?\d{1,2}$")
YEAR = re.compile(r"^(19[2-9]\d|20[0-2]\d)$")
SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "vs"}
GENERIC_LABELS = {
    "DVD_VIDEO", "DVDVOLUME", "BDROM", "BD_ROM", "LOGICAL_VOLUME_ID", "VOLUME", "DISC", "MOVIE",
    "BLURAY", "UNTITLED", "NO_LABEL",
}


@dataclass
class ParsedLabel:
    raw: str
    title: str  # cleaned, title-cased
    year: int | None = None
    disc_number: int | None = None
    generic: bool = False
    dropped: list[str] = field(default_factory=list)
    extended: bool = False  # extended / director's / unrated cut, longer than the theatrical runtime
    # The disc number came from a bare trailing digit, which may be a sequel ("GRUMBLE_2").
    bare_number: bool = False

    @property
    def queries(self) -> list[str]:
        if self.generic or not self.title:
            return []
        out = [self.title]
        if self.bare_number and self.disc_number is not None:
            out.append(f"{self.title} {self.disc_number}")
        words = self.title.split()
        # For truncated labels, also try without the last (possibly cut) word.
        if len(self.raw) >= 30 and len(words) > 2:
            out.append(drop_last_word(self.title))
        return out


def drop_last_word(query: str) -> str:
    """The query without its last word, and any small words left dangling ("of the")."""
    words = query.split()[:-1]
    while len(words) > 1 and words[-1].lower() in SMALL_WORDS:
        words = words[:-1]
    return " ".join(words)


def title_case(s: str) -> str:
    words = s.lower().split()
    out = []
    for i, w in enumerate(words):
        if 0 < i < len(words) - 1 and w in SMALL_WORDS:
            out.append(w)
        elif re.fullmatch(r"[ivx]+", w) and len(w) <= 4 and i > 0:
            out.append(w.upper())  # roman numerals, as in Rocky II
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def parse_label(label: str | None) -> ParsedLabel:
    raw = (label or "").strip()
    if not raw or raw.upper() in GENERIC_LABELS:
        return ParsedLabel(raw=raw, title="", generic=True)
    s = raw.upper().replace("_", " ").replace(".", " ").replace("-", " ").replace("&", " & ")
    s = re.sub(r"\s+", " ", s).strip()
    # "S1D2" / "SEASON 1 DISC 2" style markers are a TV concern; strip for movies.
    dropped: list[str] = []
    bare = False
    for phrase in NOISE_PHRASES:
        if phrase in s:
            s = s.replace(phrase, " ")
            dropped.append(phrase.title())
    tokens = s.split()
    year = None
    disc_no = None
    keep: list[str] = []
    for i, tok in enumerate(tokens):
        m = DISC_NUMBER.match(tok)
        prev = tokens[i - 1] if i else ""
        if YEAR.match(tok) and i > 0:
            year = int(tok)
            dropped.append(tok)
        elif m and i > 0 and (prev in {"DISC", "DISK", "D", "CD"} or not tok.isdigit()):
            disc_no = int(re.sub(r"\D", "", tok))
            dropped.append(tok)
        elif tok in NOISE_TOKENS and i > 0:
            dropped.append(tok)
        elif tok.isdigit() and i == len(tokens) - 1 and i > 0 and len(tok) == 1 and disc_no is None:
            # a trailing lone digit is usually the disc number ("MOVIE_NAME_1")
            disc_no = int(tok)
            bare = True
            dropped.append(tok)
        else:
            keep.append(tok)
    extended = any(w in ("EXT", "EXTENDED", "DC", "UNRATED") for w in s.split()) or any(
        p in dropped for p in ("Extended Edition", "Extended Cut", "Directors Cut", "Director S Cut", "Unrated"))
    title = title_case(" ".join(keep))
    title = re.sub(r"\s*&\s*", " & ", title).strip()
    return ParsedLabel(raw=raw, title=title, year=year, disc_number=disc_no, dropped=dropped, extended=extended,
                       bare_number=bare)


def clean_bdmt_title(name: str | None) -> str | None:
    """bdmt / MakeMKV disc names sometimes carry " - Blu-ray" or "(Disc 1)" suffixes."""
    if not name:
        return None
    s = re.sub(r"\s*[-–:]\s*(4K\s*)?(Ultra HD|UHD|Blu-ray|Blu ray|BD)\b.*$", "", name, flags=re.IGNORECASE)
    s = re.sub(r"\s*\((Disc|Disk)\s*\d+\)\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def name_differs_from_label(name: str | None, label: str | None) -> bool:
    """True when MakeMKV's disc name is a real title, not just the volume label echoed."""
    if not name:
        return False
    return name.strip().upper() != (label or "").strip().upper()
