"""makemkvcon process handling, with logs, timeouts, kills, cancels and odd output."""

from __future__ import annotations

import asyncio
import os
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

from ripshelf.makemkv import runner

from .conftest import posix_only, private


def script(tmp_path: Path, body: str) -> str:
    p = tmp_path / "fake_mkv.py"
    p.write_text(textwrap.dedent(body))
    return f"{sys.executable} {p}"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); check its state.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


async def test_output_is_logged_and_streamed(tmp_path):
    cmd = script(tmp_path, """
        import sys
        print('MSG:1005,0,1,"v",""'); print("PRGV:1,2,3")
        sys.stdout.buffer.write(b"CINFO:2,0,\\"Caf\\xe9\\"\\n")
    """)
    lines = []
    res = await runner.MakeMKV(cmd).run(["info"], tmp_path, tmp_path / "log", on_line=lines.append)
    assert res.returncode == 0 and not res.timed_out and not res.cancelled
    assert len(lines) == 3 and any("�" in ln for ln in lines)
    assert (tmp_path / "log").read_text().count("\n") == 3


async def test_home_is_passed_as_HOME(tmp_path):
    cmd = script(tmp_path, "import os; print(os.environ['HOME'])")
    lines = []
    await runner.MakeMKV(cmd).run([], tmp_path / "home", tmp_path / "log", on_line=lines.append)
    assert lines == [f"{tmp_path / 'home'}\n"]


@posix_only
async def test_timeout_with_no_output_kills_the_whole_process_group(tmp_path):
    pidfile = tmp_path / "child.pid"
    cmd = script(tmp_path, f"""
        import subprocess, sys, time
        # makemkvcon-like, a helper child that would outlive a plain kill of the parent.
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open({str(pidfile)!r}, "w").write(str(child.pid))
        time.sleep(60)
    """)
    m = runner.MakeMKV(cmd)
    res = await m.run([], tmp_path, tmp_path / "log", timeout=1.0)
    assert res.timed_out and res.returncode is not None
    child = int(pidfile.read_text())
    for _ in range(50):
        if not alive(child):
            break
        await asyncio.sleep(0.05)
    assert not alive(child)
    assert not m.running


async def test_idle_timeout_spares_a_slow_scan_that_keeps_reporting(tmp_path):
    # A damaged disc, where read errors keep coming for longer than the idle timeout.
    cmd = script(tmp_path, """
        import time
        for i in range(8):
            print(f'MSG:2003,0,3,"Error reading {i}"', flush=True); time.sleep(0.25)
    """)
    res = await runner.MakeMKV(cmd).run([], tmp_path, tmp_path / "log", timeout=30, idle_timeout=1.0)
    assert res.returncode == 0 and not res.timed_out and not res.stalled


async def test_idle_timeout_kills_once_output_stops(tmp_path):
    cmd = script(tmp_path, """
        import time
        print("MSG:1005", flush=True); time.sleep(60)
    """)
    m = runner.MakeMKV(cmd)
    res = await m.run([], tmp_path, tmp_path / "log", timeout=30, idle_timeout=1.0)
    assert res.timed_out and res.stalled and not m.running
    assert "MSG:1005" in (tmp_path / "log").read_text()


async def test_overall_timeout_still_applies_to_a_chatty_run(tmp_path):
    cmd = script(tmp_path, """
        import time
        while True:
            print("PRGV:1,2,3", flush=True); time.sleep(0.1)
    """)
    res = await runner.MakeMKV(cmd).run([], tmp_path, tmp_path / "log", timeout=1.5, idle_timeout=1.0)
    assert res.timed_out and not res.stalled


async def test_cancel_mid_run(tmp_path):
    cmd = script(tmp_path, "import time\nprint('PRGV:0,1,2', flush=True)\ntime.sleep(60)")
    m = runner.MakeMKV(cmd)
    task = asyncio.create_task(m.run([], tmp_path, tmp_path / "log"))
    await asyncio.sleep(0.5)
    m.cancel()
    res = await asyncio.wait_for(task, 5)
    assert res.cancelled and res.returncode != 0


