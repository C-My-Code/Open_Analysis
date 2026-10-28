# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Eddy-current R(f), L_int(f): Bessel round wire, DC R = rho/A, HF surface-impedance asymptote."""

from __future__ import annotations

import _xs
import numpy as np
import pytest
from scipy.special import ive  # type: ignore[import-untyped,unused-ignore]

from oa_impedance import _physics_compat as phys
from oa_impedance.fem import FemParams, solve_fem, solve_loss, with_fem_loss
from oa_impedance.service import ImpedanceServiceImpl
from oa_model.xsection import CrossSection, XsCircle, XsConductor, XsPlane
from oa_testkit.golden import assert_close_to_golden

SUITE = "impedance_fem"
RADIUS, HEIGHT, SIGMA = 0.25e-3, 5e-3, 5.8e7


def wire_over_plane() -> CrossSection:
    """Round copper wire 20 radii over a near-ideal return sheet (isolates the wire's own loss)."""
    return CrossSection(
        conductors=(
            XsConductor(
                id="s1",
                role="signal",
                shape=XsCircle(u_center=0.0, z_center=HEIGHT, radius=RADIUS),
                conductivity=SIGMA,
            ),
            XsConductor(
                id="g1",
                role="ground",
                shape=XsPlane(z_bottom=0.0, thickness=0.0),
                conductivity=5.8e13,
            ),
        ),
        dielectrics=(),
        topology_hint="wire_over_plane",
        symmetric_about_u0=True,
    )


def bessel_zint(f: float) -> complex:
    """Internal impedance per metre of an isolated round wire: gamma/(2 pi a sigma) I0/I1."""
    g = np.sqrt(1j * 2 * np.pi * f * phys.MU0 * SIGMA)
    return complex(g / (2 * np.pi * RADIUS * SIGMA) * ive(0, g * RADIUS) / ive(1, g * RADIUS))


R_DC = 1.0 / (SIGMA * np.pi * RADIUS**2)
FREQS = [1e3, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10]


@pytest.mark.golden
@pytest.mark.slow
def test_round_wire_matches_bessel() -> None:
    R, L, _ = solve_loss(wire_over_plane(), np.array(FREQS))
    ratio = [float(R[i, 0, 0] / R_DC) for i in range(len(FREQS))]
    exact = [bessel_zint(f).real / R_DC for f in FREQS]
    assert_close_to_golden({"r_over_rdc": ratio}, SUITE, "round_wire_bessel")
    np.testing.assert_allclose(ratio, exact, rtol=0.02)  # 1 kHz .. 10 GHz
    # internal inductance: change of L(f) against the Bessel L_int(f) - L_int(f0)
    dl = [(L[i, 0, 0] - L[0, 0, 0]) for i in range(len(FREQS))]
    dl_b = [
        bessel_zint(f).imag / (2 * np.pi * f) - bessel_zint(FREQS[0]).imag / (2 * np.pi * FREQS[0])
        for f in FREQS
    ]
    np.testing.assert_allclose(dl, dl_b, atol=2e-9)  # 2 nH/m of 250 nH/m external


def test_dc_resistance_is_rho_over_area() -> None:
    # isolated conductor: single signal, near-ideal return
    r1, _, _ = solve_loss(wire_over_plane(), np.array([0.0]))
    assert float(r1[0, 0, 0]) == pytest.approx(R_DC, rel=2e-3)
    # strip between copper planes: loop R(DC) = signal rho/A plus the (larger-area) return
    xs = _strip_with_copper_planes()
    r, _, sol = solve_loss(xs, np.array([0.0]))
    strip = next(c for c in xs.conductors if c.id == "s1")
    area = strip.shape.w_bottom * strip.shape.thickness  # type: ignore[union-attr]
    assert float(r[0, 0, 0]) > 1.0 / (SIGMA * area)
    assert float(r[0, 0, 0]) < 1.2 / (SIGMA * area)
    assert sorted(sol.volume[0]) == ["g1", "g2", "s1"]


def _strip_with_copper_planes() -> CrossSection:
    xs = _xs.stripline(0.5, 1.0, t_over_b=35e-3)
    cond = tuple(
        c.model_copy(update={"shape": c.shape.model_copy(update={"thickness": 35e-6})})
        if isinstance(c.shape, XsPlane)
        else c
        for c in xs.conductors
    )
    return xs.model_copy(update={"conductors": cond})


@pytest.mark.slow
@pytest.mark.parametrize("f", [1e9, 5e9, 10e9])
def test_hf_asymptote_matches_surface_impedance_bem(f: float) -> None:
    """Stripline, 35 um copper: FEM R(f) within 5 % of the BEM perturbation where delta < t/4."""
    xs = _strip_with_copper_planes()
    assert phys.skin_depth(f, SIGMA) < 35e-6 / 4
    bem = ImpedanceServiceImpl().solve(xs, [f], method="bem")
    assert bem.rlgc is not None
    r_bem = float(bem.rlgc.R.array[0, 0, 0])
    r_fem = float(solve_loss(xs, np.array([f]))[0][0, 0, 0])
    assert r_fem == pytest.approx(r_bem, rel=0.05)


def test_fem_loss_replaces_bem_loss_and_is_flagged() -> None:
    xs = _xs.stripline(0.5, 1.0)
    bem = ImpedanceServiceImpl().solve(xs, [1e9], method="bem")
    new = with_fem_loss(bem, xs, FemParams())
    assert new.accuracy_class == "reference"
    assert any(f.code == "loss_model" for f in new.validity_flags)
    assert new.rlgc is not None and bem.rlgc is not None
    np.testing.assert_allclose(new.rlgc.C.array, bem.rlgc.C.array)  # C, G untouched
    assert new.rlgc.R.array[0, 0, 0] > 0


def test_solve_fem_returns_eddy_rlgc() -> None:
    r = solve_fem(wire_over_plane(), [1e8])
    assert r.method == "fem" and r.rlgc is not None
    assert float(r.rlgc.R.array[0, 0, 0]) == pytest.approx(bessel_zint(1e8).real, rel=0.03)
    assert any("eddy-current" in n for n in r.notes)
