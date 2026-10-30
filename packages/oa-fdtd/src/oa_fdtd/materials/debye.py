# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-pole Debye fits of a measured or analytic permittivity (ADR-0010 section 5).

Convention ``exp(+j w t)``: ``eps = eps' - j eps''``. A fit is
``eps_inf + sum_k d_k / (1 + j w t_k)`` with every ``d_k >= 0`` (non-negative weights on a fixed
log-spaced pole set, solved by non-negative least squares), so the model is causal and passive by
construction. The pole count
grows until the error limits of ADR-0010 section 5 hold over the band (permittivity 0.2 %, loss
tangent 2 %).
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
TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class DebyeFit:
    """``eps_inf`` [-], pole strengths ``delta`` [-] and relaxation times ``tau`` [s]."""

    eps_inf: float
    delta: FloatArray
    tau: FloatArray
    f_lo: float  # [Hz] fit band
    f_hi: float  # [Hz]
    err_eps: float  # max |eps' fit - eps'| / eps' over the band [-]
    err_tand: float  # max |tand fit - tand| / tand over the band [-]

    @property
    def poles(self) -> tuple[tuple[float, float], ...]:
        """``(delta, tau)`` pairs for :class:`oa_fdtd.model.Region`."""
        return tuple((float(d), float(t)) for d, t in zip(self.delta, self.tau, strict=True))

    def eval(self, freqs: FloatArray) -> ComplexArray:
        w = TWO_PI * np.asarray(freqs, dtype=np.float64)
        out = np.full(w.shape, self.eps_inf, dtype=np.complex128)
        for d, t in zip(self.delta, self.tau, strict=True):
            out = out + d / (1.0 + 1j * w * t)
        return out


def djordjevic_sarkar_eps(
    freqs: FloatArray, dk: float, df: float, f_ref: float, f_low: float, f_high: float
) -> ComplexArray:
    """Djordjevic-Sarkar permittivity matched to ``dk`` and ``df`` at ``f_ref`` [Hz].

    Same closed form as ``oa_physics.dielectric`` (Djordjevic et al., IEEE Trans. EMC 43(4), 2001).
    """
    a1, a2 = TWO_PI * f_low, TWO_PI * f_high
    wr = TWO_PI * f_ref
    span = math.atan(wr / a1) - math.atan(wr / a2)
    c = dk * df / span
    eps_inf = dk - c * 0.5 * math.log((a2**2 + wr**2) / (a1**2 + wr**2))
    w = TWO_PI * np.asarray(freqs, dtype=np.float64)
    real = eps_inf + c * 0.5 * np.log((a2**2 + w**2) / (a1**2 + w**2))
    loss = np.arctan(w / a1) - np.arctan(w / a2)
    return np.asarray(real - 1j * c * loss, dtype=np.complex128)


def _design(w: FloatArray, tau: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Real and imaginary design matrices for unknowns ``[eps_inf - 1, d_1 .. d_N]``."""
    wt = w[:, None] * tau[None, :]
    d = 1.0 / (1.0 + wt**2)
    re = np.column_stack([np.ones_like(w), d])
    im = np.column_stack([np.zeros_like(w), wt * d])  # eps'' = sum d wt / (1 + wt^2)
    return re, im


def fit_debye(
    freqs: FloatArray,
    eps: ComplexArray,
    f_lo: float,
    f_hi: float,
    *,
    tol_eps: float = 2e-3,
    tol_tand: float = 2e-2,
    max_poles: int = 12,
    min_poles: int = 3,
    margin_decades: float = 1.0,
) -> DebyeFit:
    """Fit ``eps`` (``eps' - j eps''``) sampled at ``freqs`` with the fewest Debye poles that meet
    the tolerances over ``[f_lo, f_hi]``; the best fit with ``max_poles`` otherwise.

    Poles are log-spaced over the band widened by ``margin_decades`` on each side.
    """
    f = np.asarray(freqs, dtype=np.float64)
    eps = np.asarray(eps, dtype=np.complex128)
    sel = (f >= f_lo * (1 - 1e-9)) & (f <= f_hi * (1 + 1e-9))
    f, eps = f[sel], eps[sel]
    if f.size < 4 or not 0 < f_lo < f_hi:
        raise ValueError("fit needs 0 < f_lo < f_hi and at least four samples in the band")
    w = TWO_PI * f
    tand = -eps.imag / eps.real
    best: DebyeFit | None = None
    for n in range(min_poles, max_poles + 1):
        f_p = np.geomspace(f_lo * 10**-margin_decades, f_hi * 10**margin_decades, n)
        tau = 1.0 / (TWO_PI * f_p)
        re, im = _design(w, tau)
        # both errors in units of eps'; a 2 % tand error weighs like a 0.2 % eps' error
        w_re = 1.0 / eps.real
        w_im = (tol_eps / tol_tand) / eps.real
        a = np.vstack([re * w_re[:, None], im * w_im[:, None]])
        b = np.concatenate([(eps.real - 1.0) * w_re, -eps.imag * w_im])
        x, _ = nnls(a, b, maxiter=50 * a.shape[1])
        fit = DebyeFit(1.0 + float(x[0]), x[1:].copy(), tau, f_lo, f_hi, 0.0, 0.0)
        ev = fit.eval(f)
        err_eps = float(np.max(np.abs(ev.real - eps.real) / eps.real))
        err_tand = float(np.max(np.abs(-ev.imag / ev.real - tand) / np.maximum(tand, 1e-12)))
        fit = DebyeFit(fit.eps_inf, fit.delta, fit.tau, f_lo, f_hi, err_eps, err_tand)
        if best is None or (err_eps / tol_eps + err_tand / tol_tand) < (
            best.err_eps / tol_eps + best.err_tand / tol_tand
        ):
            best = fit
        if err_eps <= tol_eps and err_tand <= tol_tand:
            return fit
    assert best is not None
    return best


def fit_djordjevic_sarkar(
    dk: float,
    df: float,
    f_ref: float,
    f_lo: float,
    f_hi: float,
    f_low: float = 1e4 / TWO_PI,
    f_high: float = 1e12 / TWO_PI,
    n_samples: int = 400,
) -> DebyeFit:
    """Multi-pole Debye fit of a Djordjevic-Sarkar dielectric over ``[f_lo, f_hi]`` [Hz]."""
    f = np.geomspace(f_lo, f_hi, n_samples)
    eps = djordjevic_sarkar_eps(f, dk, df, f_ref, f_low, f_high)
    return fit_debye(f, eps, f_lo, f_hi)


def fit_callable(
    fn: Callable[[FloatArray], ComplexArray], f_lo: float, f_hi: float, n_samples: int = 400
) -> DebyeFit:
    """Fit any ``eps(f)`` callable (for example a table interpolant) over the band."""
    f = np.geomspace(f_lo, f_hi, n_samples)
    return fit_debye(f, fn(f), f_lo, f_hi)
