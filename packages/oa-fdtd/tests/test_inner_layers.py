# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inner-layer structures (regressions for B-EM-02b and B-EM-03).

- An inner copper layer (the z gap between two slabs) is laminate wherever there is no copper;
  it was vacuum, which put a 2.8 % delay error on a 35 um stripline.
- The closed lossless via-through-planes box conserves energy (no dissipative term on inner
  edges).
- The wave planes stay in the uniform line next to their port; on a line too short for
  well-conditioned planes the port S is used (B-EM-03 lost 4.5 dB to planes inside the via cage).
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from fdtd_inner_helpers import C0, stripline_two_slabs, via_box

from oa_fdtd.adapter.sim import SolveOptions, solve_problem
from oa_fdtd.grid import Grid
from oa_fdtd.model import (
    Conductor,
    Model,
    ModelError,
    ModePort,
    SoftSource,
    Waveform,
    compile_model,
)
from oa_fdtd.ports import add_wave_planes, uniform_length
from oa_fdtd.reference import NumpyEngine
from oa_fdtd.subcell.pcb import build_model


def test_inner_copper_layer_is_filled_with_laminate() -> None:
    p = stripline_two_slabs()
    built = build_model(p, copper="pec")
    assert built.meta["resin_fills"] == 1
    c = compile_model(built.model, "float64")
    g = c.grid
    strip = p.structure.conductors[0]
    zmid = 0.5 * (strip.z_bottom + strip.z_top)
    k = int(np.argmin(np.abs(g.centers(2) - zmid)))  # the cell layer of the copper
    assert g.z[k] <= zmid <= g.z[k + 1]
    # E_z edges of that layer away from the strip carry the laminate permittivity, not vacuum
    j = int(np.argmin(np.abs(g.y - (-1.5e-3))))
    i = g.n[0] // 2
    assert c.eps_tab[c.idx[2][k + 1, j + 1, i + 1]] == pytest.approx(4.0, rel=1e-9)


def _delay_err(r: object, er: float, length: float, f: float) -> float:
    freqs, s = r.freqs, r.s  # type: ignore[attr-defined]
    ph = np.unwrap(np.angle(s[:, 1, 0]))
    i = int(np.argmin(np.abs(freqs - f)))
    tau = -ph[i] / (2.0 * np.pi * freqs[i])
    return float(tau / (math.sqrt(er) * length / C0) - 1.0)


@pytest.mark.gpu
def test_stripline_delay_matches_cohn_tem() -> None:
    """A 35 um centred stripline (copper layer between two slabs): delay within 0.5 % of the TEM
    delay sqrt(er) L / c of Cohn's stripline at 5 and 10 GHz, lossless |S21|."""
    length, er = 10e-3, 4.0
    r = solve_problem(
        stripline_two_slabs(length=length, er=er),
        SolveOptions(engine="cuda", dtype="float64", copper="pec", end_db=-60.0),
    )
    for f in (5e9, 10e9):
        assert abs(_delay_err(r, er, length, f)) < 5e-3, f
    band = r.freqs >= 1e9
    assert np.abs(20 * np.log10(np.abs(r.s[band, 1, 0]))).max() < 0.05


def test_stripline_delay_matches_cohn_tem_numpy() -> None:
    """The same on the NumPy reference engine (CPU), a 5 mm line."""
    length, er = 5e-3, 4.0
    r = solve_problem(
        stripline_two_slabs(length=length, er=er),
        SolveOptions(engine="numpy", dtype="float64", copper="pec", end_db=-60.0),
    )
    assert abs(_delay_err(r, er, length, 10e9)) < 5e-3


