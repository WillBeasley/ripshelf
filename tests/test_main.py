"""Start-up: the drive backend, the app's fake-hardware extras, ``ripshelf worker`` and ``run``."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ripshelf import main, proc
from ripshelf.dev.fake import FakeDrives
from ripshelf.hw import drives, windows
from ripshelf.identify import tmdb
from ripshelf.makemkv import runner

from .conftest import posix_only


def test_fake_hardware_runs_the_fake_makemkvcon(settings):
    settings.makemkvcon = "makemkvcon"
    assert isinstance(main.drive_backend(settings), FakeDrives)
    assert proc.split_command(settings.makemkvcon) == [sys.executable, "-m", "ripshelf.dev.fake_makemkvcon"]
    # A makemkvcon of your own is kept, such as a stand-in in a test.
    settings.makemkvcon = "/opt/stub-makemkvcon"
    main.drive_backend(settings)
    assert settings.makemkvcon == "/opt/stub-makemkvcon"


def test_linux_uses_sysfs_with_the_configured_tools(settings):
    settings.fake_hardware = False
    settings.blkid, settings.sevenzip = "/sbin/blkid", "/usr/bin/7zz"
    backend = main.drive_backend(settings)
    assert isinstance(backend, drives.SysfsDrives)
    assert (backend.blkid, backend.sevenzip) == ("/sbin/blkid", "/usr/bin/7zz")


def test_windows_uses_drive_letters_and_finds_makemkv(settings, monkeypatch):
    class Letters:
        pass

    settings.fake_hardware = False
    settings.makemkvcon = "makemkvcon"
    monkeypatch.setattr(proc, "WINDOWS", True)
    monkeypatch.setattr(windows, "WindowsDrives", Letters)
    monkeypatch.setattr(runner, "default_command",
                        lambda c: r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe" if c == "makemkvcon" else c)
    assert isinstance(main.drive_backend(settings), Letters)
    assert settings.makemkvcon == r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe"


def test_default_static_dir_prefers_the_packaged_ui(tmp_path, monkeypatch):
    pkg = tmp_path / "src" / "ripshelf"
    monkeypatch.setattr(main, "__file__", str(pkg / "main.py"))
    assert main.default_static_dir() is None
    build = tmp_path / "web" / "build"
    build.mkdir(parents=True)
    (build / "index.html").write_text("<html>")
    assert main.default_static_dir() == build, "a source checkout's built UI"
    (pkg / "static").mkdir(parents=True)
    (pkg / "static" / "index.html").write_text("<html>")
    assert main.default_static_dir() == pkg / "static", "the UI the image ships"


def test_fake_tmdb_and_roomy_disks_with_the_fake_hardware(settings, monkeypatch):
    monkeypatch.setenv("RIPSHELF_FAKE_TMDB", "1")
    monkeypatch.setenv("RIPSHELF_FAKE_ROOMY_DISK", "1")
    monkeypatch.setattr(tmdb, "API", tmdb.API)  # put back afterwards
    monkeypatch.setattr(shutil, "disk_usage", shutil.disk_usage)
    settings.port = 8399
    with TestClient(main.create_app(settings, backend=FakeDrives(present=set()))) as c:
        assert tmdb.API == "http://127.0.0.1:8399/fake-tmdb/3", "TMDB calls go to this server"
        assert c.get("/fake-tmdb/3/configuration").status_code == 200
        assert c.get("/fake-escl/eSCL/ScannerCapabilities").status_code == 200
    assert shutil.disk_usage(settings.data_dir).free >= 2**40


def test_without_the_fake_hardware_there_are_no_fakes(settings, monkeypatch):
    monkeypatch.setenv("RIPSHELF_FAKE_TMDB", "1")
    monkeypatch.setattr(tmdb, "API", tmdb.API)
    settings.fake_hardware = False
    with TestClient(main.create_app(settings, backend=FakeDrives(present=set()))) as c:
        assert c.get("/fake-tmdb/3/configuration").status_code == 404
        assert c.get("/fake-escl/eSCL/ScannerCapabilities").status_code == 404
    assert tmdb.API == "https://api.themoviedb.org/3"


# --- ripshelf worker ------------------------------------------------------------------

@pytest.fixture
def worker(settings, monkeypatch):
    """A worker's settings, with the agent and its event loop left out."""
    started = []
    monkeypatch.setattr(main.asyncio, "run", lambda coro: (started.append(coro), coro.close()))
    monkeypatch.setattr("ripshelf.workers.agent.Agent", lambda s, backend: ("agent", s, backend))

    async def worker_main(agent):
        pass

    monkeypatch.setattr(main, "_worker_main", worker_main)
    settings.worker_token = None
    settings.worker_token_file = None
    return settings, started


def test_worker_without_a_token_says_how_to_set_one(worker):
    s, started = worker
    with pytest.raises(SystemExit, match="RIPSHELF_WORKER_TOKEN"):
        main.run_worker(s)
    assert started == []


def test_worker_with_a_token_starts(worker):
    s, started = worker
    s.worker_token = SecretStr("t0ken")
    main.run_worker(s)
    assert len(started) == 1


