# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mesh rules, polygon helpers and dielectric fits (no openEMS needed)."""

from __future__ import annotations

import numpy as np
import pytest

from oa_em_openems.dispersion import constant_equivalent, eval_eps, fit_debye
from oa_em_openems.geom2d import keyhole_ring, point_in_ring, ring_edge_normals
from oa_em_openems.mesh import round_lines, smooth_axis, third_rule_lines
from oa_model.materials import ConstantDkDf, DjordjevicSarkar, MultiPoleDebye


def test_third_rule_puts_two_thirds_outside_and_one_third_inside() -> None:
    outside, inside = third_rule_lines(1.0, +1.0, 0.3)
    assert outside == pytest.approx(1.2)
    assert inside == pytest.approx(0.9)
    outside, inside = third_rule_lines(-1.0, -1.0, 0.3)
    assert outside == pytest.approx(-1.2)
    assert inside == pytest.approx(-0.9)


def test_smooth_axis_respects_max_cell_grading_and_fixed_lines() -> None:
    d_max = 0.7e-3
    fixed = [(0.0, d_max), (10e-3, 0.05e-3), (25e-3, d_max)]
    am = smooth_axis(fixed, d_max, 1.3)
    d = np.diff(am.lines)
    assert d.max() <= d_max * (1 + 1e-9)  # lambda/N bound
    assert {0.0, 10e-3, 25e-3} <= {round(float(x), 12) for x in am.lines}
    assert am.lines[0] == 0.0 and am.lines[-1] == 25e-3
    ratio = np.maximum(d[1:] / d[:-1], d[:-1] / d[1:])
    assert ratio.max() <= 1.5  # <= 1.3 in graded regions, a little more across fixed lines
    assert am.max_grading == pytest.approx(ratio.max())


def test_smooth_axis_is_deterministic() -> None:
    fixed = [(0.0, 1e-3), (3e-3, 1e-4), (9e-3, 1e-3)]
    a = smooth_axis(fixed, 1e-3, 1.3).lines
    b = smooth_axis(fixed, 1e-3, 1.3).lines
    assert np.array_equal(a, b)


def test_locked_intervals_are_single_cells() -> None:
    fixed = [(0.0, 1e-3), (1e-4, 1e-4), (2e-4, 1e-4), (1e-3, 1e-3)]
    am = smooth_axis(fixed, 1e-3, 1.3, locked=[1e-4, 2e-4])
    lines = [round(float(x), 12) for x in am.lines]
    assert lines.index(2e-4) - lines.index(1e-4) == 1


def test_round_lines_snaps_to_nanometres() -> None:
    out = round_lines(np.array([0.0, 1.0004e-3, 1.0004004e-3, 2e-3]), 1e-6)
    assert out.tolist() == [0.0, 1000.4, 2000.0]  # 0.4 pm apart merges, in micrometres


def test_keyhole_ring_matches_polygon_with_hole() -> None:
    ext = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    th = np.linspace(0, 2 * np.pi, 16, endpoint=False)[::-1]
    hole = [(5 + 1.5 * np.cos(t), 5 + 1.5 * np.sin(t)) for t in th]
    ring = keyhole_ring(ext, [hole])
    assert len(ring) == len(ext) + len(hole) + 2
    rng = np.random.default_rng(0)
    for x, y in rng.uniform(0, 10, size=(400, 2)):
        in_poly = point_in_ring((x, y), ext) and not point_in_ring((x, y), hole)
        assert point_in_ring((x, y), ring) == in_poly


def test_edge_normals_point_away_from_material() -> None:
    ext = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]])  # counter-clockwise
    normals = {(a, b): n for a, b, n in ring_edge_normals(ext)}
    assert normals[((0.0, 0.0), (2.0, 0.0))] == (0.0, -1.0)
    assert normals[((2.0, 0.0), (2.0, 1.0))] == (1.0, 0.0)


def test_djordjevic_sarkar_reproduces_dk_and_df_at_reference() -> None:
    m = DjordjevicSarkar(dk=4.3, df=0.02, f_ref=1e9)
    e = eval_eps(m, np.array([1e9]))[0]
    assert e.real == pytest.approx(4.3, rel=1e-12)
    assert -e.imag / e.real == pytest.approx(0.02, rel=1e-12)
    low, high = eval_eps(m, np.array([1e6, 1e10]))
    assert low.real > 4.3 > high.real  # causal: permittivity falls with frequency


def test_constant_model_and_equivalent() -> None:
    m = ConstantDkDf(dk=3.5, df=0.01, f_ref=2e9)
    eps, kappa = constant_equivalent(m, 2e9)
    assert eps == pytest.approx(3.5)
    assert kappa == pytest.approx(2 * np.pi * 2e9 * 8.8541878128e-12 * 3.5 * 0.01, rel=1e-6)


def test_multipole_debye_eval() -> None:
    m = MultiPoleDebye(eps_inf=3.0, poles=((1.0, 1e9),))
    e = eval_eps(m, np.array([1e9]))[0]
    assert e == pytest.approx(3.0 + 1.0 / (1 + 1j))


def test_single_pole_fit_error_is_reported_and_bounded() -> None:
    m = DjordjevicSarkar(dk=4.3, df=0.02, f_ref=1e9)
    fit = fit_debye(m, 0.5e9, 10e9)
    assert fit.delta_eps >= 0 and fit.kappa >= 0 and fit.tau > 0
    assert fit.max_rel_err_eps < 0.03
    assert fit.max_rel_err_tand < 0.2  # documented single-pole limit on a 20:1 band
    f = np.array([2e9, 5e9, 10e9])
    assert np.allclose(fit.eval(f).real, eval_eps(m, f).real, rtol=0.03)


def test_exact_debye_is_fitted_exactly() -> None:
    m = MultiPoleDebye(eps_inf=3.0, poles=((1.2, 3e9),))
    fit = fit_debye(m, 0.5e9, 10e9)
    assert fit.max_rel_err_eps < 1e-3
    assert fit.eps_inf == pytest.approx(3.0, abs=0.01)
    assert fit.delta_eps == pytest.approx(1.2, abs=0.01)
