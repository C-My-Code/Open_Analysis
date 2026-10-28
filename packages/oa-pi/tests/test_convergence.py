# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh convergence: ring contacts are mesh independent, point contacts are not.

Documented convergence behaviour (also in the package README):

- Ring contact (every via/pad in the solver): the spreading resistance is Rs ln(b/r)/(2 pi) with
  the ring radius r fixed by geometry, so the solution converges with the usual P1 rate; the
  tests below see < 0.5 % change per 2x refinement (and < 0.3 % against the formula).
- Point contact (radius 0, *not* used by the solver): R = Rs/(2 pi) (ln(b/h) + c) grows by
  Rs ln 2/(2 pi) per halving of the element size h, i.e. there is no convergence (order -> log).
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np
import pytest
import scipy.sparse as sp
import scipy.sparse.linalg as spla
from pi_helpers import (
    PLANE_CFG,
    disc,
    kicad_solve,
    make_network,
    make_pin,
    make_sheet,
    ring_poly,
    sheet_resistance,
)

from oa_mesh import Contact, SheetPiece, SizeField, mesh_copper_sheets
from oa_model.arrays import Points
from oa_model.geometry import Polygon
from oa_pi.dc.fem import _element_data, solve_fem
from oa_pi.dc.network import Barrel

T = 35e-6


def _via_disc(hp: float) -> tuple[float, float]:
    big, r_out = 10e-3, 0.1e-3
    rim = ring_poly(big, big + 0.4e-3)
    plate = disc(big + 0.4e-3)
    s1 = make_sheet("L1", "N", [plate], T, z=1e-3)
    s2 = make_sheet("L4", "N", [plate], T)
    bar = Barrel(
        "v", "N", (0.0, 0.0), r_out, ("L1", "L4"), (1e-3,), (1.6e-3,), 1e-8, "via", {"L4": "p2"}
    )
    net = make_network(
        [s1, s2],
        make_pin("p1", "J1", "L1", "N", rim),
        make_pin("p2", "J2", "L4", "N", plate),
        [bar],
        h_plane=hp,
    )
    r = 1.0 - solve_fem(net).group_voltage["load:J2"]
    return r, sheet_resistance(T) * math.log(big / r_out) / (2 * math.pi) + 1e-3


def test_ring_contact_is_mesh_independent() -> None:
    res = [_via_disc(h) for h in (1e-3, 0.5e-3, 0.25e-3)]
    for (a, _), (b, _) in itertools.pairwise(res):
        assert abs(b / a - 1) < 0.005
    for r, exact in res:
        assert r == pytest.approx(exact, rel=0.003)


@pytest.mark.parametrize("seg", [16, 32])
def test_power_plane_drop_converges_under_refinement(seg: int) -> None:
    drops = []
    for hp in (1.6e-3, 0.8e-3, 0.4e-3):
        cfg: dict[str, Any] = PLANE_CFG | {"mesh": {"h_plane": hp, "via_ring_segments": seg}}
        sol = kicad_solve("power_plane", cfg)
        drops.append(1.0 - sol.group_voltage["load:U1"] + sol.group_voltage["load_ret:U1"])
    assert abs(drops[1] / drops[0] - 1) < 0.01
    assert abs(drops[2] / drops[1] - 1) < 0.01


def _point_contact_r(h: float) -> float:
    big = 10e-3
    th = 2 * np.pi * np.arange(128) / 128
    poly = Polygon(exterior=Points(np.column_stack([big * np.cos(th), big * np.sin(th)])))
    piece = SheetPiece(
        id="d", layer="L1", net="N", polygons=(poly,), contacts=(Contact(id="c", x=0.0, y=0.0),)
    )
    field = SizeField(kind="points", size=h, points=Points([(0.0, 0.0)]), grading=1.3)
    (m,) = mesh_copper_sheets([piece], h_default=1e-3, size_fields=[field])
    nodes, tri = m.nodes.array, m.triangles.array.astype(np.int64)
    b, c, area, _ = _element_data(nodes, tri)
    rs = sheet_resistance(T)
    ke = (np.einsum("mi,mj->mij", b, b) + np.einsum("mi,mj->mij", c, c)) / (4 * area * rs)[
        :, None, None
    ]
    n = len(nodes)
    k = sp.coo_matrix(
        (ke.reshape(-1), (np.repeat(tri, 3, axis=1).reshape(-1), np.tile(tri, (1, 3)).reshape(-1))),
        shape=(n, n),
    ).tocsr()
    src = int(m.contact_nodes("c")[0])
    rim = np.flatnonzero(np.hypot(nodes[:, 0], nodes[:, 1]) > big * 0.999)
    free = np.setdiff1d(np.arange(n), rim)
    rhs = np.zeros(len(free))
    rhs[np.searchsorted(free, src)] = 1.0  # 1 A in
    v = spla.spsolve(k[free][:, free].tocsc(), rhs)
    return float(v[np.searchsorted(free, src)])


def test_point_contact_does_not_converge_log_divergence() -> None:
    """R grows by about Rs ln(2)/(2 pi) per halving of h: why contacts are rings."""
    rs = sheet_resistance(T)
    r = [_point_contact_r(h) for h in (0.4e-3, 0.2e-3, 0.1e-3)]
    expected_step = rs * math.log(2) / (2 * math.pi)
    for a, b in itertools.pairwise(r):
        assert b > a
        assert (b - a) == pytest.approx(expected_step, rel=0.35)
