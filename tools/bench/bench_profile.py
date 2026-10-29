# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run profiles, memory cap, CPU-load precheck and machine fingerprint (WP-304)."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]

# BENCHMARKS.md section 1: `bench` is for the holder of the heavy/GPU token; `default` is rule 10.
PROFILES: dict[str, dict[str, float]] = {
    "bench": {"threads": 8, "mem_gb": 16, "gpu_mem_gb": 12},
    "default": {"threads": 2, "mem_gb": 8, "gpu_mem_gb": 0},
}
CPU_LOAD_LIMIT_PCT = 25.0
_THREAD_VARS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OA_NUM_THREADS",
)
_APPLIED = "OA_BENCH_PROFILE"


def apply_profile(name: str) -> dict[str, float]:
    """Set thread caps and the memory cap for this process tree.

    The cap reuses the root ``conftest.py`` Job Object code: that module applies it at import,
    from ``OA_TEST_MEM_GB``. Child processes (the case workers) inherit the job.
    """
    prof = PROFILES[name]
    if os.environ.get(_APPLIED):
        return prof
    for var in _THREAD_VARS:
        os.environ[var] = str(int(prof["threads"]))
    os.environ["OA_TEST_MEM_GB"] = str(prof["mem_gb"])
    if prof["gpu_mem_gb"]:
        os.environ["OA_GPU_MEM_GB"] = str(int(prof["gpu_mem_gb"]))
    os.environ[_APPLIED] = name
    if sys.platform == "win32":
        spec = importlib.util.spec_from_file_location(
            "oa_bench_root_conftest", REPO / "conftest.py"
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # side effect: Job Object with OA_TEST_MEM_GB
    else:  # best effort off Windows: address-space limit (cgroup scopes are set by the caller)
        import resource

        lim = int(prof["mem_gb"] * (1 << 30))
        resource.setrlimit(resource.RLIMIT_AS, (lim, lim))
    return prof


def cpu_load_pct(seconds: float = 3.0) -> float | None:
    """CPU utilisation sampled over ``seconds``; None when psutil is unavailable."""
    try:
        import psutil
    except ImportError:
        return None
    return float(psutil.cpu_percent(interval=seconds))


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, timeout=30, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def gpu_info() -> dict[str, Any] | None:
    """CUDA device name and memory through CuPy; None when no device is visible."""
    try:
        import cupy

        if cupy.cuda.runtime.getDeviceCount() < 1:
            return None
        props = cupy.cuda.runtime.getDeviceProperties(0)
        name = props["name"]
        return {
            "name": name.decode() if isinstance(name, bytes) else str(name),
            "total_mb": int(props["totalGlobalMem"] // (1 << 20)),
            "cupy": cupy.__version__,
        }
    except Exception:  # no CuPy, no driver, no device: all mean "no device"
        return None


def fingerprint() -> dict[str, Any]:
    """Machine and software fingerprint recorded with every result file."""
    versions: dict[str, str] = {}
    for mod in ("numpy", "scipy", "psutil"):
        with contextlib.suppress(ImportError):
            versions[mod] = __import__(mod).__version__
    ram_gb = None
    try:
        import psutil

        ram_gb = round(psutil.virtual_memory().total / (1 << 30), 1)
    except ImportError:
        pass
    return {
        "host": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()} {platform.version()}",
        "cpu": _cpu_name(),
        "logical_cores": os.cpu_count(),
        "ram_gb": ram_gb,
        "python": platform.python_version(),
        "libs": versions,
        "gpu": gpu_info(),
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
    }