def test_worker_waits_for_the_controller_to_write_the_token_file(worker, monkeypatch, tmp_path, caplog):
    s, started = worker
    s.worker_token_file = tmp_path / "shared" / "worker-token"
    naps = []

    def sleep(seconds):
        naps.append(seconds)
        if len(naps) == 2:  # the controller starts meanwhile
            s.worker_token_file.parent.mkdir()
            s.worker_token_file.write_text("from-the-controller\n")

    monkeypatch.setattr(main.time, "sleep", sleep)
    caplog.set_level(logging.INFO, "ripshelf.main")
    main.run_worker(s)
    assert naps == [5, 5] and len(started) == 1
    assert s.secret("worker_token") == "from-the-controller"
    assert not s.worker_token_file.with_suffix(".tmp").exists(), "a worker never makes the file itself"
    assert "waiting for the worker token" in caplog.text


class Agent:
    def __init__(self, run_forever):
        self.run_forever = run_forever
        self.killed: list[str] = []
        self.closed = False
        agent = self

        class Run:
            def __init__(self, name):
                self.name = name

            def kill(self):
                agent.killed.append(self.name)

        class Client:
            async def aclose(self):
                agent.closed = True

        self.runs = {1: Run("rip 1"), 2: Run("rip 2")}
        self.client = Client()


@posix_only
async def test_worker_stops_its_rips_on_sigterm():
    async def until_stopped():
        os.kill(os.getpid(), signal.SIGTERM)  # docker stop
        await asyncio.sleep(30)

    agent = Agent(until_stopped)
    await asyncio.wait_for(main._worker_main(agent), 10)
    assert agent.killed == ["rip 1", "rip 2"] and agent.closed


async def test_worker_cleans_up_when_the_agent_fails(monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", True)  # Ctrl+C by signal.signal, not the event loop
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.setdefault(sig, fn))

    async def fails():
        raise RuntimeError("controller unreachable")

    agent = Agent(fails)
    with pytest.raises(RuntimeError, match="unreachable"):
        await main._worker_main(agent)
    assert agent.killed == ["rip 1", "rip 2"] and agent.closed
    assert set(handlers) == {signal.SIGINT}


async def test_ctrl_c_on_windows_stops_the_worker(monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", True)
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda sig, fn: handlers.setdefault(sig, fn))

    async def until_stopped():
        handlers[signal.SIGINT](signal.SIGINT, None)  # as Windows delivers Ctrl+C
        await asyncio.sleep(30)

    agent = Agent(until_stopped)
    await asyncio.wait_for(main._worker_main(agent), 10)
    assert agent.killed == ["rip 1", "rip 2"] and agent.closed


# --- ripshelf (the controller) --------------------------------------------------------

@pytest.fixture
def run(settings, monkeypatch):
    """``main.run`` up to the server, which is recorded instead of started."""
    monkeypatch.setattr(main, "load_settings", lambda: settings)
    monkeypatch.setattr(main.applog, "install_file", lambda d: None)
    monkeypatch.setattr(main.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr(main.logging, "shutdown", lambda *a: None)
    # uvicorn's Config sets up logging as it is made, which a test must not do.
    monkeypatch.setattr(main.uvicorn, "Config", lambda app, **kw: SimpleNamespace(app=app, **kw))
    calls = {}

    class Server:
        def __init__(self, config):
            calls["config"] = config

        def run(self):
            calls["ran"] = True

    def exit_(code):
        calls["exit"] = code

    monkeypatch.setattr(main, "Server", Server)
    monkeypatch.setattr(main.os, "_exit", exit_)
    monkeypatch.setattr(main, "run_worker", lambda s: calls.setdefault("worker", s))
    monkeypatch.setattr(main, "create_app", lambda s: ("app", s))
    return calls


def test_run_serves_the_app_and_exits_without_waiting(run, settings, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ripshelf"])
    settings.host, settings.port = "127.0.0.1", 8390
    main.run()
    config = run["config"]
    assert (config.app, config.host, config.port) == (("app", settings), "127.0.0.1", 8390)
    assert config.timeout_graceful_shutdown == main.SHUTDOWN_GRACE_S, "open UI pages don't hold up a stop"
    assert config.log_config["loggers"]["uvicorn"]["propagate"]
    assert run["ran"] and run["exit"] == 0 and "worker" not in run


def test_run_worker_subcommand(run, settings, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ripshelf", "worker"])
    main.run()
    assert run["worker"] is settings and "config" not in run and "exit" not in run


def test_uvicorn_logs_through_the_root_logger_but_not_each_request():
    cfg = main.uvicorn_log_config()
    assert cfg["loggers"]["uvicorn"] == {"handlers": [], "level": "INFO", "propagate": True}
    assert cfg["loggers"]["uvicorn.access"]["handlers"] == ["access"]
    assert not cfg["loggers"]["uvicorn.access"].get("propagate")
    from uvicorn.config import LOGGING_CONFIG

    assert LOGGING_CONFIG["loggers"]["uvicorn"] != cfg["loggers"]["uvicorn"], "uvicorn's own copy is untouched"


async def test_shutdown_tells_the_app_at_once(settings):
    app = main.create_app(settings, backend=FakeDrives(present=set()))
    main.begin_shutdown(app)  # before start-up: nothing to tell yet
    assert not app.state.closing.is_set()
    app.state.loop = asyncio.get_running_loop()
    server = main.Server(main.uvicorn.Config(app, log_config=None))
    server.handle_exit(signal.SIGTERM, None)
    await asyncio.wait_for(app.state.closing.wait(), 5)
    assert server.should_exit
