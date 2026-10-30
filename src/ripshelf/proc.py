"""Killing a helper program (makemkvcon, ffmpeg) together with everything it started.

makemkvcon starts a Java helper for BD-J discs that can outlive it and keep our
pipe open, so killing just the process is not enough.

On POSIX the program starts in a new session (its own process group) and the group
is killed. Windows has no process groups to kill, so the program is put in a Job
Object, which takes in whatever it starts later, and the job is terminated.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys

WINDOWS = sys.platform == "win32"


def split_command(command: str) -> list[str]:
    """A program setting as an argument list. The path of a program (spaces and all, as
    in "C:\\Program Files (x86)\\MakeMKV\\makemkvcon64.exe"), or a command line."""
    if os.path.isfile(command):
        return [command]
    if WINDOWS:
        # posix=False keeps backslashes; it also keeps the quotes, so take them off.
        return [a[1:-1] if len(a) > 1 and a[0] == a[-1] == '"' else a
                for a in shlex.split(command, posix=False)]
    return shlex.split(command)


def join_command(args: list[str]) -> str:
    """The command line ``split_command`` turns back into ``args``."""
    return subprocess.list2cmdline(args) if WINDOWS else shlex.join(args)


def start_options() -> dict:
    """Extra ``create_subprocess_exec`` arguments for a program ``Tree`` will kill."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class Tree:
    """A started program and the processes it starts, killed together."""

    def __init__(self, pid: int):
        self.pid = pid
        self._job = _job_for(pid) if WINDOWS else None

    def kill(self) -> None:
        if not WINDOWS:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        elif self._job:
            _kernel32().TerminateJobObject(self._job, 1)
        else:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.pid)], capture_output=True)

    def close(self) -> None:
        """Kill what is left and let go of the job."""
        self.kill()
        if self._job:
            _kernel32().CloseHandle(self._job)
            self._job = None


def _kernel32():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


def _job_for(pid: int):
    """A Job Object holding ``pid`` (and later its children), or None if that failed."""
    import ctypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", ctypes.c_uint64 * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    job_object_extended_limit_information = 9
    kill_on_job_close = 0x2000
    process_set_quota, process_terminate = 0x0100, 0x0001

    k32 = _kernel32()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = kill_on_job_close
    k32.SetInformationJobObject(job, job_object_extended_limit_information, ctypes.byref(limits),
                                ctypes.sizeof(limits))
    handle = k32.OpenProcess(process_set_quota | process_terminate, False, pid)
    ok = bool(handle) and k32.AssignProcessToJobObject(job, handle)
    if handle:
        k32.CloseHandle(handle)
    if not ok:
        k32.CloseHandle(job)
        return None
    return job
