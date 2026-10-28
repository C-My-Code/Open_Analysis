# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thin local shim for the ``oa_physics`` functions of interfaces.md section 10.

``oa_physics`` (WP-106) is not available yet. This module is the only place that knows; when it
lands, the bodies below are replaced by imports and nothing else in ``oa_impedance`` changes.
All quantities are SI.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from oa_model.materials import (
    CannonballHuray,
    ConstantDkDf,
    DjordjevicSarkar,
    Groisse,
    Hammerstad,
    Huray,
    MultiPoleDebye,
    TableDkDf,
)

MU0 = 1.25663706212e-6  # H/m (CODATA 2018, same value as oa_testkit.analytic)
C0 = 299_792_458.0  # m/s
EPS0 = 1.0 / (MU0 * C0 * C0)  # F/m
ETA0 = MU0 * C0  # ohm

__all__ = [
    "C0",
    "EPS0",
    "ETA0",
    "MU0",
    "eps_complex",
    "is_frequency_independent",
    "roughness_factor",
    "skin_depth",
    "surface_resistance",
]


def skin_depth(f: float, sigma: float) -> float:
    """Skin depth [m] of a non-magnetic conductor."""
    return 1.0 / math.sqrt(math.pi * f * MU0 * sigma)


def surface_resistance(f: float, sigma: float) -> float:
    """Rs = 1/(sigma delta) [ohm/sq]."""
    return math.sqrt(math.pi * f * MU0 / sigma)


def _datasheet_frequency(model: Any) -> float:
    if isinstance(model, DjordjevicSarkar):
        return float(model.f_ref)
    if isinstance(model, TableDkDf):
        return float(np.asarray(getattr(model.freqs, "array", model.freqs))[0])
    return 1e9


def eps_complex(model: Any, f: float) -> complex:
    """Complex relative permittivity eps' - j eps'' of a ``DielectricModel`` at ``f`` [Hz].

    Convention: eps = Dk (1 - j Df) (engineering sign, matches Y = j w C~).
    """
    if f <= 0.0:  # static request: evaluate at the datasheet point
        f = _datasheet_frequency(model)
    if isinstance(model, ConstantDkDf):
        return complex(model.dk, -model.dk * model.df)
    if isinstance(model, DjordjevicSarkar):
        w0 = 2.0 * math.pi * model.f_ref
        w1, w2 = 2.0 * math.pi * model.f_low, 2.0 * math.pi * model.f_high
        m1, m2 = math.log10(w1), math.log10(w2)

        def lg(w: float) -> complex:
            return complex(np.log10((w2 + 1j * w) / (w1 + 1j * w)))

        l0 = lg(w0)
        d_eps = -model.dk * model.df * (m2 - m1) / l0.imag
        eps_inf = model.dk - d_eps / (m2 - m1) * l0.real
        w = 2.0 * math.pi * max(f, 1.0)
        return complex(eps_inf + d_eps / (m2 - m1) * lg(w))
    if isinstance(model, MultiPoleDebye):
        w = 2.0 * math.pi * f
        eps = complex(model.eps_inf)
        for d_eps, fp in model.poles:
            eps += d_eps / (1.0 + 1j * f / fp)
        if model.sigma_dc > 0.0 and w > 0.0:
            eps -= 1j * model.sigma_dc / (w * EPS0)
        return eps
    if isinstance(model, TableDkDf):
        fr = np.asarray(getattr(model.freqs, "array", model.freqs), dtype=float)
        dk = float(np.interp(f, fr, np.asarray(getattr(model.dk, "array", model.dk))))
        df = float(np.interp(f, fr, np.asarray(getattr(model.df, "array", model.df))))
        return complex(dk, -dk * df)
    raise TypeError(f"unsupported dielectric model {type(model).__name__}")


def is_frequency_independent(model: Any) -> bool:
    return isinstance(model, ConstantDkDf)


def roughness_factor(rough: Any, f: float, sigma: float) -> float:
    """Loss multiplier K(f) >= 1 for the conductor surface roughness models of oa_model."""
    if f <= 0.0:
        return 1.0
    delta = skin_depth(f, sigma)
    if isinstance(rough, Hammerstad):
        return 1.0 + (2.0 / math.pi) * math.atan(1.4 * (rough.rq / delta) ** 2)
    if isinstance(rough, Groisse):
        return 1.0 + math.exp(-((delta / (2.0 * rough.rq)) ** 1.6))
    if isinstance(rough, Huray):
        a = rough.sphere_radius
        return rough.flat_ratio + 1.5 * rough.surface_ratio / (
            1.0 + delta / a + delta**2 / (2.0 * a * a)
        )
    if isinstance(rough, CannonballHuray):
        # Simonovich 2015: 14 spheres of radius r in a square-based pyramid on a 6r x 6r tile,
        # stack height (2 + 2 sqrt 2) r ~= Rz (see the [verify] resolution in the README).
        r = rough.rz / (2.0 + 2.0 * math.sqrt(2.0))
        sr = 14.0 * 4.0 * math.pi * r * r / (36.0 * r * r)
        return 1.0 + 1.5 * sr / (1.0 + delta / r + delta**2 / (2.0 * r * r))
    return 1.0