@posix_only
async def test_task_cancellation_kills_makemkv(tmp_path):
    cmd = script(tmp_path, "import os, time\nprint(os.getpid(), flush=True)\ntime.sleep(60)")
    m = runner.MakeMKV(cmd)
    pids = []
    task = asyncio.create_task(m.run([], tmp_path, tmp_path / "log", on_line=lambda s: pids.append(int(s))))
    while not pids:
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(50):
        if not alive(pids[0]):
            break
        await asyncio.sleep(0.05)
    assert not alive(pids[0])


@posix_only
async def test_killed_mid_run_reports_returncode(tmp_path):
    cmd = script(tmp_path, "import os, signal\nprint('PRGV:0,1,2', flush=True)\nos.kill(os.getpid(), signal.SIGKILL)")
    res = await runner.MakeMKV(cmd).run([], tmp_path, tmp_path / "log")
    assert res.returncode == -9 and not res.cancelled


async def test_missing_binary(tmp_path):
    res = await runner.MakeMKV("/nonexistent/makemkvcon").run([], tmp_path, tmp_path / "log")
    assert res.error and "could not start" in res.error
    assert "could not start" in (tmp_path / "log").read_text()


async def test_kill_and_cancel_when_not_running_are_harmless(tmp_path):
    m = runner.MakeMKV("true")
    m.kill()
    m.cancel()
    assert not m.running


# --- MakeMKV homes and private data -------------------------------------------------


@posix_only
def test_prepare_home_sets_and_replaces_key(tmp_path):
    home = runner.prepare_home(tmp_path, "drive-a", "KEY-1")
    conf = home / ".MakeMKV" / "settings.conf"
    conf.write_text(conf.read_text() + 'app_DefaultOutputFileName = "x"\n')
    runner.prepare_home(tmp_path, "drive-a", "KEY-2")
    text = conf.read_text()
    assert 'app_Key = "KEY-2"' in text and "KEY-1" not in text
    assert "app_DefaultOutputFileName" in text
    assert private(conf)
    # With no key (a per-drive worker), the key the controller wrote stays.
    runner.prepare_home(tmp_path, "drive-a", None)
    assert 'app_Key = "KEY-2"' in conf.read_text()
    runner.set_key(home / ".MakeMKV", None)
    assert "app_Key" not in conf.read_text() and "app_DefaultOutputFileName" in conf.read_text()


def make_tar(path: Path) -> Path:
    src = path.parent / "hkd_0001.bin"
    src.write_bytes(b"x")
    with tarfile.open(path, "w") as t:
        t.add(src, arcname="hkd_0001.bin")
    return path


def test_import_private_data_copies_and_seeds_template(tmp_path):
    tar = make_tar(tmp_path / "_private_data.tar")
    mk = tmp_path / "makemkv"
    n = runner.import_private_data(mk, tar, ["pioneer"])
    assert n == 2  # the drive and the template for drives seen later
    assert runner.has_private_data(mk, "pioneer")
    assert tar.exists(), "the source is copied, never moved"
    # A new drive's home is seeded from the template.
    runner.prepare_home(mk, "new-drive", None)
    assert runner.has_private_data(mk, "new-drive")
    # The template is only written once.
    assert runner.import_private_data(mk, tar, ["other"]) == 1


def test_firmware_dump_goes_to_every_home_and_is_kept(tmp_path):
    dump = make_tar(tmp_path / "dump_FW_ABC.tgz")
    mk = tmp_path / "makemkv"
    home = runner.prepare_home(mk, "lg", "key")
    assert runner.import_dump(mk, dump, dump.name, ["lg"]) == 2
    assert (home / ".MakeMKV" / dump.name).read_bytes() == dump.read_bytes()
    assert runner.has_dump(mk, "lg") and not runner.has_dump(mk, "other")
    # A drive seen later gets it from the template; preparing a home again keeps it.
    runner.prepare_home(mk, "other", None)
    assert runner.has_dump(mk, "other")
    runner.prepare_home(mk, "lg", "key2")
    assert (home / ".MakeMKV" / dump.name).exists()


