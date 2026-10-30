"""Remote workers, with a real controller server and a worker agent talking over HTTP."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from ripshelf.config import Settings
from ripshelf.dev.fake import FakeDrives
from ripshelf.main import begin_shutdown, create_app
from ripshelf.workers import agent as agentmod
from ripshelf.workers import hub as hubmod
from ripshelf.workers.agent import Agent, ControllerGone

from .conftest import private, until

TOKEN = "worker-secret"
PM = "/dev/fake-sr1"


class NoDrives(FakeDrives):
    """A controller with no drives of its own."""

    def discover(self, uhd_models):
        return []


@pytest.fixture
def controller(settings):
    settings.worker_token = SecretStr(TOKEN)
    app = create_app(settings, backend=NoDrives(present=set()))
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}", app
    begin_shutdown(app)  # so the workers' long-polls end now, not at uvicorn's timeout
    srv.should_exit = True
    t.join(10)


def worker_settings(tmp_path: Path, url: str, shared: Path | None = None, token: str = TOKEN) -> Settings:
    s = Settings(_env_file=None, data_dir=tmp_path / "w-data", scratch_dir=tmp_path / "w-scratch",
                 library_dir=tmp_path / "w-lib", fake_hardware=True,
                 makemkvcon=f"{sys.executable} -m ripshelf.dev.fake_makemkvcon",
                 controller_url=url, worker_token=SecretStr(token), worker_id="den", worker_name="Den PC",
                 worker_shared_scratch=shared is not None, transcode_encoder="x265")
    for d in (s.data_dir, s.scratch_dir, s.makemkv_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


def api(url: str, path: str):
    return httpx.get(f"{url}{path}", timeout=10).json()


def wait_http(pred, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out")


async def test_remote_drive_rips_into_the_controller_library(controller, tmp_path, monkeypatch):
    url, app = controller
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.5")
    agent = Agent(worker_settings(tmp_path, url), FakeDrives(present={PM}))
    task = asyncio.create_task(agent.run_forever())
    try:
        drives = await asyncio.to_thread(wait_http, lambda: [d for d in api(url, "/api/drives")
                                                             if d["id"] == "fake-ihas122"])
        assert drives[0]["worker"] == "Den PC" and drives[0]["device"] == PM
        workers = api(url, "/api/workers")
        assert workers[0]["online"] and "fake-ihas122" in workers[0]["drives"]
        job = await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["state"] == "review" and j["rip"]["state"] == "done"), None))
        assert job["drive_id"] == "fake-ihas122" and job["main_title"]["duration_s"] > 3600
        detail = api(url, f"/api/jobs/{job['id']}")
        assert any("Scan found" in e["message"] for e in detail["events"])
        assert api(url, f"/api/jobs/{job['id']}/log?kind=scan")  # remote output reached the log
        r = httpx.post(f"{url}/api/jobs/{job['id']}/confirm",
                       json={"candidate": {"title": "Practical Magic", "year": 1998, "source": "manual"}})
        assert r.status_code == 200
        done = await asyncio.to_thread(wait_http, lambda: (j := api(url, f"/api/jobs/{job['id']}"))
                                       ["state"] == "done" and j)
        dest = Path(done["destination"])
        assert dest.exists() and dest.stat().st_size > 0
        s = app.state.settings
        assert dest.is_relative_to(s.library_dir)
        assert not list((tmp_path / "w-scratch").glob("remote-run-*/*.mkv")), "worker cleaned up its copy"
        assert (dest.parent / ".disc/VIDEO_TS/VIDEO_TS.IFO").exists(), "structure files came from the worker"
        rec = app.state.manager.db.get_job(job["id"])
        assert rec.disc_ids.get("content_hash"), "the disc's file sizes came from the worker too"
    finally:
        task.cancel()


async def test_shared_scratch_skips_the_upload(controller, tmp_path, monkeypatch):
    url, app = controller
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.3")
    uploads = []
    real_put = httpx.AsyncClient.put

    async def counting_put(self, u, *a, **k):
        if "/files/" in str(u) and not str(u).endswith("structure.tar"):
            uploads.append(u)
        return await real_put(self, u, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "put", counting_put)
    agent = Agent(worker_settings(tmp_path, url, shared=app.state.settings.scratch_dir), FakeDrives(present={PM}))
    task = asyncio.create_task(agent.run_forever())
    try:
        job = await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["rip"]["state"] == "done"), None))
        assert job["rip"]["file"] and Path(job["rip"]["file"]).exists()
        assert uploads == []
    finally:
        task.cancel()


async def test_wrong_token_is_refused(controller, tmp_path):
    url, _ = controller
    agent = Agent(worker_settings(tmp_path, url, token="nope"), FakeDrives(present=set()))
    with pytest.raises(ControllerGone, match="401"):
        await agent.register()
    await agent.client.aclose()


async def test_workers_off_without_a_token(settings, tmp_path):
    from fastapi.testclient import TestClient

    settings.worker_token = None
    with TestClient(create_app(settings, backend=NoDrives(present=set()))) as c:
        r = c.post("/api/workers/register", json={"id": "x", "name": "x"}, headers={"Authorization": "Bearer x"})
        assert r.status_code == 403 and "off" in r.json()["detail"]
        assert c.get("/api/workers").json() == []


def test_worker_api_validation(settings):
    from fastapi.testclient import TestClient

    settings.worker_token = SecretStr(TOKEN)
    h = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(settings, backend=NoDrives(present=set()))) as c:
        assert c.post("/api/workers/register", json={"id": "a/b", "name": "x"}, headers=h).status_code == 422
        assert c.post("/api/workers/register", json={"id": "a", "name": "x", "drives": [{"id": 1}]},
                      headers=h).status_code == 422
        assert c.post("/api/workers/ghost/heartbeat", json={"drives": []}, headers=h).status_code == 404
        assert c.post("/api/workers/register", json={"id": "w", "name": "W", "drives": [
            {"id": "d1", "device": "/dev/sr0", "capabilities": ["dvd", "warp"]}]}, headers=h).status_code == 200
        assert c.put("/api/workers/w/runs/999/files/x.mkv", content=b"x", headers=h).status_code == 404
        assert c.post("/api/workers/w/heartbeat", json={"drives": [{"id": "d1", "status": "nonsense"},
                                                                   {"id": "nope"}]}, headers=h).status_code == 200
        d = next(x for x in c.get("/api/drives").json() if x["id"] == "d1")
        assert d["status"] in ("error", "empty") and d["capabilities"] == ["dvd"]


@pytest.mark.parametrize("name, ok", [("title_t00.mkv", True), ("C1_t01.mkv", True), ("../etc/passwd", False),
                                      (".hidden", False), ("a/b.mkv", False), ("", False), ("x" * 300, False)])
def test_upload_names(name, ok):
    assert hubmod.safe_file_name(name) is ok


async def test_worker_going_quiet_fails_its_runs(tmp_path, monkeypatch):
    h = hubmod.WorkerHub()
    h.register("w", "W", [{"id": "d", "device": "/dev/sr0"}], [], False)
    m = hubmod.RemoteMakeMKV(h, "w", "d")
    task = asyncio.create_task(m.run(["-r", "info", "dev:/dev/sr0"], tmp_path, tmp_path / "log"))
    await until(lambda: h.runs)
    assert (await h.next_commands("w", wait=1))[0]["type"] == "makemkv"
    monkeypatch.setattr(hubmod, "HEARTBEAT_TIMEOUT_S", 0)
    h.check_timeouts()
    res = await asyncio.wait_for(task, 2)
    assert res.error == "the worker stopped answering" and not h.workers["w"].online
    assert "stopped answering" in (tmp_path / "log").read_text()
    # With an offline worker, new runs fail at once, and status reads as error.
    res = await m.run(["-r", "info", "dev:/dev/sr0"], tmp_path, tmp_path / "log2")
    assert res.error and "offline" in res.error
    assert hubmod.RemoteBackend(h, "w").status("/dev/sr0").value == "error"


async def test_long_poll_ends_when_the_server_shuts_down():
    # Otherwise uvicorn cancels it after the grace period and logs a traceback for each worker.
    h = hubmod.WorkerHub()
    h.register("w", "W", [{"id": "d", "device": "/dev/sr0"}], [], False)
    stop = asyncio.Event()
    poll = asyncio.create_task(h.next_commands("w", wait=30, stop=stop))
    await asyncio.sleep(0.05)
    stop.set()
    assert await asyncio.wait_for(poll, 1) == []
    # A command queued after that is still there for the next poll.
    h.workers["w"].commands.put_nowait({"type": "eject"})
    assert await h.next_commands("w", wait=1) == [{"type": "eject"}]


async def test_cancel_and_reregister(tmp_path):
    h = hubmod.WorkerHub()
    h.register("w", "W", [{"id": "d", "device": "/dev/sr0"}], [], False)
    m = hubmod.RemoteMakeMKV(h, "w", "d")
    task = asyncio.create_task(m.run(["-r", "mkv", "dev:/dev/sr0", "0", str(tmp_path / "out")], tmp_path,
                                     tmp_path / "log"))
    await until(lambda: h.runs)
    run_id = next(iter(h.runs))
    m.cancel()
    cmds = await h.next_commands("w", wait=1)
    assert [c["type"] for c in cmds] == ["makemkv", "cancel"] and cmds[1]["run"] == run_id
    h.lines(run_id, ["PRGV:1,2,3", "MSG:1,0,0,\"x\"\n"])
    h.register("w", "W", [{"id": "d", "device": "/dev/sr0"}], [], False)  # worker restarted
    res = await asyncio.wait_for(task, 2)
    assert res.error == "the worker restarted"
    assert "PRGV:1,2,3" in (tmp_path / "log").read_text()


async def test_worker_drives_keep_their_own_id_unless_taken():
    h = hubmod.WorkerHub(local_ids=lambda: {"local-drive"})
    a = h.register("a", "A", [{"id": "lg", "device": "/dev/sr3"}], [], False)
    assert [rd.hw.id for rd in a.drives.values()] == ["lg"]
    b = h.register("b", "B", [{"id": "lg", "device": "/dev/sr0"}, {"id": "local-drive", "device": "/dev/sr1"}],
                   [], False)
    assert sorted(rd.hw.id for rd in b.drives.values()) == ["b:lg", "b:local-drive"]
    # Re-registering keeps the same ids (the worker restarted).
    a2 = h.register("a", "A", [{"id": "lg", "device": "/dev/sr3"}], [], False)
    assert [rd.hw.id for rd in a2.drives.values()] == ["lg"]


async def test_an_offline_workers_drive_id_can_be_taken_over():
    h = hubmod.WorkerHub()
    h.register("old", "Old", [{"id": "lg-1", "device": "/dev/sr0"}], [], False)
    assert h.register("new", "New", [{"id": "lg-1", "device": "/dev/sr0"}], [], False).drives["lg-1"].hw.id \
        == "new:lg-1", "taken by an online worker"
    h.disconnect("old", "revoked")
    h.register("new", "New", [{"id": "lg-1", "device": "/dev/sr0"}], [], False)
    assert h.workers["new"].drives["lg-1"].hw.id == "lg-1" and not h.workers["old"].drives


def test_worker_token_file_is_made_once_by_the_controller_and_read_by_workers(tmp_path):
    from ripshelf.config import load_worker_token

    f = tmp_path / "makemkv" / ".worker-token"
    worker = Settings(_env_file=None, data_dir=tmp_path, worker_token_file=f)
    assert load_worker_token(worker, create=False) is None and not f.exists()
    controller = Settings(_env_file=None, data_dir=tmp_path, worker_token_file=f)
    token = load_worker_token(controller, create=True)
    assert token and len(token) == 48 and private(f)
    assert load_worker_token(Settings(_env_file=None, data_dir=tmp_path, worker_token_file=f), create=True) == token
    assert load_worker_token(worker, create=False) == token
    explicit = Settings(_env_file=None, data_dir=tmp_path, worker_token_file=f, worker_token=SecretStr("typed"))
    assert load_worker_token(explicit, create=True) == "typed"


@pytest.mark.parametrize("shared", [True, False])
async def test_backup_on_a_worker_needs_shared_scratch(controller, tmp_path, monkeypatch, shared):
    url, app = controller
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.3")
    app.state.settings.rip_mode = "backup"
    uhd = "/dev/fake-sr2"
    agent = Agent(worker_settings(tmp_path, url, shared=app.state.settings.scratch_dir if shared else None),
                  FakeDrives(present={uhd}))
    task = asyncio.create_task(agent.run_forever())
    try:
        job = await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["rip"]["state"] == "done"), None))
        if shared:
            assert job["mode"] == "backup"
            assert (Path(job["rip"]["file"]) / "BDMV/index.bdmv").exists()
        else:
            assert job["mode"] == "titles" and job["rip"]["file"].endswith(".mkv")
            detail = api(url, f"/api/jobs/{job['id']}")
            assert any("doesn't share the scratch folder" in e["message"] for e in detail["events"])
    finally:
        task.cancel()


def test_worker_drive_with_a_new_id_keeps_its_history(settings):
    from fastapi.testclient import TestClient

    settings.worker_token = SecretStr(TOKEN)
    h = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(settings, backend=NoDrives(present=set()))) as c:
        m = c.app.state.manager
        m.db.create_job("lg-0a1b", "/dev/sr3")
        m.set_drive_name("lg-0a1b", "Top bay")
        assert c.post("/api/workers/register", json={"id": "w", "name": "W", "drives": [
            {"id": "lg", "device": "/dev/sr3", "legacy_id": 5}]}, headers=h).status_code == 422
        assert c.post("/api/workers/register", json={"id": "w", "name": "W", "drives": [
            {"id": "lg-5e7a9c01", "device": "/dev/sr3", "legacy_id": "lg-0a1b"}]}, headers=h).status_code == 200
        d = next(x for x in c.get("/api/drives").json() if x["id"] == "lg-5e7a9c01")
        assert d["display_name"] == "Top bay" and d["job_id"] is not None
        assert [j["drive_id"] for j in c.get("/api/jobs").json()] == ["lg-5e7a9c01"]


# --- staying connected through outages, abandoned runs and refused tokens ------------------------------

async def test_hub_says_which_runs_it_has_given_up_on(tmp_path):
    h = hubmod.WorkerHub()
    h.register("w", "W", [{"id": "d", "device": "/dev/sr0"}], [], False)
    h.register("v", "V", [], [], False)
    run = h.start_run("w", {"type": "makemkv"}, tmp_path / "log", None, None)
    assert run.id > 10**12, "ids aren't reused after a controller restart"
    assert h.given_up("w", [run.id, 5]) == [5]
    assert h.given_up("v", [run.id]) == [run.id], "another worker's run is not yours"
    h.disconnect("w", "its token was revoked")
    assert h.given_up("w", [run.id]) == [run.id]


def flaky_client(url: str, token: str = TOKEN):
    """A worker's client whose connection can be cut with ``client.down = True``."""
    class Flaky(httpx.AsyncHTTPTransport):
        down = False

        async def handle_async_request(self, request):
            if Flaky.down:
                raise httpx.ConnectError("network down", request=request)
            return await super().handle_async_request(request)

    client = httpx.AsyncClient(base_url=url, headers={"Authorization": f"Bearer {token}"},
                               timeout=httpx.Timeout(60, connect=10), transport=Flaky())
    return client, Flaky


