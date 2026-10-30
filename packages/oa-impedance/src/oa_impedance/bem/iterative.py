# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="operator,arg-type"
"""Matrix-free GMRES for sections whose dense BEM matrices exceed the memory budget.

The operator recomputes the analytic influence coefficients in row blocks on every product, so the
peak memory is ``O(n * (restart + blocks))`` instead of ``O(n^2)``. All right-hand sides advance
in lock step, so one kernel sweep serves every signal conductor. A block-Jacobi preconditioner
(dense LU of contiguous groups of panels, which follow the boundary polylines) is applied from the
right; the border unknowns (additive constant, floating potentials) are left unpreconditioned.
"""

from __future__ import annotations

from collections.abc import Callable, Generator

import numpy as np
import numpy.typing as npt
from scipy.linalg import lu_factor, lu_solve  # type: ignore[import-untyped,unused-ignore]

from .geometry import KIND_INTERFACE, KIND_RIBBON, KIND_THICK, Panels
from .kernels import influence

__all__ = ["EpsOperator", "gmres_lockstep"]

C128 = npt.NDArray[np.complex128]
F64 = npt.NDArray[np.float64]
Vec = npt.NDArray[np.generic]

_BLOCK_ENTRIES = 1 << 19
_PRE_BLOCK = 192


