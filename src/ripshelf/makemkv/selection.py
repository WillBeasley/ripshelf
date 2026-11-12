"""Which audio and subtitle tracks MakeMKV keeps.

makemkvcon has no option for picking tracks. It applies its default selection rule
(``app_DefaultSelectionString`` in settings.conf, "Default selection rule" in the
app's expert preferences) to every title it saves, so ripshelf writes the rule into
the drive's MakeMKV home before each rip (``runner.MakeMKV.run(conf=...)``; a worker
does the same with the rule the controller sends).

A rule is a comma-separated list of ``action:condition``, applied in order. ``+sel``
selects the tracks the condition matches and ``-sel`` unselects them. Conditions
combine tokens with ``|`` (or), ``&`` (and), ``!`` (not) and brackets. The tokens
used here: a language code ("eng"), ``all``, ``audio``, ``subtitle``, ``mvcvideo``
(the 3D layer), ``nolang`` (no language given), ``single`` (the only track of its
kind), ``special`` (commentary and the like), ``forced``, ``havemulti`` (a stereo
track with a surround one in the same language) and ``havecore`` (the core of an HD
track that is kept whole). MakeMKV's own default (2.0) is
``-sel:all,+sel:(favlang|nolang),-sel:mvcvideo,=100:all,-10:favlang``, where favlang is
the preferred language (English unless set). Expert mode is turned on with a rule, as
the app only offers the rule in expert mode.

The generated rule keeps every video track except the 3D layer, as the default
does, then adds audio and subtitles as chosen in Settings.
"""

from __future__ import annotations

from ..config import Settings

RULE_KEY = "app_DefaultSelectionString"

# Languages with two ISO 639-2 codes (bibliographic, terminology). Discs use either,
# so a rule names both.
_TWIN_CODES = {
    "alb": "sqi", "arm": "hye", "baq": "eus", "bur": "mya", "chi": "zho", "cze": "ces",
    "dut": "nld", "fre": "fra", "geo": "kat", "ger": "deu", "gre": "ell", "ice": "isl",
    "mac": "mkd", "mao": "mri", "may": "msa", "per": "fas", "rum": "ron", "slo": "slk",
    "tib": "bod", "wel": "cym",
}
_TWIN_CODES |= {t: b for b, t in _TWIN_CODES.items()}


def _languages(codes: list[str]) -> str:
    """``(eng|fre|fra)``, or "" for no languages."""
    out: list[str] = []
    for c in codes:
        out += [c] + ([_TWIN_CODES[c]] if c in _TWIN_CODES else [])
    return "(" + "|".join(dict.fromkeys(out)) + ")" if out else ""


def rule(s: Settings) -> str | None:
    """The selection rule for ``s``, or None to leave MakeMKV's own."""
    if not s.track_selection:
        return None
    if s.track_rule and s.track_rule.strip():
        return s.track_rule.strip()
    langs = _languages(s.track_languages)
    parts = ["+sel:all", "-sel:(audio|subtitle)", "-sel:mvcvideo"]
    if s.track_audio == "all":
        parts.append("+sel:audio")
    else:
        parts.append(f"+sel:audio&({langs[1:-1] + '|' if langs else ''}nolang|single)")
        parts.append("-sel:audio&(havemulti|havecore)")
    if not s.track_commentary:
        parts.append("-sel:audio&special")
    if s.track_subtitles == "all":
        parts.append("+sel:subtitle")
    elif langs and s.track_subtitles == "languages":
        parts.append(f"+sel:subtitle&{langs}")
    elif langs and s.track_subtitles == "forced":
        parts.append(f"+sel:subtitle&forced&{langs}")
    return ",".join(parts)


def conf(s: Settings) -> dict[str, str | None]:
    """The settings.conf lines a rip sets (None removes a line, back to MakeMKV's default)."""
    r = rule(s)
    return {RULE_KEY: r, "app_ExpertMode": "1"} if r else {RULE_KEY: None}
