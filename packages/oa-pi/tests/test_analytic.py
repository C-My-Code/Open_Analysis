# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden `pi_dc` analytic cases: strip, annulus, via arrays, WP-102 boards, conservation."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
import yaml
from pi_helpers import (
    BOARDS,
    PLANE_CFG,
    T20,
    annulus_network,
    kicad_solve,
    make_network,
    make_pin,
    make_sheet,
    sheet_resistance,
    strip_network,
    two_point_resistance,
)
from shapely import Polygon as ShPolygon

from oa_pi.dc.fem import solve_fem
from oa_pi.dc.network import Barrel
from oa_testkit.golden import load_case

pytestmark = pytest.mark.golden


def _r(sol: Any, load: str = "J2") -> float:
    return 1.0 - sol.group_voltage[f"load:{load}"]


def test_strip_matches_rs_l_over_w() -> None:
    case = load_case("pi_dc", "strip_uniform")
    net = strip_network(case.inputs["length"], case.inputs["width"], h_plane=1e-3)
    assert _r(solve_fem(net)) == pytest.approx(
        case.expected["resistance_ohm"], rel=case.tolerance.rtol
    )
    # Rs is the oa_physics value at 20 degC
    assert case.expected["resistance_ohm"] == pytest.approx(sheet_resistance(35e-6) * 10, rel=1e-9)


def test_annulus_matches_log_formula() -> None:
    case = load_case("pi_dc", "annulus")
    i = case.inputs
    net = annulus_network(i["a"], i["b"], h_plane=i["h_plane"])
    mesh = net.cfg.mesh.model_copy(update={"via_ring_segments": i["via_ring_segments"]})
    net.cfg = net.cfg.model_copy(update={"mesh": mesh})
    assert _r(solve_fem(net)) == pytest.approx(
        case.expected["resistance_ohm"], rel=case.tolerance.rtol
    )


def _array_network(n: int, layers: int) -> tuple[Any, tuple[float, ...]]:
    """``n`` barrels through ``layers`` copper layers; end pads cover every barrel."""
    t, side = 35e-6, 20e-3
    plate = ShPolygon([(0, 0), (side, 0), (side, side), (0, side)])
    pad = ShPolygon([(4e-3, 4e-3), (16e-3, 4e-3), (16e-3, 16e-3), (4e-3, 16e-3)])
    lids = [f"L{k + 1}" for k in range(layers)]
    sheets = [make_sheet(lid, "N", [plate], t) for lid in lids]
    pts = [(8e-3 + 2e-3 * (k % 3), 8e-3 + 2e-3 * (k // 3)) for k in range(n)]
    seg_r = tuple(1.2e-3 * (k + 1) for k in range(layers - 1))
    top = make_pin("p1", "J1", lids[0], "N", pad)
    bot = make_pin("p2", "J2", lids[-1], "N", pad)
    barrels = [
        Barrel(
            f"v{k}",
            "N",
            xy,
            0.15e-3,
            tuple(lids),
            seg_r,
            (1e-3,) * (layers - 1),
            1e-8,
            "via",
            {lids[0]: "p1", lids[-1]: "p2"},
        )
        for k, xy in enumerate(pts)
    ]
    return make_network(sheets, top, bot, barrels), seg_r


@pytest.mark.parametrize("n", [1, 4, 9])
def test_parallel_vias(n: int) -> None:
    net, seg_r = _array_network(n, 2)
    assert _r(solve_fem(net)) == pytest.approx(seg_r[0] / n, rel=1e-9)


def test_series_barrel_segments() -> None:
    net, seg_r = _array_network(9, 4)
    assert _r(solve_fem(net)) == pytest.approx(sum(seg_r) / 9, rel=1e-9)


def test_kicad_two_layer_vias_n9() -> None:
    case = load_case("pi_dc", "two_layer_vias_n9")
    sol = kicad_solve(
        "power_two_layer_vias",
        {
            "rail": {"net": "VX"},
            "temperature": T20,
            "vrm": {"component": "J1"},
            "loads": [{"component": "J2", "current": 1.0}],
        },
    )
    assert two_point_resistance(sol, "J2", 1.0, 1.0) == pytest.approx(
        case.expected["resistance_ohm"], rel=case.tolerance.rtol
    )
    cur = [b.currents[0] for b in sol.barrels]
    assert max(cur) - min(cur) < 1e-9  # symmetric array shares the current equally
    assert sum(cur) == pytest.approx(1.0, rel=1e-9)


@pytest.mark.parametrize(
    ("board", "net"), [("power_strip", "STRIP"), ("power_two_layer_vias", "VX")]
)
def test_expected_yaml_dc(board: str, net: str) -> None:
    exp = yaml.safe_load((BOARDS / board / "expected.yaml").read_text(encoding="utf-8"))
    (case,) = exp["dc"]["cases"]
    cfg = {
        "rail": {"net": net},
        "vrm": {"component": "J1"},
        "loads": [{"component": "J2", "current": 1.0}],
        "temperature": T20,
    }
    sol = kicad_solve(board, cfg)
    r = two_point_resistance(sol, "J2", 1.0, 1.0)
    assert r == pytest.approx(case["resistance_ohm"], rel=case["tolerance_rel"])


def test_symmetry_swap_regulator_and_load() -> None:
    a = solve_fem(strip_network(0.04, 0.006))
    net = strip_network(0.04, 0.006)
    n2 = make_network(net.sheets, net.loads[0].rail_pins[0], net.vrm_p[0], h_plane=1e-3)
    assert _r(a) == pytest.approx(_r(solve_fem(n2), "J1"), rel=1e-9)


def test_current_conservation_and_kcl_on_power_plane() -> None:
    sol = kicad_solve("power_plane", PLANE_CFG)
    for role in ("load", "load_ret", "vrm", "vrm_ret"):
        group = [p for p in sol.pins if p.role == role]
        assert sum(p.current for p in group) == pytest.approx(10.0, rel=1e-9)
    assert sol.i_source == pytest.approx(10.0, rel=1e-9)
    assert sol.residual < 1e-9
    vload = sol.group_voltage["load:U1"] - sol.group_voltage["load_ret:U1"]
    loss = sol.loss_sheets + sol.loss_barrels
    assert sol.v_source * sol.i_source - vload * 10.0 == pytest.approx(loss, rel=1e-6)
    for f in sol.fields:
        assert np.isfinite(f.v).all()
        assert np.isfinite(f.j_mag).all()
