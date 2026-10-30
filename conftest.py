# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repository-wide test guards.

The development machine has hard-frozen three times when parallel test runs exhausted CPU or
memory. These guards are enforced in code, so a mistyped ``-m`` expression cannot defeat them:

* Thread caps: numerical libraries and openEMS default to 2 threads.
* Memory cap (Windows): the whole pytest process tree runs inside a Job Object limited to
  ``OA_TEST_MEM_GB`` (default 8). A runaway test gets a MemoryError instead of freezing the host.
* Slow tests are skipped unless ``OA_RUN_SLOW=1``.

This must run before numpy or scipy are imported, which is why it lives in the rootdir conftest.
"""

from __future__ import annotations

import os
import sys

import pytest

_DEFAULT_THREADS = "2"
for _var in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OA_NUM_THREADS",  # openEMS engine threads (oa_em_openems)
):
    os.environ.setdefault(_var, _DEFAULT_THREADS)


def _limit_memory_windows(limit_gb: float) -> None:
    """Put this process and every child it starts into a Job Object with a hard memory cap."""
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (n, ctypes.c_ulonglong)
            for n in (
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
        return
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = job_object_limit_job_memory
    info.JobMemoryLimit = int(limit_gb * (1 << 30))
    ok = kernel32.SetInformationJobObject(
        job,
        job_object_extended_limit_information,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if ok:
        kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())
    # The handle stays open for the process lifetime; closing it would not lift the limit.


_MEM_GB = float(os.environ.get("OA_TEST_MEM_GB", "8"))
if sys.platform == "win32" and _MEM_GB > 0:
    _limit_memory_windows(_MEM_GB)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_slow = os.environ.get("OA_RUN_SLOW") == "1"
    run_perf = os.environ.get("OA_RUN_PERF") == "1"
    skip_slow = pytest.mark.skip(
        reason="slow test: set OA_RUN_SLOW=1 to run (see docs/TASKS.md rule 10)"
    )
    skip_perf = pytest.mark.skip(
        reason="wall-clock timing test: set OA_RUN_PERF=1 on known hardware (see tools/bench)"
    )
    for item in items:
        if "slow" in item.keywords and not run_slow:
            item.add_marker(skip_slow)
        if "perf" in item.keywords and not run_perf:
            item.add_marker(skip_perf)
