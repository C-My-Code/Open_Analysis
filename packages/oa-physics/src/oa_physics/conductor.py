# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Conductor physics: skin effect, surface impedance, roughness, rho(T), etch (interfaces.md 10).

References: research 02 sections 2.2-2.3. Hammerstad-Bekkadal 1975; Groisse 1996; Huray et al.
DesignCon 2010; Simonovich DesignCon 2015 (cannonball); causal surface impedance after Bracken
DesignCon 2012 (K(f) applied to Zs and fitted by first-order sections).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from numpy.typing import ArrayLike, NDArray

from oa_model.materials import (
    CannonballHuray,
    ConductorMaterial,
    Groisse,
    Hammerstad,
    Huray,
    NoRoughness,
    Roughness,
)
from oa_model.stackup import Etch
from oa_physics.constants import MU0

CArray = NDArray[np.complex128]
FArray = NDArray[np.float64]

__all__ = [
    "cannonball_huray_parameters",
    "etch_widths",
    "resistivity",
    "roughness_factor",
    "sheet_resistance",
    "skin_depth",
    "surface_impedance",
]

#: Cannonball stack: 14 equal spheres in a 3-layer square pyramid (9 + 4 + 1).
CANNONBALL_SPHERES = 14
#: Stack height = (2 + 2 sqrt 2) r.
CANNONBALL_HEIGHT_FACTOR = 2.0 + 2.0 * math.sqrt(2.0)
#: Flat tile area under one stack, in units of r^2: the 3x3 base layer footprint (6 r)^2.
#: [verify] against Simonovich DesignCon 2015, whose hexagonal-tile area is not reproduced here.
CANNONBALL_TILE_AREA_FACTOR = 36.0


def skin_depth(sigma: float, freqs: ArrayLike, mu_r: float = 1.0) -> FArray:
    """``delta = 1 / sqrt(pi f mu sigma)`` [m]."""
    f = np.atleast_1d(np.asarray(freqs, dtype=float))
    return np.asarray(1.0 / np.sqrt(math.pi * f * MU0 * mu_r * sigma), dtype=np.float64)


def cannonball_huray_parameters(rz: float) -> tuple[float, float]:
    """Return ``(sphere_radius, surface_ratio)`` of the cannonball stack for a foil ``Rz`` [m]."""
    r = rz / CANNONBALL_HEIGHT_FACTOR
    sr = CANNONBALL_SPHERES * 4.0 * math.pi * r * r / (CANNONBALL_TILE_AREA_FACTOR * r * r)
    return r, sr


def roughness_factor(roughness: Roughness, freqs: ArrayLike, sigma: float) -> FArray:
    """Power-loss multiplier ``K(f) >= 1`` of a roughness model (non-causal, real)."""
    f = np.atleast_1d(np.asarray(freqs, dtype=float))
    if isinstance(roughness, NoRoughness):
        return np.ones_like(f)
    delta = skin_depth(sigma, np.maximum(f, 1e-9))
    if isinstance(roughness, Hammerstad):
        return np.asarray(
            1.0 + (2.0 / math.pi) * np.arctan(1.4 * (roughness.rq / delta) ** 2), dtype=np.float64
        )
    if isinstance(roughness, Groisse):
        return np.asarray(1.0 + np.exp(-((delta / (2.0 * roughness.rq)) ** 1.6)), dtype=np.float64)
    if isinstance(roughness, Huray):
        a = roughness.sphere_radius
        return np.asarray(
            roughness.flat_ratio
            + 1.5 * roughness.surface_ratio / (1.0 + delta / a + delta**2 / (2.0 * a * a)),
            dtype=np.float64,
        )
    if isinstance(roughness, CannonballHuray):
        r, sr = cannonball_huray_parameters(roughness.rz)
        return np.asarray(
            1.0 + 1.5 * sr / (1.0 + delta / r + delta**2 / (2.0 * r * r)), dtype=np.float64
        )
    raise TypeError(f"unsupported roughness model {type(roughness).__name__}")


