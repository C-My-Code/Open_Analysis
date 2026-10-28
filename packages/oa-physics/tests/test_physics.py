# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Golden and property tests for oa_physics (WP-106)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from oa_model.arrays import Floats
from oa_model.materials import (
    CannonballHuray,
    ConductorMaterial,
    ConstantDkDf,
    DjordjevicSarkar,
    Groisse,
    Hammerstad,
    Huray,
    MultiPoleDebye,
    NoRoughness,
    TableDkDf,
)
from oa_model.stackup import Etch
from oa_physics import (
    as_causal,
    ds_parameters,
    eps_complex,
    etch_widths,
    fit_ds,
    kk_residual,
    resistivity,
    roughness_factor,
    sheet_resistance,
    skin_depth,
    surface_impedance,
)
from oa_physics import via as V
from oa_testkit.golden import assert_close_to_golden

SIGMA = 5.8e7


def test_ds_roundtrip_golden() -> None:
    m = DjordjevicSarkar(dk=4.2, df=0.02, f_ref=1e9)
    e = eps_complex(m, [1e9])[0]
    assert_close_to_golden({"eps_re": e.real, "eps_im": e.imag}, "physics", "ds_roundtrip")


def test_ds_kk_consistency_and_constant_is_not_causal() -> None:
    f = np.logspace(3, 11, 200)
    assert kk_residual(f, eps_complex(DjordjevicSarkar(dk=4.0, df=0.02), f)) < 1e-3
    assert kk_residual(f, eps_complex(ConstantDkDf(dk=4.0, df=0.02), f)) > 1e-2


def test_ds_parameters_use_model_band() -> None:
    m = DjordjevicSarkar(dk=3.5, df=0.01)
    eps_inf, d_eps, m1, m2 = ds_parameters(m)
    assert m1 == pytest.approx(4.0) and m2 == pytest.approx(12.0)
    assert d_eps > 0 and 1.0 < eps_inf < 3.5


def test_fit_ds_recovers_model_and_as_causal() -> None:
    truth = DjordjevicSarkar(dk=3.8, df=0.015, f_ref=2e9)
    f = np.logspace(8, 10.5, 12)
    e = eps_complex(truth, f)
    fit = fit_ds(f, e.real, -e.imag / e.real)
    assert np.allclose(eps_complex(fit, f), e, rtol=1e-4)
    c = as_causal(ConstantDkDf(dk=4.0, df=0.02))
    assert isinstance(c, DjordjevicSarkar) and c.dk == 4.0
    t = as_causal(
        TableDkDf(
            freqs=Floats([1e8, 1e9, 1e10]),
            dk=Floats([4.1, 4.0, 3.9]),
            df=Floats([0.018, 0.02, 0.022]),
        )
    )
    assert isinstance(t, DjordjevicSarkar)
    mp = MultiPoleDebye(eps_inf=3.0, poles=((1.0, 1e9),))
    assert as_causal(mp) is mp


def test_debye_is_causal() -> None:
    f = np.logspace(3, 12, 200)
    e = eps_complex(MultiPoleDebye(eps_inf=3.0, poles=((1.0, 1e9), (0.5, 1e7))), f)
    assert kk_residual(f, e) < 1e-2


def test_skin_depth_golden() -> None:
    d = float(skin_depth(SIGMA, [1e9])[0])
    assert_close_to_golden({"delta_m": d}, "physics", "skin_depth_cu_1ghz")


def test_sheet_resistance_golden() -> None:
    cu = ConductorMaterial(name="cu")
    rs = sheet_resistance(cu, 35e-6, 293.15)
    assert_close_to_golden({"rs_ohm_sq": rs}, "physics", "sheet_resistance_1oz")
    assert float(resistivity(cu, 373.15)) > float(resistivity(cu, 293.15))


