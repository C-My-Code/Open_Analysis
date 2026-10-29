# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""DC IR validation: analytic strip / annulus / via cases, WP-102 boards, FEM vs raster."""

from __future__ import annotations

import math
import sys
import time

from val_model import REPO, Case, case_dir, load_case

sys.path.insert(0, str(REPO / "packages" / "oa-pi" / "tests"))

from pi_helpers import (
    PLANE_CFG,
    T20,
    annulus_network,
    kicad_solve,
    read_kicad,
    strip_network,
    two_point_resistance,
)

from oa_geometry import BoardGeometry
from oa_physics import via as physics_via
from oa_pi.dc.config import DcConfig
from oa_pi.dc.fem import solve_fem
from oa_pi.dc.network import build_network
from oa_pi.reference import solve_raster
from oa_testkit.builders import power_plane_board

CAT = "dc-ir"
SIGMA = 5.8e7  # S/m, the copper default of oa_model at 20 degC


def _drop(sol, load: str = "J2") -> float:  # type: ignore[no-untyped-def]
    return 1.0 - sol.group_voltage[f"load:{load}"]


def analytic_cases() -> list[Case]:
    out: list[Case] = []
    rs = 1.0 / (SIGMA * 35e-6)
    g = load_case("pi_dc", "strip_uniform")
    i = g["inputs"]
    t0 = time.perf_counter()
    got = _drop(solve_fem(strip_network(i["length"], i["width"], h_plane=1e-3)))
    out.append(
        Case(
            "dc.strip_uniform",
            CAT,
            "Uniform strip, R = Rs L / W",
            "analytic: R = Rs L / W, "
            "Rs = 1/(sigma t), sigma = 5.8e7 S/m, t = 35 um (ROADMAP gate 0.1 %)",
            "analytic",
            True,
            "R",
            "ohm",
            rs * i["length"] / i["width"],
            got,
            "rel",
            1e-3,
            case_dir("pi_dc", "strip_uniform"),
            runtime_s=time.perf_counter() - t0,
        )
    )
    g = load_case("pi_dc", "annulus")
    i = g["inputs"]
    net = annulus_network(i["a"], i["b"], h_plane=i["h_plane"])
    mesh = net.cfg.mesh.model_copy(update={"via_ring_segments": i["via_ring_segments"]})
    net.cfg = net.cfg.model_copy(update={"mesh": mesh})
    t0 = time.perf_counter()
    got = _drop(solve_fem(net))
    out.append(
        Case(
            "dc.annulus",
            CAT,
            "Annulus between ring electrodes, R = Rs ln(b/a) / 2 pi",
            "analytic: R = Rs ln(b/a) / (2 pi) (ROADMAP gate 0.1 %)",
            "analytic",
            True,
            "R",
            "ohm",
            rs * math.log(i["b"] / i["a"]) / (2 * math.pi),
            got,
            "rel",
            1e-3,
            case_dir("pi_dc", "annulus"),
            runtime_s=time.perf_counter() - t0,
        )
    )
    # one barrel: 0.30 mm drill, 25 um plating, 1.6 mm. 1.27 mohm of the ROADMAP is rounded.
    g = load_case("physics", "via_barrel_r")
    i = g["inputs"]
    ro = i["drill_m"] / 2
    r_formula = (1.0 / SIGMA) * i["h_m"] / (math.pi * (ro**2 - (ro - i["plating_m"]) ** 2))
    out.append(
        Case(
            "dc.via_barrel_roadmap",
            CAT,
            "Via barrel R (oa_physics), ROADMAP figure 1.27 mohm",
            g["source"],
            "analytic",
            True,
            "R",
            "ohm",
            g["expected"]["r_ohm"],
            physics_via.barrel_resistance(i["h_m"], i["drill_m"], i["plating_m"]),
            "rel",
            g["tolerance"]["rtol"],
            case_dir("physics", "via_barrel_r"),
            note=f"formula with sigma = 5.8e7 gives {r_formula * 1e3:.4f} mohm, 0.57 % above the "
            "rounded ROADMAP figure; the 0.5 % ROADMAP gate cannot be met against 1.27 itself",
        )
    )
    g = load_case("pi_dc", "two_layer_vias_n9")
    i = g["inputs"]
    ro = i["drill"] / 2
    rb = (1.0 / SIGMA) * i["barrel_length"] / (math.pi * (ro**2 - (ro - i["plating"]) ** 2))
    t0 = time.perf_counter()
    sol = kicad_solve(
        "power_two_layer_vias",
        {
            "rail": {"net": "VX"},
            "temperature": T20,
            "vrm": {"component": "J1"},
            "loads": [{"component": "J2", "current": 1.0}],
        },
    )
    out.append(
        Case(
            "dc.via_array_n9",
            CAT,
            "Nine parallel via barrels, R = R_barrel / 9",
            g["source"],
            "analytic",
            True,
            "R",
            "ohm",
            rb / i["n_vias"],
            two_point_resistance(sol, "J2", 1.0, 1.0),
            "rel",
            5e-3,
            case_dir("pi_dc", "two_layer_vias_n9"),
            runtime_s=time.perf_counter() - t0,
        )
    )
    # the WP-102 power_strip board against its expected.yaml oracle (strip R = Rs L / W)
    import yaml

    for board, net_name in (("power_strip", "STRIP"), ("power_two_layer_vias", "VX")):
        exp = yaml.safe_load(
            (REPO / "tests" / "boards" / "kicad10" / board / "expected.yaml").read_text(
                encoding="utf-8"
            )
        )
        (case,) = exp["dc"]["cases"]
        sol = kicad_solve(
            board,
            {
                "rail": {"net": net_name},
                "vrm": {"component": "J1"},
                "loads": [{"component": "J2", "current": 1.0}],
                "temperature": T20,
            },
        )
        out.append(
            Case(
                f"dc.board.{board}",
                CAT,
                f"WP-102 board '{board}' vs expected.yaml: {case['name']}",
                f"tests/boards/kicad10/{board}/expected.yaml dc.cases ({case['formula']})",
                "analytic",
                True,
                "R",
                "ohm",
                case["resistance_ohm"],
                two_point_resistance(sol, "J2", 1.0, 1.0),
                "rel",
                case["tolerance_rel"],
                f"tests/boards/kicad10/{board}",
            )
        )
    return out


