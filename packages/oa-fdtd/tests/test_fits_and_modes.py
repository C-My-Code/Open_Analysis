# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Material fits, the 2-D mode solver and the gamma extraction (CPU only, fast)."""

from __future__ import annotations

import numpy as np

from oa_fdtd.grid import C0
from oa_fdtd.materials import fit_djordjevic_sarkar
from oa_fdtd.ports import gamma_from_planes, solve_modes
from oa_fdtd.subcell import fit_surface_impedance, slab_surface_impedance


def test_debye_fit_meets_adr_limits_over_0p1_to_40_ghz() -> None:
    """ADR-0010 section 5: eps' 0.2 %, tan d 2 % over the band, FR-4 and low-loss laminates."""
    for dk, df in [(4.3, 0.02), (3.8, 0.012), (3.5, 0.004), (3.0, 0.0015)]:
        fit = fit_djordjevic_sarkar(dk, df, 1e9, 0.1e9, 40e9)
        assert fit.err_eps <= 2e-3, (dk, df, fit.err_eps)
        assert fit.err_tand <= 2e-2, (dk, df, fit.err_tand)
        assert 3 <= len(fit.delta) <= 12 and np.all(fit.delta >= 0)  # passive by construction


def test_sibc_fit_accuracy() -> None:
    """The R-L ladder reproduces Re Zs of 35 um copper to 0.1 % (smooth) and 3 % (1 um rms)."""
    smooth = fit_surface_impedance(5.8e7, 35e-6, 1e8, 40e9)
    assert smooth.err < 1e-3 and smooth.r0 > 0 and np.all(smooth.rp >= 0)
    f = np.geomspace(1e8, 40e9, 50)
    zs = slab_surface_impedance(f, 5.8e7, 35e-6)
    assert np.abs(smooth.eval(f).imag / zs.imag - 1.0).max() < 2e-2  # reactance follows causally
    z_dc = slab_surface_impedance(np.array([1.0]), 5.8e7, 35e-6)[0].real
    assert abs(z_dc * 5.8e7 * 35e-6 - 1.0) < 1e-3  # DC limit 1 / (sigma t)
    rough = fit_surface_impedance(5.8e7, 35e-6, 1e8, 40e9, rms=1e-6)
    assert rough.err < 3e-2


def test_mode_solver_homogeneous_coupled_pair() -> None:
    """Two strips in a homogeneous dielectric: both modes have eps_eff = eps_r (exactly TEM)."""
    u = np.linspace(0, 6e-3, 61)
    v = np.linspace(0, 2e-3, 21)
    labels = -np.ones((61, 21), np.int64)
    labels[:, 0] = 0  # ground plane
    labels[:, -1] = 0  # second plane (stripline)
    labels[20:25, 10] = 1
    labels[35:40, 10] = 2
    ms = solve_modes(u, v, np.full((60, 20), 4.0), labels, 2)
    assert np.allclose(ms.eps_eff, 4.0, atol=1e-9)
    assert ms.z[0] > 0 and ms.z[1] > 0
    assert abs(ms.z[0] - ms.z[1]) / ms.z.max() > 0.003  # even and odd impedance differ


def test_gamma_from_unequal_planes() -> None:
    """Newton refinement for unequal spacings recovers gamma behind a reflecting termination."""
    f = np.linspace(2e9, 10e9, 9)
    g_true = 0.5 + 1j * 2 * np.pi * f * 2.0 / C0
    da, db = 1.0e-3, 1.1e-3
    refl = 0.3 * np.exp(1j * 0.7)

    def volt(z: float) -> np.ndarray:
        return np.exp(-g_true * z) + refl * np.exp(g_true * z)

    g = gamma_from_planes(volt(-da), volt(0.0), volt(db), da, db)
    assert np.abs(g - g_true).max() < 1e-6 * np.abs(g_true).max()
