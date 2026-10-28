# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python reference formulas against independent known values."""

from __future__ import annotations

import math

import pytest

from oa_testkit import analytic as an


def test_eta0_matches_codata() -> None:
    assert pytest.approx(376.730313668, rel=1e-10) == an.ETA0


@pytest.mark.parametrize("k", [0.01, 0.3, 0.7, 0.999])
def test_ellipk_against_series_identity(k: float) -> None:
    # Legendre-free check: K(k) = pi/2 * sum ((2n)!/(2^2n n!^2))^2 k^2n
    s, term = 0.0, 1.0
    for n in range(0, 20000):
        s += term * k ** (2 * n)
        term *= ((2 * n + 1) / (2 * n + 2)) ** 2
        if term * k ** (2 * n + 2) < 1e-18 and n > 10:
            break
    assert an.ellipk(k) == pytest.approx(math.pi / 2 * s, rel=1e-9)


def test_ellipk_matches_scipy_when_available() -> None:
    sp = pytest.importorskip("scipy.special")
    for k in (0.1, 0.5, 0.9, 0.99):
        assert an.ellipk(k) == pytest.approx(sp.ellipk(k * k), rel=1e-13)


def test_coax_ln_b_over_a_is_one() -> None:
    assert an.coax_z0(math.e) == pytest.approx(59.958, abs=5e-4)  # docs/research/02 sec 4.1
    assert an.coax_z0(math.e, er=2.25) == pytest.approx(an.coax_z0(math.e) / 1.5, rel=1e-12)


def test_wire_and_two_wire() -> None:
    # two wires at D with diameter d == 2x one wire at h = D/2 over a plane, radius d/2
    d, big_d = 1.0, 2.5
    assert an.two_wire_z0(big_d / d) == pytest.approx(
        2 * an.wire_over_plane_z0((big_d / 2) / (d / 2)), rel=1e-12
    )


@pytest.mark.parametrize(
    ("w_over_b", "z0"),
    [(0.1, 194.23), (0.25, 139.92), (0.5, 100.43), (1.0, 65.35), (2.0, 38.58)],
)
def test_cohn_stripline_matches_research_seed(w_over_b: float, z0: float) -> None:
    assert an.cohn_stripline_z0(w_over_b) == pytest.approx(z0, abs=6e-3)


@pytest.mark.parametrize(
    ("s_over_b", "z0e", "z0o"),
    [(0.1, 122.89, 69.87), (0.25, 114.77, 83.52), (0.5, 107.15, 93.22)],
)
def test_cohn_edge_coupled_matches_research_seed(s_over_b: float, z0e: float, z0o: float) -> None:
    r = an.cohn_edge_coupled_stripline(0.5, s_over_b)
    assert r["z0e"] == pytest.approx(z0e, abs=6e-3)
    assert r["z0o"] == pytest.approx(z0o, abs=6e-3)
    assert r["zdiff"] == pytest.approx(2 * r["z0o"])
    assert r["zcomm"] == pytest.approx(r["z0e"] / 2)


def test_edge_coupled_converges_to_single_line() -> None:
    r = an.cohn_edge_coupled_stripline(0.5, 50.0)
    single = an.cohn_stripline_z0(0.5)
    assert r["z0e"] == pytest.approx(single, rel=1e-6)
    assert r["z0o"] == pytest.approx(single, rel=1e-6)


def test_wheeler_thin_limit_close_to_cohn() -> None:
    assert an.wheeler_stripline_z0(0.5, 1e-4) == pytest.approx(an.cohn_stripline_z0(0.5), rel=2e-3)
    assert an.wheeler_stripline_z0(0.5, 0.0) == an.cohn_stripline_z0(0.5)


def test_cpw_infinite_substrate_seed() -> None:
    assert an.cpw_z0_eeff(1.0, 0.5, 1.0)[0] == pytest.approx(120.57, abs=5e-3)
    z0, eeff = an.cpw_z0_eeff(1.0, 0.5, 9.8)
    assert z0 == pytest.approx(51.88, abs=5e-3)
    assert eeff == pytest.approx(5.4)


def test_cpw_thick_substrate_approaches_half_space() -> None:
    z0_inf, _ = an.cpw_z0_eeff(1.0, 0.5, 9.8)
    z0_h, eeff = an.cpw_z0_eeff(1.0, 0.5, 9.8, h=200.0)
    assert z0_h == pytest.approx(z0_inf, rel=1e-4)
    assert eeff == pytest.approx(5.4, rel=1e-4)


@pytest.mark.parametrize(
    ("u", "er", "z0", "eeff"),
    [
        (0.5, 1.0, 166.700, 1.0000),
        (0.5, 4.3, 96.404, 2.9901),
        (1.0, 4.3, 71.751, 3.1045),
        (1.9, 4.3, 50.739, 3.2612),
        (2.0, 4.3, 49.192, 3.2755),
        (2.0, 10.2, 32.936, 7.3067),
        (4.0, 4.3, 30.911, 3.4869),
    ],
)
def test_hj_matches_research_table(u: float, er: float, z0: float, eeff: float) -> None:
    got_z0, got_eeff = an.hammerstad_jensen(u, er)
    assert got_z0 == pytest.approx(z0, abs=6e-4)
    assert got_eeff == pytest.approx(eeff, abs=6e-5)


def test_hj_thickness_lowers_impedance() -> None:
    z_thin, _ = an.hammerstad_jensen(2.0, 4.3)
    z_thick, _ = an.hammerstad_jensen(2.0, 4.3, t_over_h=0.1)
    assert z_thick < z_thin