def cross_cases() -> list[Case]:
    g = load_case("crosssolver", "dc_fem_vs_raster")
    cdir = case_dir("crosssolver", "dc_fem_vs_raster")
    out: list[Case] = []

    def drop_fem(sol, name: str) -> float:  # type: ignore[no-untyped-def]
        return (
            1.0 - sol.group_voltage[f"load:{name}"] + sol.group_voltage.get(f"load_ret:{name}", 0.0)
        )

    for neck in (None, 6e-3):
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
        t0 = time.perf_counter()
        fem = drop_fem(solve_fem(net), "U2")
        ras = 1.0 - solve_raster(net, 0.8e-3 / 4).load_voltage("U2")
        out.append(
            Case(
                f"x.dc.power_plane_testkit.{'neck' if neck else 'plain'}",
                "dc-ir/cross-solver",
                f"FEM vs raster FVM, testkit power_plane board{' with 6 mm neck' if neck else ''}",
                g["source"],
                "cross-solver",
                False,
                "voltage drop",
                "V",
                ras,
                fem,
                "rel",
                g["tolerance"]["rtol"],
                cdir,
                runtime_s=time.perf_counter() - t0,
            )
        )
    board = read_kicad("power_plane")
    net = build_network(board, BoardGeometry.build(board), DcConfig.model_validate(PLANE_CFG))
    t0 = time.perf_counter()
    fem = drop_fem(solve_fem(net), "U1")
    ras = 1.0 - solve_raster(net, 0.2e-3).load_voltage("U1")
    out.append(
        Case(
            "x.dc.power_plane_wp102",
            "dc-ir/cross-solver",
            "FEM vs raster FVM, WP-102 power_plane",
            g["source"],
            "cross-solver",
            False,
            "voltage drop",
            "V",
            ras,
            fem,
            "rel",
            g["tolerance"]["rtol"],
            cdir,
            runtime_s=time.perf_counter() - t0,
        )
    )
    k = load_case("crosssolver", "dc_kicad_irdrop_padne")
    out.append(
        Case(
            "x.dc.kicad_irdrop",
            "dc-ir/cross-solver",
            "[verify] kicad-irdrop 4-layer board (3.04 mohm)",
            k["source"],
            "cross-solver",
            True,
            "R",
            "ohm",
            k["expected"]["r_ohm"],
            None,
            "rel",
            k["tolerance"]["rtol"],
            case_dir("crosssolver", "dc_kicad_irdrop_padne"),
            note="pending: external oracle not run",
            forced_status="pending",
        )
    )
    return out


def run() -> list[Case]:
    return analytic_cases() + cross_cases()
