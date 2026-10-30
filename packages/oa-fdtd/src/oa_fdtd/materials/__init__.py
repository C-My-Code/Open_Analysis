# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dispersive dielectrics for the ADE kernels (ADR-0010 section 5): multi-pole Debye fits."""

from oa_fdtd.materials.debye import (
    DebyeFit,
    djordjevic_sarkar_eps,
    fit_callable,
    fit_debye,
    fit_djordjevic_sarkar,
)

__all__ = [
    "DebyeFit",
    "djordjevic_sarkar_eps",
    "fit_callable",
    "fit_debye",
    "fit_djordjevic_sarkar",
]
