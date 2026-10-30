"""Season, disc and volume numbers in TV disc labels.

    FAWLTY_TOWERS_S1_D2, THE_OFFICE_SEASON_2_DISC_1, DOCTOR_WHO_SERIES_1_VOL_3,
    FRIENDS_S03D04, SARAH_&_DUCK_BROLLY_BUS (no numbers, so a compilation or a film)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..identify.label import parse_label, title_case

_SEP = r"[ _.\-]*"
# Combined forms first, like S01D02 and S1_D2. S01E01 is not a disc label but is caught as season.
_COMBINED = re.compile(rf"\bS(?:EASON)?{_SEP}(\d{{1,2}}){_SEP}(?:D|DISC|DISK|DVD|BD){_SEP}(\d{{1,2}})\b", re.I)
_SEASON = re.compile(rf"\b(?:SEASON|SERIES|SAISON|STAFFEL|S)(?![A-Z]){_SEP}(\d{{1,2}})\b", re.I)
_DISC = re.compile(rf"\b(?:DISC|DISK|D|DVD|BD|CD)(?![A-Z]){_SEP}(\d{{1,2}})\b", re.I)
_VOLUME = re.compile(rf"\b(?:VOLUME|VOL|V)(?![A-Z]){_SEP}(\d{{1,2}})\b", re.I)
_TV_WORDS = re.compile(r"\b(SEASON|SERIES|EPISODES?|COMPLETE|BOX[ _]?SET|STAFFEL|SAISON)\b", re.I)


@dataclass
class TvLabel:
    raw: str
    show: str  # cleaned show name, title-cased
    season: int | None = None
    disc: int | None = None
    volume: int | None = None
    hints: list[str] = field(default_factory=list)  # human-readable reasons it looks like TV

    @property
    def looks_like_tv(self) -> bool:
        return bool(self.hints)


def parse_tv_label(label: str | None) -> TvLabel:
    raw = (label or "").strip()
    s = re.sub(r"[_.]+", " ", raw).upper()
    hints: list[str] = []
    season = disc = volume = None

    m = _COMBINED.search(s)
    if m:
        season, disc = int(m.group(1)), int(m.group(2))
        hints.append(f"label has season {season}, disc {disc}")
        s = s[: m.start()] + " " + s[m.end():]
    else:
        m = _SEASON.search(s)
        if m:
            season = int(m.group(1))
            hints.append(f"label has season {season}")
            s = s[: m.start()] + " " + s[m.end():]
        m = _DISC.search(s)
        if m:
            disc = int(m.group(1))
            s = s[: m.start()] + " " + s[m.end():]
    m = _VOLUME.search(s)
    if m:
        volume = int(m.group(1))
        hints.append(f"label has volume {volume}")
        s = s[: m.start()] + " " + s[m.end():]
    w = _TV_WORDS.search(s)
    if w:
        hints.append(f"label says “{w.group(1).title()}”")
        s = _TV_WORDS.sub(" ", s)
    show = parse_label(s.strip().replace(" ", "_")).title if s.strip() else ""
    return TvLabel(raw=raw, show=title_case(show) if show.isupper() else show, season=season,
                   disc=disc, volume=volume, hints=hints)


# "The Complete Series", "Complete Season", "Box Set" count only at either end of a name, so a
# show called "A Series of Unfortunate Events" keeps its words.
_EDGE_WORDS = re.compile(r"(?:\s+(?:THE|COMPLETE|SERIES|SEASONS?|BOX[ _]?SET|COLLECTION))+$", re.I)
_NUMBERS = ["ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN"]
_ORDINALS = ["FIRST", "SECOND", "THIRD", "FOURTH", "FIFTH", "SIXTH", "SEVENTH", "EIGHTH", "NINTH", "TENTH"]
# "The Complete Second Season", "Season Two"
_WORD_SEASON = re.compile(rf"\b(?:THE\s+)?(?:COMPLETE\s+)?(?:({'|'.join(_ORDINALS)})\s+(?:SEASON|SERIES)"
                          rf"|(?:SEASON|SERIES)\s+({'|'.join(_NUMBERS)}))\b", re.I)


def split_show_name(name: str | None) -> TvLabel:
    """A disc name or typed search ("Boardwalk Empire Season 1 Disc 2") split into the
    show's name, in its own case, and the season, disc and volume numbers."""
    raw = (name or "").strip()
    s = re.sub(r"[_]+", " ", raw)
    season = disc = volume = None
    m = _COMBINED.search(s)
    if m:
        season, disc = int(m.group(1)), int(m.group(2))
        s = s[: m.start()] + " " + s[m.end():]
    else:
        if m := _SEASON.search(s):
            season = int(m.group(1))
            s = s[: m.start()] + " " + s[m.end():]
        elif m := _WORD_SEASON.search(s):
            word = (m.group(1) or m.group(2)).upper()
            season = (_ORDINALS.index(word) if m.group(1) else _NUMBERS.index(word)) + 1
            s = s[: m.start()] + " " + s[m.end():]
        if m := _DISC.search(s):
            disc = int(m.group(1))
            s = s[: m.start()] + " " + s[m.end():]
    if m := _VOLUME.search(s):
        volume = int(m.group(1))
        s = s[: m.start()] + " " + s[m.end():]
    s = re.sub(r"\s+", " ", s).strip(" -–:,()[]")
    if season is not None or disc is not None or volume is not None:
        s = _EDGE_WORDS.sub("", s).strip(" -–:,()[]")
    hints = [f"season {season}"] * (season is not None)
    return TvLabel(raw=raw, show=re.sub(r"\s+", " ", s) or raw, season=season, disc=disc, volume=volume,
                   hints=hints)
