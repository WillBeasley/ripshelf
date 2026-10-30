"""Controller side of remote workers.

A worker is another ripshelf process (``ripshelf worker``), in another
container or on another machine, that owns optical drives. It registers its
drives here, then keeps a long-poll open, and the controller hands it commands
(run makemkvcon, read the disc structure, eject, cancel) and the worker
streams back output lines, results and ripped files.

On the controller a remote drive looks like a local one. It gets a
``RemoteBackend`` (status and disc identity from the worker's heartbeats) and
``RemoteMakeMKV`` runners (same interface as ``runner.MakeMKV``), so the
pipeline in ``jobs.Manager`` does not know the difference.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import discfs
from ..bus import next_item
from ..hw.drives import DiscIdentity, DriveHw
from ..makemkv.runner import RunResult
from ..models import Capability, DriveStatus

log = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_S = 45.0
COMMAND_WAIT_S = 20.0
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()&'+,-]{0,200}$")


def safe_file_name(name: str) -> bool:
    """A plain file name for an upload, with no paths and no hidden files."""
    return bool(_SAFE_NAME.match(name)) and "/" not in name and ".." not in name


@dataclass
class RemoteDrive:
    local_id: str  # the drive's id on its worker
    hw: DriveHw  # id is "<worker>:<local id>"
    status: DriveStatus = DriveStatus.error
    ident: DiscIdentity | None = None


@dataclass
class Run:
    """One remote makemkvcon (or structure read) in flight."""

    id: int
    worker: str
    log_path: Path
    out_dir: Path | None
    on_line: Callable[[str], None] | None
    done: asyncio.Future
    kind: str = "makemkv"
    log_fh: object = None
    files: list[str] = field(default_factory=list)


@dataclass
class Worker:
    id: str
    name: str
    encoders: list[str]
    shared_scratch: bool
    makemkv_eula_pending: bool = False  # MakeMKV's licence isn't accepted on the worker, so it won't rip
    drives: dict[str, RemoteDrive] = field(default_factory=dict)
    commands: asyncio.Queue = field(default_factory=asyncio.Queue)
    last_seen: float = field(default_factory=time.monotonic)
    online: bool = True
    loop: asyncio.AbstractEventLoop | None = None


class WorkerHub:
    def __init__(self, on_drives_changed: Callable[[Worker], None] | None = None,
                 local_ids: Callable[[], set[str]] | None = None):
        self.workers: dict[str, Worker] = {}
        self.local_ids = local_ids  # the controller's own drives, whose ids a worker can't take
        self.runs: dict[int, Run] = {}
        # Run ids aren't reused after a restart, so a worker can't mistake an old run for a new one.
        self._ids = itertools.count(int(time.time() * 1000))
        self.on_drives_changed = on_drives_changed

    # --- registration and heartbeats ---------------------------------------------------

    def drive_id(self, worker_id: str, local_id: str) -> str:
        """A worker's drive keeps its own id (so its history, settings and MakeMKV data are the
        same as when it was a local drive), unless another online worker already has that id.
        An offline one (revoked, or the drive moved to another machine) gives it up."""
        for wid, w in self.workers.items():
            if wid != worker_id and w.online and any(rd.hw.id == local_id for rd in w.drives.values()):
                return f"{worker_id}:{local_id}"
        if self.local_ids and local_id in self.local_ids():
            return f"{worker_id}:{local_id}"
        return local_id

    def register(self, worker_id: str, name: str, drives: list[dict], encoders: list[str],
                 shared_scratch: bool, makemkv_eula_pending: bool = False) -> Worker:
        old = self.workers.get(worker_id)
        if old:
            self._fail_runs(worker_id, "the worker restarted")
        w = Worker(id=worker_id, name=name, encoders=encoders, shared_scratch=shared_scratch,
                   makemkv_eula_pending=makemkv_eula_pending, loop=asyncio.get_running_loop())
        for d in drives:
            caps = [Capability(c) for c in d.get("capabilities", []) if c in Capability.__members__]
            hw = DriveHw(id=self.drive_id(worker_id, d["id"]), device=d["device"], sg_device=d.get("sg_device"),
                         vendor=d.get("vendor", ""), model=d.get("model", ""), revision=d.get("revision", ""),
                         serial=d.get("serial"), capabilities=caps, port=f"{worker_id}:{d.get('port', d['id'])}",
                         legacy_id=self.drive_id(worker_id, d["legacy_id"]) if d.get("legacy_id") else None,
                         virtual=bool(d.get("virtual")))
            w.drives[d["id"]] = RemoteDrive(local_id=d["id"], hw=hw)
        ids = {rd.hw.id for rd in w.drives.values()}
        for other in self.workers.values():
            if other.id != worker_id and not other.online:
                other.drives = {k: rd for k, rd in other.drives.items() if rd.hw.id not in ids}
        self.workers[worker_id] = w
        if self.on_drives_changed:
            self.on_drives_changed(w)
        return w

    def heartbeat(self, worker_id: str, drives: list[dict]) -> Worker:
        w = self.workers[worker_id]
        w.last_seen, w.online = time.monotonic(), True
        for d in drives:
            rd = w.drives.get(d.get("id"))
            if not rd:
                continue
            try:
                rd.status = DriveStatus(d.get("status", "error"))
            except ValueError:
                rd.status = DriveStatus.error
            i = d.get("ident")
            rd.ident = DiscIdentity(label=i.get("label"), uuid=i.get("uuid"), fs_type=i.get("fs_type"),
                                    fs_version=i.get("fs_version"), size_bytes=i.get("size_bytes")) if i else None
        return w

    def given_up(self, worker_id: str, run_ids: list[int]) -> list[int]:
        """Of the runs a worker says it's working on, the ones this controller no longer has:
        failed (the worker went quiet, its token was revoked), or from before a restart."""
        return [i for i in run_ids if (run := self.runs.get(i)) is None or run.worker != worker_id]

    async def next_commands(self, worker_id: str, wait: float = COMMAND_WAIT_S,
                            stop: asyncio.Event | None = None) -> list[dict]:
        """Long-poll for the commands queued for a worker (empty after ``wait`` seconds, or
        as soon as ``stop`` is set)."""
        w = self.workers[worker_id]
        first = await next_item(w.commands, wait, stop)
        if first is None:
            return []
        out = [first]
        while not w.commands.empty():
            out.append(w.commands.get_nowait())
        return out

    def check_timeouts(self) -> None:
        now = time.monotonic()
        for w in self.workers.values():
            if w.online and now - w.last_seen > HEARTBEAT_TIMEOUT_S:
                log.warning("worker %s went quiet", w.id)
                w.online = False
                for d in w.drives.values():
                    d.status = DriveStatus.error
                self._fail_runs(w.id, "the worker stopped answering")

    def disconnect(self, worker_id: str, why: str) -> None:
        """Take a worker offline now (its token was revoked). Its drives go to error and its runs fail."""
        w = self.workers.get(worker_id)
        if not w:
            return
        log.warning("worker %s disconnected: %s", worker_id, why)
        w.online = False
        for d in w.drives.values():
            d.status = DriveStatus.error
        self._fail_runs(worker_id, why)

    def _fail_runs(self, worker_id: str, why: str) -> None:
        for run in list(self.runs.values()):
            if run.worker == worker_id and not run.done.done():
                self._write(run, f"ripshelf: {why}\n")
                run.done.set_result(RunResult(None, error=why))
                self._close(run)

    # --- runs ----------------------------------------------------------------------------

    def start_run(self, worker_id: str, command: dict, log_path: Path, out_dir: Path | None,
                  on_line: Callable[[str], None] | None) -> Run:
        loop = asyncio.get_running_loop()
        run = Run(id=next(self._ids), worker=worker_id, log_path=log_path, out_dir=out_dir, on_line=on_line,
                  done=loop.create_future(), kind=command["type"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        run.log_fh = log_path.open("w", encoding="utf-8")
        self.runs[run.id] = run
        self.workers[worker_id].commands.put_nowait({**command, "run": run.id})
        return run

    def lines(self, run_id: int, lines: list[str]) -> None:
        run = self.runs.get(run_id)
        if not run or run.done.done():
            return
        for line in lines:
            text = line if line.endswith("\n") else line + "\n"
            self._write(run, text)
            if run.on_line:
                run.on_line(text)

    def finish(self, run_id: int, returncode: int | None, timed_out: bool, cancelled: bool,
               error: str | None, files: list[str], stalled: bool = False) -> None:
        run = self.runs.get(run_id)
        if not run or run.done.done():
            return
        run.files = files
        run.done.set_result(RunResult(returncode, timed_out=timed_out, stalled=stalled,
                                         cancelled=cancelled, error=error))
        self._close(run)

    def upload_target(self, run_id: int, name: str) -> tuple[Path, Run]:
        run = self.runs.get(run_id)
        if not run or run.out_dir is None or run.done.done():
            raise KeyError("no such run")
        if not safe_file_name(name):
            raise ValueError("bad file name")
        return run.out_dir / name, run

    @staticmethod
    def unpack_structure(tar_path: Path, out_dir: Path) -> None:
        """A worker's structure files arrive as one tar; unpack without letting paths escape."""
        import tarfile

        with tarfile.open(tar_path) as t:
            t.extractall(out_dir, filter="data")
        tar_path.unlink(missing_ok=True)

    def cancel_run(self, run: Run) -> None:
        w = self.workers.get(run.worker)
        if w:
            w.commands.put_nowait({"type": "cancel", "run": run.id})

    @staticmethod
    def _write(run: Run, text: str) -> None:
        if run.log_fh:
            run.log_fh.write(text)
            run.log_fh.flush()

    def _close(self, run: Run) -> None:
        if run.log_fh:
            run.log_fh.close()
            run.log_fh = None
        self.runs.pop(run.id, None)


