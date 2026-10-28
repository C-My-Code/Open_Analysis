# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Causal dielectric models (interfaces.md section 10; research 02 section 2.4).

Sign convention: ``eps = eps' - j eps''`` (engineering, ``exp(+j w t)``), so a lossy medium has a
negative imaginary part and ``eps(f_ref) = Dk (1 - j Df)``.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.optimize import least_squares  # type: ignore[import-untyped]

from oa_model.materials import (
    ConstantDkDf,
    DielectricModel,
    DjordjevicSarkar,
    MultiPoleDebye,
    TableDkDf,
)
from oa_physics.constants import EPS0

CArray = NDArray[np.complex128]
FArray = NDArray[np.float64]

__all__ = ["as_causal", "ds_parameters", "eps_complex", "fit_ds", "kk_residual"]


def _lg(w: FArray, m1: float, m2: float) -> CArray:
    """``log10((w2 + j w) / (w1 + j w))``."""
    w1 = 10.0**m1
    w2 = 10.0**m2
    return np.asarray(np.log10((w2 + 1j * w) / (w1 + 1j * w)), dtype=np.complex128)


def _ds_m(m: DjordjevicSarkar) -> tuple[float, float]:
    return math.log10(2.0 * math.pi * m.f_low), math.log10(2.0 * math.pi * m.f_high)


def ds_parameters(m: DjordjevicSarkar) -> tuple[float, float, float, float]:
    """Return ``(eps_inf, d_eps, m1, m2)`` such that ``eps(f_ref) = Dk (1 - j Df)`` exactly."""
    m1, m2 = _ds_m(m)
    lg0 = _lg(np.array([2.0 * math.pi * m.f_ref]), m1, m2)[0]
    d_eps = -m.dk * m.df * (m2 - m1) / lg0.imag
    eps_inf = m.dk - d_eps / (m2 - m1) * lg0.real
    return float(eps_inf), float(d_eps), m1, m2


def eps_complex(model: DielectricModel, freqs: ArrayLike) -> CArray:
    """Complex relative permittivity ``eps' - j eps''`` at ``freqs`` [Hz]."""
    f = np.atleast_1d(np.asarray(freqs, dtype=float))
    if isinstance(model, ConstantDkDf):
        return np.full(f.shape, model.dk * (1.0 - 1j * model.df), dtype=np.complex128)
    if isinstance(model, DjordjevicSarkar):
        eps_inf, d_eps, m1, m2 = ds_parameters(model)
        return np.asarray(
            eps_inf + d_eps / (m2 - m1) * _lg(2.0 * math.pi * f, m1, m2), dtype=np.complex128
        )
    if isinstance(model, MultiPoleDebye):
        eps = np.full(f.shape, model.eps_inf, dtype=np.complex128)
        for d_eps_k, f_k in model.poles:
            eps = eps + d_eps_k / (1.0 + 1j * f / f_k)
        if model.sigma_dc > 0.0:
            with np.errstate(divide="ignore"):
                eps = eps - 1j * model.sigma_dc / (2.0 * math.pi * f * EPS0)
        return eps
    if isinstance(model, TableDkDf):
        lf = np.log(model.freqs.array)
        x = np.log(np.maximum(f, 1e-30))
        dk = np.interp(x, lf, model.dk.array)
        df = np.interp(x, lf, model.df.array)
        return np.asarray(dk * (1.0 - 1j * df), dtype=np.complex128)
    raise TypeError(f"unsupported dielectric model {type(model).__name__}")


