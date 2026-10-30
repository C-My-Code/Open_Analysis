# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Matrix-free CG on a uniform grid (raster FVM): the operator is ``ops.stencil_apply``."""

from __future__ import annotations

from typing import Any

from oa_accel import ops
from oa_accel.backend import Backend

__all__ = ["cg_stencil"]


def cg_stencil(
    be: Backend,
    weights: tuple[float, ...],
    rhs: Any,
    *,
    tol: float = 1e-10,
    maxiter: int = 20_000,
) -> tuple[Any, int]:
    """Solve ``stencil(weights) u = rhs`` (homogeneous Dirichlet outside the grid) by Jacobi-
    preconditioned CG on the backend's device. Returns host ``(u, iterations)``; raises
    ``AccelError(ACCEL_NOT_CONVERGED)`` when ``tol`` (relative residual) is not reached."""
    xp = be.xp
    b = be.to_device(rhs)
    inv_d = 1.0 / (2.0 * sum(weights))
    x = xp.zeros_like(b)
    r = b.copy()
    z = inv_d * r
    p = z.copy()
    rz = float((r * z).sum())
    bn = float(xp.sqrt((b * b).sum())) or 1.0
    it = 0
    while it < maxiter and float(xp.sqrt((r * r).sum())) > tol * bn:
        ap = ops.stencil_apply(xp, p, weights)
        alpha = rz / float((p * ap).sum())
        x += alpha * p
        r -= alpha * ap
        z = inv_d * r
        rz_new = float((r * z).sum())
        p = z + (rz_new / rz) * p
        rz = rz_new
        it += 1
    if float(xp.sqrt((r * r).sum())) > tol * bn:
        raise ops.not_converged("cg", it, tol)
    return be.to_host(x), it
