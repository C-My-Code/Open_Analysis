# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Builders for hand-made ``DcNetwork`` objects (no Board needed) and shared test constants."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from shapely import Polygon as ShPolygon

from oa_model.materials import ConductorMaterial
from oa_pi.dc.config import DcConfig
from oa_pi.dc.network import Barrel, DcNetwork, LoadSpec, Pin, Sheet, build_network

REPO = Path(__file__).resolve().parents[3]
BOARDS = REPO / "tests" / "boards" / "kicad10"
T20 = 293.15
CU = ConductorMaterial(name="copper")
SIGMA = CU.conductivity


def sheet_resistance(t: float) -> float:
    """Rs of the default copper at 20 degC [ohm/sq]."""
    return 1.0 / (SIGMA * t)


def disc(r: float, n: int = 256, cx: float = 0.0, cy: float = 0.0) -> ShPolygon:
    th = 2 * math.pi * np.arange(n) / n
    return ShPolygon(np.column_stack([cx + r * np.cos(th), cy + r * np.sin(th)]))


def ring_poly(r0: float, r1: float, n: int = 256) -> ShPolygon:
    return ShPolygon(disc(r1, n).exterior.coords, [disc(r0, n).exterior.coords])


def make_pin(pid: str, refdes: str, layer: str, net: str, region: ShPolygon) -> Pin:
    c = region.centroid
    return Pin(pid, refdes, "1", net, layer, region, (float(c.x), float(c.y)))


def make_sheet(
    layer: str, net: str, polys: list[ShPolygon], t: float = 35e-6, z: float = 0.0
) -> Sheet:
    return Sheet(f"{layer}:{net}", layer, net, z, t, CU, polys)


def make_network(
    sheets: list[Sheet],
    vrm: Pin,
    load: Pin,
    barrels: list[Barrel] | None = None,
    *,
    current: float = 1.0,
    vset: float = 1.0,
    temperature: float = T20,
    h_plane: float = 0.5e-3,
    extra: dict[str, Any] | None = None,
) -> DcNetwork:
    """A one-rail network (ideal return): regulator pin ``vrm``, one load pin."""
    net_name = vrm.net
    cfg = DcConfig.model_validate(
        {
            "rail": {"net": net_name},
            "vrm": {"component": vrm.refdes, "vset": vset},
            "loads": [{"component": load.refdes, "current": current}],
            "temperature": temperature,
            "mesh": {"h_plane": h_plane} | (extra or {}),
        }
    )
    return DcNetwork(
        cfg=cfg,
        sheets=sheets,
        barrels=barrels or [],
        vrm_p=[vrm],
        vrm_n=[],
        sense_p=[],
        sense_n=[],
        loads=[LoadSpec(load.refdes, cfg.loads[0], [load], [])],
        temperature=temperature,
    )


def annulus_network(a: float, b: float, *, t: float = 35e-6, h_plane: float = 0.3e-3) -> DcNetwork:
    """Inner disc electrode (r < a) to outer rim electrode (r > b): R = Rs ln(b/a) / (2 pi)."""
    inner = disc(a)
    outer = ring_poly(b, b + 0.4e-3)
    sheet = make_sheet("L1", "N", [disc(b + 0.4e-3)], t)
    return make_network(
        [sheet],
        make_pin("p1", "J1", "L1", "N", inner),
        make_pin("p2", "J2", "L1", "N", outer),
        h_plane=h_plane,
    )


def strip_network(
    length: float, width: float, *, t: float = 35e-6, h_plane: float = 1e-3, pad: float = 1e-3
) -> DcNetwork:
    """Rectangular strip with full-width end pads that abut it: R = Rs L / W."""
    p1 = ShPolygon([(-pad, 0), (0, 0), (0, width), (-pad, width)])
    p2 = ShPolygon([(length, 0), (length + pad, 0), (length + pad, width), (length, width)])
    body = ShPolygon([(-pad, 0), (length + pad, 0), (length + pad, width), (-pad, width)])
    return make_network(
        [make_sheet("L1", "N", [body], t)],
        make_pin("p1", "J1", "L1", "N", p1),
        make_pin("p2", "J2", "L1", "N", p2),
        h_plane=h_plane,
    )


def read_kicad(name: str) -> Any:
    from oa_ingest_kicad import KiCadReader

    return KiCadReader().read(BOARDS / name / "board.kicad_pcb")


def kicad_solve(name: str, config: dict[str, Any], *, board: Any = None) -> Any:
    """Build the network of a WP-102 board and solve it with the FEM."""
    from oa_geometry import BoardGeometry
    from oa_pi.dc.fem import solve_fem

    b = board if board is not None else read_kicad(name)
    net = build_network(b, BoardGeometry.build(b), DcConfig.model_validate(config))
    return solve_fem(net)


def two_point_resistance(sol: Any, load: str, vset: float, current: float) -> float:
    return (vset - sol.group_voltage[f"load:{load}"]) / current


PLANE_CFG: dict[str, Any] = {
    "rail": {"net": "+1V0", "return": "GND"},
    "vrm": {"component": "U5", "vset": 1.0},
    "loads": [{"component": "U1", "current": "10A"}],
    "temperature": "20degC",
}
