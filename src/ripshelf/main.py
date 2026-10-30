"""Entry point for ``ripshelf`` (or ``python -m ripshelf.main``)."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from . import __version__, applog, proc
from .api import api, mount_ui
from .bus import EventBus
from .config import Settings, load_settings, load_worker_token
from .db import Database
from .jobs import Manager
from .system import HardwareCache
from .workers.api import admin as remote_workers_router
from .workers.api import router as workers_router

SHUTDOWN_GRACE_S = 3


def default_static_dir() -> Path | None:
    for p in (Path(__file__).parent / "static", Path(__file__).parents[2] / "web" / "build"):
        if (p / "index.html").exists():
            return p
    return None


def drive_backend(s: Settings):
    """This machine's drives, either simulated, Windows drive letters, or Linux's sysfs."""
    if s.fake_hardware:
        from .dev.fake import FakeDrives

        if s.makemkvcon == "makemkvcon":
            s.makemkvcon = proc.join_command([sys.executable, "-m", "ripshelf.dev.fake_makemkvcon"])
        return FakeDrives()
    if proc.WINDOWS:
        from .hw.windows import WindowsDrives
        from .makemkv.runner import default_command

        s.makemkvcon = default_command(s.makemkvcon)
        return WindowsDrives()
    from .hw.drives import SysfsDrives

    return SysfsDrives(blkid=s.blkid, sevenzip=s.sevenzip)


def create_app(settings: Settings | None = None, backend=None) -> FastAPI:
    s = settings or load_settings()
    load_worker_token(s, create=True)
    applog.install_buffer()
    if backend is None:
        backend = drive_backend(s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.loop = asyncio.get_running_loop()
        db = Database(s.db_path)
        bus = EventBus()
        m = Manager(s, db, backend, bus)
        app.state.settings, app.state.bus, app.state.manager = s, bus, m
        app.state.hardware = HardwareCache()
        await m.start()
        try:
            yield
        finally:
            await m.stop()
            db.close()

    app = FastAPI(title="ripshelf", version=__version__, lifespan=lifespan)
    # Set when shutdown starts, so worker long-polls and UI event streams end by themselves
    # instead of being cancelled (with a traceback each) when the grace period runs out.
    app.state.closing = asyncio.Event()
    app.include_router(api)
    app.include_router(workers_router)
    app.include_router(remote_workers_router)
    mount_ui(app, s.static_dir or default_static_dir())
    return app


def run_worker(s: Settings) -> None:
    """``ripshelf worker`` owns this host's drives for a controller elsewhere."""
    from .workers.agent import Agent

    backend = drive_backend(s)
    if not s.secret("worker_token") and s.worker_token_file:
        # The controller writes the token file on its first start, so wait for it.
        while not load_worker_token(s, create=False):
            logging.getLogger(__name__).info("waiting for the worker token in %s", s.worker_token_file)
            time.sleep(5)
    if not s.secret("worker_token"):
        raise SystemExit("set RIPSHELF_WORKER_TOKEN (the same token as on the controller), "
                         "or RIPSHELF_WORKER_TOKEN_FILE")
    asyncio.run(_worker_main(Agent(s, backend)))


async def _worker_main(agent) -> None:
    """Run the worker until SIGTERM (docker stop), then stop MakeMKV and leave quickly."""
    import signal

    task = asyncio.create_task(agent.run_forever())
    loop = asyncio.get_running_loop()
    if proc.WINDOWS:
        # No signal handlers in Windows' event loop, so Ctrl+C arrives as SIGINT in the main thread.
        signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(task.cancel))
    else:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        for m in list(agent.runs.values()):
            m.kill()
        await agent.client.aclose()


def begin_shutdown(app: FastAPI) -> None:
    loop = getattr(app.state, "loop", None)
    if loop and not loop.is_closed():
        loop.call_soon_threadsafe(app.state.closing.set)


class Server(uvicorn.Server):
    """uvicorn's server, telling the app as soon as SIGTERM or Ctrl+C arrives."""

    def handle_exit(self, sig, frame) -> None:
        super().handle_exit(sig, frame)
        begin_shutdown(self.config.app)


def uvicorn_log_config() -> dict:
    """uvicorn's own messages (start-up, errors) go through the root logger, so they
    reach the log file and the Logs page too; its per-request access log does not."""
    from uvicorn.config import LOGGING_CONFIG

    cfg = copy.deepcopy(LOGGING_CONFIG)
    cfg["loggers"]["uvicorn"] = {"handlers": [], "level": "INFO", "propagate": True}
    return cfg


def run() -> None:
    logging.basicConfig(level=logging.INFO, format=applog.FORMAT)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    s = load_settings()
    applog.install_file(s.data_dir)
    if sys.argv[1:2] == ["worker"]:
        run_worker(s)
        return
    # Open UI pages hold an SSE stream; without a limit uvicorn waits for them
    # forever on SIGTERM (docker stop then kills it after 10 s).
    Server(uvicorn.Config(create_app(s), host=s.host, port=s.port, log_level="info",
                          log_config=uvicorn_log_config(), timeout_graceful_shutdown=SHUTDOWN_GRACE_S)).run()
    # Shutdown is done (MakeMKV stopped, database closed). Don't wait at exit for
    # a status ioctl stuck in a thread on a hung drive.
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    run()
