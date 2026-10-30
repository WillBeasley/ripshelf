"""A real uvicorn server, with the SSE stream and the static UI."""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
import uvicorn

from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app, default_static_dir


@pytest.fixture
def server(settings):
    app = create_app(settings, backend=FakeDrives(present=set()))
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(10)


def test_sse_stream_delivers_events(server):
    got = []
    with httpx.Client(timeout=10) as c, c.stream("GET", f"{server}/api/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        lines = r.iter_lines()
        assert next(lines) == "retry: 3000"
        # Trigger events. Putting a disc in a fake drive starts a job.
        httpx.post(f"{server}/api/dev/insert/fake-ihas122")
        for line in lines:
            if line.startswith("data: "):
                got.append(json.loads(line[6:]))
            if {"job", "drive"} <= {e["type"] for e in got}:
                break
    types = {e["type"] for e in got}
    assert {"job", "drive"} <= types
    job = next(e["job"] for e in got if e["type"] == "job")
    assert job["drive_id"] == "fake-ihas122" and "quick_fp" not in job  # internals not exposed


def test_ui_is_served(server):
    if default_static_dir() is None:
        pytest.skip("UI not built")
    r = httpx.get(f"{server}/finished?job=3")
    assert r.status_code == 200 and "<!doctype html>" in r.text.lower()
    for path, kind in [
        ("brand/logo.svg", "image/svg+xml"),
        ("brand/logo-mono.svg", "image/svg+xml"),
        ("brand/app-icon-dark.svg", "image/svg+xml"),
        ("brand/app-icon-light.svg", "image/svg+xml"),
        ("brand/banner.svg", "image/svg+xml"),
        ("brand/apple-touch-icon.png", "image/png"),
        ("brand/icon-192.png", "image/png"),
        ("brand/icon-512.png", "image/png"),
        ("brand/icon-maskable-512.png", "image/png"),
        ("manifest.webmanifest", "application/manifest+json"),
    ]:
        r = httpx.get(f"{server}/{path}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(kind), path