class RemoteMakeMKV:
    """``runner.MakeMKV`` for a drive on a worker."""

    def __init__(self, hub: WorkerHub, worker_id: str, local_drive: str):
        self.hub, self.worker_id, self.local_drive = hub, worker_id, local_drive
        self._run: Run | None = None
        self._cancelled = False
        self.waiting = False

    async def run(self, args: list[str], home: Path, log_path: Path,
                  on_line: Callable[[str], None] | None = None, timeout: float | None = None,
                  idle_timeout: float | None = None) -> RunResult:
        self._cancelled = False
        w = self.hub.workers.get(self.worker_id)
        if not w or not w.online:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("ripshelf says the worker is offline\n", encoding="utf-8")
            return RunResult(None, error="its worker is offline")
        # The last argument of "mkv" (and "backup") is the output folder. On the controller
        # here; the worker writes to its own scratch (or the same path when shared) and uploads.
        # A backup is only sent to a worker that shares scratch (it is a folder tree).
        out_dir = Path(args[-1]) if "mkv" in args or "backup" in args else None
        cmd = {"type": "makemkv", "drive": self.local_drive, "args": args, "timeout": timeout,
               "idle_timeout": idle_timeout,
               "out_dir": str(out_dir) if out_dir else None}
        self._run = self.hub.start_run(self.worker_id, cmd, log_path, out_dir, on_line)
        try:
            return await self._run.done
        except asyncio.CancelledError:
            self.kill()
            raise
        finally:
            self._run = None

    def kill(self) -> None:
        if self._run:
            self.hub.cancel_run(self._run)

    def cancel(self) -> None:
        self._cancelled = True
        self.kill()

    @property
    def running(self) -> bool:
        return self._run is not None


