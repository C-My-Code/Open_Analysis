# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic 2-port line generator used for the committed ``network_quality`` fixtures."""

from __future__ import annotations

import numpy as np
import skrf

from oa_model.materials import ConstantDkDf, DjordjevicSarkar
from oa_physics import eps_complex

MU0 = 4e-7 * np.pi
C0 = 299_792_458.0
EPS0 = 1.0 / (MU0 * C0**2)


def line_network(
    freqs: np.ndarray,
    *,
    length: float = 0.03,
    z0: float = 50.0,
    dk: float = 4.0,
    df: float = 0.02,
    causal: bool = True,
    r0_per_m: float = 68.0,
    r1_per_m: float = 8.0,
    name: str = "line",
) -> skrf.Network:
    """Matched-reference 2-port of a lossy line: ABCD with Zc = sqrt(Z/Y), gamma = sqrt(ZY).

    Dielectric: Djordjevic-Sarkar (causal) or constant Dk/Df. Conductor: ``r0 + r1 sqrt(j f/1GHz)``
    per metre (DC resistance plus a causal skin-effect term).
    """
    f = np.asarray(freqs, dtype=float)
    w = 2 * np.pi * f
    model = DjordjevicSarkar(dk=dk, df=df) if causal else ConstantDkDf(dk=dk, df=df)
    eps = eps_complex(model, np.maximum(f, 1.0)) if f[0] == 0 else eps_complex(model, f)
    if f[0] == 0:
        eps[0] = eps[1].real  # DC: lossless
    g = np.sqrt(MU0 / EPS0) / (z0 * np.sqrt(dk))  # geometry factor so Zc = z0 at eps = dk
    ell = MU0 / g
    cap = EPS0 * g * eps
    z_ser = 1j * w * ell + r0_per_m + r1_per_m * np.sqrt(1j * f / 1e9)
    y_sh = 1j * w * cap + r0_per_m / z0**2  # distortionless at DC: G0 = R0 / Z0^2
    y_safe = np.where(y_sh == 0, 1e-30, y_sh)
    zc = np.sqrt(z_ser / y_safe)
    gam = np.sqrt(z_ser * y_sh)
    gl = gam * length
    a = np.cosh(gl)
    b = zc * np.sinh(gl)
    c = np.sinh(gl) / zc
    d = a
    # at DC the ABCD of a resistance R*length
    if f[0] == 0:
        gam0 = np.sqrt(r0_per_m * r0_per_m / z0**2) * length
        a[0] = d[0] = np.cosh(gam0)
        b[0] = z0 * np.sinh(gam0)
        c[0] = np.sinh(gam0) / z0
    den = a + b / z0 + c * z0 + d
    s = np.zeros((f.size, 2, 2), dtype=complex)
    s[:, 0, 0] = (a + b / z0 - c * z0 - d) / den
    s[:, 0, 1] = 2 * (a * d - b * c) / den
    s[:, 1, 0] = 2 / den
    s[:, 1, 1] = (-a + b / z0 - c * z0 + d) / den
    return skrf.Network(frequency=skrf.Frequency.from_f(f, unit="hz"), s=s, z0=z0, name=name)
