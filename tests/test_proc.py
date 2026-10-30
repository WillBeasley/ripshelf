from __future__ import annotations

import asyncio
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
