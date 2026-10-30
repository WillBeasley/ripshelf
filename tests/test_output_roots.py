"""Separate movies and TV folders (RIPSHELF_MOVIES_DIR / RIPSHELF_TV_DIR), with the
single-library fallback."""

from __future__ import annotations

import json

import httpx
import respx
from pydantic import SecretStr

from ripshelf import integrations
from ripshelf.models import Candidate, JobState

PM = "/dev/fake-sr1"
EMBY = "http://emby.test:8096"


def test_fallback_is_movies_and_tv_inside_the_library(settings):
    assert settings.movies == settings.library_dir / "Movies"
    assert settings.tv == settings.library_dir / "TV"
    assert settings.output_roots() == [(settings.library_dir, "library folder")]


def test_separate_roots(settings, tmp_path):
    settings.movies_dir, settings.tv_dir = tmp_path / "films", tmp_path / "tv"
    assert settings.movies == tmp_path / "films" and settings.tv == tmp_path / "tv"
    assert settings.output_roots() == [(tmp_path / "films", "movies folder"), (tmp_path / "tv", "TV folder")]
    settings.tv_dir = None
    assert settings.output_roots() == [(tmp_path / "films", "movies folder"), (settings.library_dir, "library folder")]


async def test_each_root_is_a_setup_problem_when_missing_and_never_created(harness, settings, tmp_path):
    settings.movies_dir, settings.tv_dir = tmp_path / "films", tmp_path / "tv"
    async with harness() as h:
        msgs = [p.message for p in h.m.problems() if p.code == "library_missing"]
        assert msgs == [f"Movies folder {tmp_path / 'films'} does not exist (is the share mounted?)",
                        f"TV folder {tmp_path / 'tv'} does not exist (is the share mounted?)"]
        (tmp_path / "tv").mkdir()
        assert len([p for p in h.m.problems() if p.code == "library_missing"]) == 1
    assert not (tmp_path / "films").exists()


async def test_films_go_to_the_movies_folder(harness, settings, tmp_path):
    films = tmp_path / "films"
    films.mkdir()
    settings.movies_dir = films
    settings.emby_url, settings.emby_api_key = EMBY, SecretStr("k")
    settings.emby_movies_path = "/media/films"
    with respx.mock(assert_all_called=False) as router:
        emby = router.post(f"{EMBY}/Library/Media/Updated").mock(return_value=httpx.Response(204))
        async with harness(PM) as h:
            job = await h.wait_job(lambda j: j.state == JobState.review)
            await h.m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
            done = await h.wait_job(lambda j: j.state == JobState.done)
    assert done.destination == str(films / "Practical Magic (1998)" / "Practical Magic (1998).mkv")
    assert not (settings.library_dir / "Movies").exists()
    assert json.loads(emby.calls[0].request.content)["Updates"][0]["Path"] == "/media/films/Practical Magic (1998)/Practical Magic (1998).mkv"


async def test_missing_movies_folder_fails_the_job_not_creates_it(harness, settings, tmp_path):
    settings.movies_dir = tmp_path / "unmounted"
    async with harness(PM) as h:
        job = await h.wait_job(lambda j: j.state == JobState.review)
        await h.m.confirm(job.id, Candidate(source="manual", title="Practical Magic", year=1998))
        failed = await h.wait_job(lambda j: j.state == JobState.failed)
    assert failed.problem.code == "library_missing" and "movies folder" in failed.problem.message
    assert not (tmp_path / "unmounted").exists()


def test_emby_paths(settings, tmp_path):
    settings.movies_dir, settings.tv_dir = tmp_path / "films", tmp_path / "tv"
    settings.emby_movies_path, settings.emby_tv_path = "/m", "/t"
    p = integrations.server_path
    assert p(settings, str(tmp_path / "films" / "A (1)")) == "/m/A (1)"
    assert p(settings, str(tmp_path / "tv" / "S (2)")) == "/t/S (2)"
    assert p(settings, str(tmp_path / "filmsX" / "A")) == str(tmp_path / "filmsX" / "A"), "prefix, not a parent"
    settings.movies_dir = None  # the single-library fallback maps <library>/Movies the same way
    assert p(settings, str(settings.library_dir / "Movies" / "B")) == "/m/B"
    settings.emby_tv_path = None
    assert p(settings, str(tmp_path / "tv" / "S")) == str(tmp_path / "tv" / "S")


VIRTUAL_FOLDERS = [
    {"Name": "Films", "CollectionType": "movies", "ItemId": "11", "Locations": ["/media/Movies"]},
    {"Name": "Telly", "CollectionType": "tvshows", "ItemId": "22", "Locations": ["/mnt/a/TV", "/mnt/b/TV"]},
    {"Name": "Music", "CollectionType": "music", "ItemId": "33", "Locations": []},
]


