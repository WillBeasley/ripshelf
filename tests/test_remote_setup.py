"""Setting up remote workers from the controller, with per-worker tokens, and the MakeMKV key,
private data and firmware dumps a worker fetches when it registers."""

from __future__ import annotations

import asyncio
import hashlib
import io
import tarfile
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf.dev.fake import FakeDrives
from ripshelf.main import create_app
from ripshelf.makemkv import files as mkfiles
from ripshelf.makemkv import runner
from ripshelf.workers.agent import Agent, ControllerGone
from ripshelf.workers.api import compose_snippet, hash_token, slug

from .conftest import private
from .test_workers import TOKEN, NoDrives, controller, worker_settings  # noqa: F401

PM = "/dev/fake-sr1"  # the worker's fake iHAS122, drive id fake-ihas122


def tar_bytes(member: str = "hkd_0001.bin", body: bytes = b"x") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo(member)
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
    return buf.getvalue()


@pytest.fixture
def client(settings):
    settings.worker_token = None
    with TestClient(create_app(settings, backend=NoDrives(present=set()))) as c:
        yield c


def register(c, token, worker_id="den", drives=None):
    return c.post("/api/workers/register", headers={"Authorization": f"Bearer {token}"}, json={
        "id": worker_id, "name": "Den PC", "drives": drives or [{"id": "lg", "device": "/dev/sr0"}]})


# --- the controller's store -----------------------------------------------------------------

def test_store_replaces_merges_and_prunes(tmp_path):
    store = mkfiles.SupportFiles(tmp_path / "store")
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(tar_bytes(body=b"a"))
    b.write_bytes(tar_bytes(body=b"b"))
    fa = store.add(a, "_private_data.tar", ["d1", "d2"])
    assert private(store.path(fa.sha256)) and private(store.root / "index.json")
    assert fa.sha256 == hashlib.sha256(a.read_bytes()).hexdigest() and fa.size == a.stat().st_size
    fb = store.add(b, "_private_data.tar", ["d2"])  # d2 gets a new tar, d1 keeps the old one
    assert {(f.sha256, tuple(f.drives)) for f in store.list()} == {(fa.sha256, ("d1",)), (fb.sha256, ("d2",))}
    store.add(a, "_private_data.tar", ["d3"])  # the same bytes for another drive, so one entry
    assert next(f for f in store.list() if f.sha256 == fa.sha256).drives == ["d1", "d3"]
    store.add(b, "_private_data.tar", None)  # for every drive, so it replaces the others
    assert [(f.sha256, f.drives) for f in store.list()] == [(fb.sha256, None)]
    assert not store.path(fa.sha256).exists(), "an unused copy is deleted"
    store.add(a, "dump_FW1.tgz", ["d1"])
    store.add(b, "dump_FW1.tgz", ["d2"])  # a dump of the same name replaces the older one
    dumps = [f for f in store.list() if f.name == "dump_FW1.tgz"]
    assert [(f.sha256, f.drives) for f in dumps] == [(fb.sha256, ["d2"])]
    store.rename_drive("d2", "d2-new")
    assert store.list()[-1].drives == ["d2-new"]
    assert store.remove(fb.sha256, "dump_FW1.tgz") and not store.remove(fb.sha256, "dump_FW1.tgz")
    assert store.path(fb.sha256).exists(), "still used by the private data"
    assert store.remove(fb.sha256) and not store.list() and not list(store.root.glob("*.bin"))


@pytest.mark.parametrize("name", ["../_private_data.tar", "private.tar", "dump_../x.tgz", "dump_a/b.tgz", ""])
def test_store_refuses_other_names(tmp_path, name):
    src = tmp_path / "x"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        mkfiles.SupportFiles(tmp_path / "s").add(src, name, None)


def test_store_refuses_big_files_and_bad_hashes(tmp_path, monkeypatch):
    monkeypatch.setattr(mkfiles, "MAX_BYTES", 3)
    src = tmp_path / "x"
    src.write_bytes(b"1234")
    store = mkfiles.SupportFiles(tmp_path / "s")
    with pytest.raises(ValueError, match="too big"):
        store.add(src, "_private_data.tar", None)
    for bad in ("../index.json", "A" * 64, "0" * 63):
        with pytest.raises(ValueError):
            store.path(bad)


# --- installing on a worker ---------------------------------------------------------------------

