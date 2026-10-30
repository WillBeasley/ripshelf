"""HTTP API, with the happy path through a fake disc, and everything a client can get wrong."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ripshelf.bus import EventBus
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.models import JobUpdated

from .conftest import private


@pytest.fixture
def client(settings):
    app = create_app(settings, backend=FakeDrives(present=set()))
    with TestClient(app) as c:
        yield c


def wait(c: TestClient, pred, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        jobs = c.get("/api/jobs").json()
        if jobs and pred(jobs[0]):
            return jobs[0]
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_status_drives_settings(client):
    st = client.get("/api/status").json()
    assert st["fake_hardware"] and st["review_count"] == 0
    drives = client.get("/api/drives").json()
    assert {d["id"] for d in drives} >= {"fake-pioneer", "fake-ihas122"}
    s = client.get("/api/settings").json()
    assert s["tmdb_api_key_set"] is False and "tmdb_api_key" not in s


def test_disc_to_library_through_the_api(client, settings):
    assert client.post("/api/dev/insert/fake-ihas122").status_code == 204
    job = wait(client, lambda j: j["state"] == "review")
    assert client.get("/api/status").json()["review_count"] == 1
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["events"] and detail["titles"]
    assert client.get(f"/api/jobs/{job['id']}/log?kind=scan").json()
    assert client.get(f"/api/jobs/{job['id']}/log?kind=transcode").json() == []
    r = client.post(f"/api/jobs/{job['id']}/confirm",
                    json={"candidate": {"title": "Practical Magic", "year": 1998, "source": "manual"}})
    assert r.status_code == 200, r.text
    done = wait(client, lambda j: j["state"] == "done")
    assert Path(done["destination"]).parts[-2:] == ("Practical Magic (1998)", "Practical Magic (1998).mkv")
    # Confirming again, when it's not waiting for an identity any more.
    r = client.post(f"/api/jobs/{job['id']}/confirm", json={"candidate": {"title": "X"}})
    assert r.status_code == 409


@pytest.mark.parametrize("method, path", [
    ("get", "/api/jobs/999"), ("post", "/api/jobs/999/retry"), ("post", "/api/jobs/999/cancel"),
    ("post", "/api/jobs/999/identify-again"), ("post", "/api/drives/nope/eject"),
    ("post", "/api/drives/nope/scan"), ("post", "/api/dev/insert/nope"), ("get", "/api/nope"),
])
def test_unknown_ids(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code in (404, 409), (path, r.status_code)


def test_job_actions_on_missing_job_are_errors_not_crashes(client):
    for action in ("retry", "cancel", "identify-again"):
        assert client.post(f"/api/jobs/999/{action}").status_code == 409
    assert client.post("/api/jobs/999/search", json={"query": "x"}).status_code == 404
    assert client.post("/api/jobs/999/confirm", json={"candidate": {"title": "x"}}).status_code == 409


@pytest.mark.parametrize("path, body", [
    ("/api/jobs/1/confirm", {}),
    ("/api/jobs/1/confirm", {"candidate": {}}),
    ("/api/jobs/1/confirm", {"candidate": {"title": "x", "source": "evil"}}),
    ("/api/jobs/1/search", {"year": 1999}),
    ("/api/jobs/abc/confirm", {"candidate": {"title": "x"}}),
])
def test_invalid_payloads(client, path, body):
    assert client.post(path, json=body).status_code == 422


def test_bad_query_values(client):
    assert client.get("/api/jobs?state=bogus").status_code == 422
    assert client.get("/api/jobs/1/log?kind=../../etc/passwd").status_code == 400


def test_search_without_key(client):
    client.post("/api/dev/insert/fake-ihas122")
    job = wait(client, lambda j: j["state"] == "review")
    r = client.post(f"/api/jobs/{job['id']}/search", json={"query": "Practical Magic"})
    assert r.status_code == 409 and "TMDB" in r.json()["detail"]


def test_settings_update_validation_and_secrets(client, settings):
    assert client.put("/api/settings", json={"auto_confirm_threshold": 2}).status_code == 422
    assert client.put("/api/settings", json={"scan_timeout_s": 1}).status_code == 422
    r = client.put("/api/settings", json={"tmdb_api_key": "s3cret", "auto_confirm_threshold": 0.95})
    body = r.json()
    assert body["tmdb_api_key_set"] is True and body["auto_confirm_threshold"] == 0.95
    assert "s3cret" not in r.text
    assert private(settings.data_dir / "settings.json")
    r = client.put("/api/settings", json={"tmdb_api_key": ""})
    assert r.json()["tmdb_api_key_set"] is False
    # Unknown fields are ignored; the data folder (where settings.json lives) is deployment config.
    r = client.put("/api/settings", json={"data_dir": "/etc"})
    assert r.status_code == 200 and settings.data_dir != Path("/etc")


def test_transcode_sources_setting(client, settings):
    s = client.get("/api/settings").json()["transcode_sources"]
    assert s["bluray"] == {"transcode": True, "crop": True} and s["uhd"] == {"transcode": False, "crop": True}
    r = client.put("/api/settings", json={"transcode_sources": {"bluray": {"transcode": True, "crop": False}}})
    assert r.json()["transcode_sources"]["bluray"] == {"transcode": True, "crop": False}
    assert settings.transcode_source("bluray") == {"transcode": True, "crop": False}
    assert client.put("/api/settings", json={"transcode_sources": {"hddvd": {}}}).status_code == 422


def test_folders_from_settings(client, settings, tmp_path):
    films = tmp_path / "films"
    r = client.put("/api/settings", json={"library_dir": str(tmp_path / "lib2"), "movies_dir": str(films),
                                          "scratch_dir": str(tmp_path / "scratch2")})
    body = r.json()
    assert body["library_dir"] == str(tmp_path / "lib2") and body["custom_movies_dir"] == str(films)
    assert body["movies_dir"] == str(films) and body["tv_dir"] == str(tmp_path / "lib2" / "TV")
    assert (tmp_path / "scratch2").is_dir() and settings.scratch_dir == tmp_path / "scratch2"
    saved = json.loads((settings.data_dir / "settings.json").read_text())
    assert saved["movies_dir"] == str(films)
    # "" puts films back inside the library; the library and scratch can't be unset.
    body = client.put("/api/settings", json={"movies_dir": ""}).json()
    assert body["custom_movies_dir"] == "" and body["movies_dir"] == str(tmp_path / "lib2" / "Movies")
    assert client.put("/api/settings", json={"library_dir": " "}).status_code == 400
    assert client.put("/api/settings", json={"scratch_dir": "relative/path"}).status_code == 400


def test_scratch_folder_stays_while_jobs_run(client, settings, tmp_path):
    client.post("/api/dev/insert/fake-ihas122")
    wait(client, lambda j: j["state"] == "review")
    r = client.put("/api/settings", json={"scratch_dir": str(tmp_path / "elsewhere")})
    assert r.status_code == 409 and settings.scratch_dir == tmp_path / "scratch"
    assert client.put("/api/settings", json={"scratch_dir": str(settings.scratch_dir)}).status_code == 200


def test_check_paths_uses_the_typed_folders(client, tmp_path):
    r = client.post("/api/settings/test", json={"check": "paths", "values": {"movies_dir": str(tmp_path / "nope")}})
    assert r.json()["ok"] is False and "nope" in r.json()["message"]



def test_settings_rip_mode(client, settings):
    assert client.put("/api/settings", json={"rip_mode": "iso"}).status_code == 422
    r = client.put("/api/settings", json={"rip_mode": "backup", "drive_modes": {"fake-ihas122": "titles"}})
    body = r.json()
    assert body["rip_mode"] == "backup" and body["drive_modes"] == {"fake-ihas122": "titles"}
    assert body["disc_backups_dir"] == str(settings.library_dir / "Disc backups")
    assert settings.rip_mode_for("fake-ihas122") == "titles" and settings.rip_mode_for("fake-pioneer") == "backup"

def tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("hkd_0001.bin")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    return buf.getvalue()


def test_private_data_import(client, settings, tmp_path):
    url = "/api/setup/makemkv/private-data"
    r = client.post(url, data={"drive_ids": "fake-pioneer"}, files={"file": ("_private_data.tar", tar_bytes())})
    assert r.status_code == 200 and r.json()["drive_ids"] == ["fake-pioneer"]
    assert client.get("/api/settings").json()["makemkv_drive_data"]["fake-pioneer"] is True
    codes = [p["code"] for p in client.get("/api/status").json()["problems"]]
    assert "private_data_missing" not in codes
    assert client.post(url, files={"file": ("x.tar", b"not a tar")}).status_code == 400
    assert client.post(url, data={"drive_ids": "ghost"}, files={"file": ("x.tar", tar_bytes())}).status_code == 400
    assert client.post(url, data={"path": str(tmp_path / "missing.tar")}).status_code == 400
    assert client.post(url).status_code == 400
    src = tmp_path / "_private_data.tar"
    src.write_bytes(tar_bytes())
    r = client.post(url, data={"path": str(src), "drive_ids": "all"})
    assert r.status_code == 200 and src.exists()


def test_firmware_dump_import(client, settings):
    url = "/api/setup/makemkv/private-data"
    r = client.post(url, data={"drive_ids": "fake-ggc-h20n"}, files={"file": ("dump_FW_ABC.tgz", tar_bytes())})
    assert r.status_code == 200
    home = settings.makemkv_dir / "fake-ggc-h20n" / ".MakeMKV"
    assert (home / "dump_FW_ABC.tgz").exists()
    assert not (home / "_private_data.tar").exists(), "a dump is not private data"
    assert (settings.makemkv_dir / "_template" / "dump_FW_ABC.tgz").exists()
    view = client.get("/api/settings").json()
    assert view["makemkv_drive_dumps"]["fake-ggc-h20n"] is True
    assert client.post(url, files={"file": ("dump_FW_X.tgz", b"not a tar")}).status_code == 400


def test_eject_and_rescan_errors(client):
    assert client.post("/api/drives/fake-ihas122/eject").status_code == 204
    r = client.post("/api/drives/fake-ihas122/scan")
    assert r.status_code == 409 and "no disc" in r.json()["detail"]
    assert client.get("/api/jobs").json() == []


def test_spa_fallback_and_static(client):
    r = client.get("/review")
    if r.status_code == 404:
        pytest.skip("UI not built")
    assert "<!doctype html>" in r.text.lower()
    assert client.get("/../../etc/passwd").status_code in (200, 404)
    assert "root:" not in client.get("/..%2F..%2Fetc%2Fpasswd").text
    assert client.get("/api/does-not-exist").status_code == 404


def test_event_bus_drops_stalled_subscribers():
    bus = EventBus(queue_size=1)
    with bus.subscribe() as q:
        from ripshelf.models import Status, StatusChanged
        ev = StatusChanged(status=Status(version="x", fake_hardware=True, problems=[], review_count=0))
        bus.publish(ev)
        bus.publish(ev)  # queue full, so the subscriber is dropped, not blocking
        assert q.qsize() == 1
        assert not bus._subs
    assert JobUpdated.model_fields["type"].default == "job"


def test_drive_names(client, settings):
    d = next(d for d in client.get("/api/drives").json() if d["id"] == "fake-ihas122")
    assert d["name"] is None and d["display_name"] == " ".join(f"{d['vendor']} {d['model']}".split())
    r = client.put("/api/drives/fake-ihas122/name", json={"name": "  Top bay  "})
    assert r.status_code == 200 and r.json()["name"] == "Top bay" == r.json()["display_name"]
    assert client.get("/api/settings").json()["drive_names"] == {"fake-ihas122": "Top bay"}
    assert settings.drive_names == {"fake-ihas122": "Top bay"}
    stats = {s["drive_id"]: s for s in client.get("/api/drives/stats").json()}
    assert stats["fake-ihas122"]["name"] == "Top bay" and stats["fake-pioneer"]["name"] is None
    # Too long, unknown drive; an empty (or blank) name clears it.
    assert client.put("/api/drives/fake-ihas122/name", json={"name": "x" * 41}).status_code == 422
    assert client.put("/api/drives/nope/name", json={"name": "Bottom bay"}).status_code == 404
    r = client.put("/api/drives/fake-ihas122/name", json={"name": "   "})
    assert r.json()["name"] is None and r.json()["display_name"] == d["display_name"]
    assert settings.drive_names == {}


def test_setup_flag_and_tools(client, settings):
    settings.setup_complete = False
    assert client.get("/api/settings").json()["setup_complete"] is False  # a fresh install
    tools = client.get("/api/setup/tools").json()
    assert tools["makemkv"]["found"]  # the simulated makemkvcon
    assert {"ffmpeg", "ffprobe", "java", "makemkv_registered"} <= tools.keys()
    assert client.put("/api/settings", json={"setup_complete": True}).json()["setup_complete"] is True


def test_makemkv_eula_is_shown_until_accepted(client, settings, tmp_path):
    assert not client.get("/api/setup/tools").json()["makemkv_eula_pending"]
    assert client.get("/api/setup/makemkv/eula").status_code == 404  # MakeMKV didn't come with ripshelf
    settings.makemkv_eula_file = tmp_path / "eula_en_linux.txt"
    settings.makemkv_eula_file.write_text("END USER LICENSE AGREEMENT", encoding="utf-8")
    assert client.get("/api/setup/tools").json()["makemkv_eula_pending"]
    r = client.get("/api/setup/makemkv/eula")
    assert r.status_code == 200 and "LICENSE" in r.text and r.headers["content-type"].startswith("text/plain")
    settings.accept_makemkv_eula = True
    assert not client.get("/api/setup/tools").json()["makemkv_eula_pending"]


def test_installs_from_before_the_setup_page_count_as_set_up(settings, monkeypatch):
    from ripshelf import config

    monkeypatch.setenv("RIPSHELF_DATA_DIR", str(settings.data_dir))
    monkeypatch.setenv("RIPSHELF_SCRATCH_DIR", str(settings.scratch_dir))
    monkeypatch.setenv("RIPSHELF_LIBRARY_DIR", str(settings.library_dir))
    assert not config.load_settings().setup_complete  # nothing saved yet, so a fresh install
    (settings.data_dir / "settings.json").write_text('{"auto_eject": false}', encoding="utf-8")
    assert config.load_settings().setup_complete


def test_folder_check_and_create(client, tmp_path):
    new = tmp_path / "films" / "4K"
    r = client.post("/api/setup/folder", json={"path": str(new)}).json()
    assert r["exists"] is False and r["free"] and not new.exists()  # free space of the folder it'd be made in
    r = client.post("/api/setup/folder", json={"path": str(new), "create": True}).json()
    assert r["exists"] and r["created"] and new.is_dir()
    assert client.post("/api/setup/folder", json={"path": "relative"}).status_code == 400
