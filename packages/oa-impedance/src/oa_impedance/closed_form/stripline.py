# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stripline: Cohn 1954 (exact, t = 0) with Wheeler 1978 thickness, offset and edge-coupled.

References:

- S. B. Cohn, IRE T-MTT 2, 1954, pp. 52-57 (zero-thickness symmetric stripline, exact).
- S. B. Cohn, IRE T-MTT 3, Oct. 1955, pp. 29-38 (shielded coupled strips, exact for t = 0).
- H. A. Wheeler, IEEE T-MTT 26, Nov. 1978, pp. 866-876, as Wadell eq. 3.5.1 (thickness).
- Offset stripline: parallel-combination approximation, Wadell sec. 3.6.
"""

from __future__ import annotations

import math

from oa_impedance._physics_compat import ETA0

from ._common import CFResult, RangeCheck, kratio

__all__ = [
    "cohn_z0",
    "edge_coupled_stripline",
    "offset_stripline",
    "stripline",
    "wheeler_delta_w",
    "wheeler_z0",
]


def cohn_z0(w_over_b: float, er: float = 1.0) -> float:
    """Zero-thickness symmetric stripline: Z0 = eta0/(4 sqrt(er)) K(k)/K(k'), k = sech(pi W/2b)."""
    x = math.pi * w_over_b / 2.0
    return ETA0 / (4.0 * math.sqrt(er)) * kratio(1.0 / math.cosh(x), math.tanh(x))


def wheeler_delta_w(w_over_b: float, x: float) -> float:
    """Wheeler 1978 dW/(b - t) for x = t/b."""
    m = 2.0 / (1.0 + (2.0 / 3.0) * x / (1.0 - x))
    return (
        x
        / (math.pi * (1.0 - x))
        * (1.0 - 0.5 * math.log((x / (2.0 - x)) ** 2 + (0.0796 * x / (w_over_b + 1.1 * x)) ** m))
    )


def wheeler_z0(w: float, b: float, t: float, er: float = 1.0) -> float:
    """Wheeler 1978 thick-strip stripline Z0 (~0.5 % for W'/(b-t) < 10). Cohn when t == 0."""
    if t <= 0.0:
        return cohn_z0(w / b, er)
    x = t / b
    wp = w / (b - t) + wheeler_delta_w(w / b, x)
    q = 4.0 / (math.pi * wp)
    return (
        ETA0
        / (4.0 * math.pi * math.sqrt(er))
        * math.log(1.0 + q * (2.0 * q + math.sqrt((2.0 * q) ** 2 + 6.27)))
    )


def stripline(w: float, b: float, t: float, er: float) -> CFResult:
    """Symmetric (centred) stripline, plane-to-plane spacing ``b``."""
    z0 = wheeler_z0(w, b, t, er)
    wp = w / (b - t) + (wheeler_delta_w(w / b, t / b) if t > 0 else 0.0)
    return CFResult(
        model="cohn_1954+wheeler_1978" if t > 0 else "cohn_1954",
        z0=z0,
        eeff=er,
        checks=(
            RangeCheck("W'/(b-t)", wp, 0.0, 10.0),
            RangeCheck("t/b", t / b, 0.0, 0.25),
        ),
        notes=("exact for t = 0 (conformal mapping); Wheeler thickness ~0.5 %",),
    )


def offset_stripline(w: float, h1: float, h2: float, t: float, er: float) -> CFResult:
    """Offset stripline, strip faces at distance h1 / h2 from the planes (parallel approx.)."""
    b1, b2 = 2.0 * h1 + t, 2.0 * h2 + t
    z1, z2 = wheeler_z0(w, b1, t, er), wheeler_z0(w, b2, t, er)
    z0 = 2.0 * z1 * z2 / (z1 + z2)
    asym = abs(h1 - h2) / (h1 + h2 + t)
    return CFResult(
        model="offset_stripline_parallel_approximation",
        z0=z0,
        eeff=er,
        checks=(
            RangeCheck("W/(b-t)", w / (h1 + h2), 0.0, 5.0),
            RangeCheck("offset ratio |h1-h2|/b", asym, 0.0, 0.3),
        ),
        notes=("approximate (2-5 %); homogeneous dielectric assumed",),
    )


def edge_coupled_stripline(w: float, s: float, b: float, t: float, er: float) -> CFResult:
    """Edge-coupled centred stripline pair.

    t = 0: Cohn 1955 exact. t > 0: Cohn evaluated for the equivalent zero-thickness geometry
    (plane spacing b - t, strip widened and gap narrowed by Wheeler's dW); approximate (1-2 %).
    """
    bb, ww, ss = b, w, s
    notes = ["Cohn 1955 exact for zero thickness"]
    if t > 0.0:
        bb = b - t
        dw = wheeler_delta_w(w / b, t / b) * bb
        ww, ss = w + dw, max(s - dw, 1e-3 * s)
        notes = ["thickness via equivalent zero-thickness geometry (Wheeler dW): approx 1-2 %"]
    a = math.pi * ww / (2.0 * bb)
    c = math.pi * (ww + ss) / (2.0 * bb)
    ke = math.tanh(a) * math.tanh(c)
    ko = math.tanh(a) / math.tanh(c)
    pref = ETA0 / (4.0 * math.sqrt(er))
    z0e = pref / kratio(ke)
    z0o = pref / kratio(ko)
    return CFResult(
        model="cohn_1955_edge_coupled_stripline",
        z0=wheeler_z0(w, b, t, er),
        eeff=er,
        z0e=z0e,
        z0o=z0o,
        eeff_e=er,
        eeff_o=er,
        checks=(
            RangeCheck("t/b", t / b, 0.0, 0.25),
            RangeCheck("W/b", w / b, 0.05, 2.0),
            RangeCheck("S/b", s / b, 0.05, 5.0),
        ),
        notes=tuple(notes),
    )
