# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scalar view of ``oa_physics.eps_complex`` with the static-request convention.

``oa_physics`` is the single source of the dielectric models (tabulated Dk/Df is interpolated
linearly in ln f there). This module only adds the "f <= 0 means the datasheet point" rule that
the static (0 Hz) solvers use.
"""

from __future__ import annotations

from typing import Any

from oa_model.materials import ConstantDkDf, DjordjevicSarkar, TableDkDf
from oa_physics import eps_complex

__all__ = ["eps_at", "is_frequency_independent"]


def _datasheet_frequency(model: Any) -> float:
    if isinstance(model, DjordjevicSarkar):
        return float(model.f_ref)
    if isinstance(model, TableDkDf):
        return float(model.freqs.array[0])
    return 1e9


def eps_at(model: Any, f: float) -> complex:
    """Complex relative permittivity ``Dk (1 - j Df)`` of ``model`` at ``f`` [Hz].

    ``f <= 0`` is a static request and is evaluated at the datasheet point.
    """
    if f <= 0.0:
        f = _datasheet_frequency(model)
    return complex(eps_complex(model, f)[0])


def is_frequency_independent(model: Any) -> bool:
    return isinstance(model, ConstantDkDf)
