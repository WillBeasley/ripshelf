"""Jellyfin and Plex library refresh, next to Emby's (tests/test_output_roots.py)."""

from __future__ import annotations

import json

import httpx
import respx
from pydantic import SecretStr

from ripshelf import checks, integrations
from ripshelf.models import Candidate, JobState, SettingsUpdate

PM = "/dev/fake-sr1"
EMBY = "http://emby.test:8096"
JELLYFIN = "http://jellyfin.test:8096"
PLEX = "http://plex.test:32400"

SECTIONS = {"MediaContainer": {"size": 3, "Directory": [
    {"key": "1", "title": "Films", "type": "movie", "Location": [{"id": 1, "path": "/data/films"}]},
    {"key": "2", "title": "Telly", "type": "show", "Location": [{"id": 2, "path": "/data/tv"}]},
    {"key": "3", "title": "Music", "type": "artist", "Location": []},
]}}


def plex(settings, movies: str | None = "1", movies_path: str | None = "/data/films",
         tv: str | None = "2", tv_path: str | None = "/data/tv"):
    settings.plex_url, settings.plex_token = PLEX, SecretStr("pt")
    settings.plex_movies_library, settings.plex_movies_path = movies, movies_path
    settings.plex_tv_library, settings.plex_tv_path = tv, tv_path


def test_only_servers_with_a_url_and_key_count(settings):
    assert integrations.configured_servers(settings) == []
    settings.jellyfin_url = JELLYFIN
    assert integrations.configured_servers(settings) == []
    settings.jellyfin_api_key = SecretStr("jk")
    plex(settings)
    assert integrations.configured_servers(settings) == ["jellyfin", "plex"]
    assert integrations.names(["Emby", "Jellyfin", "Plex"]) == "Emby, Jellyfin and Plex"
    assert integrations.names(["Plex"]) == "Plex"


async def test_plex_sections_become_libraries(settings):
    with respx.mock() as router:
        route = router.get(f"{PLEX}/library/sections").mock(return_value=httpx.Response(200, json=SECTIONS))
        libs = await integrations.server_libraries("plex", PLEX + "/", "pt")
    assert [(lib.id, lib.name, lib.type, lib.locations) for lib in libs] == [
        ("1", "Films", "movies", ["/data/films"]), ("2", "Telly", "tvshows", ["/data/tv"]), ("3", "Music", "artist", [])]
    assert route.calls[0].request.headers["X-Plex-Token"] == "pt"
    assert route.calls[0].request.headers["Accept"] == "application/json"


async def test_jellyfin_uses_its_own_auth_header(settings):
    with respx.mock() as router:
        route = router.get(f"{JELLYFIN}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=[
            {"Name": "Films", "CollectionType": "movies", "ItemId": "a1", "Locations": ["/media/films"]}]))
        libs = await integrations.server_libraries("jellyfin", JELLYFIN, "jk")
    assert libs[0].id == "a1" and libs[0].locations == ["/media/films"]
    assert route.calls[0].request.headers["Authorization"] == 'MediaBrowser Token="jk"'


async def test_plex_refresh_scans_each_new_folder(settings):
    plex(settings)
    eps = [str(settings.tv / "Show (2001)" / "Season 01" / f"Show - S01E0{i}.mkv") for i in (1, 2)]
    film = str(settings.movies / "A (1)" / "A (1).mkv")
    with respx.mock() as router:
        tv = router.get(f"{PLEX}/library/sections/2/refresh").mock(return_value=httpx.Response(200))
        films = router.get(f"{PLEX}/library/sections/1/refresh").mock(return_value=httpx.Response(200))
        assert await integrations.server_refresh(settings, "plex", [*eps, film]) is None
    assert tv.call_count == 1, "one scan for the season folder, not one per episode"
    assert tv.calls[0].request.url.params["path"] == "/data/tv/Show (2001)/Season 01"
    assert films.calls[0].request.url.params["path"] == "/data/films/A (1)"
    assert tv.calls[0].request.headers["X-Plex-Token"] == "pt"


async def test_plex_without_a_folder_or_library_scans_more(settings):
    film = str(settings.movies / "A (1)" / "A (1).mkv")
    plex(settings, movies_path=None)
    with respx.mock() as router:
        route = router.get(f"{PLEX}/library/sections/1/refresh").mock(return_value=httpx.Response(200))
        assert await integrations.server_refresh(settings, "plex", [film]) is None
    assert "path" not in route.calls[0].request.url.params, "the whole library, as no folder is mapped"
    plex(settings, movies=None, movies_path=None)
    with respx.mock() as router:
        route = router.get(f"{PLEX}/library/sections/all/refresh").mock(return_value=httpx.Response(200))
        assert await integrations.server_refresh(settings, "plex", [film]) is None
    assert route.call_count == 1


