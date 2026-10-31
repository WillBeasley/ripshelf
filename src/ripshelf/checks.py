"""The Settings page's "Test" buttons, which try a connection before saving it.

Each check takes the values typed into the form; a blank secret means "the
saved one". Nothing is saved, and nothing is written outside a throwaway file.
"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import httpx

from . import integrations
from .config import EULA_PENDING, Settings
from .identify import catalogue as catalogue_lookup
from .identify.tmdb import API as TMDB_API
from .makemkv import runner
from .models import MediaServer, SettingsTestResult, SettingsUpdate
from .providers.omdb import Omdb, OmdbError
from .providers.thediscdb import DiscDbError, TheDiscDb
from .providers.tvmaze import Tvmaze, TvmazeError
from .tv.tvdb import Tvdb, TvdbError


def _value(s: Settings, v: SettingsUpdate, name: str) -> str | None:
    """The form's value, or the saved one when the form left it blank."""
    typed = getattr(v, name, None)
    if isinstance(typed, str) and typed.strip():
        return typed.strip()
    current = getattr(s, name, None)
    if hasattr(current, "get_secret_value"):
        return current.get_secret_value() or None
    return current or None


def _fail(message: str, *details: str) -> SettingsTestResult:
    return SettingsTestResult(ok=False, message=message, details=list(details))


