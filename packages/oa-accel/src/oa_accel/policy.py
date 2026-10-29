# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Device selection (ADR-0009 section 3).

Precedence: explicit argument, then config (``accel.device``), then env ``OA_DEVICE``
(``auto|cpu|cuda|cuda:N|rocm|rocm:N``), then ``auto``. ``auto`` picks a GPU only when CuPy sees a
supported device, the problem is above the measured crossover, and the memory estimate fits the
budget. The GPU lease (``OA_GPU_SLOTS``) is enforced by ``oa_jobs``, not here.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

from oa_accel.backend import Backend, CpuBackend, CupyBackend, Fallback
from oa_accel.errors import AccelError, ErrorInfo
from oa_accel.memory import MemoryBudget
from oa_accel.probe import DeviceInfo, Probe, probe

_DEVICE_RE = re.compile(r"^(auto|cpu|cuda|rocm)(?::(\d+))?$")


@dataclass(frozen=True)
class Thresholds:
    """Crossover sizes below which the CPU wins once transfer and launch overhead count.

    Initial ADR-0009 defaults; WP-504 replaces them with values measured by WP-304.
    """

    dense_n: int = 1500  # matrix order [-]
    sparse_nnz: int = 2_000_000  # stored entries [-]
    fdtd_cells: int = 200_000  # grid cells [-]


@dataclass(frozen=True)
class Problem:
    """Size of the work ``auto`` is deciding about."""

    kind: Literal["dense", "sparse", "fdtd"]
    n: int = 0  # dense order [-]
    nnz: int = 0  # sparse stored entries [-]
    cells: int = 0  # FDTD cells [-]
    estimate_bytes: int | None = None  # device bytes needed, from oa_accel.memory helpers [bytes]


@dataclass(frozen=True)
class DevicePolicy:
    device: str | None = None  # explicit argument
    config_device: str | None = None  # setup/config ``accel.device``
    fallback: Fallback = "cpu"
    mem_gb: float | None = None  # overrides OA_GPU_MEM_GB [GiB]
    problem: Problem | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)


@dataclass(frozen=True)
class Decision:
    backend: str  # "cpu" | "cuda" | "rocm"
    device_id: int | None
    reason: str
    note: str | None = None  # recorded in AccelReport.fallbacks when set
    error: ErrorInfo | None = None


def parse_device(spec: str) -> tuple[str, int | None]:
    m = _DEVICE_RE.match(spec.strip().lower())
    if m is None:
        raise AccelError(
            ErrorInfo(
                code="ACCEL_BAD_DEVICE",
                message=f"Unknown device {spec!r}.",
                path="device",
                hint="Use auto, cpu, cuda, cuda:N, rocm or rocm:N.",
            )
        )
    return m.group(1), (int(m.group(2)) if m.group(2) is not None else None)


def resolve_spec(policy: DevicePolicy, env: Mapping[str, str]) -> str:
    for cand in (policy.device, policy.config_device, env.get("OA_DEVICE")):
        if cand is not None and cand.strip() != "":
            return cand
    return "auto"


def _above_crossover(problem: Problem, th: Thresholds) -> tuple[bool, str]:
    if problem.kind == "dense":
        return problem.n >= th.dense_n, f"dense n={problem.n} vs {th.dense_n}"
    if problem.kind == "sparse":
        return problem.nnz >= th.sparse_nnz, f"sparse nnz={problem.nnz} vs {th.sparse_nnz}"
    return problem.cells >= th.fdtd_cells, f"fdtd cells={problem.cells} vs {th.fdtd_cells}"


def decide(policy: DevicePolicy, pr: Probe, env: Mapping[str, str] | None = None) -> Decision:
    """Pure decision function: no allocation, no device calls beyond the supplied probe."""
    e = os.environ if env is None else env
    kind, idx = parse_device(resolve_spec(policy, e))
    if kind == "cpu":
        return Decision("cpu", None, "device=cpu requested")

    if kind != "auto":
        pool = [d for d in pr.devices if d.kind == kind and (idx is None or d.index == idx)]
        if pool and pr.cupy_available:
            best = max(pool, key=lambda d: (d.free_bytes, -d.index))
            return Decision(kind, best.index, "device requested explicitly")
        want = kind if idx is None else f"{kind}:{idx}"
        msg = f"device {want} unavailable ({pr.error or 'no match'})"
        if policy.fallback == "cpu":
            return Decision("cpu", None, msg, note=f"select: {msg}; fell back to cpu")
        return Decision(
            "cpu",
            None,
            msg,
            error=ErrorInfo(
                code="ACCEL_NO_DEVICE",
                message=f"Requested {kind} device is not available.",
                path="device",
                hint="Install oa-accel[cuda12] and a current driver, or set OA_DEVICE=cpu.",
            ),
        )

    # auto
    cands: list[DeviceInfo] = [d for d in pr.devices if d.supported] if pr.cupy_available else []
    if not cands:
        return Decision("cpu", None, f"auto: no supported GPU ({pr.error or 'none found'})")
    if policy.problem is None:
        return Decision("cpu", None, "auto: no problem size given, staying on cpu")
    above, why = _above_crossover(policy.problem, policy.thresholds)
    if not above:
        return Decision("cpu", None, f"auto: below crossover ({why})")
    best = max(cands, key=lambda d: (d.free_bytes, -d.index))  # hybrid graphics: most free memory
    est = policy.problem.estimate_bytes
    if est is not None:
        budget = MemoryBudget.from_env(best.total_bytes, gpu_gb=policy.mem_gb, env=e)
        if budget.check(est, "auto") is not None:
            return Decision(
                "cpu", None, f"auto: estimate {est} B exceeds budget {budget.gpu_bytes} B"
            )
    return Decision(best.kind, best.index, f"auto: above crossover ({why})")


def select(
    policy: DevicePolicy | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    pr: Probe | None = None,
) -> Backend:
    """Build the backend for ``policy`` (a :class:`DevicePolicy`, a device string, or None).

    The CPU backend is returned whenever the GPU is not selected; raises :class:`AccelError` only
    for a bad device string or ``fallback="error"`` with the requested device missing.
    """
    pol = DevicePolicy(device=policy) if isinstance(policy, str) else (policy or DevicePolicy())
    e = os.environ if env is None else env
    # Probing imports CuPy; skip it when the answer is CPU regardless.
    kind, _ = parse_device(resolve_spec(pol, e))
    probe_result = pr if pr is not None else (Probe() if kind == "cpu" else probe())
    d = decide(pol, probe_result, e)
    if d.error is not None:
        raise AccelError(d.error)
    if d.backend == "cpu":
        return CpuBackend(pol.fallback, notes=[d.note] if d.note else None)
    assert d.device_id is not None
    total = probe_result.devices[d.device_id].total_bytes
    budget = MemoryBudget.from_env(total, gpu_gb=pol.mem_gb, env=e)
    return CupyBackend(d.backend, d.device_id, budget, pol.fallback)
