# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Coplanar waveguide (CPW) and grounded CPW (CPWG).

Conformal mapping: G. Ghione, C. Naldi, Electron. Lett. 20(4) 1984 (CPW) and 19(18) 1983 (CPWG);
R. N. Simons, Coplanar Waveguide Circuits, Components and Systems, Wiley 2001. Thickness: Gupta,
Garg, Bahl, Bhartia, Microstrip Lines and Slotlines, 2nd ed., eqs. 7.98-7.100 (the first-order
form that ``skrf.media.CPW``, BSD-3, also uses; used as the cross-check). ``scipy.special.ellipk``
takes the parameter m = k^2; Hilberg's approximation is provided as an independent check.
"""

from __future__ import annotations

import math

from ._common import CFResult, RangeCheck, kratio

__all__ = ["cpw", "hilberg_kratio"]

# The CPW literature (Ghione-Naldi, Simons) uses 120 pi ohm for the free-space impedance.
ETA0 = 120.0 * math.pi


def hilberg_kratio(k: float) -> float:
    """K(k)/K(k') by Hilberg's approximation (about 3 ppm)."""
    kp = math.sqrt(1.0 - k * k)
    if k <= 1.0 / math.sqrt(2.0):
        sq = math.sqrt(kp)
        return math.pi / math.log(2.0 * (1.0 + sq) / (1.0 - sq))
    sq = math.sqrt(k)
    return math.log(2.0 * (1.0 + sq) / (1.0 - sq)) / math.pi


def cpw(
    s: float,
    g: float,
    t: float,
    er: float,
    h: float | None = None,
    *,
    backside: bool = False,
    ground_width: float | None = None,
) -> CFResult:
    """CPW (centre strip ``s``, gaps ``g``) on a substrate of height ``h`` (None = half-space).

    ``backside=True`` is the grounded CPW (CPWG). ``ground_width`` (lateral ground extent) is
    only used for the validity check; the formulas assume infinitely wide side grounds.
    """
    if backside and h is None:
        raise ValueError("CPWG needs a substrate height h")
    k1 = s / (s + 2.0 * g)
    q1 = kratio(k1)  # K(k)/K(k')
    q3 = 0.0
    if backside:
        assert h is not None
        k3 = math.tanh(math.pi * s / (4.0 * h)) / math.tanh(math.pi * (s + 2.0 * g) / (4.0 * h))
        q3 = kratio(k3)
        e = 1.0 + q3 / (q1 + q3) * (er - 1.0)
        zr = ETA0 / 2.0 / (q1 + q3)
    elif h is None:
        e = (er + 1.0) / 2.0
        zr = ETA0 / 4.0 / q1
    else:
        k2 = math.sinh(math.pi * s / (4.0 * h)) / math.sinh(math.pi * (s + 2.0 * g) / (4.0 * h))
        e = 1.0 + (er - 1.0) / 2.0 * kratio(k2) / q1
        zr = ETA0 / 4.0 / q1
    if t > 0.0:
        d = 1.25 * t / math.pi * (1.0 + math.log(4.0 * math.pi * s / t))
        ke = k1 + (1.0 - k1 * k1) * d / (2.0 * g)
        ke = min(ke, 0.999999)
        qe = kratio(ke)
        zr = ETA0 / 2.0 / (qe + q3) if backside else ETA0 / 4.0 / qe
        e = e - (0.7 * (e - 1.0) * t / g) / (q1 + 0.7 * t / g)
    z0 = zr / math.sqrt(e)
    checks = [
        RangeCheck("S/(S+2G)", k1, 0.1, 0.9),
        RangeCheck("t/G", t / g, 0.0, 0.3),
        RangeCheck("er", er, 1.0, 40.0),
    ]
    if h is not None:
        checks.append(RangeCheck("h/(S+2G)", h / (s + 2.0 * g), 0.2, 20.0))
    if ground_width is not None:
        checks.append(RangeCheck("ground_width/(S+2G)", ground_width / (s + 2.0 * g), 2.0, 1e9))
    notes = [
        "CPWG: conductor-backed conformal mapping, ideal via stitching" if backside else "CPW",
        "infinite side grounds assumed",
    ]
    return CFResult(
        model="ghione_naldi_cpwg" if backside else "ghione_naldi_cpw",
        z0=z0,
        eeff=e,
        checks=tuple(checks),
        notes=tuple(notes),
    )
