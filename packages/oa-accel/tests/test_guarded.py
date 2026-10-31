# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""WP-519: ``solvers.guarded.run_guarded`` and ``solvers.lu.lu_functions``.

``run_guarded`` is exercised with a stub backend (no GPU needed); the device LU is checked on the
reference GPU when ``OA_RUN_GPU=1``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from accel_helpers import random_general

from oa_accel.errors import AccelError, ErrorInfo
from oa_accel.memory import MemoryBudget
from oa_accel.solvers.guarded import run_guarded
from oa_accel.solvers.lu import lu_functions


class _Report:
    def __init__(self) -> None:
        self.fallbacks: list[str] = []


class _Backend:
    """The part of ``Backend`` that ``run_guarded`` touches."""

    name = "cuda"

    def __init__(self, fallback: str = "cpu", gpu_bytes: int = 1 << 30) -> None:
        self.fallback = fallback
        self.memory = MemoryBudget(gpu_bytes=gpu_bytes, pinned_bytes=0)
        self._report = _Report()
        self._fault: Exception | None = None

    def report(self) -> _Report:
        return self._report


def _run(be: _Backend, gpu: Any, estimate: int = 1 << 20) -> str:
    return run_guarded(be, "op", estimate, gpu, lambda reason: f"cpu:{reason}")  # type: ignore[arg-type]


def test_success_runs_on_the_device_and_records_nothing() -> None:
    be = _Backend()
    assert _run(be, lambda: "gpu") == "gpu"
    assert be.report().fallbacks == []


def test_injected_fault_falls_back_and_is_recorded() -> None:
    be = _Backend()
    be._fault = RuntimeError("boom")
    assert _run(be, lambda: "gpu") == "cpu:RuntimeError"
    assert be.report().fallbacks == ["op: RuntimeError"]


def test_over_budget_estimate_is_refused_before_the_device_runs() -> None:
    be = _Backend(gpu_bytes=1 << 20)
    called: list[int] = []

    def gpu() -> str:
        called.append(1)
        return "gpu"

    assert _run(be, gpu, estimate=1 << 30) == "cpu:ACCEL_OUT_OF_MEMORY"
    assert not called


def test_memory_error_falls_back() -> None:
    def gpu() -> str:
        raise MemoryError("out of device memory")

    assert _run(_Backend(), gpu) == "cpu:MemoryError"


def test_singular_system_is_an_error_of_the_problem_not_the_device() -> None:
    err = AccelError(ErrorInfo(code="ACCEL_SINGULAR", message="singular", path="x"))

    def gpu() -> str:
        raise err

    be = _Backend()
    with pytest.raises(AccelError) as ei:
        _run(be, gpu)
    assert ei.value.info.code == "ACCEL_SINGULAR"
    assert be.report().fallbacks == []


def test_numerical_errors_of_the_host_code_propagate() -> None:
    def gpu() -> str:
        raise np.linalg.LinAlgError("singular")

    with pytest.raises(np.linalg.LinAlgError):
        _run(_Backend(), gpu)


def test_fallback_error_reraises_the_original_failure() -> None:
    be = _Backend(fallback="error")
    be._fault = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        _run(be, lambda: "gpu")


def test_lu_functions_on_the_host_solve_a_system() -> None:
    a = random_general(30, seed=1)
    b = np.random.default_rng(2).standard_normal((30, 3))
    lu_factor, lu_solve = lu_functions(None)
    x = lu_solve(lu_factor(a.copy(), check_finite=False), b, check_finite=False)
    assert np.allclose(a @ x, b, rtol=1e-12, atol=1e-12)


@pytest.mark.gpu
def test_lu_functions_on_the_device_match_the_host() -> None:
    from oa_accel.policy import DevicePolicy, select

    be = select(DevicePolicy(device="cuda"), env={})
    a = random_general(300, seed=3)
    b = np.random.default_rng(4).standard_normal((300, 5))
    lu_factor, lu_solve = lu_functions(be)
    lu = lu_factor(be.to_device(a), check_finite=False)  # factored once ...
    x1 = be.to_host(lu_solve(lu, be.to_device(b), check_finite=False))
    x2 = be.to_host(lu_solve(lu, be.to_device(2.0 * b), check_finite=False))  # ... solved twice
    ref = np.linalg.solve(a, b)
    assert np.allclose(x1, ref, rtol=1e-10, atol=1e-12)
    assert np.allclose(x2, 2.0 * ref, rtol=1e-10, atol=1e-12)
