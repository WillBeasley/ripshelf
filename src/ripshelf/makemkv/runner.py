"""Running makemkvcon.

Each drive gets its own MakeMKV home (``<data>/makemkv/<drive id>/.MakeMKV``),
because the per-drive private data (LibreDrive HK data, SDF files) lives in
``_private_data.tar`` there and parallel instances must not fight over it.

makemkvcon finds its home through HOME on Linux and USERPROFILE on Windows. The
registration key is the exception on Windows, where makemkvcon reads it from the registry
(per Windows user), so there is one key for all drives, shared with the MakeMKV app.

Firmware dumps (``dump_*.tgz``) live next to it. MakeMKV after 1.17.7 tries to
make one itself for a drive without LibreDrive support and can hang doing it
(the LG GGC-H20N, see docs/troubleshooting.md). A dump made once by 1.17.6 fixes that.

Timeouts and cancellation kill makemkvcon with everything it started (``proc.Tree``):
``timeout`` around a container run left makemkvcon running.
``idle_timeout`` kills it only once its output has stopped for that long, because a damaged
disc can keep a scan busy (read retries, slow AACS) well past any fixed limit.

Every makemkvcon probes *every* drive it can see when it starts, and that probe
blocks while another instance is opening its disc (the AACS / LibreDrive
handshake). Started together, three instances stalled each other for minutes on
the real drives. The ``OpenGate`` lets one instance at a time through start-up:
the next starts once the previous reports its disc open (or gives up), after
which they run in parallel without trouble.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import proc as procs

log = logging.getLogger(__name__)

PRIVATE_DATA = "_private_data.tar"


def drive_home(makemkv_dir: Path, drive_id: str) -> Path:
    # A remote drive's id is "<worker>:<drive>", and Windows doesn't allow ":" in names.
    return makemkv_dir / (drive_id.replace(":", "_") if procs.WINDOWS else drive_id)


def migrate_home(makemkv_dir: Path, old_id: str, new_id: str) -> bool:
    """Carry a drive's MakeMKV home (private data, firmware dump, settings) over to its new
    id. It's renamed if the new one doesn't exist yet, else its files the new one lacks are copied.
    True if anything moved."""
    old, new = drive_home(makemkv_dir, old_id), drive_home(makemkv_dir, new_id)
    if not old.is_dir() or old == new:
        return False
    if not new.exists():
        old.rename(new)
        return True
    moved = False
    for f in (old / ".MakeMKV").glob("*"):
        dest = new / ".MakeMKV" / f.name
        if f.is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            moved = True
    return moved


def prepare_home(makemkv_dir: Path, drive_id: str, key: str | None) -> Path:
    """Create the drive's MakeMKV home, seed it from the shared template, set the key.

    Without a key, a key already in the home is left alone, because a per-drive worker has no
    key of its own, and its home's key is written by the controller.
    """
    home = drive_home(makemkv_dir, drive_id)
    conf_dir = home / ".MakeMKV"
    conf_dir.mkdir(parents=True, exist_ok=True)
    template = makemkv_dir / "_template" / PRIVATE_DATA
    if not (conf_dir / PRIVATE_DATA).exists() and template.exists():
        shutil.copy2(template, conf_dir / PRIVATE_DATA)
        (conf_dir / IMPORTED_MARKER).write_text("seeded from the imported template\n", encoding="utf-8")
    for dump in template.parent.glob(DUMP_GLOB):
        if not (conf_dir / dump.name).exists():
            shutil.copy2(dump, conf_dir / dump.name)
    if key is not None:
        set_key(conf_dir, key)
    return home


def home_env(home: Path | str) -> dict[str, str]:
    """The environment that makes makemkvcon use ``home``."""
    env = {**os.environ, "HOME": str(home)}
    if procs.WINDOWS:
        env["USERPROFILE"] = str(home)
    return env


def default_command(command: str) -> str:
    """The default "makemkvcon" is on the PATH on Linux; on Windows it means MakeMKV's install folder."""
    if command != "makemkvcon" or not procs.WINDOWS or shutil.which(command):
        return command
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        for exe in ("makemkvcon64.exe", "makemkvcon.exe"):
            if base and (Path(base) / "MakeMKV" / exe).is_file():
                return str(Path(base) / "MakeMKV" / exe)
    return command


