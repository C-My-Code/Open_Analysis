# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Closed forms against scikit-rf media (BSD-3; test-only dependency): 20 cases, <= 0.1 %."""

from __future__ import annotations

import numpy as np
import pytest

from oa_impedance import closed_form as cf

skrf = pytest.importorskip("skrf")
from skrf.media import CPW, MLine  # type: ignore[import-untyped,unused-ignore]  # noqa: E402

TOL = 1e-3  # 0.1 %

MICROSTRIP_CASES = [  # (u = W/h, er, t/h, f [Hz])
    (0.3, 2.2, 0.0, 1e9),
    (0.5, 4.3, 0.0, 1e9),
    (1.0, 4.3, 0.175, 2e9),
    (1.9, 4.3, 0.175, 5e9),
    (2.0, 9.8, 0.0, 1e9),
    (2.0, 10.2, 0.05, 10e9),
    (4.0, 4.3, 0.175, 3e9),
    (0.8, 3.38, 0.1, 20e9),
    (1.5, 6.15, 0.1, 8e9),
    (3.0, 3.66, 0.0, 15e9),
]


@pytest.mark.parametrize(("u", "er", "th", "f"), MICROSTRIP_CASES)
def test_microstrip_hj_kj_vs_skrf_mline(u: float, er: float, th: float, f: float) -> None:
    h = 0.2e-3
    freq = skrf.Frequency.from_f(np.array([f]), unit="hz")
    m = MLine(freq, w=u * h, h=h, t=th * h, ep_r=er, tand=0.0, rho=1e-8)
    res = cf.microstrip_hj(u * h, h, th * h, er, f)
    z_ref = float(np.real(m.z0_characteristic[0]))
    e_ref = float(np.real(m.ep_reff_f[0]))
    assert res.z0 == pytest.approx(z_ref, rel=TOL)
    assert res.eeff == pytest.approx(e_ref, rel=TOL)


CPW_CASES = [  # (S, G, er, h, t, backside)  [mm]
    (1.0, 0.5, 9.8, 5.0, 0.0, False),
    (0.3, 0.15, 4.3, 1.6, 0.0, False),
    (0.2, 0.2, 4.3, 0.8, 0.035, False),
    (0.5, 0.3, 3.55, 1.0, 0.0, False),
    (0.4, 0.2, 10.2, 2.0, 0.018, False),
    (0.3, 0.2, 4.3, 1.6, 0.0, True),
    (0.5, 0.25, 4.3, 0.8, 0.035, True),
    (0.2, 0.15, 3.38, 0.5, 0.0, True),
    (0.6, 0.4, 2.2, 1.0, 0.0, True),
    (0.25, 0.25, 6.15, 0.64, 0.018, True),
]


@pytest.mark.parametrize(("s", "g", "er", "h", "t", "back"), CPW_CASES)
def test_cpw_cpwg_vs_skrf_cpw(
    s: float, g: float, er: float, h: float, t: float, back: bool
) -> None:
    # skrf adds Frankel/Gevorgian dispersion (negligible at 300 MHz) and warns unless t > 3 delta
    f = 3e8
    freq = skrf.Frequency.from_f(np.array([f]), unit="hz")
    mm = 1e-3
    c = CPW(
        freq,
        w=s * mm,
        s=g * mm,
        h=h * mm,
        t=t * mm if t else None,
        ep_r=er,
        tand=0.0,
        rho=1e-8,
        has_metal_backside=back,
    )
    res = cf.cpw(s * mm, g * mm, t * mm, er, h * mm, backside=back)
    assert res.eeff == pytest.approx(float(np.real(c.ep_reff[0])), rel=TOL)
    # skrf uses eta0 = sqrt(mu0/eps0); the CPW literature (and our golden) uses 120 pi (+0.07 %)
    assert res.z0 == pytest.approx(float(np.real(c.zl_eff[0])), rel=TOL)
