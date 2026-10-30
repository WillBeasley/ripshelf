from __future__ import annotations

import asyncio
import os
import sys
import textwrap
from pathlib import Path

import pytest

from ripshelf.bus import EventBus
from ripshelf.config import Settings
from ripshelf.db import Database
from ripshelf.dev.fake import FakeDrives
from ripshelf.jobs import Manager

FIXTURES = Path(__file__).parent / "fixtures"

# Tests of POSIX behaviour, such as file modes, process groups, sysfs, signals.
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")


def private(path: Path) -> bool:
    """Readable by its owner only (Windows has no mode bits for that, so there it's always True)."""
    return sys.platform == "win32" or path.stat().st_mode & 0o777 == 0o600


# Read-only folders. Windows has no mode bits for that, and root ignores them.
needs_unix_permissions = pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions as a normal user")


@pytest.fixture
def fixture_text():
    def load(name: str) -> str:
        return (FIXTURES / name).read_text()

    return load


@pytest.fixture(autouse=True)
def makemkv_registry(monkeypatch):
    """MakeMKV's key in the Windows registry is never touched. On Windows, setting a key
    writes it there, and a test's fake key would replace the real one."""
    from ripshelf.makemkv import runner

    store: dict[str, str | None] = {"key": None}
    monkeypatch.setattr(runner, "registry_key", lambda: store["key"])
    monkeypatch.setattr(runner, "set_registry_key", lambda key: store.update(key=key))
    return store


@pytest.fixture(autouse=True)
def discdb(monkeypatch):
    """TheDiscDB is never asked for real. Lookups answer ``discdb.match`` (None for an unknown disc)
    and record the fingerprints they were given in ``discdb.asked``. ``discdb.real`` is the
    real lookup, for tests that mock its HTTP."""
    from types import SimpleNamespace

    from ripshelf.providers import thediscdb

    state = SimpleNamespace(match=None, asked=[], real=thediscdb.TheDiscDb.lookup)

    async def lookup(self, fps):
        state.asked.append(fps)
        return state.match.model_copy(deep=True) if state.match else None

    monkeypatch.setattr(thediscdb.TheDiscDb, "lookup", lookup)
    return state


@pytest.fixture(autouse=True)
def no_real_contributions(monkeypatch):
    """Nothing is ever sent to the real TheDiscDB, because tests post to a mocked address."""
    from ripshelf.providers import contribute

    post = contribute.Contributor._post

    async def guarded(self, path, what, **kw):
        assert "thediscdb.com" not in self.base, "a test tried to send to the real TheDiscDB"
        return await post(self, path, what, **kw)

    monkeypatch.setattr(contribute.Contributor, "_post", guarded)


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch):
    """Every disk has at least 1 TiB free. The fake discs claim titles of up to 60 GB, and the
    scratch check before a rip would fail them on a small disk, such as a CI runner's. Tests
    of low space set their own ``disk_usage``."""
    import shutil

    real = shutil.disk_usage

    def roomy(path):
        u = real(path)
        free = max(u.free, 2**40)
        return type(u)(max(u.total, u.used + free), u.used, free)

    monkeypatch.setattr(shutil, "disk_usage", roomy)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch) -> Settings:
    """Settings for a Manager on fake drives, everything under tmp_path."""
    monkeypatch.setenv("RIPSHELF_FAKE_RIP_SECONDS", "0.5")
    s = Settings(
        _env_file=None,
        data_dir=tmp_path / "data", scratch_dir=tmp_path / "scratch", library_dir=tmp_path / "library",
        fake_hardware=True, makemkvcon=f"{sys.executable} -m ripshelf.dev.fake_makemkvcon",
        poll_interval_s=0.2, scan_timeout_s=30, tmdb_api_key=None, makemkv_key=None,
        thediscdb_contribute_url="https://discdb.test", setup_complete=True,
    )
    for d in (s.data_dir, s.scratch_dir, s.library_dir, s.jobs_dir, s.makemkv_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s


async def until(pred, timeout: float = 30, what: str = "condition"):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        v = pred()
        if v:
            return v
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class Harness:
    def __init__(self, settings: Settings, present: set[str]):
        self.s = settings
        self.db = Database(settings.db_path)
        self.backend = FakeDrives(present=present)
        self.bus = EventBus()
        self.m = Manager(settings, self.db, self.backend, self.bus)

    async def __aenter__(self) -> Harness:
        await self.m.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.m.stop()
        self.db.close()

    def job(self, job_id: int | None = None):
        if job_id is not None:
            return self.db.get_job(job_id)
        jobs = self.db.list_jobs()
        return jobs[0] if jobs else None

    async def wait_job(self, pred, timeout: float = 30, what: str = "job state"):
        return await until(lambda: (j := self.job()) and pred(j) and j, timeout, what)


@pytest.fixture
def harness(settings):
    def make(*devices: str) -> Harness:
        return Harness(settings, set(devices))

    return make


@pytest.fixture
def mkv_script(tmp_path: Path):
    """A stand-in makemkvcon, with ``info`` and ``mkv`` behaviours given as Python snippets.

    Snippets see ``args`` (non-flag arguments), ``out(line)``, ``FIX`` (the fixtures dir).
    """

    def make(info: str = "replay('scan-dvd-practical-magic.log')", rip: str = "rip_ok()") -> str:
        p = tmp_path / "mkv_stub.py"
        head = textwrap.dedent(f"""\
            import os, sys, time
            from pathlib import Path
            FIX = Path({str(FIXTURES)!r})
            args = [a for a in sys.argv[1:] if not a.startswith('-')]
            def out(line):
                sys.stdout.write(line + "\\n"); sys.stdout.flush()
            def replay(name):
                for line in (FIX / name).read_text().splitlines():
                    out(line)
            def rip_ok(steps=5, name="title_t00.mkv"):
                out('PRGT:5024,0,"Saving all titles to MKV files"')
                Path(args[3], name).write_bytes(os.urandom(64 * 1024))
                for i in range(1, steps + 1):
                    out(f"PRGV:0,{{65536 * i // steps}},65536")
                out('MSG:5036,260,1,"Copy complete. 1 titles saved.","%1","1"')
            out('MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3"')
        """)
        body = (
            "if args[0] == 'info':\n" + textwrap.indent(textwrap.dedent(info).strip() or "pass", "    ")
            + "\nelse:\n" + textwrap.indent(textwrap.dedent(rip).strip() or "pass", "    ") + "\n"
        )
        p.write_text(head + body)
        return f"{sys.executable} {p}"

    return make