def test_dump_names():
    assert runner.is_dump_name("dump_FW_0123456789ABCDEF0123.tgz")
    assert not runner.is_dump_name("_private_data.tar")
    assert not runner.is_dump_name("dump_../x.tgz")
    assert not runner.is_dump_name("MKB_v82_save.tgz")


def test_no_private_data():
    assert not runner.has_private_data(Path("/nonexistent"), "x")


# --- start-up gate ----------------------------------------------------------------


def test_opened_markers():
    assert runner.opened('MSG:3007,0,0,"Using direct disc access mode"')
    assert runner.opened('MSG:1011,0,1,"Using LibreDrive mode"')
    assert runner.opened("TCOUNT:24")
    assert runner.opened('PRGT:5024,0,"Saving all titles to MKV files"')
    assert not runner.opened('MSG:1005,0,1,"MakeMKV v2.0.0 started"')
    assert not runner.opened('DRV:0,2,999,12,"BD-RE PIONEER"')
    assert not runner.opened("MSG:x")


async def test_gate_serialises_startup_then_runs_in_parallel(tmp_path):
    # Each fake prints a start line, waits, reports its disc open, then keeps working.
    log = tmp_path / "order"
    cmd = script(tmp_path, f"""
        import sys, time
        name = sys.argv[1]
        open({str(log)!r}, "a").write(f"start {{name}} {{time.monotonic():.2f}}\\n")
        print('MSG:1005,0,1,"started"', flush=True)
        time.sleep(0.4)
        print('MSG:3007,0,0,"Using direct disc access mode"', flush=True)
        time.sleep(0.8)
        open({str(log)!r}, "a").write(f"end {{name}} {{time.monotonic():.2f}}\\n")
    """)
    gate = runner.OpenGate()
    runs = [runner.MakeMKV(cmd, gate).run([n], tmp_path, tmp_path / f"{n}.log") for n in "abc"]
    results = await asyncio.gather(*runs)
    assert all(r.returncode == 0 for r in results)
    events = [ln.split() for ln in log.read_text().splitlines()]
    starts = sorted(float(t) for kind, _, t in events if kind == "start")
    ends = sorted(float(t) for kind, _, t in events if kind == "end")
    # Starts are staggered by the open phase (~0.4 s)...
    assert all(b - a >= 0.35 for a, b in zip(starts, starts[1:], strict=False))
    # ...but the work overlaps, and the second starts before the first ends.
    assert starts[1] < ends[0]
    assert not gate.busy


async def test_gate_released_when_makemkv_never_opens(tmp_path):
    hang = script(tmp_path, "import time\nprint('MSG:1005,0,1,\"started\"', flush=True)\ntime.sleep(60)")
    quick = tmp_path / "quick.py"
    quick.write_text("print('TCOUNT:0')")
    gate = runner.OpenGate(max_hold_s=0.5)
    stuck = asyncio.create_task(runner.MakeMKV(hang, gate).run([], tmp_path, tmp_path / "h.log", timeout=5))
    await asyncio.sleep(0.1)
    res = await asyncio.wait_for(
        runner.MakeMKV(f"{sys.executable} {quick}", gate).run([], tmp_path, tmp_path / "q.log"), 3)
    assert res.returncode == 0
    stuck.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stuck


async def test_gate_released_on_failure_and_cancel_while_waiting(tmp_path):
    gate = runner.OpenGate()
    assert (await runner.MakeMKV("/nonexistent", gate).run([], tmp_path, tmp_path / "x.log")).error
    assert not gate.busy
    hold = await gate.enter()
    m = runner.MakeMKV(script(tmp_path, "print('TCOUNT:0')"), gate)
    task = asyncio.create_task(m.run([], tmp_path, tmp_path / "y.log"))
    await asyncio.sleep(0.1)
    assert m.waiting
    m.cancel()
    hold()
    res = await asyncio.wait_for(task, 2)
    assert res.cancelled and res.returncode is None
    assert not gate.busy
    hold()  # releasing twice is harmless