@pytest.mark.slow
async def test_short_outage_mid_rip_ends_nothing(controller, tmp_path, monkeypatch):
    url, app = controller
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "6")
    monkeypatch.setattr(agentmod, "RETRY_S", 0.2)
    client, net = flaky_client(url)
    agent = Agent(worker_settings(tmp_path, url), FakeDrives(present={PM}), client=client)
    task = asyncio.create_task(agent.run_forever())
    try:
        await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["rip"]["state"] == "running"), None))
        worker = app.state.manager.hub.workers["den"]
        net.down = True
        await asyncio.sleep(3)  # well inside the controller's 45 s
        net.down = False
        job = await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["rip"]["state"] in ("done", "failed")), None))
        assert job["rip"]["state"] == "done", job.get("problem")
        assert app.state.manager.hub.workers["den"] is worker, "it didn't register again"
    finally:
        task.cancel()
        await client.aclose()


async def test_a_run_the_controller_gave_up_on_is_stopped_on_the_worker(controller, tmp_path, monkeypatch):
    url, app = controller
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "60")
    agent = Agent(worker_settings(tmp_path, url), FakeDrives(present={PM}))
    task = asyncio.create_task(agent.run_forever())
    try:
        await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["rip"]["state"] == "running"), None))
        await until(lambda: agent.runs)  # makemkvcon is running on the worker
        assert list((tmp_path / "w-scratch").glob("remote-run-*"))
        # The controller gives up (as after 45 s without heartbeats); the worker is still there.
        hub = app.state.manager.hub
        hub.workers["den"].loop.call_soon_threadsafe(hub.disconnect, "den", "a test")
        job = await asyncio.to_thread(wait_http, lambda: next(
            (j for j in api(url, "/api/jobs") if j["rip"]["state"] == "failed"), None))
        assert "ripshelf: a test" in httpx.get(f"{url}/api/jobs/{job['id']}/log?kind=rip", timeout=10).text
        assert job["problem"]["message"] == "MakeMKV rip failed: a test", "the reason, not MakeMKV's last line"
        # Its next heartbeat brings it back online, and is told to stop that run.
        await until(lambda: not agent.active, timeout=20)
        assert not agent.runs and not list((tmp_path / "w-scratch").glob("remote-run-*"))
        assert hub.workers["den"].online
    finally:
        task.cancel()


