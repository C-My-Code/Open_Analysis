# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sub-cell conductor treatment: thick-copper surface impedance (ADR-0010 section 3)."""

from oa_fdtd.subcell.sibc import (
    SibcFit,
    fit_surface_impedance,
    hammerstad_jensen,
    slab_surface_impedance,
)

__all__ = ["SibcFit", "fit_surface_impedance", "hammerstad_jensen", "slab_surface_impedance"]
