# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-514: interior coarsening (``mesh.h_far``) and parallel meshing keep the FEM result."""

from __future__ import annotations

import math

import pytest
from pi_helpers import disc, make_network, make_pin, make_sheet, ring_poly, sheet_resistance

from oa_pi.dc.fem import solve_fem

A, B = 2e-3, 20e-3


def _annulus(extra: dict[str, float | int]) -> float:
    sheet = make_sheet("L1", "N", [disc(B + 0.4e-3)])
    net = make_network(
        [sheet],
        make_pin("p1", "J1", "L1", "N", disc(A)),
        make_pin("p2", "J2", "L1", "N", ring_poly(B, B + 0.4e-3)),
        h_plane=0.2e-3,
        extra=extra,
    )
    sol = solve_fem(net)
    return 1.0 - sol.group_voltage["load:J2"]


def test_h_far_matches_uniform_and_analytic() -> None:
    exact = sheet_resistance(35e-6) * math.log(B / A) / (2 * math.pi)
    uniform = _annulus({})
    coarse = _annulus({"h_far": 3e-3})
    assert coarse == pytest.approx(uniform, rel=5e-3)
    assert coarse == pytest.approx(exact, rel=1e-2)


def test_workers_do_not_change_result() -> None:
    assert _annulus({"mesh_workers": 1, "h_far": 3e-3}) == _annulus({"h_far": 3e-3})