def fit_ds(
    freqs: ArrayLike,
    dk: ArrayLike,
    df: ArrayLike,
    *,
    m1: float = 4.0,
    m2: float = 12.0,
) -> DjordjevicSarkar:
    """Least-squares Djordjevic-Sarkar model for tabulated ``Dk(f)`` and ``Df(f)``.

    One point is reproduced exactly (``eps(f) = Dk (1 - j Df)``). With several points the single
    reference point ``(dk, df)`` at the point nearest the geometric-mean frequency is chosen to
    minimise the complex permittivity misfit over all points. ``m1``/``m2`` fix the transition band
    (``w = 10**m`` rad/s).
    """
    f = np.atleast_1d(np.asarray(freqs, dtype=float))
    k = np.atleast_1d(np.asarray(dk, dtype=float))
    d = np.atleast_1d(np.asarray(df, dtype=float))
    if not (f.shape == k.shape == d.shape) or f.size == 0:
        raise ValueError("freqs, dk and df must be non-empty and of equal length")
    f_low = 10.0**m1 / (2.0 * math.pi)
    f_high = 10.0**m2 / (2.0 * math.pi)
    i0 = int(np.argmin(np.abs(np.log(f) - np.mean(np.log(f)))))
    f_ref = float(f[i0])

    def build(p: FArray) -> DjordjevicSarkar:
        return DjordjevicSarkar(
            dk=max(float(p[0]), 1.0),
            df=min(max(float(p[1]), 0.0), 0.999),
            f_ref=f_ref,
            f_low=f_low,
            f_high=f_high,
        )

    if f.size == 1:
        return build(np.array([k[0], d[0]]))
    target = k * (1.0 - 1j * d)

    def resid(p: FArray) -> FArray:
        e = eps_complex(build(p), f)
        r = (e - target) / k
        return np.concatenate([r.real, r.imag])

    sol = least_squares(resid, np.array([k[i0], max(d[i0], 1e-6)]), x_scale=[1.0, 0.01])
    return build(sol.x)


def as_causal(model: DielectricModel) -> DielectricModel:
    """Return a causal equivalent: Constant -> DS, Table -> DS fit. Others are already causal."""
    if isinstance(model, ConstantDkDf):
        return DjordjevicSarkar(dk=model.dk, df=model.df, f_ref=model.f_ref)
    if isinstance(model, TableDkDf):
        return fit_ds(model.freqs.array, model.dk.array, model.df.array)
    return model


def kk_residual(freqs: ArrayLike, eps: ArrayLike, *, decades: float = 8.0) -> float:
    """Kramers-Kronig consistency of ``eps(f) = eps' - j eps''`` sampled at ``freqs``.

    ``eps'`` is reconstructed from ``eps''`` with the subtractive-constant KK integral
    ``eps'(w) = eps_inf + (2/pi) PV int w' eps''(w') / (w'^2 - w^2) dw'`` (Maclaurin
    alternate-point principal value on a uniform ``ln w`` grid). ``eps''`` is interpolated
    log-log and extended ``decades`` beyond the band by power-law tails taken from the end
    slopes (rising below, falling above). The unknown ``eps_inf`` is the mean offset. Returns the
    maximum ``|eps'_data - eps'_KK - offset|`` divided by the mean ``eps'`` (0 = causal).
    """
    f = np.asarray(freqs, dtype=float)
    e = np.asarray(eps, dtype=complex)
    order = np.argsort(f)
    f, e = f[order], e[order]
    epp = np.maximum(-e.imag, 1e-300)
    ln_f = np.log(f)
    ln10 = math.log(10.0)
    u0 = ln_f[0] - decades * ln10
    u1 = ln_f[-1] + decades * ln10
    n = round((u1 - u0) / ln10 * 40) + 1
    u = np.linspace(u0, u1, n)
    du = float(u[1] - u[0])
    lq = np.log(epp)
    ly = np.interp(u, ln_f, lq)
    if f.size >= 2:
        lo = (lq[1] - lq[0]) / (ln_f[1] - ln_f[0])
        hi = (lq[-1] - lq[-2]) / (ln_f[-1] - ln_f[-2])
        below = u < ln_f[0]
        above = u > ln_f[-1]
        ly[below] = lq[0] + max(lo, 0.0) * (u[below] - ln_f[0])
        ly[above] = lq[-1] + min(hi, 0.0) * (u[above] - ln_f[-1])
    w2 = np.exp(2.0 * u)
    epp_g = np.exp(ly)
    # Maclaurin rule: integrand (2/pi) w'^2 eps'' / (w'^2 - w^2) in u = ln w', summing only the
    # grid points of opposite parity to the evaluation point (the singularity is skipped).
    kk_grid = np.empty(n)
    idx = np.arange(n)
    for i in range(n):
        sel = idx[(idx % 2) != (i % 2)]
        kk_grid[i] = (2.0 / math.pi) * 2.0 * du * np.sum(w2[sel] * epp_g[sel] / (w2[sel] - w2[i]))
    kk = np.interp(ln_f, u, kk_grid)
    offset = float(np.mean(e.real - kk))
    resid = np.abs(e.real - kk - offset)
    return float(np.max(resid) / np.mean(e.real))
