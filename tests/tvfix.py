"""Serve the TMDB TV fixtures through respx."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ripshelf.identify.tmdb import API

TV = Path(__file__).parent / "fixtures" / "tmdb_tv"


def load(name: str) -> dict:
    return json.loads((TV / f"{name}.json").read_text())


def mock_show(router, name: str, search_override: list | None = None) -> dict:
    f = load(name)
    sid = f["show"]["id"]
    results = search_override if search_override is not None else f["search"]["results"]
    router.get(f"{API}/search/tv").mock(return_value=httpx.Response(200, json={"results": results}))
    router.get(f"{API}/tv/{sid}").mock(return_value=httpx.Response(200, json=f["show"]))
    for n, season in f["seasons"].items():
        router.get(f"{API}/tv/{sid}/season/{n}").mock(return_value=httpx.Response(200, json=season))
    return f
