from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from ripshelf import proc


def test_split_command_keeps_a_program_path_whole(tmp_path):
    exe = tmp_path / "Program Files (x86)" / "MakeMKV" / "makemkvcon64.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    assert proc.split_command(str(exe)) == [str(exe)]


def test_split_command_round_trips_a_command_line():
    args = [sys.executable, "-m", "ripshelf.dev.fake_makemkvcon"]
    assert proc.split_command(proc.join_command(args)) == args
    assert proc.split_command("makemkvcon --cache=1") == ["makemkvcon", "--cache=1"]


def test_split_command_windows_paths(monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", True)
    exe = r"C:\Program Files (x86)\MakeMKV\makemkvcon64.exe"
    assert proc.split_command(f'"{exe}" -r') == [exe, "-r"]
    assert proc.split_command(r"C:\tools\ffmpeg.exe") == [r"C:\tools\ffmpeg.exe"]


@pytest.mark.skipif(proc.WINDOWS, reason="POSIX process groups")
async def test_tree_kills_what_the_program_started(tmp_path):
    pid_file = tmp_path / "child"
    # The program starts a child that outlives it (like makemkvcon's Java helper).
    script = f"sleep 60 & echo $! > {pid_file}; wait"
    p = await asyncio.create_subprocess_exec("sh", "-c", script, **proc.start_options())
    tree = proc.Tree(p.pid)
    for _ in range(50):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    child = int(pid_file.read_text())
    tree.close()
    await p.wait()
    for _ in range(50):
        try:
            with open(f"/proc/{child}/stat") as f:
                if f.read().split()[2] == "Z":
                    break
        except (FileNotFoundError, ProcessLookupError):  # gone (reading a vanishing one says ESRCH)
            break
        await asyncio.sleep(0.05)
    else:
        pytest.fail("the child is still running")


# --- communicate: a program that hangs is killed, not left behind ---------------------

def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def start(code: str, stdin: bool = False):
    return await asyncio.create_subprocess_exec(
        *python(code), stdin=asyncio.subprocess.PIPE if stdin else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)


async def test_communicate_returns_the_output():
    p = await start("import sys; print('out'); print('err', file=sys.stderr)")
    out, err = await proc.communicate(p, 30)
    assert (out.strip(), err.strip(), p.returncode) == (b"out", b"err", 0)


async def test_communicate_input_feeds_stdin():
    p = await start("import sys; print(sys.stdin.read().upper())", stdin=True)
    out, _ = await proc.communicate_input(p, b"moonlit orchard", 30)
    assert out.strip() == b"MOONLIT ORCHARD"


async def test_communicate_times_out_and_kills_the_program():
    p = await start("import time; print('started', flush=True); time.sleep(60)")
    # Bounded, so a program left running fails the test instead of holding it for a minute.
    assert await asyncio.wait_for(proc.communicate(p, 0.5), 10) is None
    assert p.returncode is not None, "killed and reaped, not left running"


async def test_a_cancelled_communicate_kills_the_program():
    p = await start("import time; time.sleep(60)")
    task = asyncio.create_task(proc.communicate(p, 30))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert p.returncode is not None


async def test_communicate_after_the_program_was_killed_elsewhere():
    """The program is gone by the time the timeout kills it: no error."""
    p = await start("import time; time.sleep(60)")

    real_kill = p.kill

    def kill_then_fail():
        real_kill()
        raise ProcessLookupError

    p.kill = kill_then_fail  # type: ignore[method-assign]
    assert await asyncio.wait_for(proc.communicate(p, 0.3), 10) is None
    assert p.returncode is not None


# --- Tree on POSIX, and the Windows Job Object through a stand-in kernel32 -------------

@pytest.mark.skipif(proc.WINDOWS, reason="POSIX process groups")
def test_killing_a_tree_that_has_already_gone_is_fine():
    proc.Tree(2**22 + 12345).close()  # no such process group


class FakeKernel32:
    """Records calls; ``fail`` names the calls that fail (return 0)."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls: list[tuple] = []
        self.limit_flags = None

    def _call(self, name, *args, ok=1):
        self.calls.append((name, *args))
        return 0 if name in self.fail else ok

    def CreateJobObjectW(self, *a):
        return self._call("CreateJobObjectW", ok=0x50)

    def SetInformationJobObject(self, job, cls, limits, size):
        self.limit_flags = limits._obj.BasicLimitInformation.LimitFlags
        return self._call("SetInformationJobObject", job, cls)

    def OpenProcess(self, access, inherit, pid):
        return self._call("OpenProcess", access, pid, ok=0x60)

    def AssignProcessToJobObject(self, job, handle):
        return self._call("AssignProcessToJobObject", job, handle)

    def TerminateJobObject(self, job, code):
        return self._call("TerminateJobObject", job, code)

    def CloseHandle(self, h):
        return self._call("CloseHandle", h)

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def k32(monkeypatch):
    def make(fail=()):
        fake = FakeKernel32(fail)
        monkeypatch.setattr(proc, "WINDOWS", True)
        monkeypatch.setattr(proc, "_kernel32", lambda: fake)
        return fake

    return make


def test_windows_tree_puts_the_program_in_a_job_that_kills_on_close(k32):
    fake = k32()
    tree = proc.Tree(4321)
    assert tree._job == 0x50
    assert fake.limit_flags == 0x2000, "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE"
    assert ("OpenProcess", 0x0101, 4321) in fake.calls
    assert ("AssignProcessToJobObject", 0x50, 0x60) in fake.calls
    assert ("CloseHandle", 0x60) in fake.calls, "the process handle isn't kept"
    fake.calls.clear()
    tree.close()
    assert fake.calls == [("TerminateJobObject", 0x50, 1), ("CloseHandle", 0x50)]
    assert tree._job is None


@pytest.mark.parametrize(("fail", "closed"), [
    ({"CreateJobObjectW"}, []),
    ({"OpenProcess"}, [0x50]),
    ({"AssignProcessToJobObject"}, [0x60, 0x50]),
])
def test_windows_tree_falls_back_to_taskkill_without_a_job(k32, monkeypatch, fail, closed):
    fake = k32(fail)
    ran = []
    monkeypatch.setattr(proc.subprocess, "run", lambda args, **kw: ran.append(args))
    tree = proc.Tree(4321)
    assert tree._job is None
    assert [c[1] for c in fake.calls if c[0] == "CloseHandle"] == closed, "no handle leaks"
    tree.close()
    assert ran == [["taskkill", "/F", "/T", "/PID", "4321"]]
    assert "TerminateJobObject" not in fake.names()


def test_windows_start_options(monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", True)
    monkeypatch.setattr(proc.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    assert proc.start_options() == {"creationflags": 0x200}
    monkeypatch.setattr(proc, "WINDOWS", False)
    assert proc.start_options() == {"start_new_session": True}


def test_join_command_on_windows_quotes_for_cmd(monkeypatch):
    monkeypatch.setattr(proc, "WINDOWS", True)
    args = [r"C:\Program Files\Python312\python.exe", "-m", "ripshelf.dev.fake_makemkvcon"]
    line = proc.join_command(args)
    assert line == r'"C:\Program Files\Python312\python.exe" -m ripshelf.dev.fake_makemkvcon'
    assert proc.split_command(line) == args


@pytest.mark.skipif(not proc.WINDOWS, reason="real Win32 Job Objects")
async def test_tree_kills_what_the_program_started_on_windows(tmp_path):
    """The real kernel32: a child that outlives its parent goes with the job."""
    pid_file = tmp_path / "child"
    code = ("import subprocess, sys, time; "
            "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            f"open({str(pid_file)!r}, 'w').write(str(c.pid)); time.sleep(60)")
    p = await asyncio.create_subprocess_exec(*python(code), **proc.start_options())
    tree = proc.Tree(p.pid)
    assert tree._job, "the program went into a Job Object"
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    child = int(pid_file.read_text())
    tree.close()
    await asyncio.wait_for(p.wait(), 10)
    for _ in range(100):
        out = subprocess.run(["tasklist", "/FI", f"PID eq {child}", "/NH"], capture_output=True, text=True).stdout
        if str(child) not in out:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("the child is still running")
