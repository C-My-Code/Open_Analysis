# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Analytic via estimators and pi model (service ``via_model``; research 07 section 1.2).

Formulas (SI):
- barrel inductance, Goldfarb-Pucel: ``L = mu0 h / (2 pi) (ln(4 h / d) + 1)`` (the classic
  ``5.08 h [ln(4h/d) + 1]`` nH with h in inches);
- pad capacitance, Johnson & Graham: ``C = 1.41 eps_r T D1 / (D2 - D1)`` pF with lengths in inches
  (D1 pad diameter, D2 antipad diameter, T the dielectric thickness the pad sees);
- via-pair loop inductance ``L = mu0 h / pi ln(s / r)``;
- barrel resistance ``R = rho h / (pi (r_o^2 - r_i^2))``;
- open stub quarter-wave resonance ``f = c / (4 l sqrt(eps_r))``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from oa_physics.constants import C0, MU0

__all__ = [
    "ViaEstimate",
    "ViaModelService",
    "barrel_inductance",
    "barrel_resistance",
    "estimate",
    "make_service",
    "pad_capacitance",
    "pi_model_sparams",
    "stub_resonance",
    "via_pair_inductance",
]

_RHO_CU_20C = 1.0 / 5.8e7  # ohm m
_INCH = 0.0254


def barrel_inductance(height: float, drill: float) -> float:
    """Goldfarb-Pucel barrel inductance [H]; ``height`` and ``drill`` in metres."""
    return MU0 * height / (2.0 * math.pi) * (math.log(4.0 * height / drill) + 1.0)


def pad_capacitance(eps_r: float, t: float, d_pad: float, d_antipad: float) -> float:
    """Johnson & Graham pad/antipad capacitance [F]; lengths in metres."""
    if d_antipad <= d_pad:
        raise ValueError("antipad diameter must exceed pad diameter")
    return 1.41e-12 * eps_r * (t / _INCH) * d_pad / (d_antipad - d_pad)


def via_pair_inductance(height: float, pitch: float, radius: float) -> float:
    """Loop inductance [H] of a signal/return via pair: ``mu0 h / pi ln(s / r)``."""
    return MU0 * height / math.pi * math.log(pitch / radius)


def barrel_resistance(
    height: float, drill: float, plating: float, rho: float = _RHO_CU_20C
) -> float:
    """DC resistance [ohm] of the plated barrel (annulus between ``drill/2 - plating`` and
    ``drill/2``; ``drill`` is the finished hole diameter)."""
    r_o = drill / 2.0
    r_i = r_o - plating
    if r_i < 0.0:
        raise ValueError("plating thicker than the via radius")
    return rho * height / (math.pi * (r_o**2 - r_i**2))


def stub_resonance(length: float, eps_r: float) -> float:
    """First (quarter-wave) resonance [Hz] of an open via stub."""
    return C0 / (4.0 * length * math.sqrt(eps_r))


@dataclass(frozen=True)
class ViaEstimate:
    """Lumped pi model: ``C_pad`` is split half on each side of series ``R + jwL``."""

    r: float  # [ohm]
    l: float  # [H]  # noqa: E741
    c_pad: float  # [F] total shunt capacitance (both pads)
    stub_length: float  # [m]
    eps_r: float
    stub_f0: float | None  # [Hz]


def estimate(
    *,
    height: float,
    drill: float,
    plating: float,
    d_pad: float,
    d_antipad: float,
    eps_r: float,
    stub_length: float = 0.0,
    t_pad: float | None = None,
) -> ViaEstimate:
    """Pi-model parameters for a signal via (all lengths in metres).

    ``t_pad`` is the dielectric thickness seen by one pad; it defaults to ``height / 4`` (a
    typical stack-up fraction), so pass the real value when known.
    """
    t = height / 4.0 if t_pad is None else t_pad
    c = 2.0 * pad_capacitance(eps_r, t, d_pad, d_antipad)
    return ViaEstimate(
        r=barrel_resistance(height, drill, plating),
        l=barrel_inductance(height, drill),
        c_pad=c,
        stub_length=stub_length,
        eps_r=eps_r,
        stub_f0=stub_resonance(stub_length, eps_r) if stub_length > 0.0 else None,
    )


def pi_model_sparams(
    est: ViaEstimate,
    freqs: ArrayLike,
    z0: float = 50.0,
    z_stub: float | None = None,
) -> NDArray[np.complex128]:
    """2-port S-parameters ``(n, 2, 2)`` of the pi model, with the lossless open stub as a shunt
    admittance ``j tan(beta l) / z_stub`` at the barrel midpoint (stub impedance defaults to
    ``z0``)."""
    f = np.atleast_1d(np.asarray(freqs, dtype=float))
    w = 2.0 * np.pi * f
    zs = est.r + 1j * w * est.l
    y_half = 1j * w * est.c_pad / 2.0
    if est.stub_length > 0.0:
        beta = w * math.sqrt(est.eps_r) / C0
        y_stub = 1j * np.tan(beta * est.stub_length) / (z_stub or z0)
    else:
        y_stub = np.zeros_like(w, dtype=np.complex128)
    # ABCD cascade: shunt(C/2) series(Z/2) shunt(stub) series(Z/2) shunt(C/2)
    n = f.size
    t = np.tile(np.eye(2, dtype=np.complex128), (n, 1, 1))

    def shunt(y: NDArray[np.complex128]) -> NDArray[np.complex128]:
        m = np.tile(np.eye(2, dtype=np.complex128), (n, 1, 1))
        m[:, 1, 0] = y
        return m

    def series(z: NDArray[np.complex128]) -> NDArray[np.complex128]:
        m = np.tile(np.eye(2, dtype=np.complex128), (n, 1, 1))
        m[:, 0, 1] = z
        return m

    for m in (
        shunt(np.asarray(y_half, dtype=np.complex128)),
        series(np.asarray(zs / 2, dtype=np.complex128)),
        shunt(y_stub),
        series(np.asarray(zs / 2, dtype=np.complex128)),
        shunt(np.asarray(y_half, dtype=np.complex128)),
    ):
        t = t @ m
    a, b, c, d = t[:, 0, 0], t[:, 0, 1], t[:, 1, 0], t[:, 1, 1]
    den = a + b / z0 + c * z0 + d
    s = np.empty((n, 2, 2), dtype=np.complex128)
    s[:, 0, 0] = (a + b / z0 - c * z0 - d) / den
    s[:, 0, 1] = 2.0 * (a * d - b * c) / den
    s[:, 1, 0] = 2.0 / den
    s[:, 1, 1] = (-a + b / z0 - c * z0 + d) / den
    return s


class ViaModelService:
    """The ``via_model`` service (interfaces.md section 3.4)."""

    estimate = staticmethod(estimate)
    sparams = staticmethod(pi_model_sparams)
    barrel_inductance = staticmethod(barrel_inductance)
    barrel_resistance = staticmethod(barrel_resistance)
    pad_capacitance = staticmethod(pad_capacitance)
    via_pair_inductance = staticmethod(via_pair_inductance)
    stub_resonance = staticmethod(stub_resonance)


def make_service() -> ViaModelService:
    """Entry-point factory (group ``open_analysis.services``)."""
    return ViaModelService()
