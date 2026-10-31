"""Notifications (Pushover) and media server (Emby, Jellyfin, Plex) library refresh.
All off until configured."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import PurePath

import httpx

from .config import Settings
from .models import MediaServer, ServerLibrary

log = logging.getLogger(__name__)


PUSHOVER_API = "https://api.pushover.net/1/messages.json"

# Events that can notify, in the order the Settings page lists them.
EVENTS = {
    "disc": "Disc inserted",
    "review": "Needs review",
    "exists": "Already in library",
    "ripped": "Rip finished",
    "transcoded": "Transcode finished",
    "copied": "Copied to library",
    "done": "Done",
    "failed": "Failed",
    "setup": "Setup problem",
}
# Where a summary of several notifications of one kind links to.
SUMMARY_PATHS = {"disc": "/", "review": "/review", "exists": "/review", "ripped": "/", "transcoded": "/",
                 "copied": "/", "done": "/finished", "failed": "/", "setup": "/settings"}


@dataclass
class Note:
    event: str  # a key of EVENTS
    title: str
    message: str
    click_path: str | None = None
    link_title: str | None = None


def notifications_enabled(settings: Settings) -> bool:
    return bool(settings.secret("pushover_user_key") and settings.secret("pushover_app_token"))


def link(settings: Settings, path: str | None) -> str | None:
    return settings.public_url.rstrip("/") + path if path and settings.public_url else None


async def pushover(settings: Settings, note: Note, priority: int, *, user_key: str | None = None,
                   app_token: str | None = None) -> str | None:
    """Send one message. Returns an error string, or None once Pushover accepted it.

    Priority is capped at 1, because 2 (emergency) needs retry/expire handling.
    """
    url = link(settings, note.click_path)
    message = note.message or note.title
    if url:
        # The raw link too, because it's tappable straight from the notification.
        message = message[: 1024 - len(url) - 1] + "\n" + url
    data = {
        "token": app_token or settings.secret("pushover_app_token"),
        "user": user_key or settings.secret("pushover_user_key"),
        "title": note.title[:250], "message": message[:1024],
        "priority": str(max(-2, min(1, priority))),
    }
    if url:
        data["url"] = url
        data["url_title"] = note.link_title or "Open in ripshelf"
    if settings.pushover_device:
        data["device"] = settings.pushover_device
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(PUSHOVER_API, data=data)
            if r.status_code >= 400:
                # Pushover explains bad keys in "errors"; never log the keys themselves.
                try:
                    errs = r.json().get("errors")
                except ValueError:
                    errs = None
                log.warning("pushover failed: HTTP %s %s", r.status_code, errs or "")
                return f"Pushover refused it (HTTP {r.status_code}{': ' + '; '.join(errs) if errs else ''})"
        return None
    except httpx.HTTPError as e:
        log.warning("pushover failed: %s", type(e).__name__)
        return f"could not reach Pushover ({type(e).__name__})"


class Notifier:
    """Sends notifications through Pushover, at most one per kind per batch window.

    The first notification of a kind goes out at once. Any more of that kind
    within ``notify_batch_s`` are held and then sent as one summary, so loading
    four discs gives two messages, not eight.
    """

    def __init__(self, settings: Settings):
        self.s = settings
        self._held: dict[str, list[Note]] = {}
        self._tasks: set[asyncio.Task] = set()

    def priority(self, event: str) -> int | None:
        # An event added since the settings were saved gets its default.
        events = self.s.notify_events
        return events[event] if event in events else type(self.s).model_fields["notify_events"].default.get(event)

    def send(self, note: Note) -> None:
        prio = self.priority(note.event)
        if prio is None or not notifications_enabled(self.s):
            return
        if self.s.notify_batch_s <= 0:
            self._spawn(pushover(self.s, note, prio))
            return
        if note.event in self._held:  # a window is open, so hold it for the summary
            self._held[note.event].append(note)
            return
        self._held[note.event] = []
        self._spawn(pushover(self.s, note, prio))
        self._spawn(self._close_window(note.event))

    async def _close_window(self, event: str) -> None:
        await asyncio.sleep(self.s.notify_batch_s)
        held = self._held.pop(event, [])
        prio = self.priority(event)
        if not held or prio is None:
            return
        if len(held) == 1:
            await pushover(self.s, held[0], prio)
            return
        summary = Note(event, f"{EVENTS[event]}: {len(held)} more", "\n".join(n.title for n in held),
                       SUMMARY_PATHS[event], "Open in ripshelf")
        await pushover(self.s, summary, prio)

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def cancel(self) -> None:
        for t in list(self._tasks):
            t.cancel()


# Media servers told about new files. Jellyfin began as a fork of Emby and kept its
# library API, so the two share code. Plex has its own.
SERVERS: dict[MediaServer, str] = {"emby": "Emby", "jellyfin": "Jellyfin", "plex": "Plex"}
# Plex's section types, as Emby's CollectionType names them.
PLEX_TYPES = {"movie": "movies", "show": "tvshows"}


class ServerError(Exception):
    pass


def server_key(server: MediaServer) -> str:
    """The setting that holds a server's API key (Plex calls it a token)."""
    return "plex_token" if server == "plex" else f"{server}_api_key"


def server_configured(settings: Settings, server: MediaServer) -> bool:
    return bool(getattr(settings, f"{server}_url") and settings.secret(server_key(server)))