def test_wanted_for_prefers_a_drives_own_file():
    entries = [{"name": "_private_data.tar", "sha256": "a", "drives": None},
               {"name": "_private_data.tar", "sha256": "b", "drives": ["lg"]},
               {"name": "dump_1.tgz", "sha256": "c", "drives": None},
               {"name": "dump_2.tgz", "sha256": "d", "drives": ["other"]}]
    assert mkfiles.wanted_for(entries, "lg") == {"_private_data.tar": "b", "dump_1.tgz": "c"}
    assert mkfiles.wanted_for(entries, None) == {"_private_data.tar": "a", "dump_1.tgz": "c"}


def test_install_is_atomic_private_and_skips_what_is_there(tmp_path):
    conf = tmp_path / "home" / ".MakeMKV"
    src = tmp_path / "src"
    src.write_bytes(b"v1")
    sha1 = hashlib.sha256(b"v1").hexdigest()
    assert mkfiles.needs(conf, "_private_data.tar", sha1)
    mkfiles.install(conf, "_private_data.tar", sha1, src)
    tar = conf / "_private_data.tar"
    assert tar.read_bytes() == b"v1" and private(tar)
    assert runner.has_private_data(tmp_path, "home")
    assert sorted(p.name for p in conf.iterdir()) == [runner.IMPORTED_MARKER, mkfiles.SYNC_STATE,
                                                       "_private_data.tar"], "no temporary files left"
    assert not mkfiles.needs(conf, "_private_data.tar", sha1)
    tar.write_bytes(b"MakeMKV added to it")  # MakeMKV rewrites its own tar, so it's left alone
    assert not mkfiles.needs(conf, "_private_data.tar", sha1)
    assert mkfiles.needs(conf, "_private_data.tar", hashlib.sha256(b"v2").hexdigest()), "a new upload"


def test_files_put_there_before_are_adopted(tmp_path):
    conf = tmp_path / ".MakeMKV"
    conf.mkdir()
    (conf / "dump_1.tgz").write_bytes(b"old")
    assert not mkfiles.needs(conf, "dump_1.tgz", "f" * 64), "a dump's name names its firmware"
    (conf / "_private_data.tar").write_bytes(b"MakeMKV's own")
    assert mkfiles.needs(conf, "_private_data.tar", "e" * 64), "never imported, so install"
    (conf / runner.IMPORTED_MARKER).write_text("imported\n")
    assert not mkfiles.needs(conf, "_private_data.tar", "e" * 64), "imported by hand or by the controller"
    assert mkfiles._state(conf) == {"dump_1.tgz": "f" * 64, "_private_data.tar": "e" * 64}


def test_set_key_is_atomic_and_private(tmp_path):
    conf = tmp_path / ".MakeMKV"
    conf.mkdir()
    (conf / "settings.conf").write_text('app_Key = "old"\nio_ErrorRetryCount = "4"\n')
    runner.set_key(conf, "T-new")
    assert (conf / "settings.conf").read_text() == 'app_Key = "T-new"\nio_ErrorRetryCount = "4"\n'
    assert private(conf / "settings.conf") and [p.name for p in conf.iterdir()] == ["settings.conf"]


# --- per-worker tokens ------------------------------------------------------------------------

def test_slug_and_compose():
    assert slug("Den PC") == "den-pc" and slug("  ") == "worker" and slug("Ä/../x") == "x"
    text = compose_snippet('Den "PC"', "den-pc", "tok-en_1", "https://ripper.lan")
    env = yaml.safe_load(text)["services"]["ripshelf-worker"]["environment"]
    assert env["RIPSHELF_WORKER_TOKEN"] == "tok-en_1" and env["RIPSHELF_WORKER_NAME"] == 'Den "PC"'
    assert env["RIPSHELF_CONTROLLER_URL"] == "https://ripper.lan" and env["RIPSHELF_WORKER_ID"] == "den-pc"
    assert "RIPSHELF_MAKEMKV_KEY" not in env


def test_remote_workers_off_until_one_is_added(client):
    r = register(client, "anything")
    assert r.status_code == 403 and "Remote workers" in r.json()["detail"]
    assert client.get("/api/remote-workers").json() == {"workers": [], "shared_token": False}