async def test_refused_token_stops_the_worker_with_one_clear_message(tmp_path, monkeypatch, caplog):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401, json={"detail": "wrong worker token"})

    monkeypatch.setattr(agentmod, "REFUSED_RETRY_S", 0.05)
    client = httpx.AsyncClient(base_url="http://c", transport=httpx.MockTransport(handler))
    agent = Agent(worker_settings(tmp_path, "http://c"), FakeDrives(present=set()), client=client)
    task = asyncio.create_task(agent.run_forever())
    await until(lambda: len(calls) >= 3)
    task.cancel()
    refused = [r for r in caplog.records if "refused the worker token" in r.getMessage()]
    assert len(refused) == 1 and refused[0].levelname == "ERROR" and "new token" in refused[0].getMessage()
    assert not any("unreachable" in r.getMessage() for r in caplog.records)


def test_leftover_rip_folders_are_removed_at_start(tmp_path):
    s = worker_settings(tmp_path, "http://c")
    (s.scratch_dir / "remote-run-7").mkdir()
    (s.scratch_dir / "remote-run-7" / "t00.mkv").write_bytes(b"x")
    (s.scratch_dir / "keep").mkdir()
    Agent(s, FakeDrives(present=set())).clear_leftovers()
    assert not (s.scratch_dir / "remote-run-7").exists() and (s.scratch_dir / "keep").exists()