async def tmdb(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    key = _value(s, v, "tmdb_api_key")
    if not key:
        return _fail("No TMDB key")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{TMDB_API}/configuration", params={"api_key": key})
    except httpx.HTTPError as e:
        return _fail(f"Could not reach TMDB ({type(e).__name__})")
    if r.status_code == 401:
        return _fail("TMDB refused the key (use the v3 API key, not the read access token)")
    if r.status_code >= 400:
        return _fail(f"TMDB returned HTTP {r.status_code}")
    return SettingsTestResult(ok=True, message="TMDB accepted the key")


async def tvdb(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    key = _value(s, v, "tvdb_api_key")
    if not key:
        return _fail("No TheTVDB key")
    client = Tvdb(key, _value(s, v, "tvdb_pin"))
    try:
        await client.login()
        hits = await client.search_series("Garden")
    except TvdbError as e:
        msg = str(e)
        return _fail(msg[:1].upper() + msg[1:])
    finally:
        await client.aclose()
    return SettingsTestResult(ok=True, message="TheTVDB accepted the key",
                              details=[f"a test search found {len(hits)} series"])


async def omdb(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    key = _value(s, v, "omdb_api_key")
    if not key:
        return _fail("No OMDb key")
    client = Omdb(key)
    try:
        hits = await client.search("Garden")
    except OmdbError as e:
        return _fail(str(e))
    finally:
        await client.aclose()
    return SettingsTestResult(ok=True, message="OMDb accepted the key", details=[f"a test search found {len(hits)} titles"])


async def tvmaze(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    client = Tvmaze()
    try:
        hits = await client.search("Garden")
    except TvmazeError as e:
        msg = str(e)
        return _fail(msg[:1].upper() + msg[1:])
    finally:
        await client.aclose()
    return SettingsTestResult(ok=True, message="TVmaze answered (it needs no key)", details=[f"a test search found {len(hits)} shows"])


async def thediscdb(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    client = TheDiscDb()
    try:
        hits = await client.search("Boardwalk Empire")
    except DiscDbError as e:
        msg = str(e)
        return _fail(msg[:1].upper() + msg[1:])
    finally:
        await client.aclose()
    return SettingsTestResult(ok=True, message="TheDiscDB answered (it needs no key)", details=[f"a test search found {len(hits)} entries"])


async def pushover(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    user, token = _value(s, v, "pushover_user_key"), _value(s, v, "pushover_app_token")
    if not (user and token):
        return _fail("Pushover needs both the user key and the application token")
    test = s.model_copy(update={"public_url": _value(s, v, "public_url"),
                                "pushover_device": _value(s, v, "pushover_device")})
    note = integrations.Note("done", "ripshelf test", "Notifications work.", "/settings", "Open ripshelf")
    err = await integrations.pushover(test, note, 0, user_key=user, app_token=token)
    if err:
        return _fail(err)
    link = integrations.link(test, "/settings")
    return SettingsTestResult(ok=True, message="Sent a test message",
                              details=[f"link: {link}" if link else "no link, so set this UI's address first"])


def media_server(server: MediaServer):
    """The Test button for Emby, Jellyfin or Plex. It connects, lists the libraries,
    and checks the ones picked are still there."""
    name, key_field = integrations.SERVERS[server], integrations.server_key(server)
    what = "token" if server == "plex" else "API key"

    async def check(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
        url, key = _value(s, v, f"{server}_url"), _value(s, v, key_field)
        if not (url and key):
            return _fail(f"{name} needs the URL and {'a token' if server == 'plex' else 'an API key'}")
        # Plex describes itself at its root, Emby and Jellyfin at /System/Info.
        path = "/" if server == "plex" else "/System/Info"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(url.rstrip("/") + path, headers=integrations.server_headers(server, key))
        except httpx.HTTPError as e:
            return _fail(f"Could not reach {name} at {url} ({type(e).__name__})")
        if r.status_code in (401, 403):
            return _fail(f"{name} refused the {what}")
        if r.status_code >= 400:
            return _fail(f"{name} returned HTTP {r.status_code}")
        try:
            info = r.json()
            if server == "plex":
                info = info["MediaContainer"]
                info = {"ServerName": info.get("friendlyName"), "Version": info.get("version")}
        except (ValueError, KeyError, TypeError):
            return _fail(f"{url} answered, but not like {name}")
        details = [f"version {info.get('Version') or '?'}"]
        try:
            libraries = {lib.id: lib for lib in await integrations.server_libraries(server, url, key)}
        except integrations.ServerError as e:
            return _fail(f"Connected, but could not list the libraries: {e}")
        for label, kind in (("Movies", "movies"), ("TV", "tv")):
            lib_id, path = _value(s, v, f"{server}_{kind}_library"), _value(s, v, f"{server}_{kind}_path")
            lib = libraries.get(lib_id) if lib_id else None
            if lib_id and not lib:
                return _fail(f"The {label} library chosen is no longer in {name}, so pick it again")
            details.append(f"{label} → {lib.name} ({path})" if lib else f"{label}: no library chosen")
        return SettingsTestResult(ok=True, message=f"Connected to {info.get('ServerName') or name}, "
                                                   f"{len(libraries)} libraries", details=details)

    return check


emby = media_server("emby")


async def catalogue(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    """Look up a known catalogue number (EO51710 is The Mortal Instruments: City of Bones)."""
    test = s.model_copy(update={
        "catalogue_search": v.catalogue_search or s.catalogue_search,
        "searxng_url": _value(s, v, "searxng_url"),
    })
    if test.catalogue_search == "off":
        return _fail("Catalogue lookup is off")
    started = time.monotonic()
    try:
        found = await catalogue_lookup.lookup(test, "EO51710", "dvd")
    except catalogue_lookup.CatalogueError as e:
        msg = str(e)
        return _fail(msg[:1].upper() + msg[1:])
    took = f"{time.monotonic() - started:.1f} s"
    if not found.names:
        return _fail(f"The search worked but found no page naming EO51710 ({took})",
                     f"{found.pages} page(s) mention it")
    return SettingsTestResult(ok=True, message=f"EO51710 → “{found.names[0]}” ({took})",
                              details=[f"{t}: {n} page(s)" for t, n in found.titles])


async def makemkv(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    key = _value(s, v, "makemkv_key")
    if not key:
        return _fail("No MakeMKV key")
    if s.fake_hardware:
        return SettingsTestResult(ok=True, message="Simulated drives, so the key was not checked")
    if s.makemkv_eula_pending():
        return _fail(EULA_PENDING)
    ok, message = await runner.check_key(s.makemkvcon, key)
    return SettingsTestResult(ok=ok, message=message)


def _check_dir(name: str, path: Path, need_gb: float = 0) -> tuple[bool, str]:
    if not path.is_dir():
        return False, f"{name}: {path} does not exist (is it mounted?)"
    probe = path / f".ripshelf-write-test-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError as e:
        return False, f"{name}: {path} is not writable ({e.strerror or e})"
    free = shutil.disk_usage(path).free / 1024**3
    low = f" (a UHD rip needs about {need_gb:.0f} GB)" if need_gb and free < need_gb else ""
    return True, f"{name}: {path}, {free:.0f} GB free{low}"


async def paths(s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    s = s.with_folders(v.model_dump())  # the folders as typed into the form
    results = [
        _check_dir("Data", s.data_dir),
        _check_dir("Scratch", s.scratch_dir, need_gb=100),
        *[_check_dir(what[:1].upper() + what[1:].removesuffix(" folder"), root) for root, what in s.output_roots()],
        _check_dir("Backups", s.backups),
    ]
    bad = [m for ok, m in results if not ok]
    return SettingsTestResult(ok=not bad, message="All folders are writable" if not bad else bad[0],
                              details=[m for _, m in results])


CHECKS = {
    "tmdb": tmdb,
    "tvdb": tvdb,
    "tvmaze": tvmaze,
    "omdb": omdb,
    "thediscdb": thediscdb,
    "pushover": pushover,
    "emby": emby,
    "jellyfin": media_server("jellyfin"),
    "plex": media_server("plex"),
    "catalogue": catalogue,
    "makemkv": makemkv,
    "paths": paths,
}

# The secrets each check uses. A box left empty means "the saved one", so a pass
# with an empty box says it tested the saved key, not the (empty) form.
CHECK_SECRETS = {
    "tmdb": ("tmdb_api_key",),
    "tvdb": ("tvdb_api_key",),
    "omdb": ("omdb_api_key",),
    "makemkv": ("makemkv_key",),
    "emby": ("emby_api_key",),
    "jellyfin": ("jellyfin_api_key",),
    "plex": ("plex_token",),
    "pushover": ("pushover_user_key", "pushover_app_token"),
}


async def run(name: str, s: Settings, v: SettingsUpdate) -> SettingsTestResult:
    result = await CHECKS[name](s, v)
    saved = [k for k in CHECK_SECRETS.get(name, ())
             if not (getattr(v, k, None) or "").strip() and s.secret(k)]
    if saved:
        note = "the saved key" if len(saved) == 1 else "the saved keys"
        result.message += f" (tested {note}: type one to test it instead)"
    return result
