# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dielectric models evaluated on a frequency grid and fitted by a single Debye pole.

openEMS time-steps Debye materials directly, but a wideband Djordjevic-Sarkar model has no
finite pole set. We fit ``eps(w) = eps_inf + d_eps / (1 + j w tau) + kappa / (j w eps0)`` (one
Debye pole plus a conductivity) to the model over the simulated band and report the fit error, so
the user knows what the single pole costs. Convention: ``exp(+j w t)``, ``eps = eps' - j eps''``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import nnls  # type: ignore[import-untyped,unused-ignore]

from oa_model.materials import (
    ConstantDkDf,
    DielectricModel,
    DjordjevicSarkar,
    MultiPoleDebye,
    TableDkDf,
)

EPS0 = 8.8541878128e-12
_TWO_PI = 2.0 * math.pi

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]


def eval_eps(model: DielectricModel, freqs: FloatArray) -> ComplexArray:
    """Complex relative permittivity ``eps' - j eps''`` of a dielectric model at ``freqs`` [Hz]."""
    f = np.asarray(freqs, dtype=np.float64)
    w = _TWO_PI * f
    if isinstance(model, ConstantDkDf):
        return np.full(f.shape, model.dk * (1.0 - 1j * model.df), dtype=np.complex128)
    if isinstance(model, DjordjevicSarkar):
        a1, a2 = _TWO_PI * model.f_low, _TWO_PI * model.f_high
        wr = _TWO_PI * model.f_ref
        span = math.atan(wr / a1) - math.atan(wr / a2)
        # c = d_eps / ((m2 - m1) ln 10), fixed by matching the loss tangent at f_ref
        c = model.dk * model.df / span
        re_ref = 0.5 * math.log((a2**2 + wr**2) / (a1**2 + wr**2))
        eps_inf = model.dk - c * re_ref
        loss = np.arctan(w / a1) - np.arctan(w / a2)
        real = eps_inf + c * 0.5 * np.log((a2**2 + w**2) / (a1**2 + w**2))
        return np.asarray(real - 1j * c * loss, dtype=np.complex128)
    if isinstance(model, MultiPoleDebye):
        eps = np.full(f.shape, model.eps_inf, dtype=np.complex128)
        for delta, f_relax in model.poles:
            eps = eps + delta / (1.0 + 1j * f / f_relax)
        if model.sigma_dc > 0:
            with np.errstate(divide="ignore"):
                eps = eps - 1j * model.sigma_dc / (np.maximum(w, 1e-30) * EPS0)
        return eps
    if isinstance(model, TableDkDf):
        tf = np.asarray(model.freqs.array, dtype=np.float64)
        dk = np.interp(np.log10(np.maximum(f, 1e-30)), np.log10(tf), model.dk.array)
        df = np.interp(np.log10(np.maximum(f, 1e-30)), np.log10(tf), model.df.array)
        return np.asarray(dk * (1.0 - 1j * df), dtype=np.complex128)
    raise TypeError(f"unsupported dielectric model {type(model).__name__}")


@dataclass(frozen=True)
class DebyeFit:
    """Single-pole Debye + conductivity fit and its error over the band."""

    eps_inf: float
    delta_eps: float
    tau: float  # relaxation time, s
    kappa: float  # conductivity, S/m
    f_lo: float
    f_hi: float
    max_rel_err_eps: float  # max |eps'_fit - eps'| / eps' over the band
    max_rel_err_tand: float  # max |tand_fit - tand| / tand over the upper half of the band
    max_abs_err_tand: float

    def eval(self, freqs: FloatArray) -> ComplexArray:
        w = _TWO_PI * np.asarray(freqs, dtype=np.float64)
        return np.asarray(
            self.eps_inf
            + self.delta_eps / (1.0 + 1j * w * self.tau)
            - 1j * self.kappa / (w * EPS0),
            dtype=np.complex128,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "eps_inf": self.eps_inf,
            "delta_eps": self.delta_eps,
            "tau_s": self.tau,
            "kappa_s_per_m": self.kappa,
            "f_lo_hz": self.f_lo,
            "f_hi_hz": self.f_hi,
            "max_rel_err_eps": self.max_rel_err_eps,
            "max_rel_err_tand": self.max_rel_err_tand,
            "max_abs_err_tand": self.max_abs_err_tand,
        }