def test_add_use_and_revoke_a_worker_token(client):
    r = client.post("/api/remote-workers", json={"name": "Den PC", "controller_url": "http://ripper.lan:8380/"})
    assert r.status_code == 200
    new = r.json()
    token, w = new["token"], new["worker"]
    assert len(token) >= 40 and w["worker_id"] == "den-pc" and w["last_seen_at"] is None and not w["online"]
    env = yaml.safe_load(new["compose"])["services"]["ripshelf-worker"]["environment"]
    assert env["RIPSHELF_WORKER_TOKEN"] == token and env["RIPSHELF_CONTROLLER_URL"] == "http://ripper.lan:8380"
    db = client.app.state.manager.db
    row = db.worker_tokens()[0]
    assert row["token_hash"] == hash_token(token) and token not in str(row), "only the hash is kept"
    # A second worker of the same name gets its own id; the list never shows tokens.
    assert client.post("/api/remote-workers", json={"name": "Den PC"}).json()["worker"]["worker_id"] == "den-pc-2"
    assert client.post("/api/remote-workers", json={"name": "  "}).status_code == 422
    listed = client.get("/api/remote-workers").json()["workers"]
    assert [x["worker_id"] for x in listed] == ["den-pc", "den-pc-2"] and token not in str(listed)

    assert register(client, "wrong", "den-pc").status_code == 401
    assert register(client, token, "someone-else").status_code == 403, "a token is for its own worker only"
    assert register(client, token, "den-pc").status_code == 200
    h = {"Authorization": f"Bearer {token}"}
    other = client.post("/api/remote-workers", json={"name": "Attic"}).json()
    register(client, other["token"], "attic")
    assert client.post("/api/workers/attic/heartbeat", json={"drives": []}, headers=h).status_code == 403
    assert client.post("/api/workers/den-pc/heartbeat", json={"drives": []}, headers=h).status_code == 200
    seen = client.get("/api/remote-workers").json()["workers"][0]
    assert seen["online"] and seen["last_seen_at"]

    assert client.delete(f"/api/remote-workers/{w['id']}").status_code == 204
    assert client.delete(f"/api/remote-workers/{w['id']}").status_code == 404
    assert client.post("/api/workers/den-pc/heartbeat", json={"drives": []}, headers=h).status_code == 401
    assert client.get("/api/workers/den-pc/makemkv", headers=h).status_code == 401
    assert not client.app.state.manager.hub.workers["den-pc"].online, "revoking takes it offline at once"
    again = client.post("/api/remote-workers", json={"name": "Den PC"}).json()
    assert again["worker"]["worker_id"] == "den-pc", "added back, with the same id"


def test_shared_token_works_and_is_reported(settings):
    settings.worker_token = SecretStr(TOKEN)
    with TestClient(create_app(settings, backend=NoDrives(present=set()))) as c:
        assert c.get("/api/remote-workers").json()["shared_token"] is True
        assert register(c, TOKEN, "anyone").status_code == 200
        assert register(c, "nope", "anyone").status_code == 401


# --- bootstrap, key, manifest, downloads -----------------------------------------------------