def _fit_causal_k(
    f: FArray, target_re: FArray, rs: FArray, sections_per_decade: int = 3
) -> tuple[FArray, FArray]:
    """Fit ``Zs_c = Zs (1 + sum c_k jf/(jf + f_k))`` so that ``Re Zs_c ~= K Rs``.

    Each section ``jf/(jf+f_k)`` is a causal (stable, minimum-phase) response, so the sum is
    causal by construction. Returns ``(f_k, c_k)``; the fit is linear least squares with a tiny
    ridge for conditioning.
    """
    lo = max(float(np.min(f)) / 10.0, 1.0)
    hi = float(np.max(f)) * 10.0
    decades = max(math.log10(hi / lo), 1.0)
    n_sec = max(math.ceil(decades * sections_per_decade), 4)
    fk = np.logspace(math.log10(lo), math.log10(hi), n_sec)
    # Re{(1+j) Rs * jf/(jf+fk)} = Rs (f^2 - f fk)/(f^2 + fk^2)
    ff = f[:, None]
    basis = rs[:, None] * (ff**2 - ff * fk[None, :]) / (ff**2 + fk[None, :] ** 2)
    rhs = target_re - rs
    w = 1.0 / rs
    a = basis * w[:, None]
    b = rhs * w
    ridge = 1e-8 * np.eye(n_sec)
    # NDArray[Any]: numpy < 2.5 (resolved on Python 3.11) types linalg results as floating[Any]
    c: NDArray[Any] = np.linalg.solve(a.T @ a + ridge, a.T @ b)
    return fk, c


def surface_impedance(
    sigma: float,
    freqs: ArrayLike,
    roughness: Roughness,
    *,
    causal: bool = True,
    mu_r: float = 1.0,
) -> CArray:
    """Surface impedance ``Zs(f)`` [ohm/sq] of a good conductor with roughness.

    Smooth: ``Zs = (1 + j) Rs``, ``Rs = 1 / (sigma delta)``. Rough, ``causal=False``:
    ``Zs = (1 + j) K Rs`` (real K, inductance scales with R; not causal).
    Rough, ``causal=True``: ``Zs = (1 + j) Rs (1 + sum c_k jf/(jf+f_k))`` with the sections fitted
    so that ``Re Zs = K Rs``; the reactance follows from causality (research 02 sec. 2.3).
    """
    f = np.atleast_1d(np.asarray(freqs, dtype=float))
    delta = skin_depth(sigma, np.maximum(f, 1e-9), mu_r)
    rs = 1.0 / (sigma * delta)
    zs = (1.0 + 1j) * rs
    if isinstance(roughness, NoRoughness):
        return np.asarray(zs, dtype=np.complex128)
    k = roughness_factor(roughness, f, sigma)
    if not causal:
        return np.asarray(zs * k, dtype=np.complex128)
    # fit on a dense log grid spanning the request so that sparse grids do not alias
    lo = max(float(f.min()) / 10.0, 1.0)
    hi = float(f.max()) * 10.0
    fg = np.logspace(math.log10(lo), math.log10(hi), max(int(math.log10(hi / lo) * 40), 40))
    dg = skin_depth(sigma, fg, mu_r)
    rg = 1.0 / (sigma * dg)
    fk, c = _fit_causal_k(fg, roughness_factor(roughness, fg, sigma) * rg, rg)
    sec = 1j * f[:, None] / (1j * f[:, None] + fk[None, :])
    return np.asarray(zs * (1.0 + sec @ c), dtype=np.complex128)


def resistivity(m: ConductorMaterial, T: float | ArrayLike) -> FArray:
    """``rho(T) = rho0 (1 + a (T - t_ref))`` [ohm m], ``T`` in kelvin."""
    t = np.asarray(T, dtype=float)
    return np.asarray(
        (1.0 / m.conductivity) * (1.0 + m.temp_coeff * (t - m.t_ref)), dtype=np.float64
    )


def sheet_resistance(m: ConductorMaterial, thickness: float, T: float) -> float:
    """DC sheet resistance ``rho(T) / t`` [ohm/sq]; ``T`` in kelvin."""
    return float(resistivity(m, T) / thickness)


def etch_widths(w_drawn: float, thickness: float, etch: Etch | None) -> tuple[float, float]:
    """Return ``(w_bottom, w_top)`` [m] of the trapezoidal trace for the drawn width."""
    if etch is None:
        return w_drawn, w_drawn
    # delta = w_bottom - w_top
    delta = 2.0 * thickness / math.tan(etch.value) if etch.kind == "angle" else etch.value
    if etch.reference == "bottom":
        return w_drawn, w_drawn - delta
    return w_drawn + delta, w_drawn
