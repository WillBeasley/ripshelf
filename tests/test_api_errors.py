"""What the API answers when a request can't be done: the status and message the UI shows."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app

from .test_api import wait

PM = "/dev/fake-sr1"


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, backend=FakeDrives(present={PM}))) as c:
        yield c


@pytest.fixture
def review(client):
    """A job waiting for review, with its rip done."""
    return wait(client, lambda j: j["state"] == "review" and j["rip"]["state"] == "done")


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/api/jobs/99/read-label"),
    ("get", "/api/jobs/99/snapshots"),
    ("get", "/api/jobs/1/snapshots/0/1.jpg"),
    ("get", "/api/jobs/1/scans/nope/thumb"),
    ("get", "/api/pending-scans/nope"),
    ("get", "/api/covers/stack/pages/nope"),
    ("get", "/api/covers/999/outside"),
    ("get", "/api/covers/999/outside?thumb=true"),
    ("post", "/api/drives/no-such-drive/eject"),
])
def test_what_isnt_there_is_404(client, method, path):
    assert getattr(client, method)(path).status_code == 404


def test_a_job_not_in_review_explains_itself(client, review):
    job = review["id"]
    assert client.post(f"/api/jobs/{job}/cancel").status_code == 200
    for method, path, body in [
        ("post", f"/api/jobs/{job}/read-label", None),
        ("post", f"/api/jobs/{job}/check-disc", None),
        ("put", f"/api/jobs/{job}/preset", {"media": "movie", "candidate": {"source": "manual", "title": "Moonlit Orchard"}}),
        ("delete", f"/api/jobs/{job}/preset", None),
        ("post", f"/api/jobs/{job}/tv/propose", {"show_key": "tmdb:1", "season": 1}),
        ("put", f"/api/jobs/{job}/tv/mapping", {"mapping": []}),
    ]:
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 409, (path, r.status_code, r.text)
        assert r.json()["detail"], path


def test_a_tray_that_wont_open(client, review, monkeypatch):
    backend = client.app.state.manager.backend

    def stuck(device):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(backend, "eject", stuck)
    assert client.post(f"/api/jobs/{review['id']}/cancel").status_code == 200
    r = client.post("/api/drives/fake-ihas122/eject")
    assert r.status_code == 409 and r.json()["detail"].startswith("eject failed")


def test_logs_fall_back_to_the_scan_before_the_rip(client, review):
    assert client.get(f"/api/jobs/{review['id']}/log?kind=rip").json()  # the rip's own log
    assert client.get(f"/api/jobs/{review['id']}/log?kind=bogus").status_code == 400


def test_a_scan_picture_sent_as_from_the_scanner_is_refused(client):
    r = client.post("/api/drives/fake-ihas122/pending-scans", data={"kind": "front", "source": "scanner"},
                    files={"file": ("a.jpg", b"\xff\xd8", "image/jpeg")})
    assert r.status_code == 400 and "pending-scans/scanner" in r.json()["detail"]


def test_a_film_search_without_a_way_to_search(client, settings):
    settings.tmdb_api_key = None  # and only TheDiscDB, which can't be searched by title
    r = client.get("/api/movies/search", params={"q": "Moonlit Orchard"})
    assert r.status_code == 409 and r.json()["detail"] == "No TMDB API key set"


def test_clearing_the_scanner_address(client, settings):
    settings.scanner_url = "http://192.0.2.10/eSCL"
    assert client.put("/api/settings", json={"scanner_url": ""}).status_code == 200
    assert client.get("/api/settings").json()["scanner_url"] is None


def test_a_folder_that_cant_be_made(client, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    r = client.post("/api/setup/folder", json={"path": str(blocker / "library"), "create": True})
    assert r.status_code == 200
    body = r.json()
    assert not body["exists"] and not body["created"] and body["error"].startswith("could not create it")
    assert client.post("/api/setup/folder", json={"path": "relative/path"}).status_code == 400
