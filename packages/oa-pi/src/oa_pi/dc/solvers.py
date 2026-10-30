# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="import-untyped,no-any-return,arg-type,return-value"
# mypy: disable-error-code="assignment,attr-defined"
"""Sparse linear solvers for the DC network.

``solve_spd``: GPU (cuDSS, or CG + Jacobi through ``oa_accel``) when the device policy selects it
(``OA_DEVICE``; ``auto`` above the measured sparse crossover) with the CPU path as the fallback;
else SuperLU (symmetric mode) by default; PARDISO (``pypardiso``) or CHOLMOD
(``scikit-sparse``) when installed; AMG-preconditioned CG (``pyamg``) above ``AMG_THRESHOLD``
unknowns. ``solve_general``: SuperLU for the saddle-point system of remote sense.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import Any

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp
import scipy.sparse.linalg as spla

__all__ = ["AMG_THRESHOLD", "available_solvers", "kcl_residual", "solve_general", "solve_spd"]

AMG_THRESHOLD = 1_000_000
F64 = npt.NDArray[np.float64]


def available_solvers() -> tuple[str, ...]:
    """Names of the solvers usable in this environment."""
    out = ["superlu"]
    if importlib.util.find_spec("pypardiso") is not None:
        out.append("pardiso")
    if importlib.util.find_spec("sksparse") is not None:
        out.append("cholmod")
    if importlib.util.find_spec("pyamg") is not None:
        out.append("amg")
    return tuple(out)


def kcl_residual(a: sp.spmatrix | sp.sparray, x: F64, b: F64) -> float:
    """Relative residual ``|A x - b| / |b|`` (the KCL imbalance of the nodal system)."""
    nb = float(np.linalg.norm(b))
    r = float(np.linalg.norm(a @ x - b))
    return r / nb if nb > 0 else r


def _superlu(a: sp.csc_matrix, b: F64, symmetric: bool) -> F64:
    if symmetric:
        lu = spla.splu(
            a, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0, options={"SymmetricMode": True}
        )
    else:
        lu = spla.splu(a)
    return np.asarray(lu.solve(b), dtype=np.float64)


def _amg(a: sp.csr_matrix, b: F64, tol: float) -> F64:
    pyamg: Any = importlib.import_module("pyamg")
    ml = pyamg.smoothed_aggregation_solver(a, max_coarse=500)
    x = ml.solve(b, tol=tol, accel="cg", maxiter=500)
    return np.asarray(x, dtype=np.float64)


def _solve_gpu(a: sp.csr_matrix, b: F64, tol: float) -> tuple[F64, str] | None:
    """The oa_accel path; None when the CPU is selected or the GPU path cannot serve it."""
    from oa_accel import DevicePolicy, Problem, select
    from oa_accel.errors import AccelError
    from oa_accel.memory import sparse_solve_bytes
    from oa_accel.solvers import THRESHOLDS, spd_solve

    n = a.shape[0]
    est = sparse_solve_bytes(n, int(a.nnz), 1, "direct", 8)
    pol = DevicePolicy(
        problem=Problem(kind="sparse", n=n, nnz=int(a.nnz), estimate_bytes=est),
        thresholds=THRESHOLDS,
    )
    be = select(pol)
    if be.name == "cpu":
        return None
    try:
        x, label = spd_solve(be, a, b, tol=tol)
    except AccelError:
        return None  # not converged / singular on the device: the CPU path decides
    finally:
        # the pool counts as committed memory on Windows (job-object caps): hand it back
        be.xp.get_default_memory_pool().free_all_blocks()
    if label.startswith("cpu"):
        return None
    return np.asarray(x, dtype=np.float64), label


def solve_spd(
    a: sp.csr_matrix, b: F64, solver: str = "auto", *, tol: float = 1e-11
) -> tuple[F64, str]:
    """Solve ``A x = b`` for symmetric positive definite ``A``; returns ``(x, solver_used)``."""
    n = a.shape[0]
    if solver == "auto" and b.ndim == 1:
        got = _solve_gpu(a, b, tol)
        if got is not None:
            return got
    have = available_solvers()
    choice = solver
    if solver == "auto":
        if n > AMG_THRESHOLD and "amg" in have:
            choice = "amg"
        elif "pardiso" in have:
            choice = "pardiso"
        elif "cholmod" in have:
            choice = "cholmod"
        else:
            choice = "superlu"
    elif solver not in have:
        choice = "superlu"  # requested backend not installed; caller reports the fallback
    if choice == "pardiso":
        pp: Any = importlib.import_module("pypardiso")
        return np.asarray(pp.spsolve(a.tocsr(), b), dtype=np.float64), "pardiso"
    if choice == "cholmod":
        sk: Any = importlib.import_module("sksparse.cholmod")
        return np.asarray(sk.cholesky(a.tocsc())(b), dtype=np.float64), "cholmod"
    if choice == "amg":
        return _amg(a.tocsr(), b, tol), "amg"
    return _superlu(a.tocsc(), b, True), "superlu"


def solve_general(a: sp.csr_matrix, b: F64) -> tuple[F64, str]:
    """Solve a general sparse system with SuperLU."""
    return _superlu(a.tocsc(), b, False), "superlu"