async def test_plex_keeps_a_windows_server_path(settings):
    plex(settings, movies_path="D:\\Media\\Films")
    with respx.mock() as router:
        route = router.get(f"{PLEX}/library/sections/1/refresh").mock(return_value=httpx.Response(200))
        await integrations.server_refresh(settings, "plex", [str(settings.movies / "A (1)" / "A (1).mkv")])
    assert route.calls[0].request.url.params["path"] == "D:\\Media\\Films\\A (1)"


async def test_plex_refresh_failure_is_reported(settings):
    plex(settings)
    with respx.mock() as router:
        router.get(f"{PLEX}/library/sections/1/refresh").mock(return_value=httpx.Response(401))
        err = await integrations.server_refresh(settings, "plex", [str(settings.movies / "A (1)" / "A (1).mkv")])
    assert err == "Plex refresh failed: HTTPStatusError"


async def test_plex_test_button(settings):
    v = SettingsUpdate(plex_url=PLEX, plex_token="pt", plex_movies_library="1", plex_movies_path="/data/films")
    with respx.mock() as router:
        router.get(f"{PLEX}/").mock(return_value=httpx.Response(200, json={
            "MediaContainer": {"friendlyName": "Den", "version": "1.41.0"}}))
        router.get(f"{PLEX}/library/sections").mock(return_value=httpx.Response(200, json=SECTIONS))
        r = await checks.run("plex", settings, v)
        assert r.ok and r.message == "Connected to Den, 3 libraries"
        assert "version 1.41.0" in r.details and "Movies → Films (/data/films)" in r.details
        assert "TV: no library chosen" in r.details
        r = await checks.run("plex", settings, v.model_copy(update={"plex_movies_library": "9"}))
        assert not r.ok and "no longer in Plex" in r.message
        router.get(f"{PLEX}/").mock(return_value=httpx.Response(401))
        assert (await checks.run("plex", settings, v)).message == "Plex refused the token"
    assert (await checks.run("plex", settings, SettingsUpdate())).message == "Plex needs the URL and a token"


async def test_jellyfin_test_button(settings):
    v = SettingsUpdate(jellyfin_url=JELLYFIN, jellyfin_api_key="jk")
    with respx.mock() as router:
        info = router.get(f"{JELLYFIN}/System/Info").mock(return_value=httpx.Response(200, json={
            "ServerName": "Box", "Version": "10.10.7"}))
        router.get(f"{JELLYFIN}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=[]))
        r = await checks.run("jellyfin", settings, v)
    assert r.ok and r.message == "Connected to Box, 0 libraries"
    assert info.calls[0].request.headers["Authorization"] == 'MediaBrowser Token="jk"'


def test_settings_keep_the_keys_secret(settings):
    from fastapi.testclient import TestClient

    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c, respx.mock() as router:
        router.get(f"{PLEX}/library/sections").mock(side_effect=httpx.ConnectError("x"))
        v = c.put("/api/settings", json={"jellyfin_url": JELLYFIN, "jellyfin_api_key": "jelly-secret",
                                         "plex_url": PLEX, "plex_token": "plex-secret", "plex_tv_library": "2"}).json()
        assert v["jellyfin_api_key_set"] and v["plex_token_set"] and v["plex_tv_library"] == "2"
        assert "jelly-secret" not in json.dumps(v) and "plex-secret" not in json.dumps(v)
        assert settings.secret("plex_token") == "plex-secret"
        r = c.post("/api/media-servers/plex/libraries", json={})
        assert r.status_code == 502 and "could not reach Plex" in r.json()["detail"]


async def test_every_server_is_refreshed_after_filing(harness, settings):
    settings.emby_url, settings.emby_api_key = EMBY, SecretStr("ek")
    settings.jellyfin_url, settings.jellyfin_api_key = JELLYFIN, SecretStr("jk")
    plex(settings)
    with respx.mock(assert_all_called=False) as router:
        emby = router.post(f"{EMBY}/Library/Media/Updated").mock(return_value=httpx.Response(204))
        jf = router.post(f"{JELLYFIN}/Library/Media/Updated").mock(return_value=httpx.Response(204))
        px = router.get(f"{PLEX}/library/sections/1/refresh").mock(side_effect=httpx.ConnectError("x"))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            await h.m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
            await h.wait_job(lambda j: j.state == JobState.done)
            events = [e.message for e in h.db.events(job.id)]
    assert emby.call_count == jf.call_count == px.call_count == 1
    assert "Emby library refresh requested" in events and "Jellyfin library refresh requested" in events
    assert "Plex refresh failed: ConnectError" in events, "a broken server is a warning, the job still finishes"
