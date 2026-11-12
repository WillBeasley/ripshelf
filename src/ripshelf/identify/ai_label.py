"""Reading a cryptic disc label with a language model ("BTTF2" is Back to the Future Part II).

Small models are good at one part of this and bad at another. Given a cryptic volume
label they expand it into the right full title in a few seconds, even on a CPU. Asked
to pick between search results they are unreliable (one chose a film because its
runtime fitted and ignored the title). So the work is split:

  1. the model expands the label into a few likely full titles (JSON reply);
  2. ripshelf searches for them with the film or show searches in Settings and
     scores every result with its usual title + runtime + year rules.

A wrong expansion then just scores low, rather than giving a confident wrong answer.
A match only the model's reading supports counts for less than one the label itself
supports, and never confirms without review.

The model is only told what the disc itself says (its label, the title on a Blu-ray,
running times): nothing any metadata provider returned is sent to it. It runs on the
vision model's connection (Settings → Scanner), with its own model name if set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from ..models import Candidate, DiscFacts, Evidence, Show
from ..scanner.vision import Provider, VisionError
from . import scoring

MAX_GUESSES = 5
# A match only a model guess supports counts for less than one the label itself supports,
# because a garbled guess can match some other title exactly.
GUESS_WEIGHT = 0.85

SCHEMA = {
    "type": "object",
    "properties": {
        "titles": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": "string", "enum": ["movie", "tv", "unknown"]},
    },
    "required": ["titles", "kind"],
    "additionalProperties": False,
}

PROMPT = (
    "You expand cryptic DVD and Blu-ray volume labels into the full official titles of films or TV series. "
    "Labels are often upper case with underscores, abbreviated (initials, sequel or part numbers) or cut "
    "off at 32 characters, and may carry disc numbers, regions or edition words (EXT, SE, DC, DISC1, R2) "
    "that are not part of the title. Use the other facts given (the disc's own title, running times) to "
    "decide. Reply with JSON: titles is up to 5 full titles, most likely first, as they are usually listed, "
    "without edition or disc words; kind is movie, tv, or unknown.\n\n")

# Edition and disc words a model sometimes keeps in its titles.
_NOISE = re.compile(r"\s*(\((?:[^)]*)\)|\b(?:disc|disk|dvd|bd)\s*\d+\b|\b(?:extended|special|collector'?s|"
                    r"ultimate|director'?s|theatrical|anniversary|limited)(?:\s+(?:edition|cut))?\b|\bedition\b)",
                    re.I)


class LabelError(Exception):
    pass


@dataclass
class Reading:
    """What the model made of the label."""

    titles: list[str]
    kind: str | None  # movie, tv or None
    seconds: float
    model: str


class _Reply(BaseModel):
    titles: list[str]
    kind: str | None = None


def describe(facts: DiscFacts, disc_name: str | None, durations: list[int], tv: bool) -> str:
    """What the disc says about itself, for the prompt."""
    lines = [f"Volume label: {facts.label or '(none)'}"]
    if disc_name and disc_name != facts.label:
        lines.append(f"Disc title (from the disc): {disc_name}")
    if facts.kind:
        lines.append(f"Disc type: {facts.kind.value}")
    if facts.main_duration_s and not tv:
        lines.append(f"Main feature: {scoring.fmt_duration(facts.main_duration_s)}"
                     + (" (PAL, 25 fps, so the film may be about 4% longer at 24 fps)" if facts.pal else ""))
    if durations:
        lens = ", ".join(scoring.fmt_duration(d) for d in durations[:12])
        lines.append(f"Titles on the disc ({len(durations)}): {lens}")
    if tv:
        lines.append("It looks like a TV series disc (several episode-length titles).")
    return "\n".join(lines)


def clean_title(t: str) -> str:
    t = _NOISE.sub(" ", t)
    return re.sub(r"\s*[-–:]\s*$", "", re.sub(r"\s+", " ", t)).strip(" -–:,")


def parse(raw: dict) -> tuple[list[str], str | None]:
    """Titles and kind from the model's reply."""
    try:
        r = _Reply.model_validate(raw)
    except ValidationError:
        raise LabelError("the reply isn't a list of titles") from None
    out: list[str] = []
    for t in r.titles:
        c = clean_title(t)[:120]
        if c and c.lower() not in {o.lower() for o in out}:
            out.append(c)
    return out[:MAX_GUESSES], r.kind if r.kind in ("movie", "tv") else None


async def read(provider: Provider, facts: DiscFacts, disc_name: str | None, durations: list[int],
               tv: bool) -> Reading:
    """Ask the model what the label stands for."""
    try:
        raw, secs = await provider.ask(PROMPT + describe(facts, disc_name, durations, tv), schema=SCHEMA,
                                       name="label_reading")
    except VisionError as e:
        raise LabelError(str(e)) from None
    titles, kind = parse(raw)
    return Reading(titles=titles, kind=kind, seconds=secs, model=provider.model)


def _note(guess: str) -> Evidence:
    return Evidence(ok=None, text=f"The AI model read the label as “{guess}”; that alone never confirms")


def _best_guess(title: str, original: str | None, guesses: list[str]) -> str:
    return max(guesses, key=lambda g: max(scoring.title_similarity(g, title),
                                          scoring.title_similarity(g, original or "")))


def weigh_films(candidates: list[Candidate], facts: DiscFacts, guesses: list[str],
                found_by_ai: list[Candidate]) -> list[Candidate]:
    """Rank films both against the label's own reading and the model's, keeping the better
    score for each (the model's weighted down). The ones only the model's reading
    supports, or only the searches for it found, say so and are marked to never
    confirm on their own."""
    by_label = scoring.rank([c.model_copy(deep=True) for c in candidates], facts)
    by_guess = scoring.rank([c.model_copy(deep=True) for c in candidates],
                            facts.model_copy(update={"names": guesses}))
    out = []
    for lab in by_label:
        g = next((x for x in by_guess if _same(x, lab)), None)
        new = any(_same(lab, c) for c in found_by_ai)
        if g is None or g.excluded or (facts.names and not lab.excluded and lab.score >= g.score * GUESS_WEIGHT):
            if new:
                lab.from_ai = True
                lab.evidence.insert(0, _note(_best_guess(lab.title, lab.original_title, guesses)))
            out.append(lab)
            continue
        g.score = round(g.score * GUESS_WEIGHT, 3)
        g.from_ai = True
        g.evidence.insert(0, _note(_best_guess(g.title, g.original_title, guesses)))
        out.append(g)
    return sorted(out, key=scoring._order)


def _same(a: Candidate, b: Candidate) -> bool:
    if a.tmdb_id or b.tmdb_id:
        return a.tmdb_id == b.tmdb_id
    if a.imdb_id or b.imdb_id:
        return a.imdb_id == b.imdb_id
    return (a.title, a.year) == (b.title, b.year)


def weigh_shows(shows: list[Show], guesses: list[str]) -> list[Show]:
    """Shows found by searching for the model's reading: weighted down, marked, and saying so."""
    for s in shows:
        s.score = round(s.score * GUESS_WEIGHT, 3)
        s.from_ai = True
        s.evidence.insert(0, _note(_best_guess(s.name, s.original_name, guesses)))
    return shows
