"""Web pages on other sites can't drive the API through a browser on the LAN."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.origins import host_ok, origin_ok, split_host


@pytest.fixture
def client(settings):
    settings.worker_token = "s3cret"
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        yield c


def test_host_header_parsing():
    assert split_host("Hestia.lan:8380") == ("hestia.lan", 8380)
    assert split_host("[::1]:8380") == ("::1", 8380)
    assert split_host("192.168.1.10") == ("192.168.1.10", None)
    assert split_host("fe80::1") == ("fe80::1", None)


@pytest.mark.parametrize("name", ["192.168.1.10", "::1", "localhost", "hestia", "ripshelf",
                                  "hestia.local", "hestia.lan", "nas.home.arpa", "box.internal", ""])
def test_lan_names_are_allowed(settings, name):
    assert host_ok(name, settings)


@pytest.mark.parametrize("name", ["evil.example", "rebind.attacker.net", "ripshelf.example.com"])
def test_public_names_are_refused(settings, name):
    assert not host_ok(name, settings)


def test_public_url_and_allowed_hosts_add_names(settings):
    settings.public_url = "https://rip.example.com"
    settings.allowed_hosts = "hestia.tail1234.ts.net, .mydomain.org"
    assert host_ok("rip.example.com", settings)
    assert host_ok("hestia.tail1234.ts.net", settings)
    assert host_ok("films.mydomain.org", settings)
    assert not host_ok("mydomain.org.evil.example", settings)
    settings.allowed_hosts = "*"
    assert host_ok("anything.example", settings)


def test_origin_must_be_the_ui_itself(settings):
    assert origin_ok("http://hestia:8380", "hestia:8380", settings)
    assert origin_ok("http://192.168.1.10:8380", "192.168.1.10:8380", settings)
    assert not origin_ok("http://192.168.1.10:9000", "192.168.1.10:8380", settings)  # another app there
    assert not origin_ok("http://evil.example", "hestia:8380", settings)
    assert not origin_ok("null", "hestia:8380", settings)
    # A reverse proxy on 443 passing Host through, or changing it to the backend's.
    assert origin_ok("https://rip.lan", "rip.lan", settings)
    assert not origin_ok("https://rip.example.com", "ripshelf:8380", settings)
    settings.public_url = "https://rip.example.com/"
    assert origin_ok("https://rip.example.com", "ripshelf:8380", settings)


def test_cross_site_changes_are_refused(client):
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
        r = client.post("/api/drives/fake-pioneer/eject", headers=headers)
        assert r.status_code == 403, headers
        assert "another web page" in r.json()["detail"]


def test_the_ui_and_scripts_can_still_make_changes(client):
    # The UI's own page (TestClient's Host is "testserver"), and curl with no Origin.
    for headers in ({"Origin": "http://testserver"}, {"Sec-Fetch-Site": "same-origin"}, {}):
        r = client.post("/api/drives/fake-pioneer/eject", headers=headers)
        assert r.status_code != 403, (headers, r.text)


def test_reads_from_another_site_are_left_to_the_browser(client):
    # A GET can't change anything, and without CORS the page can't read the answer.
    assert client.get("/api/status", headers={"Origin": "https://evil.example"}).status_code == 200


def test_rebinding_name_is_refused_everywhere(client):
    for path in ("/api/status", "/api/settings", "/"):
        r = client.get(path, headers={"Host": "rebind.evil.example:8380"})
        assert r.status_code == 403
        assert "RIPSHELF_ALLOWED_HOSTS" in r.json()["detail"]
    assert client.put("/api/settings", json={}, headers={"Host": "rebind.evil.example"}).status_code == 403


def test_workers_reach_the_controller_by_any_name(client):
    headers = {"Host": "controller.example.com", "Authorization": "Bearer s3cret"}
    r = client.post("/api/workers/register", headers=headers, json={"id": "w1", "name": "w1"})
    assert r.status_code != 403, r.text
    # Their routes still want the token.
    assert client.post("/api/workers/w1/heartbeat", headers={"Host": "controller.example.com"},
                       json={}).status_code == 401
