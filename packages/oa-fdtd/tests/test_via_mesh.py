# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Via barrels and antipads (regressions for B-EM-03, WP-517).

- The square barrel has the conformal radius of the round via (``a = r / 0.5902``), not its
  area: the equal-area square is 4.6 % larger in the radius that sets the barrel's capacitance and
  inductance, and B-EM-03 converged 0.2 dB low in SDD21 at 20 GHz with it.
- Barrels and antipads get uniform cells of at most ``r / 8`` and ``(r_anti - r) / 12``: at the
  plain ``d_max`` mesh (4 cells across the antipad gap, return vias in 190 um cells) SDD21 was
  0.6 dB low at 20 GHz.
- A grading fill line never splits a conductor's edge cell (it moved the cell-centre raster of a
  trace out by a cell), and a conductor box that overlaps a port's signal conductor is part of the
  signal in the static port solve (it was labelled ground next to the strip: 0.5 ohm).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from fdtd_inner_helpers import via_box

from oa_fdtd.grid import Grid
from oa_fdtd.model import Conductor, Model, ModePort, Waveform, compile_model
from oa_fdtd.subcell.pcb import _via_side, build_model


def test_via_square_has_the_conformal_radius_of_the_round_via() -> None:
    r = 0.125e-3
    a = _via_side(r)
    k = math.gamma(0.25) ** 2 / (4.0 * math.pi**1.5)  # conformal radius of the unit square
    assert k == pytest.approx(0.5902, abs=1e-4)
    assert a * k == pytest.approx(r, rel=1e-4)
    assert a < math.sqrt(math.pi) * r  # smaller than the equal-area square


def _cells_in(lines: np.ndarray, lo: float, hi: float) -> np.ndarray:
    d = np.diff(lines)
    mid = 0.5 * (lines[1:] + lines[:-1])
    return d[(mid > lo) & (mid < hi)]


def test_via_barrel_and_antipad_get_fine_uniform_cells() -> None:
    p = via_box()
    built = build_model(p, copper="pec")
    g = built.model.grid
    sig = next(v for v in p.structure.vias if v.role == "signal")
    r, r_anti = sig.r_outer, 0.3e-3
    cell = (r_anti - r) / 12.0
    half = 0.5 * _via_side(r)
    for a, c in ((0, sig.center[0]), (1, sig.center[1])):
        # the gap between the barrel faces and the antipad edge (inside the barrel a trace's
        # edge cell may stay coarser)
        for lo, hi in ((c - r_anti, c - half), (c + half, c + r_anti)):
            d = _cells_in(g.lines[a], lo, hi)
            assert d.size >= (r_anti - half) / cell - 1
            assert d.max() <= 1.3 * cell
    for v in p.structure.vias:  # return vias: cells of r / 8 over the barrel
        if v.role == "signal":
            continue
        for a, c in ((0, v.center[0]), (1, v.center[1])):
            assert _cells_in(g.lines[a], c - v.r_outer, c + v.r_outer).max() <= 1.3 * r / 8.0
    # the barrel faces are mesh lines
    for a, c in ((0, sig.center[0]), (1, sig.center[1])):
        for f in (c - half, c + half):
            assert np.min(np.abs(g.lines[a] - f)) < 1e-12
    plain = build_model(p, copper="pec", via_cells=0.0).model.grid
    d0 = _cells_in(plain.lines[0], sig.center[0] - r_anti, sig.center[0] + r_anti)
    assert d0.max() > 2.0 * cell  # without the refinement the gap holds a few coarse cells


def test_grading_fill_never_splits_an_edge_cell() -> None:
    """Refined via cells next to a trace edge: the trace raster stays on its thirds-rule cell."""
    p = via_box()
    built = build_model(p, copper="pec")
    g = built.model.grid
    top = next(b for b in p.structure.conductors if b.id == "cb:T")
    pts = np.asarray(top.polygons[0].exterior.array)
    y0, y1 = float(pts[:, 1].min()), float(pts[:, 1].max())
    boxes = [
        c
        for c in built.model.conductors
        if abs(c.lo[2] - top.z_bottom) < 1e-12 and c.hi[1] - c.lo[1] < 1e-3
    ]
    assert boxes
    for c in boxes:
        # metal ends one third of its edge cell inside the physical edge, never a cell outside
        assert y0 - 1e-12 <= c.lo[1] < y0 + 0.5 * (y1 - y0)
        assert y0 + 0.5 * (y1 - y0) < c.hi[1] <= y1 + 1e-12
        for a_edge, face in ((y0, c.lo[1]), (y1, c.hi[1])):
            j = int(np.argmin(np.abs(g.y - face)))
            nxt = g.y[j - 1] if face == c.lo[1] else g.y[j + 1]
            assert face + (nxt - face) / 3.0 == pytest.approx(a_edge, abs=1e-9)


def test_signal_box_wider_than_the_signal_is_not_ground() -> None:
    """A strip box one cell wider than the port's signal rectangle: the static solve sees one
    conductor (Z of a few tens of ohms), not a strip shorted to a ground sliver."""
    d = 0.05e-3
    g = Grid(np.arange(41) * d, np.arange(41) * d, np.arange(21) * d)
    sig = ((0.95e-3, 0.4e-3), (1.05e-3, 0.45e-3))
    port = ModePort("P1", 0, 0.0, (0.0, 0.0), (2e-3, 1e-3), (sig,))
    m = Model(g, Waveform("gauss_mod", 10e9, 14e9), np.array([1e9, 10e9]), ports=[port])
    # raster one cell wider than the signal on each side
    m.conductors.append(Conductor((-1.0, 0.9e-3, 0.4e-3), (3.0, 1.1e-3, 0.45e-3), model="pec"))
    m.conductors.append(Conductor((-1.0, -1.0, -1.0), (3.0, 3.0, 0.05e-3), model="pec"))
    c = compile_model(m, "float64")
    assert 20.0 < float(c.ports[0]["r_ohm"]) < 200.0
