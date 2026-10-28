# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Process-tree control: Windows job objects, POSIX process groups, RSS sampling.

Windows: every external process is assigned to a job object created with
``KILL_ON_JOB_CLOSE`` (the tree dies with us) and, when a memory budget is given, a
``JOB_MEMORY`` commit limit set well above the budget. The limit is only a backstop against a
runaway allocation between two watchdog samples; the budget itself is enforced by the RSS
watchdog so that the breach is reported as ``budget:max_memory`` rather than as a crash.

POSIX: the child leads its own session/process group; ``terminate`` sends SIGTERM to the group
and ``kill`` SIGKILL. ``RLIMIT_AS`` is deliberately *not* used: address space is not resident
memory and breaks mmap-heavy solvers.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from typing import Any

import psutil  # type: ignore[import-untyped]

__all__ = ["ProcessGuard", "kill_tree", "popen_kwargs", "tree_rss_bytes"]

IS_WINDOWS = sys.platform == "win32"

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]


def popen_kwargs() -> dict[str, Any]:
    """Extra ``subprocess.Popen`` arguments that make the child killable as a tree."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Kill ``pid`` and all its descendants (best effort, never raises)."""
    try:
        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
    except psutil.Error:
        return
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(procs, timeout=5)


def tree_rss_bytes(pid: int) -> int:
    """Resident memory of ``pid`` plus its descendants, in bytes (0 when it is gone)."""
    total = 0
    try:
        root = psutil.Process(pid)
        for p in [root, *root.children(recursive=True)]:
            with contextlib.suppress(psutil.Error):
                total += p.memory_info().rss
    except psutil.Error:
        return 0
    return total


class ProcessGuard:
    """Owns the kill switch (and on Windows the job object) of one external process tree."""

    def __init__(self, memory_limit_bytes: int | None = None) -> None:
        self.pid: int | None = None
        self._job: Any = None
        self._limit = memory_limit_bytes
        self.job_object_active = False
        self._create_job()

    def _create_job(self) -> None:
        # Positive platform guard (as elsewhere in this module): mypy skips the block on
        # non-Windows, whereas code after an early `return` would still be checked there.
        if sys.platform == "win32":
            job = _k32.CreateJobObjectW(None, None)
            if not job:
                return
            info = _ExtendedLimit()
            flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if self._limit:
                flags |= _JOB_OBJECT_LIMIT_JOB_MEMORY
                info.JobMemoryLimit = int(self._limit)
            info.BasicLimitInformation.LimitFlags = flags
            ok = _k32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
            )
            if not ok:
                _k32.CloseHandle(job)
                return
            self._job = job

    def assign(self, pid: int) -> None:
        """Put the freshly started process ``pid`` under this guard."""
        self.pid = pid
        if sys.platform == "win32" and self._job:
            h = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
            if h:
                try:
                    self.job_object_active = bool(_k32.AssignProcessToJobObject(self._job, h))
                finally:
                    _k32.CloseHandle(h)

    def terminate(self) -> None:
        """Polite stop: SIGTERM to the process group on POSIX. Windows has no equivalent for
        windowless children, so it kills the tree straight away."""
        if self.pid is None:
            return
        if sys.platform == "win32":
            self.kill()
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(self.pid), signal.SIGTERM)

    def kill(self) -> None:
        """Kill the whole tree now."""
        if sys.platform == "win32":
            if self._job and self.job_object_active:
                _k32.TerminateJobObject(self._job, 1)
        elif self.pid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(self.pid), signal.SIGKILL)
        if self.pid is not None:
            kill_tree(self.pid)

    def close(self) -> None:
        if sys.platform == "win32" and self._job:
            _k32.CloseHandle(self._job)
            self._job = None