def configured_servers(settings: Settings) -> list[MediaServer]:
    return [s for s in SERVERS if server_configured(settings, s)]


def server_headers(server: MediaServer, key: str) -> dict[str, str]:
    if server == "plex":
        return {"X-Plex-Token": key, "Accept": "application/json"}
    if server == "jellyfin":
        return {"Authorization": f'MediaBrowser Token="{key}"'}
    return {"X-Emby-Token": key}


async def server_libraries(server: MediaServer, url: str, key: str) -> list[ServerLibrary]:
    """A server's libraries, with their name, type and folders. Emby and Jellyfin list
    them at /Library/VirtualFolders, Plex calls them sections."""
    name = SERVERS[server]
    path = "/library/sections" if server == "plex" else "/Library/VirtualFolders"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url.rstrip("/") + path, headers=server_headers(server, key))
    except httpx.HTTPError as e:
        raise ServerError(f"could not reach {name} at {url} ({type(e).__name__})") from None
    if r.status_code in (401, 403):
        raise ServerError(f"{name} refused the {'token' if server == 'plex' else 'API key'}")
    if r.status_code >= 400:
        raise ServerError(f"{name} returned HTTP {r.status_code}")
    try:
        if server == "plex":
            return [ServerLibrary(id=str(d["key"]), name=d["title"], type=PLEX_TYPES.get(d.get("type"), d.get("type")),
                                  locations=[str(loc["path"]) for loc in d.get("Location") or []])
                    for d in r.json()["MediaContainer"].get("Directory") or []]
        return [ServerLibrary(id=str(f.get("ItemId") or f.get("Id") or f["Name"]), name=f["Name"],
                              type=f.get("CollectionType"), locations=[str(x) for x in f.get("Locations") or []])
                for f in r.json()]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ServerError(f"{url} answered, but not like {name}") from None


def _mapped(settings: Settings, server: MediaServer, local_path: str) -> tuple[str | None, str]:
    """Which of our folders (movies or tv) a path is under, and the path as the server
    sees it, using the folder of the library picked for it. Unmapped paths stay as they are.

    Either side may be Windows or POSIX (ripshelf on Windows, the server on Linux, or the
    other way round). The server path keeps the separator its library folder uses."""
    local = PurePath(local_path)
    for kind, root in (("movies", settings.movies), ("tv", settings.tv)):
        try:
            rel = local.relative_to(root)
        except ValueError:
            continue
        theirs = getattr(settings, f"{server}_{kind}_path")
        if not theirs:
            return kind, local_path
        sep = "\\" if "\\" in theirs and "/" not in theirs else "/"
        return kind, theirs.rstrip("/\\") + "".join(sep + part for part in rel.parts)
    return None, local_path


def server_path(settings: Settings, local_path: str, server: MediaServer = "emby") -> str:
    """A path under our movies or TV folder as the server sees it."""
    return _mapped(settings, server, local_path)[1]


def _parent(path: str) -> str:
    """The folder a server-side path is in, whichever separator it uses."""
    cut = max(path.rfind("/"), path.rfind("\\"))
    return path[:cut] if cut > 0 else path


async def server_refresh(settings: Settings, server: MediaServer, paths: list[str]) -> str | None:
    """Tell one server these new files exist. Returns an error string, or None on
    success (or when the server isn't set up).

    Emby and Jellyfin are told the files, so they scan just them. Plex scans folders,
    so it is asked to scan each new file's folder in the library picked for it, or the
    whole library when it has no folder mapped, or every library when none was picked."""
    if not server_configured(settings, server):
        return None
    url = getattr(settings, f"{server}_url").rstrip("/")
    headers = server_headers(server, settings.secret(server_key(server)))
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            if server != "plex":
                body = {"Updates": [{"Path": server_path(settings, p, server), "UpdateType": "Created"} for p in paths]}
                (await c.post(f"{url}/Library/Media/Updated", json=body, headers=headers)).raise_for_status()
                return None
            scans: dict[str, set[str | None]] = {}
            for p in paths:
                kind, theirs = _mapped(settings, "plex", p)
                section = getattr(settings, f"plex_{kind}_library") if kind else None
                folder = _parent(theirs) if section and getattr(settings, f"plex_{kind}_path") else None
                scans.setdefault(section or "all", set()).add(folder)
            for section, folders in scans.items():
                # One whole-library scan covers any folders in it.
                for folder in [None] if None in folders else sorted(folders):
                    r = await c.get(f"{url}/library/sections/{section}/refresh", headers=headers,
                                    params={"path": folder} if folder else None)
                    r.raise_for_status()
        return None
    except httpx.HTTPError as e:
        return f"{SERVERS[server]} refresh failed: {type(e).__name__}"


async def refresh_servers(settings: Settings, paths: list[str]) -> list[tuple[str, str | None]]:
    """Refresh every server that is set up, at once. Gives (name, error or None) for each."""
    servers = configured_servers(settings)
    errors = await asyncio.gather(*(server_refresh(settings, s, paths) for s in servers))
    return [(SERVERS[s], err) for s, err in zip(servers, errors, strict=True)]


def names(items: list[str]) -> str:
    """A list for a sentence, for example Emby, Jellyfin and Plex."""
    return ", ".join(items[:-1]) + " and " + items[-1] if len(items) > 1 else "".join(items)
