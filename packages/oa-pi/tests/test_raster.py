# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""FEM against the independent raster FVM reference."""

from __future__ import annotations

import os

import pytest
from pi_helpers import PLANE_CFG, T20, kicad_solve, strip_network

from oa_geometry import BoardGeometry
from oa_pi.dc.config import DcConfig
from oa_pi.dc.fem import solve_fem
from oa_pi.dc.network import build_network
from oa_pi.reference import solve_raster
from oa_testkit.builders import power_plane_board


def _drop_fem(sol, name: str) -> float:
    return 1.0 - sol.group_voltage[f"load:{name}"] + sol.group_voltage.get(f"load_ret:{name}", 0.0)


@pytest.mark.parametrize("neck", [None, 6e-3])
def test_fem_vs_raster_on_power_plane_board(neck: float | None) -> None:
    board = power_plane_board(neck_width=neck)
    cfg = DcConfig.model_validate(
        {
            "rail": {"net": "VDD", "return": "GND"},
            "vrm": {"component": "U1"},
            "loads": [{"component": "U2", "current": 5.0}],
            "temperature": T20,
            "mesh": {"h_plane": 0.8e-3},
        }
    )
    net = build_network(board, BoardGeometry.build(board), cfg)
    fem = _drop_fem(solve_fem(net), "U2")
    h_raster = 0.8e-3 / 4
    ras = solve_raster(net, h_raster)
    assert 1.0 - ras.load_voltage("U2") == pytest.approx(fem, rel=0.01)


def test_fem_vs_raster_on_wp102_power_plane() -> None:
    from pi_helpers import read_kicad

    board = read_kicad("power_plane")
    net = build_network(board, BoardGeometry.build(board), DcConfig.model_validate(PLANE_CFG))
    fem = _drop_fem(solve_fem(net), "U1")
    assert 1.0 - solve_raster(net, 0.2e-3).load_voltage("U1") == pytest.approx(fem, rel=0.01)


def test_raster_strip_converges_to_formula() -> None:
    net = strip_network(0.05, 0.005)
    exact = 0.004926108374384237
    errs = [
        abs(1.0 - solve_raster(net, h).load_voltage("J2") - exact) / exact
        for h in (0.5e-3, 0.25e-3)
    ]
    assert errs[1] < errs[0]
    assert errs[1] < 0.01


def test_kicad_helper_is_deterministic() -> None:
    a = kicad_solve("power_plane", PLANE_CFG)
    b = kicad_solve("power_plane", PLANE_CFG)
    if os.environ.get("OA_DEVICE", "auto").lower().startswith("cuda"):
        # cuDSS is not bitwise reproducible run to run (oa_accel report: deterministic=False)
        assert a.group_voltage == pytest.approx(b.group_voltage, rel=1e-10, abs=1e-14)
    else:
        assert a.group_voltage == b.group_voltage