REGISTRY_PATH = r"Software\MakeMKV"


def registry_key() -> str | None:
    """The key MakeMKV has in the Windows registry, if any."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as k:
            return winreg.QueryValueEx(k, "app_Key")[0] or None
    except OSError:
        return None


def set_registry_key(key: str | None) -> None:
    """Set (or, with None, remove) the key in the Windows registry."""
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as k:
        if key:
            winreg.SetValueEx(k, "app_Key", 0, winreg.REG_SZ, key)
        else:
            try:
                winreg.DeleteValue(k, "app_Key")
            except FileNotFoundError:
                pass


def set_key(conf_dir: Path, key: str | None) -> None:
    """Write (or remove) the registration key in ``conf_dir/settings.conf``, keeping its other lines.

    On Windows a key is also put in the registry, where makemkvcon reads it. A cleared
    key stays there, because the MakeMKV app uses it too."""
    if procs.WINDOWS and key and registry_key() != key:
        set_registry_key(key)
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf = conf_dir / "settings.conf"
    lines = conf.read_text(encoding="utf-8").splitlines() if conf.exists() else []
    lines = [ln for ln in lines if not re.match(r"\s*app_Key\s*=", ln)]
    if key:
        lines.insert(0, f'app_Key = "{key}"')
    # Atomically, 0600, so a makemkvcon starting meanwhile never reads half a file.
    fd, tmp = tempfile.mkstemp(dir=conf_dir, prefix=".settings.conf.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, conf)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


async def check_key(makemkvcon: str, key: str, timeout: float = 30) -> tuple[bool, str]:
    """Ask makemkvcon whether a registration key is valid (``makemkvcon reg``).

    It runs in a throwaway home, needs no drive and no network, and touches no
    real settings. On Windows ``reg`` saves the key in the registry, so the key
    that was there is put back afterwards.
    """
    saved = registry_key() if procs.WINDOWS else None
    with tempfile.TemporaryDirectory() as home:
        try:
            proc = await asyncio.create_subprocess_exec(
                *procs.split_command(makemkvcon), "reg", key, env=home_env(home),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        except OSError as e:
            return False, f"makemkvcon could not be started ({e.strerror or e})"
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return False, "makemkvcon did not answer"
        finally:
            if procs.WINDOWS and registry_key() != saved:
                set_registry_key(saved)
    text = out.decode(errors="replace")
    if proc.returncode == 0 and "saved" in text.lower():
        return True, "MakeMKV accepted the key"
    return False, "MakeMKV says the key is not valid"


DUMP_GLOB = "dump_*.tgz"
DUMP_NAME = re.compile(r"dump_[A-Za-z0-9_.-]+\.tgz")


def is_dump_name(name: str) -> bool:
    return bool(DUMP_NAME.fullmatch(name))


def has_dump(makemkv_dir: Path, drive_id: str) -> bool:
    return any((drive_home(makemkv_dir, drive_id) / ".MakeMKV").glob(DUMP_GLOB))


def import_dump(makemkv_dir: Path, source: Path, name: str, drive_ids: list[str]) -> int:
    """Copy a firmware dump into each drive's home and the template (kept for drives seen later)."""
    targets = [drive_home(makemkv_dir, d) / ".MakeMKV" for d in drive_ids] + [makemkv_dir / "_template"]
    for t in targets:
        t.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, t / name)
    return len(targets)


# Written next to an imported tar. MakeMKV creates a _private_data.tar of its own
# in every home, so the tar existing says nothing about whether it was imported.
IMPORTED_MARKER = ".ripshelf-imported"