def test_closed_lossless_via_box_conserves_energy() -> None:
    """PEC copper, lossless laminate of two permittivities, closed walls: after the source the
    leapfrog energy stays constant to 1e-3 (any SIBC, ADE or conductivity on an inner edge
    would drain it)."""
    built = build_model(via_box(), copper="pec")
    m = built.model
    assert built.meta["resin_fills"] == 2
    m.ports.clear()
    g = m.grid
    xs, ys = float(g.x[g.n[0] // 4]), 0.0
    z0, z1 = float(g.z[1]), float(g.z[-2])
    m.sources.append(SoftSource("s", 2, (xs, ys, z0), (xs, ys, z1)))
    c = compile_model(m, "float64")
    assert c.ade is None and c.sibc is None and not np.any(c.sig_tab > 0)
    eng = NumpyEngine(c, "float64")
    t_quiet = 2.0 * m.waveform.delay_tau * m.waveform.tau
    eng.step(math.ceil(t_quiet / c.dt) + 2)
    w0 = _leapfrog_energy(eng)
    assert w0 > 0
    worst = 0.0
    for _ in range(20):
        eng.step(150)
        worst = max(worst, abs(_leapfrog_energy(eng) / w0 - 1.0))
    assert worst < 1e-3


def _leapfrog_energy(eng: NumpyEngine) -> float:
    """``1/2 eps E^n . E^n + 1/2 mu0 H^{n-1/2} . H^{n+1/2}``, the energy the Yee scheme conserves
    (advances one step)."""
    from oa_fdtd.grid import EPS0, MU0

    c = eng.c
    e_n = [e.astype(np.float64) for e in eng.E]
    h_prev = eng.h_copy()
    eng.step(1)
    total = 0.0
    for ci in range(3):
        w = eng._weights("E", ci)
        total += 0.5 * float(np.sum((c.eps_tab[c.idx[ci]] * EPS0)[None] * w * e_n[ci] ** 2))
        wh = eng._weights("H", ci)
        total += 0.5 * MU0 * float(np.sum(wh * h_prev[ci] * eng.H[ci].astype(np.float64)))
    return total


def _line_with_obstacle(x_obst: float) -> Model:
    d = 0.1e-3
    g = Grid(np.arange(101) * d, np.arange(21) * d, np.arange(11) * d)
    port = ModePort("P1", 0, 0.0, (0.0, 0.0), (2e-3, 1e-3), (((0.9e-3, 0.4e-3), (1.1e-3, 0.5e-3)),))
    m = Model(g, Waveform("gauss_mod", 10e9, 14e9), np.array([1e9, 20e9]), ports=[port])
    m.conductors.append(Conductor((-1.0, 0.9e-3, 0.4e-3), (10e-3 + 1.0, 1.1e-3, 0.5e-3)))
    m.conductors.append(
        Conductor((x_obst, 0.2e-3, 0.0), (x_obst + 0.2e-3, 0.4e-3, 1e-3), model="pec")
    )
    return m


def test_wave_planes_stay_in_the_uniform_line() -> None:
    m = _line_with_obstacle(4.0e-3)
    port = m.ports[0]
    assert isinstance(port, ModePort)
    assert uniform_length(m, port) == pytest.approx(4.0e-3)
    pl = add_wave_planes(m, "P1", 0.5e-3)
    pos = [p.position for p in m.mode_probes]
    assert len(pos) == 3 and max(pos) <= 4.0e-3 - 0.5e-3 + 1e-12 and min(pos) >= 0.5e-3 - 1e-12
    assert pl.spacing > 0.0


def test_short_uniform_line_refuses_wave_planes() -> None:
    m = _line_with_obstacle(1.2e-3)
    with pytest.raises(ModelError) as ei:
        add_wave_planes(m, "P1", 0.25e-3)
    assert ei.value.code == "FDTD_UNEQUAL_PLANES"
    assert not m.mode_probes


def test_edge_rule_puts_no_line_on_a_metal_edge() -> None:
    from oa_fdtd.subcell.pcb import _edge_seeds
    from oa_model.arrays import Points
    from oa_model.geometry import Polygon
    from oa_testkit import builders as bld

    s = 0.09e-3
    ring = Points(bld.rect_ring(1e-3, -0.2e-3, 4e-3, 0.2e-3))
    hole = Points(bld.circle_ring(2e-3, 0.0, 0.05e-3, 24, clockwise=True))
    poly = Polygon(exterior=ring, holes=(hole,))
    lo3, hi3 = (0.0, -1e-3, 0.0), (5e-3, 1e-3, 1e-3)
    ys = sorted(round(v * 1e6, 6) for v, _ in _edge_seeds(poly, s, 1.0 / 3.0, lo3, hi3)[1])
    # y edges at -0.2 and +0.2 mm: a line s/3 inside and one 2s/3 outside, none on the edge;
    # the round hole adds no lines
    assert ys == [-260.0, -170.0, 170.0, 260.0]
    xs = sorted(round(v * 1e6, 6) for v, _ in _edge_seeds(poly, s, None, lo3, hi3)[0])
    assert xs == [1000.0, 4000.0]  # without the rule: lines on the edges


def test_port_extension_extrudes_the_port_face() -> None:
    p = stripline_two_slabs(length=4e-3)
    built = build_model(p, copper="pec", port_ext=1e-3)
    g = built.model.grid
    assert g.x[0] == pytest.approx(-1e-3) and g.x[-1] == pytest.approx(5e-3)
    assert built.meta["ref_planes"] == {"P1": 0.0, "P2": pytest.approx(4e-3)}
    pos = [q.position for q in built.model.ports]
    assert pos == [pytest.approx(-1e-3), pytest.approx(5e-3)]
    strip = [c for c in built.model.conductors if c.hi[1] - c.lo[1] < 1e-3]
    assert strip
    assert min(c.lo[0] for c in strip) <= g.x[0] and max(c.hi[0] for c in strip) >= g.x[-1]
