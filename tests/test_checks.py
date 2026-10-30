"""The Settings page's Test buttons, against mocked services."""

from __future__ import annotations

import sys

import httpx
import respx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf import checks
from ripshelf.dev.fake import FakeDrives
from ripshelf.identify.tmdb import API as TMDB
from ripshelf.integrations import PUSHOVER_API
from ripshelf.main import create_app
from ripshelf.makemkv import runner
from ripshelf.models import SettingsUpdate

from .conftest import posix_only


def client(settings):
    return TestClient(create_app(settings, backend=FakeDrives(present=set())))


def test_tmdb(settings):
    with client(settings) as c, respx.mock(assert_all_called=False) as router:
        route = router.get(f"{TMDB}/configuration").mock(return_value=httpx.Response(200, json={}))
        r = c.post("/api/settings/test", json={"check": "tmdb", "values": {"tmdb_api_key": "typed"}}).json()
        assert r["ok"] and route.calls[0].request.url.params["api_key"] == "typed"
        router.get(f"{TMDB}/configuration").mock(return_value=httpx.Response(401))
        r = c.post("/api/settings/test", json={"check": "tmdb", "values": {"tmdb_api_key": "bad"}}).json()
        assert not r["ok"] and "refused" in r["message"]
        r = c.post("/api/settings/test", json={"check": "tmdb"}).json()
        assert not r["ok"] and r["message"] == "No TMDB key"


async def test_blank_secret_means_the_saved_one(settings):
    settings.tmdb_api_key = SecretStr("saved")
    with respx.mock() as router:
        route = router.get(f"{TMDB}/configuration").mock(return_value=httpx.Response(200, json={}))
        assert (await checks.tmdb(settings, SettingsUpdate(tmdb_api_key=" "))).ok
    assert route.calls[0].request.url.params["api_key"] == "saved"
    with respx.mock() as router:
        router.get(f"{TMDB}/configuration").mock(side_effect=httpx.ConnectError("x"))
        assert "Could not reach" in (await checks.tmdb(settings, SettingsUpdate())).message


async def test_pushover_sends_a_test_message_with_a_link(settings):
    with respx.mock() as router:
        route = router.post(PUSHOVER_API).mock(return_value=httpx.Response(200, json={"status": 1}))
        r = await checks.pushover(settings, SettingsUpdate(pushover_user_key="u", pushover_app_token="t",
                                                           public_url="http://rip.lan:8380"))
    assert r.ok and r.details == ["link: http://rip.lan:8380/settings"]
    body = route.calls[0].request.content.decode()
    assert "user=u" in body and "token=t" in body and "ripshelf+test" in body
    assert not (await checks.pushover(settings, SettingsUpdate(pushover_user_key="u"))).ok
    with respx.mock() as router:
        router.post(PUSHOVER_API).mock(return_value=httpx.Response(400, json={"errors": ["user key is invalid"]}))
        r = await checks.pushover(settings, SettingsUpdate(pushover_user_key="u", pushover_app_token="t"))
    assert not r.ok and "user key is invalid" in r.message


