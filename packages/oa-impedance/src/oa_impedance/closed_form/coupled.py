# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge-coupled microstrip: Kirschning-Jansen 1984 static even/odd model.

M. Kirschning, R. H. Jansen, IEEE T-MTT 32(1), 1984, pp. 83-90, with the 1985 errata (T-MTT 33,
p. 288). The static equation set (v, ae, be, ao..do, Q1..Q10, 377 ohm in the Z0 denominator) was
checked line by line against the Qucs technical documentation, "Parallel coupled microstrip
lines" (equations 11.88-11.101; Qucs is GPL-2, only the published equations are used) and against
the BEM of this package (tests/test_closed_form_vs_bem.py). The dispersive part of the paper is
not implemented: results are static and say so in ``notes``.
"""

from __future__ import annotations

import math

from ._common import CFResult, RangeCheck
from .microstrip import hj_eeff, hj_z01

__all__ = ["edge_coupled_microstrip"]


def edge_coupled_microstrip(w: float, s: float, h: float, t: float, er: float) -> CFResult:
    u, g = w / h, s / h
    notes = ["Kirschning-Jansen 1984 static even/odd (+1985 errata); no dispersion"]
    du = 0.0
    if t > 0.0:
        th = t / h
        du = th / math.pi * math.log(1.0 + 4.0 * math.e / th * math.tanh(math.sqrt(6.517 * u)) ** 2)
        notes.append("thickness: strip widened / gap narrowed by the HJ du (approximate)")
        u, g = u + du, max(g - du, 1e-3)
    ee_s = hj_eeff(u, er)
    z_s = hj_z01(u) / math.sqrt(ee_s)
    z_air = hj_z01(u)

    v = u * (20.0 + g * g) / (10.0 + g * g) + g * math.exp(-g)
    ae = (
        1.0
        + math.log((v**4 + (v / 52.0) ** 2) / (v**4 + 0.432)) / 49.0
        + math.log(1.0 + (v / 18.1) ** 3) / 18.7
    )
    be = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    ee_e = (er + 1.0) / 2.0 + (er - 1.0) / 2.0 * (1.0 + 10.0 / v) ** (-ae * be)

    ao = 0.7287 * (ee_s - (er + 1.0) / 2.0) * (1.0 - math.exp(-0.179 * u))
    bo = 0.747 * er / (0.15 + er)
    co = bo - (bo - 0.207) * math.exp(-0.414 * u)
    do = 0.593 + 0.694 * math.exp(-0.562 * u)
    ee_o = ((er + 1.0) / 2.0 + ao - ee_s) * math.exp(-co * g**do) + ee_s

    q1 = 0.8695 * u**0.194
    q2 = 1.0 + 0.7519 * g + 0.189 * g**2.31
    q3 = (
        0.1975
        + (16.6 + (8.4 / g) ** 6) ** (-0.387)
        + math.log(g**10 / (1.0 + (g / 3.4) ** 10)) / 241.0
    )
    q4 = 2.0 * q1 / q2 / (math.exp(-g) * u**q3 + (2.0 - math.exp(-g)) * u ** (-q3))
    q5 = 1.794 + 1.14 * math.log(1.0 + 0.638 / (g + 0.517 * g**2.43))
    q6 = (
        0.2305
        + math.log(g**10 / (1.0 + (g / 5.8) ** 10)) / 281.3
        + math.log(1.0 + 0.598 * g**1.154) / 5.1
    )
    q7 = (10.0 + 190.0 * g * g) / (1.0 + 82.3 * g**3)
    q8 = math.exp(-6.5 - 0.95 * math.log(g) - (g / 0.15) ** 5)
    q9 = math.log(q7) * (q8 + 1.0 / 16.5)
    q10 = (q2 * q4 - q5 * math.exp(math.log(u) * q6 * u ** (-q9))) / q2

    sq = math.sqrt(ee_s)
    z0e = z_s * math.sqrt(ee_s / ee_e) / (1.0 - z_s * sq * q4 / 377.0)
    z0o = z_s * math.sqrt(ee_s / ee_o) / (1.0 - z_s * sq * q10 / 377.0)
    del z_air
    return CFResult(
        model="kirschning_jansen_1984_static",
        z0=z_s,
        eeff=ee_s,
        z0e=z0e,
        z0o=z0o,
        eeff_e=ee_e,
        eeff_o=ee_o,
        checks=(
            RangeCheck("W/h", w / h, 0.1, 10.0),
            RangeCheck("S/h", s / h, 0.1, 10.0),
            RangeCheck("er", er, 1.0, 18.0),
            RangeCheck("t/h", t / h, 0.0, 0.2),
        ),
        notes=tuple(notes),
    )
