# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Numerical kernels behind the backends: a SciPy oracle and a CuPy mirror of each.

Everything here is written against an array module ``xp`` where the APIs agree (stencil), and as
a CPU/GPU pair where they do not (dense and sparse solves). No function imports ``cupy`` at
module level; GPU functions receive the CuPy module from :mod:`oa_accel.probe`.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import numpy as np

from oa_accel.errors import AccelError, ErrorInfo

Method = str  # "auto" | "direct" | "cg" | "gmres"
_SCIPY_ASSUME = {"gen": "gen", "sym": "sym", "pos": "pos"}


def not_converged(method: str, info: int, tol: float) -> AccelError:
    return AccelError(
        ErrorInfo(
            code="ACCEL_NOT_CONVERGED",
            message=f"{method} did not reach relative residual {tol:g} (info={info}).",
            path=f"sparse_solve.{method}",
            hint="Use method='direct', a stronger preconditioner, or a larger maxiter.",
        )
    )


def singular(op: str) -> AccelError:
    return AccelError(
        ErrorInfo(
            code="ACCEL_SINGULAR",
            message=f"{op}: the matrix is singular or the result is not finite.",
            path=op,
            hint="Check the model for floating nodes or missing constraints.",
        )
    )


# ------------------------------------------------------------------ stencil (xp-generic)


def stencil_apply(xp: Any, u: Any, weights: tuple[float, ...]) -> Any:
    """``y = A u`` for the 5-point (2D) or 7-point (3D) operator, matrix-free.

    ``A = sum_axis w_axis * (2 I - shift_+ - shift_-)`` with homogeneous Dirichlet values outside
    the grid: symmetric positive definite, the FVM Laplacian for uniform conductance ``w`` [S].
    """
    if u.ndim not in (2, 3) or len(weights) != u.ndim:
        raise AccelError(
            ErrorInfo(
                code="ACCEL_BAD_INPUT",
                message=f"raster_stencil needs a 2D or 3D grid and one weight per axis, got "
                f"ndim={u.ndim} and {len(weights)} weights.",
                path="raster_stencil.weights",
                hint="Pass weights=(wx, wy) or (wx, wy, wz).",
            )
        )
    y = xp.zeros_like(u)
    for axis, w in enumerate(weights):
        lo = [slice(None)] * u.ndim
        hi = [slice(None)] * u.ndim
        lo[axis] = slice(0, -1)
        hi[axis] = slice(1, None)
        y += (2.0 * w) * u
        y[tuple(hi)] -= w * u[tuple(lo)]
        y[tuple(lo)] -= w * u[tuple(hi)]
    return y


# ------------------------------------------------------------------ CPU (the oracle)


def cpu_dense_solve(a: Any, b: Any, assume: str) -> Any:
    import scipy.linalg as sla  # type: ignore[import-untyped,unused-ignore]

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", sla.LinAlgWarning)  # ill-conditioned: judged by caller
            return sla.solve(a, b, assume_a=_SCIPY_ASSUME[assume])
    except (np.linalg.LinAlgError, sla.LinAlgError, ValueError) as exc:
        if isinstance(exc, ValueError) and "singular" not in str(exc).lower():
            raise
        raise singular("dense_solve") from exc


def _jacobi_cpu(a: Any) -> Any:
    from scipy.sparse.linalg import LinearOperator  # type: ignore[import-untyped,unused-ignore]

    d = np.asarray(a.diagonal())
    inv = 1.0 / d
    return LinearOperator(a.shape, matvec=lambda v: inv * v, dtype=a.dtype)


def cpu_sparse_solve(a: Any, b: Any, method: str, m: Any, tol: float, maxiter: int | None) -> Any:
    import scipy.sparse.linalg as spla  # type: ignore[import-untyped,unused-ignore]

    a = a.tocsr()
    if method == "direct":
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # MatrixRankWarning on singular input
                lu = spla.splu(a.tocsc())
        except RuntimeError as exc:  # "Factor is exactly singular"
            raise singular("sparse_solve") from exc
        return lu.solve(np.asarray(b))
    prec = _jacobi_cpu(a) if isinstance(m, str) and m == "jacobi" else m
    cols = [np.asarray(b)] if np.ndim(b) == 1 else [np.asarray(b)[:, j] for j in range(b.shape[1])]
    outs = []
    for col in cols:
        if method == "cg":
            x, info = spla.cg(a, col, rtol=tol, atol=0.0, M=prec, maxiter=maxiter)
        else:
            x, info = spla.gmres(a, col, rtol=tol, atol=0.0, M=prec, maxiter=maxiter)
        if info != 0:
            raise not_converged(method, int(info), tol)
        outs.append(x)
    return outs[0] if np.ndim(b) == 1 else np.stack(outs, axis=1)


# ------------------------------------------------------------------ GPU (CuPy mirror)


def gpu_dense_solve(cp: Any, a: Any, b: Any, assume: str) -> Any:
    """cuSOLVER LU (``getrf/getrs``) or Cholesky (``potrf``) through CuPy."""
    if assume == "pos":
        from cupyx.scipy.linalg import solve_triangular

        low = cp.linalg.cholesky(a)
        y = solve_triangular(low, b, lower=True)
        x = solve_triangular(low.conj().T, y, lower=False)
    else:  # "gen" and "sym" use LU
        x = cp.linalg.solve(a, b)
    if not bool(cp.isfinite(x).all()):
        raise singular("dense_solve")
    return x


def _jacobi_gpu(cp: Any, a: Any) -> Any:
    from cupyx.scipy.sparse.linalg import LinearOperator

    inv = 1.0 / a.diagonal()
    return LinearOperator(a.shape, matvec=lambda v: inv * v, dtype=a.dtype)


def gpu_cudss_solve(cp: Any, a: Any, b: Any, assume: str) -> Any:
    """cuDSS direct solve through ``nvmath.sparse.advanced`` (experimental in nvmath)."""
    from nvmath.sparse import advanced as adv

    a.sort_indices()
    kind = adv.DirectSolverMatrixType.SPD if assume == "spd" else adv.DirectSolverMatrixType.GENERAL
    # nvmath logs a warning per call when no multithreading library is named; keep it out of
    # the user's logs (planning runs single-threaded, slower on big matrices).
    quiet = logging.getLogger("oa_accel.nvmath")
    quiet.addHandler(logging.NullHandler())
    quiet.propagate = False
    opts = adv.DirectSolverOptions(sparse_system_type=kind, logger=quiet)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        x = adv.direct_solver(a, cp.asfortranarray(b), options=opts)
    return x


def gpu_sparse_solve(
    cp: Any, a: Any, b: Any, method: str, m: Any, tol: float, maxiter: int | None, assume: str
) -> Any:
    import cupyx.scipy.sparse.linalg as cla

    if method == "direct":
        x = gpu_cudss_solve(cp, a, b, assume)
    else:
        prec = _jacobi_gpu(cp, a) if isinstance(m, str) and m == "jacobi" else m
        cols = [b] if b.ndim == 1 else [b[:, j] for j in range(b.shape[1])]
        outs = []
        for col in cols:
            if method == "cg":
                x1, info = cla.cg(a, col, rtol=tol, atol=0.0, M=prec, maxiter=maxiter)
            else:
                x1, info = cla.gmres(a, col, rtol=tol, atol=0.0, M=prec, maxiter=maxiter)
            if info != 0:
                raise not_converged(method, int(info), tol)
            outs.append(x1)
        x = outs[0] if b.ndim == 1 else cp.stack(outs, axis=1)
    if not bool(cp.isfinite(x).all()):
        raise singular("sparse_solve")
    return x
