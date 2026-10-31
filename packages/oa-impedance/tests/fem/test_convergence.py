# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh-convergence study: error vs exact on a refinement sequence, Richardson extrapolation."""

from __future__ import annotations

import math

import _xs
import numpy as np
import pytest

from oa_impedance.fem import FemParams, solve_fem, solve_fem_raw
from oa_model.xsection import CrossSection
from oa_testkit import xsections as tk

EXACT_COHN_05 = 100.43245078474767  # W/b = 0.5, er = 1, true thin strip (golden impedance_bem)


def z0_levels(xs: CrossSection, h0: float, n: int = 4) -> tuple[list[float], list[int]]:
    """Z0 on a sequence of meshes with h and the corner size halved each time."""
    z, dofs = [], []
    for k in range(n):
        p = FemParams(h_max=h0 / 2**k, corner_refine=0.2 / 2**k, max_levels=0, loss="none")
        sol = solve_fem_raw(xs, np.array([0.0]), p)
        z.append(math.sqrt(sol.es.L_ext[0, 0] / sol.es.C[0, 0, 0].real))
        dofs.append(sol.es.n_dofs)
    return z, dofs


@pytest.mark.slow
def test_cohn_stripline_converges_and_richardson_is_tight() -> None:
    z, dofs = z0_levels(_xs.stripline(0.5, 1.0), 2e-3, 4)
    err = [abs(v / EXACT_COHN_05 - 1) for v in z]
    assert dofs == sorted(dofs)
    assert err[-1] < 1e-3 and err[-1] <= err[0]
    # Richardson on the last three levels (observed order from the difference ratio)
    d1, d2 = z[-2] - z[-3], z[-1] - z[-2]
    if abs(d2) < abs(d1) and d1 * d2 > 0:
        p = math.log2(d1 / d2)
        z_inf = z[-1] + d2 / (2**p - 1)
        assert abs(z_inf / EXACT_COHN_05 - 1) < 1e-3
    else:  # converged to the truncation floor: the finest levels agree to < 1e-3
        assert abs(z[-1] - z[-2]) / z[-1] < 1e-3


@pytest.mark.slow  # nightly: heavy reference case (CI time, 2026-10-31)
def test_refinement_loop_reports_levels_and_convergence() -> None:
    sol = solve_fem_raw(
        tk.get_case("wire_over_plane_h2r").xs, np.array([0.0]), FemParams(loss="none")
    )
    assert sol.levels and sol.levels[0]["n_dofs"] > 0
    assert sol.converged
    assert sol.levels[-1]["change"] <= FemParams().rtol
    r = solve_fem(tk.get_case("wire_over_plane_h2r").xs, [0.0], FemParams(loss="none"))
    assert r.accuracy_class == "reference"