def test_roughness_saturation_golden() -> None:
    f = [1e16]
    k = {
        "k_hammerstad": float(roughness_factor(Hammerstad(rq=1e-6), f, SIGMA)[0]),
        "k_groisse": float(roughness_factor(Groisse(rq=1e-6), f, SIGMA)[0]),
    }
    assert_close_to_golden(k, "physics", "roughness_saturation")


def test_huray_golden_and_limits() -> None:
    r = Huray(sphere_radius=0.5e-6, surface_ratio=2.9)
    assert_close_to_golden(
        {"k": float(roughness_factor(r, [1e10], SIGMA)[0])}, "physics", "huray_formula"
    )
    lo, hi = roughness_factor(r, [1e3, 1e17], SIGMA)
    assert lo == pytest.approx(1.0, abs=1e-3)
    assert hi == pytest.approx(1.0 + 1.5 * 2.9, rel=1e-3)  # no saturation at 2
    cb = roughness_factor(CannonballHuray(rz=5e-6), [1e9, 1e10], SIGMA)
    assert np.all(cb > 1.0) and cb[1] > cb[0]


def test_causal_surface_impedance_matches_k_and_is_causal() -> None:
    f = np.logspace(7, 11, 41)
    rough = Hammerstad(rq=1.0e-6)
    zc = surface_impedance(SIGMA, f, rough, causal=True)
    zs = surface_impedance(SIGMA, f, NoRoughness(), causal=True)
    k = roughness_factor(rough, f, SIGMA)
    assert np.allclose(zc.real, k * zs.real, rtol=0.03)
    # roughness adds internal inductance: reactance grows along with resistance
    assert np.all(zc.imag > zs.imag * 0.99)
    nc = surface_impedance(SIGMA, f, rough, causal=False)
    assert np.allclose(nc, (1 + 1j) * k * zs.real)


def test_etch_widths() -> None:
    assert etch_widths(1e-4, 3e-5, None) == (1e-4, 1e-4)
    wb, wt = etch_widths(1e-4, 3e-5, Etch(kind="angle", value=math.pi / 4))
    assert wb == 1e-4 and wt == pytest.approx(1e-4 - 6e-5)
    wb, wt = etch_widths(1e-4, 3e-5, Etch(kind="top_width_delta", value=1e-5, reference="top"))
    assert wt == 1e-4 and wb == pytest.approx(1.1e-4)


def test_via_goldens() -> None:
    assert_close_to_golden(
        {"r_ohm": V.barrel_resistance(1.6e-3, 0.30e-3, 25e-6)}, "physics", "via_barrel_r"
    )
    assert_close_to_golden(
        {"l_h": V.via_pair_inductance(1e-3, 1e-3, 0.15e-3)}, "physics", "via_pair_l"
    )
    assert_close_to_golden({"f_hz": V.stub_resonance(1.5e-3, 4.0)}, "physics", "stub_resonance")


def test_via_pi_model_is_passive_and_reciprocal_with_stub_notch() -> None:
    est = V.estimate(
        height=1.6e-3,
        drill=0.3e-3,
        plating=25e-6,
        d_pad=0.5e-3,
        d_antipad=0.9e-3,
        eps_r=4.0,
        stub_length=1.5e-3,
    )
    assert est.stub_f0 == pytest.approx(24.98e9, rel=1e-3)
    f = np.linspace(1e8, 40e9, 400)
    s = V.pi_model_sparams(est, f)
    assert np.allclose(s[:, 0, 1], s[:, 1, 0])
    assert float(np.max(np.linalg.svd(s, compute_uv=False))) <= 1.0 + 1e-9
    assert est.stub_f0 is not None
    i = int(np.argmin(np.abs(f - est.stub_f0)))
    assert abs(s[i, 1, 0]) < 0.2  # resonance notch


def test_via_service_entry_point_factory() -> None:
    svc = V.make_service()
    assert svc.stub_resonance(1.5e-3, 4.0) == pytest.approx(24.98e9, rel=1e-3)