def seed_template(makemkv_dir: Path, own_home: Path) -> bool:
    """Use the private data MakeMKV keeps for this user (the MakeMKV app's, on a desktop
    install) as the template for new drive homes, if nothing was imported yet. A new
    home without it has no HK data, so UHD discs can't be opened. True if seeded."""
    src, template = own_home / ".MakeMKV" / PRIVATE_DATA, makemkv_dir / "_template"
    if (template / PRIVATE_DATA).exists() or not src.is_file():
        return False
    template.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, template / PRIVATE_DATA)
    return True


def has_private_data(makemkv_dir: Path, drive_id: str) -> bool:
    """True when a _private_data.tar was imported (or seeded from an import) for the drive."""
    conf = drive_home(makemkv_dir, drive_id) / ".MakeMKV"
    if not (conf / PRIVATE_DATA).exists():
        # A home not made yet is seeded from the template when the drive's first scan starts.
        return (makemkv_dir / "_template" / PRIVATE_DATA).exists()
    return (conf / IMPORTED_MARKER).exists()


def import_private_data(makemkv_dir: Path, source: Path, drive_ids: list[str]) -> int:
    """Copy a ``_private_data.tar`` into each drive's home (and the template if none yet)."""
    targets = [drive_home(makemkv_dir, d) / ".MakeMKV" for d in drive_ids]
    template = makemkv_dir / "_template"
    if not (template / PRIVATE_DATA).exists():
        targets.append(template)
    for t in targets:
        t.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, t / PRIVATE_DATA)
        (t / IMPORTED_MARKER).write_text("imported\n", encoding="utf-8")
    return len(targets)


# Robot-mode lines that mean makemkvcon has finished probing drives and opened its disc.
OPENED_PREFIXES = ("TCOUNT:", "CINFO:", "TINFO:", "PRGT:5024", "PRGT:5017")
OPENED_MSGS = {
    1011,  # Using LibreDrive mode
    3007,  # Using direct disc access mode
    3028,  # Title #N was added
    5010,  # Failed to open disc
    5014,  # Saving N titles into directory
}
# A drive that hasn't opened its disc by then lets the next one start anyway.
# Discs normally open in 20-30 s, but a drive can sit in its start-up probe for
# minutes while another scans, holding up every drive behind it.
OPEN_GATE_MAX_S = 45.0


class OpenGate:
    """Serialises makemkvcon start-up (see the module docstring)."""

    def __init__(self, max_hold_s: float = OPEN_GATE_MAX_S):
        self._lock = asyncio.Lock()
        self.max_hold_s = max_hold_s
        self.holder: str | None = None  # the device opening its disc now
        self.since: float | None = None  # loop time it started

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def enter(self, label: str | None = None) -> Callable[[], None]:
        """Wait for the gate; returns the release function (safe to call twice)."""
        await self._lock.acquire()
        loop = asyncio.get_running_loop()
        self.holder, self.since = label, loop.time()
        released = False

        def release() -> None:
            nonlocal released
            if not released:
                released = True
                timer.cancel()
                self.holder = self.since = None
                self._lock.release()

        timer = loop.call_later(self.max_hold_s, release)
        return release


def opened(line: str) -> bool:
    if line.startswith(OPENED_PREFIXES):
        return True
    if line.startswith("MSG:"):
        code = line[4:].split(",", 1)[0]
        return code.isdigit() and int(code) in OPENED_MSGS
    return False


# Once makemkvcon has exited, how long to wait for the rest of its output.
EXIT_DRAIN_S = 3.0


class _Stalled(Exception):
    """makemkvcon wrote nothing for ``idle_timeout`` seconds."""


@dataclass
class RunResult:
    returncode: int | None
    timed_out: bool = False
    stalled: bool = False  # timed out because the output stopped (also sets timed_out)
    cancelled: bool = False
    error: str | None = None  # makemkvcon could not be started