def _solve_for_tau(
    tau: float, w: FloatArray, target: ComplexArray, w_re: float, w_im: float
) -> tuple[float, FloatArray]:
    d = 1.0 / (1.0 + (w * tau) ** 2)
    ones = np.ones_like(w)
    # unknowns: x0 = eps_inf - 1, x1 = delta_eps, x2 = kappa / eps0 [rad/s]
    a_re = np.column_stack([ones, d, np.zeros_like(w)]) * w_re
    a_im = np.column_stack([np.zeros_like(w), w * tau * d, 1.0 / w]) * w_im
    a = np.vstack([a_re, a_im])
    b = np.concatenate([(target.real - 1.0) * w_re, -target.imag * w_im])
    x, _ = nnls(a, b)
    resid = float(np.sum((a @ x - b) ** 2))
    return resid, x


def fit_debye(model: DielectricModel, f_lo: float, f_hi: float, n: int = 241) -> DebyeFit:
    """Fit one Debye pole plus conductivity to ``model`` on ``[f_lo, f_hi]``.

    The samples are linearly spaced, like the sweep: the fit is dominated by the upper decades of
    the band, where the loss of a lossy line accumulates.
    """
    if not 0.0 < f_lo < f_hi:
        raise ValueError("fit band needs 0 < f_lo < f_hi")
    f = np.linspace(f_lo, f_hi, n)
    w = _TWO_PI * f
    target = eval_eps(model, f)
    w_re = 2.0 / float(np.mean(target.real))
    w_im = 3.0 / max(float(np.mean(-target.imag)), 1e-12)
    lo = math.log10(1.0 / (_TWO_PI * f_hi * 30.0))
    hi = math.log10(1.0 / (_TWO_PI * f_lo * 0.03))
    best = (math.inf, 0.0, np.zeros(3))
    for _ in range(4):
        grid = np.linspace(lo, hi, 80)
        for lt in grid:
            tau = 10.0**lt
            resid, x = _solve_for_tau(tau, w, target, w_re, w_im)
            if resid < best[0]:
                best = (resid, lt, x)
        step = (hi - lo) / 79.0
        lo, hi = best[1] - 2 * step, best[1] + 2 * step
    tau = 10.0 ** best[1]
    x = best[2]
    fit = DebyeFit(
        eps_inf=1.0 + float(x[0]),
        delta_eps=float(x[1]),
        tau=tau,
        kappa=float(x[2]) * EPS0,
        f_lo=f_lo,
        f_hi=f_hi,
        max_rel_err_eps=0.0,
        max_rel_err_tand=0.0,
        max_abs_err_tand=0.0,
    )
    fe = np.linspace(f_lo, f_hi, 400)
    t = eval_eps(model, fe)
    m = fit.eval(fe)
    tand_t = -t.imag / t.real
    tand_m = -m.imag / m.real
    upper = fe >= 0.5 * (f_lo + f_hi)  # loss accumulates at the top of the band
    return DebyeFit(
        **{
            **fit.__dict__,
            "max_rel_err_eps": float(np.max(np.abs(m.real - t.real) / t.real)),
            "max_rel_err_tand": float(
                np.max((np.abs(tand_m - tand_t) / np.maximum(tand_t, 1e-12))[upper])
            ),
            "max_abs_err_tand": float(np.max(np.abs(tand_m - tand_t))),
        }
    )


def constant_equivalent(model: DielectricModel, f_ref: float) -> tuple[float, float]:
    """``(eps_r, kappa)`` reproducing eps' and the loss of ``model`` at ``f_ref`` only."""
    eps = complex(eval_eps(model, np.array([f_ref]))[0])
    return eps.real, float(-eps.imag * _TWO_PI * f_ref * EPS0)