async def test_emby(settings):
    v = SettingsUpdate(emby_url="http://emby:8096", emby_api_key="k")
    with respx.mock() as router:
        route = router.get("http://emby:8096/System/Info").mock(
            return_value=httpx.Response(200, json={"ServerName": "media-server", "Version": "4.9"}))
        router.get("http://emby:8096/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=[]))
        r = await checks.emby(settings, v)
    assert r.ok and r.message == "Connected to media-server, 0 libraries"
    assert route.calls[0].request.headers["X-Emby-Token"] == "k"
    with respx.mock() as router:
        router.get("http://emby:8096/System/Info").mock(return_value=httpx.Response(200, json={}))
        router.get("http://emby:8096/Library/VirtualFolders").mock(return_value=httpx.Response(500))
        assert "could not list the libraries" in (await checks.emby(settings, v)).message
    for resp, words in ((httpx.Response(401), "refused"), (httpx.Response(500), "HTTP 500"),
                        (httpx.Response(200, text="<html>"), "not like Emby")):
        with respx.mock() as router:
            router.get("http://emby:8096/System/Info").mock(return_value=resp)
            assert words in (await checks.emby(settings, v)).message
    with respx.mock() as router:
        router.get("http://emby:8096/System/Info").mock(side_effect=httpx.ConnectError("x"))
        assert "Could not reach" in (await checks.emby(settings, v)).message
    assert not (await checks.emby(settings, SettingsUpdate())).ok


@posix_only
async def test_makemkv_key(settings, tmp_path):
    fake = tmp_path / "makemkvcon"
    fake.write_text("#!/bin/sh\n[ \"$2\" = good ] && { echo 'Registration key saved.'; exit 0; }\n"
                    "echo 'Key not found or invalid'; exit 1\n")
    fake.chmod(0o755)
    settings.makemkvcon, settings.fake_hardware = str(fake), False
    assert (await checks.makemkv(settings, SettingsUpdate(makemkv_key="good"))).ok
    r = await checks.makemkv(settings, SettingsUpdate(makemkv_key="bad"))
    assert not r.ok and "not valid" in r.message
    assert not (await checks.makemkv(settings, SettingsUpdate())).ok
    settings.makemkv_eula_file = tmp_path / "eula.txt"
    r = await checks.makemkv(settings, SettingsUpdate(makemkv_key="good"))
    assert not r.ok and "EULA" in r.message
    settings.makemkv_eula_file = None
    settings.makemkvcon = str(tmp_path / "missing")
    assert "could not be started" in (await checks.makemkv(settings, SettingsUpdate(makemkv_key="k"))).message
    settings.fake_hardware = True
    assert "not checked" in (await checks.makemkv(settings, SettingsUpdate(makemkv_key="k"))).message


@posix_only
async def test_check_key_timeout(tmp_path):
    slow = tmp_path / "slow"
    slow.write_text(f"#!{sys.executable}\nimport time; time.sleep(30)\n")
    slow.chmod(0o755)
    ok, msg = await runner.check_key(str(slow), "k", timeout=0.5)
    assert not ok and "did not answer" in msg


async def test_paths(settings):
    settings.backup_dir = settings.data_dir
    r = await checks.paths(settings, SettingsUpdate())
    assert r.ok and len(r.details) == 4
    settings.library_dir = settings.data_dir / "missing"
    r = await checks.paths(settings, SettingsUpdate())
    assert not r.ok and "does not exist" in r.message


def test_makemkv_key_is_written_to_every_home(settings):
    # A per-drive worker's home in the shared makemkv folder gets it too.
    worker_home = runner.drive_home(settings.makemkv_dir, "bddvdrw-ggc-h20n-2528") / ".MakeMKV"
    worker_home.mkdir(parents=True)
    with client(settings) as c:
        c.put("/api/settings", json={"makemkv_key": "T-key-1"})
        for conf in (settings.data_dir / ".MakeMKV" / "settings.conf", worker_home / "settings.conf"):
            assert 'app_Key = "T-key-1"' in conf.read_text()
        c.put("/api/settings", json={"makemkv_key": ""})
        assert "app_Key" not in (worker_home / "settings.conf").read_text()


def test_a_blank_box_says_it_tested_the_saved_key(settings):
    with client(settings) as c, respx.mock(assert_all_called=False) as router:
        router.get(f"{TMDB}/configuration").mock(return_value=httpx.Response(200, json={}))
        for check in ("tmdb", "tvdb", "omdb"):  # nothing saved and nothing typed is never a pass
            r = c.post("/api/settings/test", json={"check": check}).json()
            assert not r["ok"] and r["message"].startswith("No ") and "saved" not in r["message"]
        settings.tmdb_api_key = SecretStr("saved")
        r = c.post("/api/settings/test", json={"check": "tmdb"}).json()
        assert r["ok"] and "tested the saved key" in r["message"]
        r = c.post("/api/settings/test", json={"check": "tmdb", "values": {"tmdb_api_key": "typed"}}).json()
        assert r["ok"] and "saved" not in r["message"]
