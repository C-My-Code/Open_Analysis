# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finite-slab surface impedance and its passive pole fit (ADR-0010 section 3).

``Zs(w) = k / sigma * coth(k t)`` with ``k = (1 + j) / delta`` (convention ``exp(+j w t)``), times
a roughness factor ``K(f)``, is fitted by ``R0 + sum_p R_p j w tau_p / (1 + j w tau_p)``: a series
resistor plus parallel R-L sections with non-negative ``R_p``. Every section is passive, and its
bilinear discretisation is unconditionally stable. The time-domain update of one section is
``v_m = alpha (J_m - J_{m-1}) + gamma v_{m-1}`` (Beggs, Luebbers, Yee and Kunz, IEEE Trans.
Antennas Propag. 40(1), 1992 [verify pages], with a ladder fit instead of their exponential one).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import nnls  # type: ignore[import-untyped,unused-ignore]

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]
MU0 = 4e-7 * math.pi
TWO_PI = 2.0 * math.pi


def slab_surface_impedance(
    freqs: FloatArray, sigma: float, thickness: float, mu_r: float = 1.0
) -> ComplexArray:
    """``Zs`` of a slab of ``thickness`` [m] with its far side shorted [ohm/sq]; DC gives
    ``1 / (sigma t)``."""
    f = np.asarray(freqs, dtype=np.float64)
    w = TWO_PI * np.maximum(f, 1e-30)
    k = np.sqrt(1j * w * MU0 * mu_r * sigma)
    kt = k * thickness
    # coth(x) = 1 / tanh(x); tanh is safe for large Re(x)
    out = k / sigma / np.tanh(kt)
    return np.asarray(out, dtype=np.complex128)


def hammerstad_jensen(freqs: FloatArray, sigma: float, rms: float, mu_r: float = 1.0) -> FloatArray:
    """Hammerstad-Jensen roughness factor ``1 + 2/pi atan(1.4 (rms / delta)^2)`` [-]."""
    if rms <= 0:
        return np.ones_like(np.asarray(freqs, dtype=np.float64))
    w = TWO_PI * np.maximum(np.asarray(freqs, dtype=np.float64), 1e-30)
    delta = np.sqrt(2.0 / (w * MU0 * mu_r * sigma))
    return np.asarray(1.0 + (2.0 / math.pi) * np.arctan(1.4 * (rms / delta) ** 2))


@dataclass(frozen=True)
class SibcFit:
    """``r0`` [ohm], section resistances ``rp`` [ohm] and time constants ``tau`` [s]."""

    r0: float
    rp: FloatArray
    tau: FloatArray
    f_lo: float
    f_hi: float
    err: float  # max |Re Z fit - Re Z| / Re Z over the band [-]

    def eval(self, freqs: FloatArray) -> ComplexArray:
        w = TWO_PI * np.asarray(freqs, dtype=np.float64)
        z = np.full(w.shape, self.r0, dtype=np.complex128)
        for r, t in zip(self.rp, self.tau, strict=True):
            jwt = 1j * w * t
            z = z + r * jwt / (1.0 + jwt)
        return z

    def recursion(self, dt: float) -> tuple[FloatArray, FloatArray]:
        """Bilinear coefficients ``(c_p, gamma_p)``: ``alpha_p = R_p c_p``."""
        x = 2.0 * self.tau / dt
        return x / (1.0 + x), (x - 1.0) / (x + 1.0)


def fit_surface_impedance(
    sigma: float,
    thickness: float,
    f_lo: float,
    f_hi: float,
    *,
    rms: float = 0.0,
    roughness: Callable[[FloatArray], FloatArray] | None = None,
    n_poles: int = 16,
    mu_r: float = 1.0,
) -> SibcFit:
    """Passive fit of ``Zs(f) * K(f)`` over ``[f_lo, f_hi]`` with ``n_poles`` sections.

    ``roughness`` overrides the Hammerstad-Jensen factor of ``rms`` (for example the
    ``oa_physics.roughness_factor`` of a Huray model).
    """
    if not 0 < f_lo < f_hi:
        raise ValueError("fit band needs 0 < f_lo < f_hi")
    f = np.geomspace(f_lo / 5.0, f_hi, 240)  # a little below the band keeps its low edge accurate
    f = np.concatenate([[f_lo * 1e-3, f_lo * 1e-2], f])  # pin the low end to R_dc
    k = roughness(f) if roughness is not None else hammerstad_jensen(f, sigma, rms, mu_r)
    z = slab_surface_impedance(f, sigma, thickness, mu_r) * k
    w = TWO_PI * f
    f_p = np.geomspace(f_lo * 0.1, f_hi * 5.0, n_poles)
    tau = 1.0 / (TWO_PI * f_p)
    wt = w[:, None] * tau[None, :]
    d = 1.0 / (1.0 + wt**2)
    re = np.column_stack([np.ones_like(w), wt**2 * d])
    im = np.column_stack([np.zeros_like(w), wt * d])
    # The loss (real part) leads: a real roughness factor on both parts is not causal, and the
    # ladder supplies the minimum-phase reactance for whatever loss it fits.
    wgt = 1.0 / np.abs(z)
    a = np.vstack([re * wgt[:, None], 0.2 * im * wgt[:, None]])
    b = np.concatenate([z.real * wgt, 0.2 * z.imag * wgt])
    x, _ = nnls(a, b, maxiter=100 * a.shape[1])
    fit = SibcFit(float(x[0]), x[1:].copy(), tau, f_lo, f_hi, 0.0)
    band = (f >= f_lo * (1 - 1e-9)) & (f <= f_hi * (1 + 1e-9))
    err = float(np.max(np.abs(fit.eval(f[band]).real - z[band].real) / z[band].real))
    return SibcFit(fit.r0, fit.rp, fit.tau, f_lo, f_hi, err)