class EpsOperator:
    """The ``_solve_eps`` system matrix for zone permittivities ``ez`` without storing it."""

    def __init__(
        self, pan: Panels, ncnd: int, float_idx: list[int], ez: npt.NDArray[np.generic]
    ) -> None:
        self.pan = pan
        n = pan.n
        self.n = n
        self.float_idx = float_idx
        self.size = n + 1 + len(float_idx)
        self.ncnd = ncnd
        self.real = not np.any(np.asarray(ez).imag)
        self.dtype = np.float64 if self.real else np.complex128
        ezv: npt.NDArray[np.generic] = np.asarray(ez).real if self.real else np.asarray(ez)
        self.e1 = ezv[pan.zone1]
        e2 = np.where(pan.zone2 >= 0, ezv[np.maximum(pan.zone2, 0)], 0.0)
        self.avg = 0.5 * (self.e1 + e2)
        self.diff = self.e1 - e2
        self.is_if = pan.kind == KIND_INTERFACE
        self.is_rib = pan.kind == KIND_RIBBON
        self.rows_per_block = max(8, _BLOCK_ENTRIES // n)
        # free-charge functional pieces that do not need D (thick conductor panels, self term)
        thick = np.nonzero(pan.kind == KIND_THICK)[0]
        self.q_diag = np.zeros(n, dtype=self.dtype)
        self.q_diag[thick] = self.e1[thick] * pan.length[thick]
        rib = np.nonzero(self.is_rib)[0]
        self.q_diag[rib] = self.avg[rib] * pan.length[rib]
        self.q_cond = np.where(self.is_if, -1, pan.cond)

    def _block(self, r0: int, r1: int) -> tuple[F64, F64]:
        pan = self.pan
        p, d = influence(
            pan.mid[r0:r1],
            pan.normal[r0:r1],
            pan.a,
            pan.b,
            pan.tangent,
            pan.normal,
            pan.length,
        )
        idx = np.arange(r1 - r0)
        d[idx, idx + r0] = 0.0
        return p, d

    def _charges(self, X: Vec, dx: dict[int, Vec]) -> Vec:
        """Free charge per conductor (ncnd, k) from the panel unknowns ``X[:n]``; ``dx`` maps a
        ribbon row to ``D[row] @ X``."""
        pan = self.pan
        k = X.shape[1]
        out = np.zeros((self.ncnd, k), dtype=self.dtype)
        sel = np.nonzero(self.q_cond >= 0)[0]
        np.add.at(out, self.q_cond[sel], self.q_diag[sel, None] * X[sel])
        for i, v in dx.items():
            out[pan.cond[i]] += pan.length[i] * self.diff[i] * v
        return out

    def matvec(self, X: Vec) -> Vec:
        n = self.n
        X = X.reshape(self.size, -1)
        x = X[:n]
        k = X.shape[1]
        Y = np.zeros((self.size, k), dtype=np.result_type(self.dtype, X.dtype))
        pan = self.pan
        dx: dict[int, Vec] = {}
        for r0 in range(0, n, self.rows_per_block):
            r1 = min(n, r0 + self.rows_per_block)
            p, d = self._block(r0, r1)
            rows = np.arange(r0, r1)
            ci = ~self.is_if[r0:r1]
            if ci.any():
                Y[rows[ci]] = p[ci] @ x + X[n]
            ii = ~ci
            if ii.any():
                Y[rows[ii]] = self.diff[rows[ii], None] * (d[ii] @ x) + (
                    self.avg[rows[ii], None] * x[rows[ii]]
                )
            for r in np.nonzero(self.is_rib[r0:r1])[0]:
                dx[r0 + int(r)] = d[r] @ x
        for j, fi in enumerate(self.float_idx):
            m = (pan.cond == fi) & ~self.is_if
            Y[np.nonzero(m)[0]] -= X[n + 1 + j]
        q = self._charges(x, dx)
        Y[n] = q.sum(axis=0)
        for j, fi in enumerate(self.float_idx):
            Y[n + 1 + j] = q[fi]
        return Y

    def charge_of(self, X: Vec) -> Vec:
        """Free charge per conductor (ncnd, k) for solutions ``X`` (size, k)."""
        n = self.n
        x = X[:n]
        pan = self.pan
        dx: dict[int, Vec] = {}
        rib = np.nonzero(self.is_rib)[0]
        for s0 in range(0, len(rib), self.rows_per_block):
            sub = rib[s0 : s0 + self.rows_per_block]
            _, d = influence(
                pan.mid[sub], pan.normal[sub], pan.a, pan.b, pan.tangent, pan.normal, pan.length
            )
            d[np.arange(len(sub)), sub] = 0.0
            for t, r in enumerate(sub):
                dx[int(r)] = d[t] @ x
        return self._charges(x, dx)

    def preconditioner(self) -> Callable[[Vec], Vec]:
        n = self.n
        pan = self.pan
        facs: list[tuple[int, int, tuple[F64, npt.NDArray[np.int32]]]] = []
        for b0 in range(0, n, _PRE_BLOCK):
            b1 = min(n, b0 + _PRE_BLOCK)
            p, d = influence(
                pan.mid[b0:b1],
                pan.normal[b0:b1],
                pan.a[b0:b1],
                pan.b[b0:b1],
                pan.tangent[b0:b1],
                pan.normal[b0:b1],
                pan.length[b0:b1],
            )
            idx = np.arange(b1 - b0)
            d[idx, idx] = 0.0
            blk = np.empty((b1 - b0, b1 - b0), dtype=self.dtype)
            ci = ~self.is_if[b0:b1]
            blk[ci] = p[ci]
            ii = ~ci
            blk[ii] = self.diff[b0:b1][ii, None] * d[ii]
            blk[ii, idx[ii]] += self.avg[b0:b1][ii]
            facs.append((b0, b1, lu_factor(blk, check_finite=False)))

        def apply(v: Vec) -> Vec:
            out = np.array(v, copy=True)
            for b0, b1, lu in facs:
                out[b0:b1] = lu_solve(lu, v[b0:b1], check_finite=False)
            return out

        return apply


def _gmres(
    b: Vec, precond: Callable[[Vec], Vec], tol: float, restart: int, maxiter: int
) -> Generator[Vec, Vec, tuple[Vec, int, float]]:
    """Right-preconditioned restarted GMRES as a coroutine: yields a vector, receives its
    product with the operator. Returns ``(x, iterations, relative residual)``."""
    bn = float(np.linalg.norm(b)) or 1.0
    x = np.zeros_like(b)
    its = 0
    res = 1.0
    r = b.copy()
    beta = float(np.linalg.norm(r))
    while its < maxiter and beta > tol * bn:
        v = [r / beta]
        z: list[Vec] = []
        h = np.zeros((restart + 1, restart), dtype=b.dtype)
        g = np.zeros(restart + 1, dtype=b.dtype)
        g[0] = beta
        y = np.zeros(0, dtype=b.dtype)
        used = 0
        for j in range(restart):
            zj = precond(v[j])
            z.append(zj)
            w = yield zj
            its += 1
            for i in range(j + 1):
                h[i, j] = np.vdot(v[i], w)
                w = w - h[i, j] * v[i]
            h[j + 1, j] = np.linalg.norm(w)
            used = j + 1
            happy = abs(h[j + 1, j]) < 1e-14 * beta
            if not happy:
                v.append(w / h[j + 1, j])
            y = np.linalg.lstsq(h[: j + 2, : j + 1], g[: j + 2], rcond=None)[0]
            res = float(np.linalg.norm(g[: j + 2] - h[: j + 2, : j + 1] @ y)) / bn
            if res <= tol or happy or its >= maxiter:
                break
        for i in range(used):
            x = x + y[i] * z[i]
        ax = yield x
        r = b - ax
        beta = float(np.linalg.norm(r))
        res = beta / bn
    return x, its, res


def gmres_lockstep(
    op: EpsOperator,
    rhs: Vec,
    precond: Callable[[Vec], Vec],
    *,
    tol: float = 1e-10,
    restart: int = 60,
    maxiter: int = 400,
) -> tuple[Vec, int, float]:
    """Solve ``A x = rhs`` for every column of ``rhs``; one operator sweep per Arnoldi step serves
    all columns. Returns ``(X, max iterations, worst relative residual)``."""
    k = rhs.shape[1]
    dtype = np.result_type(op.dtype, rhs.dtype)
    gens = [
        _gmres(np.ascontiguousarray(rhs[:, m]).astype(dtype), precond, tol, restart, maxiter)
        for m in range(k)
    ]
    pending: dict[int, Vec] = {}
    out: dict[int, tuple[Vec, int, float]] = {}
    for m, g in enumerate(gens):
        try:
            pending[m] = next(g)
        except StopIteration as e:
            out[m] = e.value
    while pending:
        ids = sorted(pending)
        W = np.stack([pending[m] for m in ids], axis=1)
        AW = op.matvec(W)
        pending = {}
        for t, m in enumerate(ids):
            try:
                pending[m] = gens[m].send(AW[:, t])
            except StopIteration as e:
                out[m] = e.value
    X = np.stack([out[m][0] for m in range(k)], axis=1)
    return X, max(out[m][1] for m in range(k)), max(out[m][2] for m in range(k))