def test_worker_without_the_makemkv_eula_is_held_and_shown(settings):
    """A worker runs its own MakeMKV, so its licence is accepted there. Until it is, its
    drives get no jobs and the Overview says which worker it is."""
    from fastapi.testclient import TestClient

    settings.worker_token = SecretStr(TOKEN)
    h = {"Authorization": f"Bearer {TOKEN}"}
    body = {"id": "w", "name": "Attic", "drives": [{"id": "d1", "device": "/dev/sr0"}]}
    with TestClient(create_app(settings, backend=NoDrives(present=set()))) as c:
        m = c.app.state.manager
        assert c.post("/api/workers/register", json=body | {"makemkv_eula_pending": True},
                      headers=h).status_code == 200
        problem = next(p for p in m.problems() if p.code == "worker_makemkv_eula")
        assert "Attic" in problem.message and problem.command == 'RIPSHELF_ACCEPT_MAKEMKV_EULA: "yes"'
        assert m._held(m.drives["d1"])
        assert c.get("/api/workers").json()[0]["makemkv_eula_pending"] is True
        # The worker restarts with the licence accepted.
        assert c.post("/api/workers/register", json=body, headers=h).status_code == 200
        assert not any(p.code == "worker_makemkv_eula" for p in m.problems())
        assert not m._held(m.drives["d1"])
