"""The worker side, ``ripshelf worker``.

Runs where the drives are. It registers its drives with the controller, then:
  * heartbeats every few seconds with each drive's status (and the disc's
    identity once per insertion, so reading a disc is never repeated while it sits there);
  * long-polls for commands and runs them, such as makemkvcon (output lines are
    streamed back; ripped files are uploaded, or left in place when the
    scratch folder is shared with the controller), structure reads, eject, cancel.

A short outage ends nothing, because requests are retried until the controller answers
again (it gives a worker 45 s before failing its runs). Each heartbeat lists the
runs the worker is working on, and the controller answers with the ones it has
given up on. The worker stops those, so their makemkvcon doesn't keep the drive
and their files don't stay in scratch. The worker registers again only when the
controller no longer knows it (it restarted). A refused token stops the worker
until it's accepted again, checked every few minutes.

On every (re)registration it fetches the MakeMKV key and the support files for
its drives (``_private_data.tar``, ``dump_*.tgz``) from the controller and
installs whatever is missing or changed into each drive's MakeMKV home, so a
worker needs only the controller's address and its token. RIPSHELF_MAKEMKV_KEY,
if set on the worker, still wins over the controller's key.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import logging
import shutil
import socket
import ssl
import tarfile
import tempfile
from pathlib import Path

import httpx

from .. import discfs
from ..config import EULA_PENDING
from ..makemkv import files as mkfiles
from ..makemkv import runner
from ..models import DriveStatus

log = logging.getLogger(__name__)

HEARTBEAT_S = 5.0
LINE_FLUSH_S = 0.5
UPLOAD_CHUNK = 4 * 1024 * 1024
RETRY_S = 2.0
REFUSED_RETRY_S = 300.0
LEFTOVER_PREFIX = "remote-run-"


class ControllerGone(Exception):
    """The controller doesn't know this worker (it restarted), so register again."""


class TokenRefused(ControllerGone):
    """The controller refused the token (revoked, wrong, or remote workers turned off)."""


class RunGone(Exception):
    """The controller has given up on this run, so stop it and send nothing more."""