def test_bootstrap_gives_a_worker_its_own_files(client, settings):
    settings.makemkv_key = SecretStr("T-controller-key")
    tok = client.post("/api/remote-workers", json={"name": "den"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/workers/den/makemkv", headers=h).status_code == 404, "register first"
    # Another worker's drive takes "lg" first, so den's lg becomes "den:lg" on the controller.
    client.post("/api/remote-workers", json={"name": "attic"})
    settings.worker_token = SecretStr(TOKEN)
    register(client, TOKEN, "attic", [{"id": "lg", "device": "/dev/sr0"}])
    register(client, tok, "den", [{"id": "lg", "device": "/dev/sr0"}, {"id": "bd", "device": "/dev/sr1"}])
    url = "/api/setup/makemkv/private-data"
    assert client.post(url, data={"drive_ids": "den:lg"},
                       files={"file": ("_private_data.tar", tar_bytes(body=b"den"))}).status_code == 200
    assert client.post(url, data={"drive_ids": "lg"},
                       files={"file": ("_private_data.tar", tar_bytes(body=b"attic"))}).status_code == 200
    assert client.post(url, files={"file": ("dump_FW.tgz", tar_bytes())}).status_code == 200
    boot = client.get("/api/workers/den/makemkv", headers=h).json()
    assert boot["key"] == "T-controller-key"
    by_name = {(f["name"], tuple(f["drives"] or ())): f for f in boot["files"]}
    assert set(by_name) == {("_private_data.tar", ("lg",)), ("dump_FW.tgz", ())}, "attic's tar is not offered"
    mine = by_name[("_private_data.tar", ("lg",))]["sha256"]
    got = client.get(f"/api/workers/den/makemkv/files/{mine}", headers=h)
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == mine
    attics = next(f.sha256 for f in client.app.state.manager.support.list() if f.drives == ["lg"])
    assert client.get(f"/api/workers/den/makemkv/files/{attics}", headers=h).status_code == 404
    assert client.get("/api/workers/den/makemkv/files/..%2Findex.json", headers=h).status_code == 404
    assert client.get("/api/workers/den/makemkv").status_code in (401, 403)

    listed = client.get("/api/setup/makemkv/files").json()
    assert {f["name"] for f in listed} == {"_private_data.tar", "dump_FW.tgz"}
    assert all(len(f["sha256"]) == 64 for f in listed)
    assert client.delete(f"/api/setup/makemkv/files/{mine}").status_code == 204
    assert client.delete(f"/api/setup/makemkv/files/{mine}").status_code == 404
    assert client.delete("/api/setup/makemkv/files/nonsense").status_code == 404
    assert "T-controller-key" not in client.get("/api/settings").text, "the browser never sees the key"


def test_upload_size_limit(client, monkeypatch):
    monkeypatch.setattr(mkfiles, "MAX_BYTES", 100)
    r = client.post("/api/setup/makemkv/private-data", files={"file": ("_private_data.tar", tar_bytes())})
    assert r.status_code == 413 and not client.app.state.manager.support.list()


# --- end to end, a worker on another machine ---------------------------------------------------

async def test_worker_installs_key_and_files_from_the_controller(controller, tmp_path):  # noqa: F811
    url, app = controller
    app.state.settings.makemkv_key = SecretStr("T-from-controller")
    async with httpx.AsyncClient(base_url=url) as ui:
        new = (await ui.post("/api/remote-workers", json={"name": "den"})).json()
        ws = worker_settings(tmp_path, url, token=new["token"])
        ws.makemkv_key = None
        agent = Agent(ws, FakeDrives(present={PM}))
        await agent.register()  # registers, then syncs (nothing to install yet but the key)
        conf = runner.drive_home(ws.makemkv_dir, "fake-ihas122") / ".MakeMKV"
        assert 'app_Key = "T-from-controller"' in (conf / "settings.conf").read_text()
        assert 'app_Key = "T-from-controller"' in (ws.data_dir / ".MakeMKV" / "settings.conf").read_text()
        r = await ui.post("/api/setup/makemkv/private-data", data={"drive_ids": "fake-ihas122"},
                          files={"file": ("_private_data.tar", tar_bytes(body=b"lg"))})
        assert r.status_code == 200
        await ui.post("/api/setup/makemkv/private-data", files={"file": ("dump_FW9.tgz", tar_bytes())})
        installed = await agent.sync_makemkv()
        # The tar goes to its own drive, the dump to every drive (the fake worker has several).
        assert [i for i in installed if i.endswith(".tar")] == ["fake-ihas122/_private_data.tar"]
        assert {"fake-ihas122/dump_FW9.tgz", "fake-pioneer/dump_FW9.tgz", "home/dump_FW9.tgz"} <= set(installed)
        assert (conf / "_private_data.tar").read_bytes() == tar_bytes(body=b"lg")
        assert private(conf / "_private_data.tar") and private(conf / "settings.conf")
        assert not list(Path(ws.scratch_dir).glob(".makemkv-sync-*")), "downloads cleaned up"
        assert await agent.sync_makemkv() == [], "nothing changed, nothing fetched"
        # A worker's own RIPSHELF_MAKEMKV_KEY wins.
        ws.makemkv_key = SecretStr("T-local")
        agent2 = Agent(ws, FakeDrives(present={PM}), client=agent.client)
        await agent2.register()
        assert 'app_Key = "T-local"' in (conf / "settings.conf").read_text()
        # Revoked, so the worker is refused.
        await ui.delete(f"/api/remote-workers/{new['worker']['id']}")
        with pytest.raises(ControllerGone, match="401"):
            await agent.register()
    await agent.client.aclose()


async def test_damaged_download_is_not_installed(tmp_path):
    body = b"tampered"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/makemkv"):
            return httpx.Response(200, json={"key": None, "files": [
                {"name": "dump_X.tgz", "sha256": "0" * 64, "size": 8, "drives": None}]})
        return httpx.Response(200, content=body)

    ws = worker_settings(tmp_path, "http://controller")
    agent = Agent(ws, FakeDrives(present=set()),
                  client=httpx.AsyncClient(base_url="http://controller", transport=httpx.MockTransport(handler)))
    with pytest.raises(ValueError, match="sha256"):
        await agent.sync_makemkv()
    assert not (ws.data_dir / ".MakeMKV" / "dump_X.tgz").exists()
    await agent.client.aclose()


def test_https_controller_with_a_private_ca(tmp_path):
    import certifi

    ws = worker_settings(tmp_path, "https://ripper.lan")
    ws.controller_ca_file = Path(certifi.where())
    agent = Agent(ws, FakeDrives(present=set()))
    assert str(agent.client.base_url) == "https://ripper.lan"
    asyncio.run(agent.client.aclose())
