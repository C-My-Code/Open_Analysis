# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sparse SPD solve for DC IR, raster FVM and thermal (one entry point, CPU fallback)."""

from __future__ import annotations

from typing import Any

from oa_accel.backend import Backend

__all__ = ["spd_solve"]


def spd_solve(
    be: Backend, a: Any, b: Any, *, method: str = "auto", tol: float = 1e-10
) -> tuple[Any, str]:
    """Solve ``A x = b`` for SPD sparse ``A``; returns ``(x, label)`` with host ``x``.

    ``method``: ``auto`` (cuDSS Cholesky when available, else CG + Jacobi), ``direct``, ``cg``
    (the portable path ROCm uses). On the CPU backend ``auto`` is a direct SuperLU solve.
    ``label`` names what ran: ``cudss``, ``cg-jacobi-gpu`` or ``cpu-direct`` / ``cpu-cg``.
    """
    n_fb = len(be.report().fallbacks)
    x = be.sparse_solve(a, b, method=method, assume="spd", tol=tol, maxiter=20_000)
    if be.name == "cpu" or len(be.report().fallbacks) > n_fb:
        return x, "cpu-cg" if method == "cg" else "cpu-direct"
    has_cudss = bool(getattr(getattr(be, "_pr", None), "cudss", False))
    direct = method == "direct" or (method == "auto" and has_cudss)
    return x, "cudss" if direct else "cg-jacobi-gpu"
