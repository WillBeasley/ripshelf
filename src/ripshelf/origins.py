"""Keep web pages on other sites from using the API through the browser.

ripshelf has no login, so "keep it on your LAN" is the protection. That doesn't stop a
page open in a browser on the LAN: it can post a form to eject a drive or upload a file,
and a page on a name the attacker points at this machine's address (DNS rebinding) reads
and changes everything as if it were the UI. So:

- The Host header must be a name this machine is reached by on a LAN: an IP address,
  localhost, a name with no dots (``hestia``), one under a local-only suffix
  (``.local``, ``.lan``, ``.home.arpa``...), the public URL's, or one in
  ``RIPSHELF_ALLOWED_HOSTS``. A rebinding page's Host is its own public name.
- A request that changes something (not GET or HEAD) from a browser must come from the
  UI's own address: its Origin is this Host or the public URL's. Scripts and workers
  send no Origin and are let through.

The worker API (``/api/workers/...``) is left alone: it has its own tokens, and a worker
may reach the controller by any name.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from urllib.parse import urlsplit

from .config import Settings

log = logging.getLogger(__name__)

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Names no public DNS answers for, so a rebinding page can't be served from one.
LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")
TOKEN_PATHS = "/api/workers/"
DEFAULT_PORTS = {"http": 80, "https": 443}


def split_host(host: str) -> tuple[str, int | None]:
    """``Host`` header to (lowercase name or address, port)."""
    host = host.strip().lower().rstrip(".")
    if host.startswith("["):  # [::1]:8380
        name, _, rest = host[1:].partition("]")
        port = rest.removeprefix(":")
    elif host.count(":") == 1:
        name, port = host.split(":")
    else:
        name, port = host, ""
    return name.rstrip("."), int(port) if port.isdigit() else None


def is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def allowed_names(s: Settings) -> list[str]:
    """RIPSHELF_ALLOWED_HOSTS (comma separated; ``.example.com`` for every name under
    it, ``*`` for any) and the public URL's name."""
    names = [n.strip().lower().rstrip(".") for n in (s.allowed_hosts or "").split(",") if n.strip()]
    if s.public_url:
        public = urlsplit(s.public_url).hostname
        if public:
            names.append(public.lower().rstrip("."))
    return names


def host_ok(name: str, s: Settings) -> bool:
    if not name:  # HTTP/1.0 without a Host: never a browser
        return True
    if is_ip(name) or name == "localhost" or "." not in name or name.endswith(LOCAL_SUFFIXES):
        return True
    for allowed in allowed_names(s):
        if allowed == "*" or name == allowed or (allowed.startswith(".") and name.endswith(allowed)):
            return True
    return False


def origin_ok(origin: str, host: str, s: Settings) -> bool:
    """The page that sent this was served by this machine, at the address it was asked by
    or at the public URL (where a reverse proxy may change Host on the way)."""
    if origin == "null":  # sandboxed frames, file:// pages
        return False
    o = urlsplit(origin)
    if not o.hostname:
        return False
    port = o.port or DEFAULT_PORTS.get(o.scheme)
    name, host_port = split_host(host)
    # A Host without a port came through a proxy, or on 80/443 itself.
    if o.hostname == name and (port == host_port if host_port else port in (80, 443)):
        return True
    for allowed in allowed_names(s):
        if allowed == "*" or o.hostname == allowed or (allowed.startswith(".") and o.hostname.endswith(allowed)):
            return True
    return False


class OriginGuard:
    """ASGI middleware (not BaseHTTPMiddleware, which buffers the UI's event stream)."""

    def __init__(self, app, settings: Settings):
        self.app, self.s = app, settings
        self.warned: set[str] = set()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(TOKEN_PATHS):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        host = headers.get("host", "")
        name, _ = split_host(host)
        if not host_ok(name, self.s):
            if name not in self.warned:
                self.warned.add(name)
                log.warning("refused a request for %r: add it to RIPSHELF_ALLOWED_HOSTS, or set "
                            "the public URL in Settings, if ripshelf is meant to be reached by it", name)
            return await refuse(send, f"ripshelf isn't served at {name or 'this address'}. If that's "
                                      "your address for it, add it to RIPSHELF_ALLOWED_HOSTS or set "
                                      "Settings → System → this UI's address.")
        if scope.get("method", "GET") not in SAFE_METHODS:
            origin = headers.get("origin")
            if origin is not None:
                bad = not origin_ok(origin, host, self.s)
            else:  # an older browser that sends no Origin still says where the request came from
                bad = headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none")
            if bad:
                log.warning("refused %s %s from another site (%s)", scope["method"], scope["path"],
                            origin or headers.get("sec-fetch-site"))
                return await refuse(send, "refused a change sent from another web page")
        return await self.app(scope, receive, send)


async def refuse(send, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
