# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Solver entry points built on the :class:`oa_accel.Backend` (WP-504).

- :func:`batched_dense_solve`: many dense systems, grouped by shape (BEM ``solve_many``).
- :func:`spd_solve`: sparse SPD solve, cuDSS or CG + Jacobi on the GPU, SciPy on the CPU (DC IR,
  raster FVM, thermal).
- :func:`cg_stencil`: matrix-free CG on a uniform 2D/3D grid (raster FVM).

``THRESHOLDS`` are the crossover sizes measured on the reference machine (RTX 4080 SUPER, see
``docs/change-requests/WP-504-thresholds.md``); callers pass them to ``DevicePolicy``.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = ["THRESHOLDS", "batched_dense_solve", "cg_stencil", "spd_solve"]

_LAZY = {
    "batched_dense_solve": "oa_accel.solvers.dense",
    "spd_solve": "oa_accel.solvers.sparse",
    "cg_stencil": "oa_accel.solvers.raster",
}


def __getattr__(name: str) -> Any:
    if name == "THRESHOLDS":
        from oa_accel.policy import Thresholds

        return Thresholds(dense_n=600, sparse_nnz=1_000_000, fdtd_cells=200_000)
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module 'oa_accel.solvers' has no attribute {name!r}")
    return getattr(importlib.import_module(mod), name)