class Agent:
    def __init__(self, settings, backend, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.backend = backend
        self.worker_id = settings.worker_id or socket.gethostname()
        self.name = settings.worker_name or self.worker_id
        base = settings.controller_url.rstrip("/")
        token = settings.secret("worker_token") or ""
        # For https, the system's CAs, plus RIPSHELF_CONTROLLER_CA_FILE for a private CA.
        verify = ssl.create_default_context(cafile=str(settings.controller_ca_file)) \
            if settings.controller_ca_file else True
        self.client = client or httpx.AsyncClient(
            base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(60, connect=10),
            verify=verify)
        # The worker's own RIPSHELF_MAKEMKV_KEY, else the controller's (fetched on register).
        self.makemkv_key: str | None = settings.secret("makemkv_key")
        self.api = f"/api/workers/{self.worker_id}"
        self.gate = runner.OpenGate()
        self.drives: dict[str, object] = {}  # local id -> DriveHw
        self.idents: dict[str, dict | None] = {}
        self.runs: dict[int, runner.MakeMKV] = {}
        self.active: set[int] = set()  # runs being worked on (makemkvcon, upload, structure)
        self.abandoned: set[int] = set()  # ...that the controller has given up on
        self.drive_locks: dict[str, asyncio.Lock] = {}
        self.tasks: set[asyncio.Task] = set()

    # --- lifecycle ---------------------------------------------------------------------

    async def run_forever(self) -> None:
        self.clear_leftovers()
        if self.s.makemkv_eula_pending():
            log.warning("%s. Read %s, and if you accept it set RIPSHELF_ACCEPT_MAKEMKV_EULA=yes",
                        EULA_PENDING, self.s.makemkv_eula_file)
        refused = False
        while True:
            try:
                await self.register()
                if refused:
                    log.info("the controller accepts this worker's token again")
                refused = False
                await self._connected()
            except TokenRefused as e:
                self.abandon_all(str(e))
                if not refused:
                    log.error("%s. This worker is stopped until it's accepted again (checked every %d s); "
                              "a revoked worker needs a new token from Settings → Remote workers.",
                              e, REFUSED_RETRY_S)
                refused = True
                await asyncio.sleep(REFUSED_RETRY_S)
            except ControllerGone as e:
                log.warning("%s (it restarted?), registering again", e)
                self.abandon_all("the controller restarted")
                await asyncio.sleep(1)
            except httpx.HTTPError as e:
                log.warning("controller unreachable (%s); retrying", type(e).__name__)
                await asyncio.sleep(5)

    async def _connected(self) -> None:
        """Heartbeats and commands until one of them gives up (ControllerGone, TokenRefused)."""
        tasks = [asyncio.create_task(self._heartbeats()), asyncio.create_task(self._commands())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                t.result()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def clear_leftovers(self) -> None:
        """Rip folders a previous worker process left in scratch (it stopped mid-run)."""
        for d in Path(self.s.scratch_dir).glob(f"{LEFTOVER_PREFIX}*"):
            log.info("removing a leftover rip folder %s", d)
            shutil.rmtree(d, ignore_errors=True)

    def abandon(self, run_id: int, why: str) -> None:
        if run_id not in self.active or run_id in self.abandoned:
            return
        log.warning("run %s: %s; stopping it", run_id, why)
        self.abandoned.add(run_id)
        if m := self.runs.get(run_id):
            m.cancel()

    def abandon_all(self, why: str) -> None:
        for run_id in list(self.active):
            self.abandon(run_id, why)

    async def register(self) -> None:
        hws = self.backend.discover(self.s.uhd_models)
        self.drives = {hw.id: hw for hw in hws}
        for hw in hws:
            if hw.legacy_id:
                try:
                    if runner.migrate_home(self.s.makemkv_dir, hw.legacy_id, hw.id):
                        log.info("moved the MakeMKV home of %s to %s", hw.legacy_id, hw.id)
                except OSError as e:
                    log.warning("could not move the MakeMKV home of %s to %s: %s", hw.legacy_id, hw.id, e)
        encoders = await self._encoders()
        body = {"id": self.worker_id, "name": self.name, "encoders": encoders,
                "shared_scratch": self.s.worker_shared_scratch,
                "makemkv_eula_pending": self.s.makemkv_eula_pending(),
                "drives": [{"id": hw.id, "device": hw.device, "sg_device": hw.sg_device, "vendor": hw.vendor,
                            "model": hw.model, "revision": hw.revision, "serial": hw.serial,
                            "capabilities": [c.value for c in hw.capabilities], "port": hw.port, "virtual": hw.virtual,
                            "legacy_id": hw.legacy_id} for hw in hws]}
        r = await self.client.post("/api/workers/register", json=body)
        self._check(r)
        self.idents = {}
        log.info("registered with %s: %d drives", self.s.controller_url, len(hws))
        try:
            await self.sync_makemkv()
        except (OSError, ValueError, httpx.HTTPStatusError, ControllerGone) as e:
            # Not fatal, because drives already set up keep working (heartbeats notice a revoked token).
            log.warning("could not fetch MakeMKV settings from the controller: %s", e)

    # --- MakeMKV key and support files from the controller ----------------------------

    async def sync_makemkv(self) -> list[str]:
        """Fetch the key and support files and install them into every drive's MakeMKV home
        (and <data>/.MakeMKV, HOME in the image). Returns what was installed, as "drive/name"."""
        r = await self.client.get(f"{self.api}/makemkv")
        self._check(r)
        boot = r.json()
        if self.s.secret("makemkv_key") is None:
            self.makemkv_key = boot.get("key")
        entries = [e for e in boot.get("files", []) if mkfiles.valid_name(e.get("name", ""))
                   and mkfiles.valid_sha(e.get("sha256", ""))]
        targets: list[tuple[str, Path, dict[str, str]]] = [
            (hw_id, runner.drive_home(self.s.makemkv_dir, hw_id) / ".MakeMKV", mkfiles.wanted_for(entries, hw_id))
            for hw_id in self.drives]
        targets.append(("home", Path(self.s.data_dir) / ".MakeMKV", mkfiles.wanted_for(entries, None)))
        installed: list[str] = []
        Path(self.s.scratch_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.s.scratch_dir, prefix=".makemkv-sync-") as tmp:
            fetched: dict[str, Path] = {}
            for label, conf_dir, wanted in targets:
                conf_dir.mkdir(parents=True, exist_ok=True)
                for name, sha in sorted(wanted.items()):
                    if not await asyncio.to_thread(mkfiles.needs, conf_dir, name, sha):
                        continue
                    if sha not in fetched:
                        fetched[sha] = await self._download(sha, Path(tmp) / sha)
                    await asyncio.to_thread(mkfiles.install, conf_dir, name, sha, fetched[sha])
                    installed.append(f"{label}/{name}")
                if self.makemkv_key is not None or (conf_dir / "settings.conf").exists():
                    runner.set_key(conf_dir, self.makemkv_key)
        if installed:
            log.info("installed from the controller: %s", ", ".join(installed))
        return installed

    async def _download(self, sha: str, dest: Path) -> Path:
        h = hashlib.sha256()
        size = 0
        async with self.client.stream("GET", f"{self.api}/makemkv/files/{sha}") as r:
            self._check(r)
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > mkfiles.MAX_BYTES:
                        raise ValueError("support file too big")
                    h.update(chunk)
                    f.write(chunk)
        if h.hexdigest() != sha:
            raise ValueError("support file damaged in transit (sha256 differs)")
        return dest

    async def _encoders(self) -> list[str]:
        from .. import transcode

        try:
            return [await transcode.pick_encoder(self.s.ffmpeg, self.s)]
        except Exception:
            return []

    @staticmethod
    def _check(r: httpx.Response) -> None:
        if r.status_code == 404:
            raise ControllerGone("controller does not know this worker")
        if r.status_code in (401, 403):
            raise TokenRefused(f"controller refused the worker token ({r.status_code})")
        r.raise_for_status()

    def _check_run(self, r: httpx.Response, run_id: int) -> None:
        """For a run's own requests, 404 means the controller has no such run any more. A refused
        token ends the run too; the heartbeats deal with the worker as a whole."""
        if r.status_code in (401, 403, 404):
            self.abandoned.add(run_id)
            raise RunGone(run_id)
        self._check(r)

    async def _retrying(self, send, what: str, run_id: int | None = None) -> httpx.Response:
        """``send()`` until the controller answers. Connection errors, timeouts and 5xx are
        retried, so a short outage ends nothing. Stops (RunGone) once the run is abandoned."""
        lost = False
        while True:
            if run_id is not None and run_id in self.abandoned:
                raise RunGone(run_id)
            try:
                r = await send()
                if r.status_code < 500:
                    if lost:
                        log.info("controller answering again (%s)", what)
                    return r
                why = f"HTTP {r.status_code}"
            except httpx.TransportError as e:
                why = type(e).__name__
            if not lost:
                log.warning("controller unreachable while %s (%s); retrying", what, why)
                lost = True
            await asyncio.sleep(RETRY_S)

    # --- heartbeats ----------------------------------------------------------------------

    async def drive_report(self) -> list[dict]:
        out = []
        for local_id, hw in self.drives.items():
            try:
                status = await asyncio.wait_for(asyncio.to_thread(self.backend.status, hw.device), 10)
            except (TimeoutError, OSError):
                status = DriveStatus.error
            if status != DriveStatus.disc:
                self.idents[local_id] = None
            elif self.idents.get(local_id) is None:
                i = await self.backend.disc_identity(hw.device)
                self.idents[local_id] = {"label": i.label, "uuid": i.uuid, "fs_type": i.fs_type,
                                         "fs_version": i.fs_version, "size_bytes": i.size_bytes}
            out.append({"id": local_id, "status": status.value, "ident": self.idents.get(local_id)})
        return out

    async def _heartbeats(self) -> None:
        while True:
            body = {"drives": await self.drive_report(), "runs": sorted(self.active)}
            r = await self._retrying(lambda body=body: self.client.post(f"{self.api}/heartbeat", json=body),
                                     "sending a heartbeat")
            self._check(r)
            for run_id in r.json().get("cancel", []):
                self.abandon(run_id, "the controller has given up on it")
            await asyncio.sleep(HEARTBEAT_S)

    # --- commands -------------------------------------------------------------------------

    async def _commands(self) -> None:
        while True:
            r = await self._retrying(
                lambda: self.client.post(f"{self.api}/poll", timeout=httpx.Timeout(90, connect=10)),
                "waiting for commands")
            self._check(r)
            for cmd in r.json().get("commands", []):
                self.handle(cmd)

    def handle(self, cmd: dict) -> None:
        kind = cmd.get("type")
        if kind == "cancel":
            m = self.runs.get(cmd.get("run"))
            if m:
                m.cancel()
            return
        coro = {"makemkv": self.do_makemkv, "structure": self.do_structure, "eject": self.do_eject,
                "close_tray": self.do_close_tray}.get(kind)
        if coro is None:
            log.warning("unknown command %r", kind)
            return
        t = asyncio.create_task(self._guard(coro(cmd)))
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    async def _guard(self, coro) -> None:
        try:
            await coro
        except RunGone as e:
            log.info("run %s dropped, because the controller no longer wants it", e)
        except Exception:
            log.exception("command failed")

    def _hw(self, local_id: str):
        hw = self.drives.get(local_id)
        if hw is None:
            raise KeyError(f"no drive {local_id}")
        return hw

    async def do_eject(self, cmd: dict) -> None:
        await asyncio.to_thread(self.backend.eject, self._hw(cmd["drive"]).device)
        self.idents[cmd["drive"]] = None

    async def do_close_tray(self, cmd: dict) -> None:
        await asyncio.to_thread(self.backend.close_tray, self._hw(cmd["drive"]).device)

    async def do_structure(self, cmd: dict) -> None:
        run_id, hw = cmd["run"], self._hw(cmd["drive"])
        if self.s.makemkv_eula_pending():
            await self._finish(run_id, None, error=f"{EULA_PENDING} on {self.name}")
            return
        self.active.add(run_id)
        try:
            await self._structure(run_id, hw)
        finally:
            self.active.discard(run_id)
            self.abandoned.discard(run_id)

    async def _structure(self, run_id: int, hw) -> None:
        with tempfile.TemporaryDirectory(dir=self.s.scratch_dir) as tmp:
            files = await self.backend.extract_structure(hw.device, Path(tmp) / "s")
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as t:
                for rel in files:
                    t.add(Path(tmp) / "s" / rel, arcname=rel)
                if (Path(tmp) / "s" / discfs.SIZES).is_file():  # not in `files`, but needed
                    t.add(Path(tmp) / "s" / discfs.SIZES, arcname=discfs.SIZES)
            r = await self._retrying(lambda: self.client.put(f"{self.api}/runs/{run_id}/files/structure.tar",
                                                             content=buf.getvalue()), "sending structure files", run_id)
            self._check_run(r, run_id)
        await self._finish(run_id, 0, files=files)

    def _drive_lock(self, drive_id: str) -> asyncio.Lock:
        return self.drive_locks.setdefault(drive_id, asyncio.Lock())

    async def do_makemkv(self, cmd: dict) -> None:
        run_id, hw = cmd["run"], self._hw(cmd["drive"])
        # The drive is ours, so use our device name whatever the controller sent.
        args = [f"dev:{hw.device}" if a.startswith("dev:") else a for a in cmd["args"]]
        shared = self.s.worker_shared_scratch and cmd.get("out_dir")
        local_out = None
        self.active.add(run_id)
        try:
            # One makemkvcon per drive, so a run the controller gave up on is stopped before the
            # next one (a retry) starts.
            async with self._drive_lock(hw.id):
                if run_id in self.abandoned:
                    raise RunGone(run_id)
                if cmd.get("out_dir"):
                    local_out = Path(cmd["out_dir"]) if shared \
                        else Path(self.s.scratch_dir) / f"{LEFTOVER_PREFIX}{run_id}"
                    if not shared:
                        shutil.rmtree(local_out, ignore_errors=True)
                    local_out.mkdir(parents=True, exist_ok=True)
                    args[-1] = str(local_out)
                home = runner.prepare_home(self.s.makemkv_dir, hw.id, self.makemkv_key)
                pending: list[str] = []
                flush_task = asyncio.create_task(self._flush_lines(run_id, pending))
                # Start-ups are staggered only between this worker's own drives, because a worker that
                # sees one drive has no other drive for MakeMKV to probe.
                m = runner.MakeMKV(self.s.makemkvcon, self.gate if len(self.drives) > 1 else None,
                                   label=hw.device)
                self.runs[run_id] = m
                try:
                    res = await m.run(args, home, Path(self.s.data_dir) / "worker-logs" / f"run-{run_id}.log",
                                      on_line=pending.append, timeout=cmd.get("timeout"),
                                      idle_timeout=cmd.get("idle_timeout"))
                finally:
                    self.runs.pop(run_id, None)
                    flush_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, RunGone):
                        await flush_task
            await self._send_lines(run_id, pending)
            files: list[str] = []
            if local_out and not shared and not res.cancelled:
                for f in sorted(local_out.glob("*.mkv")):
                    await self._upload(run_id, f)
                    files.append(f.name)
                    f.unlink(missing_ok=True)
            elif local_out and shared:
                files = [f.name for f in sorted(local_out.glob("*.mkv"))]
            await self._finish(run_id, res.returncode, res.timed_out, res.cancelled, res.error, files, res.stalled)
        finally:
            self.active.discard(run_id)
            self.abandoned.discard(run_id)
            if local_out and not shared:
                shutil.rmtree(local_out, ignore_errors=True)

    async def _flush_lines(self, run_id: int, pending: list[str]) -> None:
        while True:
            await asyncio.sleep(LINE_FLUSH_S)
            await self._send_lines(run_id, pending)

    async def _send_lines(self, run_id: int, pending: list[str]) -> None:
        if not pending:
            return
        batch = pending[:]
        del pending[:len(batch)]
        r = await self._retrying(lambda: self.client.post(f"{self.api}/runs/{run_id}/lines", json={"lines": batch}),
                                 "sending output", run_id)
        self._check_run(r, run_id)

    async def _upload(self, run_id: int, path: Path) -> None:
        async def body():
            with path.open("rb") as f:
                while chunk := f.read(UPLOAD_CHUNK):
                    yield chunk

        # A new body each try, because an upload cut off part-way starts again (the controller drops
        # the partial file). The write timeout notices a connection that died silently.
        r = await self._retrying(lambda: self.client.put(
            f"{self.api}/runs/{run_id}/files/{path.name}", content=body(),
            timeout=httpx.Timeout(None, connect=10, write=120)), f"uploading {path.name}", run_id)
        self._check_run(r, run_id)

    async def _finish(self, run_id: int, rc, timed_out=False, cancelled=False, error=None, files=None,
                      stalled=False) -> None:
        body = {"returncode": rc, "timed_out": timed_out, "stalled": stalled, "cancelled": cancelled,
                "error": error, "files": files or []}
        r = await self._retrying(lambda: self.client.post(f"{self.api}/runs/{run_id}/finish", json=body),
                                 "finishing a run", run_id)
        self._check_run(r, run_id)

