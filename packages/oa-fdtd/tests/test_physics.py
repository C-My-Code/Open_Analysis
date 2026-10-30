# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ADR-0010 section 9 cases 4 and 5 analogues: ADE dielectrics and SIBC copper on a TEM line."""

from __future__ import annotations

import numpy as np
import pytest
from fdtd_phys_helpers import copper_line_s, dielectric_line_s, plate_line

from oa_fdtd.materials import fit_djordjevic_sarkar
from oa_fdtd.run import run, sparams
from oa_fdtd.subcell import hammerstad_jensen, slab_surface_impedance

ENGINES = ["numpy", pytest.param("cuda", marks=pytest.mark.gpu)]


def _db(x: np.ndarray) -> np.ndarray:
    return np.asarray(20 * np.log10(np.abs(x)))


@pytest.mark.parametrize("engine", ENGINES)
def test_debye_ds_line_matches_analytic(engine: str) -> None:
    """FR-4 (Djordjevic-Sarkar, 8-pole Debye ADE): S21 and S11 vs the analytic ABCD line."""
    fit = fit_djordjevic_sarkar(4.3, 0.02, 1e9, 0.1e9, 12e9)
    freqs = np.linspace(1e9, 10e9, 10)
    length, dy = 0.012, 0.25e-3
    m = plate_line(length, dy, freqs, 12e9, poles=fit.poles, eps_inf=fit.eps_inf)
    dtype = "float64" if engine == "numpy" else "float32"
    res = run(m, engine=engine, dtype=dtype, end_db=-60.0, max_steps=40000)
    s = sparams(m, res)
    r_port = m.ports[0].r_ohm
    s11, s21 = dielectric_line_s(freqs, fit.eval(freqs), length, r_port)
    assert np.abs(_db(s[1, 0]) - _db(s21)).max() < 0.02  # dB
    assert np.abs(np.angle(s[1, 0] / s21, deg=True)).max() < 1.0  # deg
    assert np.abs(s[0, 0] - s11).max() < 0.01


@pytest.mark.parametrize("engine", ENGINES)
def test_sibc_copper_attenuation(engine: str) -> None:
    """Plates of finite conductivity: S21 vs the line with the fitted slab impedance."""
    sigma, t = 2e5, 0.25e-3
    freqs = np.linspace(1e9, 10e9, 10)
    length, dy = 0.06, 0.25e-3
    m = plate_line(length, dy, freqs, 12e9, sigma_cu=sigma, t_cu=t)
    dtype = "float64" if engine == "numpy" else "float32"
    res = run(m, engine=engine, dtype=dtype, end_db=-60.0, max_steps=40000)
    s = sparams(m, res)
    r_port = m.ports[0].r_ohm
    zs = slab_surface_impedance(freqs, sigma, t)
    _, s21 = copper_line_s(freqs, zs, length, r_port)
    loss = _db(s21)
    assert loss.min() < -0.5  # the copper loss is visible
    assert np.abs(_db(s[1, 0]) - loss).max() < 0.02 * np.abs(loss).max()  # 2 % of the loss
    assert np.abs(np.angle(s[1, 0] / s21, deg=True)).max() < 1.0


def test_roughness_factor_limits() -> None:
    f = np.array([1e8, 1e10, 1e11])
    k = hammerstad_jensen(f, 5.8e7, 1e-6)
    assert k[0] < 1.03 and 1.9 < k[-1] <= 2.0
    assert np.all(hammerstad_jensen(f, 5.8e7, 0.0) == 1.0)