class RemoteBackend:
    """``DriveBackend`` for one worker's drives (devices are the worker's names)."""

    def __init__(self, hub: WorkerHub, worker_id: str):
        self.hub, self.worker_id = hub, worker_id

    def _drive(self, device: str) -> RemoteDrive | None:
        w = self.hub.workers.get(self.worker_id)
        if not w:
            return None
        return next((d for d in w.drives.values() if d.hw.device == device), None)

    def discover(self, uhd_models: list[str]) -> list[DriveHw]:
        w = self.hub.workers.get(self.worker_id)
        return [d.hw for d in w.drives.values()] if w else []

    def status(self, device: str) -> DriveStatus:
        w = self.hub.workers.get(self.worker_id)
        d = self._drive(device)
        return d.status if d and w and w.online else DriveStatus.error

    def eject(self, device: str) -> None:
        # Called from a thread (like the local ioctl).
        d, w = self._drive(device), self.hub.workers.get(self.worker_id)
        if not d or not w or not w.online:
            raise OSError("worker offline")
        w.loop.call_soon_threadsafe(w.commands.put_nowait, {"type": "eject", "drive": d.local_id})

    def close_tray(self, device: str) -> None:
        d, w = self._drive(device), self.hub.workers.get(self.worker_id)
        if not d or not w or not w.online:
            raise OSError("worker offline")
        w.loop.call_soon_threadsafe(w.commands.put_nowait, {"type": "close_tray", "drive": d.local_id})

    async def disc_identity(self, device: str) -> DiscIdentity:
        d = self._drive(device)
        return d.ident if d and d.ident else DiscIdentity(None, None, None, None, None)

    async def extract_structure(self, device: str, out_dir: Path) -> list[str]:
        d = self._drive(device)
        if not d:
            return []
        out_dir.mkdir(parents=True, exist_ok=True)
        run = self.hub.start_run(self.worker_id, {"type": "structure", "drive": d.local_id},
                                 out_dir.parent / "structure-remote.log", out_dir, None)
        try:
            await asyncio.wait_for(asyncio.shield(run.done), 180)
        except TimeoutError:
            return []
        return sorted(str(p.relative_to(out_dir)) for p in out_dir.rglob("*")
                      if p.is_file() and p.name != discfs.SIZES)
