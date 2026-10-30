# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Memory cap for every Python process started from the development environment.

The development machine froze several times when agent scripts outside pytest grew without
bound (one reached 125 GB). ``sitecustomize`` calls :func:`install` at interpreter startup, so
the cap also covers ad-hoc scripts, not only test runs.

* Windows: the process (and every child it starts) joins a Job Object whose total memory is
  limited to ``OA_MEMCAP_GB`` (default 8). Exceeding it raises MemoryError in the allocating
  process instead of exhausting the host.
* ``OA_MEMCAP_GB=0`` disables the cap. Values above 16 are clamped to 16.
* Other platforms: no-op (CI runners are disposable).

This package is a dev dependency only; it is never part of a product install.
"""

from __future__ import annotations

import os
import sys

_MAX_GB = 16.0
_DEFAULT_GB = 8.0
_installed = False


def limit_gb() -> float:
    """Return the configured cap in GiB (0 means disabled)."""
    raw = os.environ.get("OA_MEMCAP_GB", "")
    try:
        value = float(raw) if raw else _DEFAULT_GB
    except ValueError:
        value = _DEFAULT_GB
    return min(max(value, 0.0), _MAX_GB)


def install() -> bool:
    """Apply the cap once per process. Return True when a cap is active."""
    global _installed
    if _installed:
        return True
    gb = limit_gb()
    if sys.platform != "win32" or gb <= 0:
        return False
    try:
        _installed = _limit_memory_windows(gb)
    except OSError:
        _installed = False
    return _installed


def _limit_memory_windows(limit_gb: float) -> bool:
    if sys.platform != "win32":  # also lets mypy skip the Windows-only ctypes API on other OSes
        return False
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object_limit_job_memory = 0x00000200
    job_object_extended_limit_information = 9
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = job_object_limit_job_memory
    info.JobMemoryLimit = int(limit_gb * (1 << 30))
    ok = kernel32.SetInformationJobObject(
        job, job_object_extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)
    )
    if not ok:
        return False
    # The handle stays open for the process lifetime; closing it would not lift the limit.
    return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))
