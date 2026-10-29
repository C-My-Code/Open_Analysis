# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Reference-GPU acceptance for WP-503. Skipped unless OA_RUN_GPU=1 and a CuPy device exists.

Sizes stay small: each test uses well under 2 GB of VRAM and finishes in seconds.
"""

from __future__ import annotations

import numpy as np
import pytest
from accel_helpers import grid_laplacian, random_spd
from oa_accel.errors import AccelError
from oa_accel.memory import GIB, MemoryBudget, dense_solve_bytes
from oa_accel.policy import DevicePolicy, Problem, select
from oa_accel.tolerance import compare, relative_residual

pytestmark = pytest.mark.gpu


def test_auto_selects_cuda_above_threshold_cpu_below() -> None:
    big = DevicePolicy(device="auto", problem=Problem(kind="dense", n=4000))
    small = DevicePolicy(device="auto", problem=Problem(kind="dense", n=200))
    assert select(big, env={}).name == "cuda"
    assert select(small, env={}).name == "cpu"


def test_memory_refusal_happens_before_allocation() -> None:
    be = select(DevicePolicy(device="cuda", fallback="error", mem_gb=0.01), env={})
    pool = be.xp.get_default_memory_pool()
    before = pool.total_bytes()
    a = random_spd(1000, seed=0)  # estimate ~16 MB > 10 MB budget
    assert dense_solve_bytes(1000) > be.memory.gpu_bytes
    with pytest.raises(AccelError) as ei:
        be.dense_solve(a, np.ones(1000), "pos")
    assert ei.value.info.code == "ACCEL_OUT_OF_MEMORY"
    assert pool.total_bytes() == before
    cpu_fb = select(DevicePolicy(device="cuda", fallback="cpu", mem_gb=0.01), env={})
    x = cpu_fb.dense_solve(a, np.ones(1000), "pos")
    assert relative_residual(a, x, np.ones(1000)) < 1e-10
    assert cpu_fb.report().fallbacks == ["dense_solve: ACCEL_OUT_OF_MEMORY"]


def test_forced_cuda_error_falls_back_or_returns_errorinfo() -> None:
    a = random_spd(100, seed=1)
    b = np.ones(100)
    boom = RuntimeError("simulated CUDA_ERROR_LAUNCH_FAILED")
    be = select(DevicePolicy(device="cuda", fallback="cpu"), env={})
    be.inject_fault(boom)
    x = be.dense_solve(a, b, "pos")
    assert relative_residual(a, x, b) < 1e-10
    assert be.report().fallbacks == ["dense_solve: ACCEL_DEVICE_ERROR"]

    strict = select(DevicePolicy(device="cuda", fallback="error"), env={})
    strict.inject_fault(boom)
    with pytest.raises(AccelError) as ei:
        strict.dense_solve(a, b, "pos")
    assert ei.value.info.code == "ACCEL_DEVICE_ERROR"
    assert strict.report().fallbacks == []  # an error run never silently falls back


def test_report_has_device_and_peak() -> None:
    be = select(DevicePolicy(device="cuda", fallback="error"), env={})
    be.dense_solve(random_spd(300, seed=2), np.ones(300), "pos")
    rep = be.report()
    assert rep.backend == "cuda" and rep.device_name
    assert rep.compute_capability is not None and rep.library_versions["cupy"]
    assert rep.peak_bytes > 0 and rep.budget_bytes > 0


def test_dense_peak_within_estimate_band() -> None:
    n = 2000  # 32 MB per matrix
    be = select(DevicePolicy(device="cuda", fallback="error"), env={})
    pool = be.xp.get_default_memory_pool()
    pool.free_all_blocks()
    a = random_spd(n, seed=3)
    be.dense_solve(a, np.ones((n, 4)), "gen")
    ratio = be.report().peak_bytes / dense_solve_bytes(n, 4)
    assert 0.8 <= ratio <= 1.25, ratio


def test_sparse_direct_cudss_vs_cpu_random_spd() -> None:
    a = grid_laplacian(60, 60, seed=5)
    b = np.random.default_rng(6).standard_normal((a.shape[0], 2))
    gpu = select(DevicePolicy(device="cuda", fallback="error"), env={})
    cpu = select("cpu")
    x = gpu.sparse_solve(a, b, "direct", assume="spd")
    ref = cpu.sparse_solve(a, b, "direct")
    assert compare(x, ref, "sparse_direct", scale=float(np.abs(ref).max())).ok
    assert relative_residual(a, x, b) <= 1e-9
    assert gpu.report().deterministic is False  # cuDSS is labelled non-deterministic


def test_portable_rocm_path_forced_on_cuda() -> None:
    """CG + Jacobi, no cuDSS: the path ROCm would use, exercised on CUDA."""
    a = grid_laplacian(50, 50, seed=7)
    b = np.ones(a.shape[0])
    gpu = select(DevicePolicy(device="cuda", fallback="error"), env={})
    x1 = gpu.sparse_solve(a, b, "cg", "jacobi", 1e-12, assume="spd")
    x2 = gpu.sparse_solve(a, b, "cg", "jacobi", 1e-12, assume="spd")
    ref = select("cpu").sparse_solve(a, b, "direct")
    assert x1.tobytes() == x2.tobytes()  # bitwise repeatable on one device
    assert compare(x1, ref, "sparse_iterative").ok


def test_budget_applied_to_pool() -> None:
    be = select(DevicePolicy(device="cuda", mem_gb=1.0), env={})
    assert be.memory == MemoryBudget(
        gpu_bytes=GIB, pinned_bytes=2 * GIB, total_bytes=be.memory.total_bytes
    )
    assert be.memory_info()["pool_limit"] == GIB
