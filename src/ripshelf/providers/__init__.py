"""Metadata providers, where identification looks films, shows and discs up.

Like Emby's per-library "metadata downloaders", each kind of lookup has its own
ordered list of providers, each switched on or off in Settings:

  movie     films: a disc database first (the disc's fingerprint), then title
            searches. Every enabled search is asked and the results merged; a
            film found by several providers says so.
  tv        TV shows: asked in order until one finds the show (a later one is a
            fallback). TMDB stays the show's identity, so hits from TheTVDB or
            TVmaze are looked up on TMDB through their TMDB, TheTVDB or IMDb ids.
  episodes  a season's episode list: the first provider that has the season.

A provider that needs a key is skipped until the key is set. Keys and other
provider settings are shared by every list they appear in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings

LISTS = {"movie": "Films", "tv": "TV shows", "episodes": "TV episodes"}


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    description: str
    lists: tuple[str, ...]
    keys: tuple[str, ...] = ()  # settings that must be set before it is used
    site: str = ""  # where to get a key, or the project's home
    check: str | None = None  # the Settings test for it (checks.CHECKS)


PROVIDERS: dict[str, Provider] = {p.name: p for p in (
    Provider("thediscdb", "TheDiscDB",
             "Community database of discs. Recognises the disc itself from its fingerprint, with which title is "
             "the film or which episode. Free, no key.",
             ("movie", "tv"), site="https://thediscdb.com", check="thediscdb"),
    Provider("tmdb", "TMDB",
             "The Movie Database, for films, shows and episodes. Names everything ripshelf files, so it is needed "
             "for TV.", ("movie", "tv", "episodes"), keys=("tmdb_api_key",),
             site="https://www.themoviedb.org/settings/api", check="tmdb"),
    Provider("tvdb", "TheTVDB",
             "Shows and episodes, in aired or DVD order. Finds shows TMDB's search misses.",
             ("tv", "episodes"), keys=("tvdb_api_key",), site="https://thetvdb.com/api-information", check="tvdb"),
    Provider("tvmaze", "TVmaze",
             "Shows and episodes. Free, with no key, as a fallback when there is no TheTVDB key.",
             ("tv", "episodes"), site="https://www.tvmaze.com/api", check="tvmaze"),
    Provider("omdb", "OMDb",
             "IMDb's titles, years and runtimes, for a second film search and a cross-check of TMDB's.",
             ("movie",), keys=("omdb_api_key",), site="https://www.omdbapi.com/apikey.aspx", check="omdb"),
)}

# The order a fresh install starts with. TMDB and TheTVDB are on, skipped until
# their key is set; OMDb starts off.
DEFAULTS = {
    "movie": [("thediscdb", True), ("tmdb", True), ("omdb", False)],
    "tv": [("thediscdb", True), ("tmdb", True), ("tvdb", True), ("tvmaze", True)],
    "episodes": [("tvdb", True), ("tmdb", True), ("tvmaze", True)],
}


def defaults(tv_episode_order: str = "tvdb_aired") -> dict[str, list[dict[str, Any]]]:
    out = {k: [{"name": n, "enabled": on} for n, on in v] for k, v in DEFAULTS.items()}
    if tv_episode_order == "tmdb":  # legacy "TMDB only", so TheTVDB is off for episodes
        out["episodes"] = [{"name": "tmdb", "enabled": True}, {"name": "tvdb", "enabled": False},
                           {"name": "tvmaze", "enabled": True}]
    return out


def normalise(value: dict[str, Any] | None, tv_episode_order: str = "tvdb_aired") -> dict[str, list[dict[str, Any]]]:
    """Every list with every provider that belongs in it, once. Unknown names and
    duplicates dropped, missing providers appended as in the defaults."""
    base = defaults(tv_episode_order)
    out: dict[str, list[dict[str, Any]]] = {}
    for lst, default in base.items():
        given = (value or {}).get(lst)
        if not isinstance(given, list):
            out[lst] = default
            continue
        rows: list[dict[str, Any]] = []
        for r in given:
            name = r.get("name") if isinstance(r, dict) else None
            p = PROVIDERS.get(name) if isinstance(name, str) else None
            if p and lst in p.lists and all(x["name"] != name for x in rows):
                rows.append({"name": name, "enabled": bool(r.get("enabled", True))})
        rows += [d for d in default if all(x["name"] != d["name"] for x in rows)]
        out[lst] = rows
    return out


def lists(s: Settings) -> dict[str, list[dict[str, Any]]]:
    return normalise(s.metadata_providers, s.tv_episode_order)


def configured(s: Settings, name: str) -> bool:
    return all(s.secret(k) for k in PROVIDERS[name].keys)


def enabled(s: Settings, lst: str) -> list[str]:
    """The providers to ask for ``lst``, in order, if switched on and with their keys set."""
    return [r["name"] for r in lists(s)[lst] if r["enabled"] and configured(s, r["name"])]


def label(name: str) -> str:
    p = PROVIDERS.get(name)
    return p.label if p else name