class MakeMKV:
    def __init__(self, command: str, gate: OpenGate | None = None, label: str | None = None):
        self.command = procs.split_command(command)
        self.gate = gate
        self.label = label  # the device, shown to drives waiting at the gate
        self._proc: asyncio.subprocess.Process | None = None
        self._tree: procs.Tree | None = None
        self._cancelled = False
        self.waiting = False  # queued at the open gate

    async def run(
        self,
        args: list[str],
        home: Path,
        log_path: Path,
        on_line: Callable[[str], None] | None = None,
        timeout: float | None = None,
        idle_timeout: float | None = None,
    ) -> RunResult:
        env = home_env(home)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._cancelled = False
        release: Callable[[], None] = lambda: None  # noqa: E731
        if self.gate:
            self.waiting = True
            try:
                release = await self.gate.enter(self.label)
            finally:
                self.waiting = False
            if self._cancelled:
                release()
                return RunResult(None, cancelled=True)
        try:
            return await self._run(args, env, log_path, on_line, timeout, idle_timeout, release)
        finally:
            release()

    async def _run(self, args, env, log_path, on_line, timeout, idle_timeout, release) -> RunResult:
        command = [*self.command, *args]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL, env=env, **procs.start_options(),
            )
        except OSError as e:
            log_path.write_text(f"could not start {self.command[0]}: {e}\n", encoding="utf-8")
            return RunResult(None, error=f"could not start {self.command[0]}: {e.strerror or e}")
        log.info("makemkvcon %s (pid %s)", " ".join(args), self._proc.pid)
        self._tree = tree = procs.Tree(self._proc.pid)

        proc = self._proc
        loop = asyncio.get_running_loop()
        last_output = loop.time()

        async def read() -> None:
            nonlocal last_output
            assert proc.stdout
            with log_path.open("w", encoding="utf-8") as fh:
                while line := await proc.stdout.readline():
                    last_output = loop.time()
                    text = line.decode("utf-8", "replace").replace("\r\n", "\n")  # Windows makemkvcon writes CRLF
                    fh.write(text)
                    fh.flush()
                    if opened(text):
                        release()
                    if on_line:
                        on_line(text)

        async def pump() -> None:
            # makemkvcon can leave its BD-J helper (java) running with our pipe open, so
            # the output may never end. Stop reading shortly after makemkvcon exits.
            reader = asyncio.create_task(read())
            try:
                # proc.wait() itself waits for the pipe to close, so watch the return code.
                while proc.returncode is None and not reader.done():
                    await asyncio.wait({reader}, timeout=0.5)
                    if idle_timeout and loop.time() - last_output > idle_timeout:
                        raise _Stalled
                await asyncio.wait_for(asyncio.shield(reader), EXIT_DRAIN_S)
                if proc.returncode is None:  # output ended first, and the exit follows
                    await proc.wait()
            except TimeoutError:
                reader.cancel()
                log.warning("makemkvcon exited but its output stayed open (a leftover helper process)")
            finally:
                if not reader.done():
                    reader.cancel()
                tree.kill()  # leftovers it started

        timed_out = stalled = False
        try:
            await asyncio.wait_for(pump(), timeout)
        except (TimeoutError, _Stalled) as e:
            timed_out, stalled = True, isinstance(e, _Stalled)
            self.kill()
            await self._proc.wait()
        except asyncio.CancelledError:
            self.kill()
            raise
        finally:
            tree.close()
        rc = self._proc.returncode
        self._proc = self._tree = None
        return RunResult(rc, timed_out=timed_out, stalled=stalled, cancelled=self._cancelled)

    def kill(self) -> None:
        p = self._proc
        if p and p.returncode is None:
            (self._tree or procs.Tree(p.pid)).kill()

    def cancel(self) -> None:
        self._cancelled = True
        self.kill()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None