def test_a_tar_makemkv_wrote_itself_is_not_an_import(tmp_path):
    # MakeMKV creates _private_data.tar in every home on its first run.
    home = runner.prepare_home(tmp_path, "lg", None)
    (home / ".MakeMKV" / runner.PRIVATE_DATA).write_bytes(b"made by makemkv")
    assert not runner.has_private_data(tmp_path, "lg")
    tar = make_tar(tmp_path / "_private_data.tar")
    runner.import_private_data(tmp_path, tar, ["lg"])
    assert runner.has_private_data(tmp_path, "lg")


@posix_only
async def test_leftover_helper_holding_the_pipe_does_not_hang_the_run(tmp_path):
    # makemkvcon's BD-J helper (java) can outlive it with our stdout open.
    script = tmp_path / "mkv"
    script.write_text("#!/bin/sh\necho 'TCOUNT:1'\nsleep 60 &\necho 'MSG:5011,0,0,\"Operation successfully completed\"'\n")
    script.chmod(0o755)
    loop = asyncio.get_running_loop()
    started = loop.time()
    res = await runner.MakeMKV(str(script)).run(["info"], tmp_path, tmp_path / "log.txt", timeout=30)
    assert res.returncode == 0 and not res.timed_out
    assert loop.time() - started < runner.EXIT_DRAIN_S + 2
    assert "Operation successfully completed" in (tmp_path / "log.txt").read_text()


def test_home_env_and_default_command(monkeypatch, tmp_path):
    from ripshelf import proc

    assert runner.home_env(tmp_path)["HOME"] == str(tmp_path)
    if not proc.WINDOWS:
        assert runner.default_command("makemkvcon") == "makemkvcon"  # on the PATH outside Windows
    monkeypatch.setattr(proc, "WINDOWS", True)
    assert runner.home_env(tmp_path)["USERPROFILE"] == str(tmp_path)  # Windows makemkvcon ignores HOME
    exe = tmp_path / "MakeMKV" / "makemkvcon64.exe"
    exe.parent.mkdir()
    exe.touch()
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setattr(runner.shutil, "which", lambda c: None)
    assert runner.default_command("makemkvcon") == str(exe)
    assert runner.default_command("/opt/makemkvcon") == "/opt/makemkvcon"


def test_seed_template_from_makemkvs_own_home(tmp_path):
    own, mk = tmp_path / "user", tmp_path / "makemkv"
    assert not runner.seed_template(mk, own)  # MakeMKV never ran for this user
    (own / ".MakeMKV").mkdir(parents=True)
    (own / ".MakeMKV" / runner.PRIVATE_DATA).write_bytes(b"hk")
    assert runner.seed_template(mk, own)
    home = runner.prepare_home(mk, "drive-1", None)
    assert (home / ".MakeMKV" / runner.PRIVATE_DATA).read_bytes() == b"hk"
    assert runner.has_private_data(mk, "drive-1")
    (own / ".MakeMKV" / runner.PRIVATE_DATA).write_bytes(b"newer")
    assert not runner.seed_template(mk, own)  # an existing template (maybe imported) is kept


def test_windows_key_goes_to_the_registry_stand_in(tmp_path, monkeypatch, makemkv_registry):
    from ripshelf import proc

    monkeypatch.setattr(proc, "WINDOWS", True)
    runner.set_key(tmp_path / ".MakeMKV", "T-test")
    assert makemkv_registry["key"] == "T-test"  # the stand-in, never the real registry


def test_a_drive_not_scanned_yet_has_the_templates_private_data(tmp_path):
    mk = tmp_path / "makemkv"
    assert not runner.has_private_data(mk, "new-drive")
    (mk / "_template").mkdir(parents=True)
    (mk / "_template" / runner.PRIVATE_DATA).write_bytes(b"hk")
    assert runner.has_private_data(mk, "new-drive")  # its home gets it at the first scan
