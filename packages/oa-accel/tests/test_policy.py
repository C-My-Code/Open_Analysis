# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Device selection, memory budget, doctor, lazy import. No GPU needed: devices are faked."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from oa_accel.doctor import capabilities, format_text
from oa_accel.errors import AccelError
from oa_accel.memory import GIB, MemoryBudget, dense_solve_bytes, sparse_solve_bytes
from oa_accel.policy import DevicePolicy, Problem, decide, parse_device, select
from oa_accel.probe import DeviceInfo, Probe
from oa_accel.report import AccelReport
from oa_accel.tolerance import compare


def fake_probe(*devs: DeviceInfo, version: str | None = "14.0.0") -> Probe:
    return Probe(
        cupy_version=version, driver_version="12.9", runtime_version="12.9", devices=tuple(devs)
    )


RTX = DeviceInfo("cuda", 0, "RTX 4080 SUPER", "8.9", 16 * GIB, 15 * GIB)
OLD = DeviceInfo("cuda", 1, "GTX 1060", "6.1", 6 * GIB, 6 * GIB)
AMD = DeviceInfo("rocm", 0, "RX 7900 XTX", "gfx1100", 24 * GIB, 23 * GIB)
BIG = Problem(kind="dense", n=5000)
SMALL = Problem(kind="dense", n=100)


def test_without_cupy_auto_is_cpu() -> None:
    d = decide(DevicePolicy(problem=BIG), Probe(error="cupy not installed"), env={})
    assert d.backend == "cpu"
    assert select(pr=Probe(error="x"), env={}).name == "cpu"


def test_auto_above_and_below_threshold() -> None:
    pr = fake_probe(RTX)
    assert decide(DevicePolicy(problem=BIG), pr, env={}).backend == "cuda"
    assert decide(DevicePolicy(problem=SMALL), pr, env={}).backend == "cpu"
    assert decide(DevicePolicy(), pr, env={}).backend == "cpu"  # no size, no GPU


def test_auto_rejects_old_gpu_and_prefers_most_free_memory() -> None:
    assert decide(DevicePolicy(problem=BIG), fake_probe(OLD), env={}).backend == "cpu"
    pr = fake_probe(
        DeviceInfo("cuda", 0, "a", "8.6", 8 * GIB, 2 * GIB),
        DeviceInfo("cuda", 1, "b", "8.9", 16 * GIB, 14 * GIB),
    )
    d = decide(DevicePolicy(problem=BIG), pr, env={})
    assert (d.backend, d.device_id) == ("cuda", 1)


def test_auto_checks_memory_estimate() -> None:
    huge = Problem(kind="dense", n=5000, estimate_bytes=20 * GIB)
    assert decide(DevicePolicy(problem=huge), fake_probe(RTX), env={}).backend == "cpu"
    ok = Problem(kind="dense", n=5000, estimate_bytes=dense_solve_bytes(5000))
    assert decide(DevicePolicy(problem=ok), fake_probe(RTX), env={}).backend == "cuda"


def test_rocm_auto_and_untested_flag() -> None:
    assert decide(DevicePolicy(problem=BIG), fake_probe(AMD), env={}).backend == "rocm"
    cap_dev = AccelReport(backend="rocm", untested_on_hardware=True)
    assert cap_dev.cache_key().startswith("rocm/")


def test_precedence_explicit_config_env() -> None:
    pr = fake_probe(RTX)
    env = {"OA_DEVICE": "cpu"}
    assert decide(DevicePolicy(problem=BIG), pr, env).backend == "cpu"  # env
    assert decide(DevicePolicy(problem=BIG, config_device="cuda"), pr, env).backend == "cuda"
    pol = DevicePolicy(device="cpu", config_device="cuda", problem=BIG)
    assert decide(pol, pr, {}).backend == "cpu"  # explicit beats config
    assert decide(DevicePolicy(device="cuda:0"), pr, env).device_id == 0  # explicit beats env


def test_explicit_device_missing_cpu_fallback_vs_error() -> None:
    pr = Probe(error="cupy not installed")
    d = decide(DevicePolicy(device="cuda"), pr, env={})
    assert d.backend == "cpu" and d.note
    be = select(DevicePolicy(device="cuda"), env={}, pr=pr)
    assert be.name == "cpu" and be.report().fallbacks
    with pytest.raises(AccelError) as ei:
        select(DevicePolicy(device="cuda", fallback="error"), env={}, pr=pr)
    assert ei.value.info.code == "ACCEL_NO_DEVICE"


def test_bad_device_string() -> None:
    with pytest.raises(AccelError) as ei:
        parse_device("tpu")
    assert ei.value.info.code == "ACCEL_BAD_DEVICE"
    assert parse_device("CUDA:2") == ("cuda", 2)


def test_memory_budget_defaults_and_env() -> None:
    b = MemoryBudget.from_env(16 * GIB, env={})
    assert b.gpu_bytes == 12 * GIB  # min(0.75 x 16, 16 - 2) = 12
    assert b.pinned_bytes == 2 * GIB
    small = MemoryBudget.from_env(4 * GIB, env={})
    assert small.gpu_bytes == 2 * GIB
    assert MemoryBudget.from_env(16 * GIB, env={"OA_GPU_MEM_GB": "3"}).gpu_bytes == 3 * GIB
    assert MemoryBudget.from_env(16 * GIB, env={"OA_GPU_PINNED_GB": "1"}).pinned_bytes == GIB
    with pytest.raises(AccelError) as ei:
        MemoryBudget.from_env(16 * GIB, env={"OA_GPU_MEM_GB": "lots"})
    assert ei.value.info.code == "ACCEL_BAD_CONFIG"


def test_memory_refusal_carries_estimate_and_budget() -> None:
    b = MemoryBudget(gpu_bytes=GIB, pinned_bytes=0)
    assert b.check(GIB // 2, "op") is None
    with pytest.raises(AccelError) as ei:
        b.require(2 * GIB, "dense_solve")
    info = ei.value.info
    assert info.code == "ACCEL_OUT_OF_MEMORY"
    assert str(2 * GIB) in (info.hint or "") and str(GIB) in (info.hint or "")


def test_estimates_scale() -> None:
    assert dense_solve_bytes(2000) > 2 * 2000 * 2000 * 8
    assert sparse_solve_bytes(1000, 5000, method="cg") < sparse_solve_bytes(1000, 5000)


def test_compare_classes() -> None:
    import numpy as np

    a = np.array([1.0, 2.0])
    assert compare(a * (1 + 1e-12), a, "dense").ok
    assert not compare(a * (1 + 1e-6), a, "dense").ok
    assert compare(a + 5e-10, a, "sparse_direct").ok
    assert not compare(a + 5e-9, a, "sparse_direct").ok
    assert compare(a + 5e-5, a, "fdtd").ok
    assert not compare(a, a[:1], "dense").ok


def test_doctor_without_gpu_still_reports() -> None:
    cap = capabilities(env={})
    json.dumps(cap)
    text = format_text(cap)
    assert "CPU fallback" in text and cap["cpu_fallback"] is True


def test_import_is_lazy_and_light() -> None:
    import oa_accel

    src = str(Path(oa_accel.__file__).parent.parent)
    code = (
        f"import sys, time; sys.path.insert(0, {src!r}); t=time.perf_counter(); import oa_accel; "
        "dt=time.perf_counter()-t; "
        "print(dt, 'cupy' in sys.modules, 'scipy' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    dt, cupy_loaded, scipy_loaded = out.stdout.split()
    assert float(dt) < 0.05
    assert cupy_loaded == "False" and scipy_loaded == "False"
