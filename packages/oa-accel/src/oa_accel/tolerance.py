# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""GPU-vs-CPU agreement rules (ADR-0009 section 5).

Same device, same versions, same input is bitwise identical. GPU against CPU is judged by the
class tolerances below; the engineering targets in BENCHMARKS.md apply to each backend alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from numpy.typing import ArrayLike

ToleranceClass = Literal["dense", "sparse_direct", "sparse_iterative", "stencil", "fdtd"]


@dataclass(frozen=True)
class Tolerance:
    rtol: float  # relative difference, max-norm based [-]
    atol: float  # absolute difference, multiplied by ``scale`` in compare() [-]
    residual: float | None = None  # required relative residual on both backends [-]


TOLERANCES: dict[str, Tolerance] = {
    "dense": Tolerance(rtol=1e-10, atol=0.0),
    # node voltage max abs difference <= 1e-9 * V_set (pass scale=V_set); KCL residual <= 1e-9
    "sparse_direct": Tolerance(rtol=0.0, atol=1e-9, residual=1e-9),
    "sparse_iterative": Tolerance(rtol=1e-7, atol=0.0, residual=1e-10),
    "stencil": Tolerance(rtol=1e-12, atol=0.0),
    # |dS| <= 1e-4 linear (-80 dB), absolute
    "fdtd": Tolerance(rtol=0.0, atol=1e-4),
}


@dataclass(frozen=True)
class Comparison:
    ok: bool
    max_abs: float  # max |a - b| [unit of the data]
    rel: float  # max |a - b| / max |b| [-]
    limit_abs: float
    limit_rel: float


def compare(
    gpu: ArrayLike, cpu: ArrayLike, cls: ToleranceClass, *, scale: float = 1.0
) -> Comparison:
    """Compare a GPU result with the CPU oracle under a tolerance class.

    ``scale`` multiplies the absolute tolerance (for example V_set for ``sparse_direct``).
    """
    tol = TOLERANCES[cls]
    a = np.asarray(gpu)
    b = np.asarray(cpu)
    limit_abs = tol.atol * scale
    if a.shape != b.shape:
        return Comparison(False, float("inf"), float("inf"), limit_abs, tol.rtol)
    diff = float(np.max(np.abs(a - b))) if a.size else 0.0
    ref = float(np.max(np.abs(b))) if b.size else 0.0
    rel = diff / ref if ref > 0 else diff
    ok = bool(np.isfinite(diff)) and (diff <= limit_abs or (tol.rtol > 0 and rel <= tol.rtol))
    return Comparison(ok, diff, rel, limit_abs, tol.rtol)


def relative_residual(a: Any, x: ArrayLike, b: ArrayLike) -> float:
    """``||A x - b|| / ||b||`` for a host matrix (dense or scipy sparse)."""
    xa = np.asarray(x)
    ba = np.asarray(b)
    nb = float(np.linalg.norm(ba))
    nr = float(np.linalg.norm(a @ xa - ba))
    return nr / nb if nb > 0 else nr