def test_emby_libraries_endpoint(settings):
    from fastapi.testclient import TestClient

    from ripshelf.dev.fake import FakeDrives
    from ripshelf.main import create_app

    with TestClient(create_app(settings, backend=FakeDrives(present=set()))) as c, \
            respx.mock(assert_all_called=False) as router:
        assert c.post("/api/media-servers/emby/libraries", json={}).status_code == 400  # nothing set up yet
        route = router.get(f"{EMBY}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=VIRTUAL_FOLDERS))
        libs = c.post("/api/media-servers/emby/libraries", json={"url": EMBY, "api_key": "typed"}).json()
        assert [(lib["id"], lib["name"], lib["type"]) for lib in libs] == [
            ("11", "Films", "movies"), ("22", "Telly", "tvshows"), ("33", "Music", "music")]
        assert libs[1]["locations"] == ["/mnt/a/TV", "/mnt/b/TV"]
        assert route.calls[0].request.headers["X-Emby-Token"] == "typed"
        router.get(f"{EMBY}/Library/VirtualFolders").mock(return_value=httpx.Response(401))
        r = c.post("/api/media-servers/emby/libraries", json={"url": EMBY, "api_key": "bad"})
        assert r.status_code == 502 and "refused" in r.json()["detail"]
        router.get(f"{EMBY}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json={"x": 1}))
        assert "not like Emby" in c.post("/api/media-servers/emby/libraries", json={"url": EMBY, "api_key": "k"}).json()["detail"]


async def test_emby_test_button_shows_the_chosen_libraries(settings):
    from ripshelf import checks
    from ripshelf.models import SettingsUpdate

    v = SettingsUpdate(emby_url=EMBY, emby_api_key="k", emby_movies_library="11", emby_movies_path="/media/Movies",
                       emby_tv_library="22", emby_tv_path="/mnt/b/TV")
    with respx.mock() as router:
        router.get(f"{EMBY}/System/Info").mock(return_value=httpx.Response(200, json={"ServerName": "Media Server"}))
        router.get(f"{EMBY}/Library/VirtualFolders").mock(return_value=httpx.Response(200, json=VIRTUAL_FOLDERS))
        r = await checks.emby(settings, v)
        assert r.ok and r.message == "Connected to Media Server, 3 libraries"
        assert "Movies → Films (/media/Movies)" in r.details and "TV → Telly (/mnt/b/TV)" in r.details
        r = await checks.emby(settings, v.model_copy(update={"emby_tv_library": "99"}))
        assert not r.ok and "no longer in Emby" in r.message
        r = await checks.emby(settings, SettingsUpdate(emby_url=EMBY, emby_api_key="k"))
        assert r.ok and "Movies: no library chosen" in r.details


async def test_tv_refresh_lists_every_episode_file(settings, tmp_path):
    settings.emby_url, settings.emby_api_key = EMBY, SecretStr("k")
    settings.emby_tv_path = "/mnt/b/TV"
    with respx.mock() as router:
        route = router.post(f"{EMBY}/Library/Media/Updated").mock(return_value=httpx.Response(204))
        eps = [str(settings.tv / "Show (2001)" / "Season 01" / f"Show - S01E0{i}.mkv") for i in (1, 2)]
        assert await integrations.server_refresh(settings, "emby", eps) is None
    body = json.loads(route.calls[0].request.content)
    assert [u["Path"] for u in body["Updates"]] == [
        "/mnt/b/TV/Show (2001)/Season 01/Show - S01E01.mkv", "/mnt/b/TV/Show (2001)/Season 01/Show - S01E02.mkv"]


def test_emby_paths_ignore_trailing_slashes(settings, tmp_path):
    settings.movies_dir = tmp_path / "films"
    settings.emby_movies_path = "/media/Movies/"
    film = str(tmp_path / "films" / "A (1)" / "A (1).mkv")
    assert integrations.server_path(settings, film) == "/media/Movies/A (1)/A (1).mkv"
    settings.movies_dir = tmp_path / "films/"
    assert integrations.server_path(settings, film) == "/media/Movies/A (1)/A (1).mkv"
    assert integrations.server_path(settings, str(tmp_path / "films")) == "/media/Movies"


def test_emby_paths_keep_the_emby_side_separator(settings, tmp_path):
    settings.movies_dir = tmp_path / "films"
    settings.emby_movies_path = "D:\\Media\\Movies\\"  # Emby on Windows, ripshelf on Linux
    film = str(tmp_path / "films" / "A (1)" / "A (1).mkv")
    assert integrations.server_path(settings, film) == "D:\\Media\\Movies\\A (1)\\A (1).mkv"
    settings.emby_movies_path = "\\\\nas\\media\\Movies"
    assert integrations.server_path(settings, film) == "\\\\nas\\media\\Movies\\A (1)\\A (1).mkv"


def test_default_folders_are_next_to_ripshelf(tmp_path, monkeypatch):
    from ripshelf import config

    monkeypatch.setenv("RIPSHELF_HOME", str(tmp_path))
    assert config.install_dir() == tmp_path
    assert config._default_base() == (tmp_path, "library")
    (tmp_path / "var" / "data").mkdir(parents=True)  # a checkout from before keeps var/
    assert config._default_base() == (tmp_path / "var", "library-test")
    monkeypatch.delenv("RIPSHELF_HOME")
    assert (config.install_dir() / "pyproject.toml").is_file()  # this source checkout
