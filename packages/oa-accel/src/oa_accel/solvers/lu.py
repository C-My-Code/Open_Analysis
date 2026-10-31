# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""LU factorisations that outlive one solve: ``lu_factor`` / ``lu_solve`` of the active backend.

``Backend.dense_solve`` factors and discards. A caller that solves against the same matrix more
than once (the BEM conductor block, WP-519) keeps the factor and applies ``lu_solve`` repeatedly.
SciPy's ``lu_factor`` and ``lu_solve`` release the GIL, so host threads factor in parallel; the
CuPy versions call cuSOLVER ``getrf`` / ``getrs``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from oa_accel.backend import Backend

__all__ = ["lu_functions"]


def lu_functions(be: Backend | None) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """``(lu_factor, lu_solve)`` for ``be`` (``None`` or a CPU backend: SciPy)."""
    if be is not None and be.name != "cpu":
        from cupyx.scipy.linalg import lu_factor, lu_solve

        return lu_factor, lu_solve
    from scipy.linalg import lu_factor, lu_solve  # type: ignore[import-untyped,unused-ignore]

    return lu_factor, lu_solve
