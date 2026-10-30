# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""BEM panelling near strip edges (WP-510): geometric grading survives, eps_eff bias is gone."""

from __future__ import annotations

import _xs
import numpy as np
import pytest

from oa_impedance.bem import MeshParams
from oa_impedance.bem.geometry import KIND_INTERFACE, build_problem
from oa_impedance.service import ImpedanceServiceImpl

# exact conformal-map CPW on a half space (zero-thickness strips, infinite ground), see B-IMP-01
CPW_EXACT = {1.0: 120.56756262031708, 9.8: 51.88401801347865}


def test_interface_panels_grow_geometrically_away_from_the_strip_corner() -> None:
    """Before WP-510 the first ~10 panels next to the corner were uniform (np.interp across a
    sample interval spanning a factor of 60 in panel size)."""
    xs = _xs.microstrip(0.3, 9.8, t_over_h=0.01)
    prob = build_problem(xs, MeshParams())
    p = prob.panels
    w2 = 0.3e-3 / 2
    sel = np.nonzero((p.kind == KIND_INTERFACE) & (p.mid[:, 0] > w2) & (p.mid[:, 0] < 3 * w2))[0]
    order = sel[np.argsort(p.mid[sel, 0])]
    lengths = p.length[order]
    assert lengths[0] < 0.01 * w2  # sub-micron panel at the corner (h = 1 mm, w = 0.3 mm)
    growth = lengths[1:] / lengths[:-1]
    assert growth[:12].max() < 2.0  # no jump from the corner panel to its neighbours
    assert growth[:12].mean() > 1.05  # and it is genuinely graded, not uniform


@pytest.mark.parametrize("er", [1.0, 9.8])
def test_cpw_halfspace_within_the_exact_gate(er: float) -> None:
    r = ImpedanceServiceImpl().solve(_xs.cpw_halfspace(er), [0.0], method="bem")
    z = float(r.params.z0.array[0])
    assert abs(z / CPW_EXACT[er] - 1.0) < 0.0006  # was 0.0022 (FOLLOWUPS WP-105)
