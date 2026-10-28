# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single microstrip: Hammerstad-Jensen 1980, Wheeler 1977, Kirschning-Jansen dispersion.

References (equations implemented from the papers; cross-checked against ``skrf.media.MLine``,
BSD-3, whose coefficient tables for the dispersion models were compared term by term):

- E. Hammerstad, O. Jensen, MTT-S Digest 1980, pp. 407-409 (Z0, eeff, thickness).
- H. A. Wheeler, IEEE T-MTT 25(8), 1977, pp. 631-647, in the form of Wadell sec. 3.2.
- M. Kirschning, R. H. Jansen, Electron. Lett. 18(6), 1982 (eeff(f)).
- R. H. Jansen, M. Kirschning, AEU 37, 1983, pp. 108-112 (Z0(f)).
- Embedded cover correction: see :func:`embedded_microstrip`.

All lengths in metres, frequency in Hz.
"""

from __future__ import annotations

import math

from oa_impedance._physics_compat import ETA0

from ._common import CFResult, RangeCheck

__all__ = [
    "embedded_microstrip",
    "hj_eeff",
    "hj_z01",
    "kj_eeff",
    "kj_z0",
    "microstrip_hj",
    "microstrip_wheeler",
]


def hj_z01(u: float) -> float:
    """Air-filled zero-thickness impedance Z01(u), u = W/h (HJ 1980)."""
    f = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-((30.666 / u) ** 0.7528))
    return float(ETA0 / (2.0 * math.pi) * math.log(f / u + math.sqrt(1.0 + (2.0 / u) ** 2)))


def _hj_ab(u: float, er: float) -> tuple[float, float]:
    a = (
        1.0
        + math.log((u**4 + (u / 52.0) ** 2) / (u**4 + 0.432)) / 49.0
        + math.log(1.0 + (u / 18.1) ** 3) / 18.7
    )
    b = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    return a, b


def hj_eeff(u: float, er: float) -> float:
    """Zero-thickness static effective permittivity (HJ 1980)."""
    a, b = _hj_ab(u, er)
    return float((er + 1.0) / 2.0 + (er - 1.0) / 2.0 * (1.0 + 10.0 / u) ** (-a * b))


def kj_eeff(u: float, fn: float, er: float, eeff0: float) -> float:
    """Kirschning-Jansen 1982 eeff(f); fn = f[GHz] * h[mm]."""
    p1 = (
        0.27488
        + (0.6315 + 0.525 / (1.0 + 0.0157 * fn) ** 20) * u
        - 0.065683 * math.exp(-8.7513 * u)
    )
    p2 = 0.33622 * (1.0 - math.exp(-0.03442 * er))
    p3 = 0.0363 * math.exp(-4.6 * u) * (1.0 - math.exp(-((fn / 38.7) ** 4.97)))
    p4 = 1.0 + 2.751 * (1.0 - math.exp(-((er / 15.916) ** 8)))
    pf = p1 * p2 * ((0.1844 + p3 * p4) * fn) ** 1.5763
    return float(er - (er - eeff0) / (1.0 + pf))


def kj_z0(u: float, fn: float, er: float, eeff0: float, eeff_f: float, z0: float) -> float:
    """Jansen-Kirschning 1983 power-current Z0(f) (R1..R17 set)."""
    exp = math.exp
    r1 = min(0.03891 * er**1.4, 20.0)
    r2 = min(0.2671 * u**7, 20.0)
    r3 = 4.766 * exp(-3.228 * u**0.641)
    r4 = 0.016 + (0.0514 * er) ** 4.524
    r5 = (fn / 28.843) ** 12
    r6 = min(22.20 * u**1.92, 20.0)
    r7 = 1.206 - 0.3144 * exp(-r1) * (1.0 - exp(-r2))
    r8 = 1.0 + 1.275 * (1.0 - exp(-0.004625 * r3 * er**1.674 * (fn / 18.365) ** 2.745))
    r9 = (
        5.086
        * r4
        * r5
        / (0.3838 + 0.386 * r4)
        * exp(-r6)
        / (1.0 + 1.2992 * r5)
        * (er - 1.0) ** 6
        / (1.0 + 10.0 * (er - 1.0) ** 6)
    )
    r10 = 0.00044 * er**2.136 + 0.0184
    r11 = (fn / 19.47) ** 6 / (1.0 + 0.0962 * (fn / 19.47) ** 6)
    r12 = 1.0 / (1.0 + 0.00245 * u**2)
    r13 = 0.9408 * eeff_f**r8 - 0.9603
    r14 = (0.9408 - r9) * eeff0**r8 - 0.9603
    r15 = 0.707 * r10 * (fn / 12.3) ** 1.097
    r16 = 1.0 + 0.0503 * er**2 * r11 * (1.0 - exp(-((u / 15.0) ** 6)))
    r17 = r7 * (1.0 - 1.1241 * r12 / r16 * exp(-0.026 * fn**1.15656 - r15))
    return float(z0 * (r13 / r14) ** r17)


def _checks(u: float, er: float, t_over_h: float, fn: float | None, model: str) -> list[RangeCheck]:
    c = [
        RangeCheck("W/h", u, 0.01, 100.0),
        RangeCheck("er", er, 1.0, 128.0),
        RangeCheck("t/h", t_over_h, 0.0, 0.5),
    ]
    if fn is not None and fn > 0.0:
        c = [
            RangeCheck("W/h", u, 0.1, 100.0),
            RangeCheck("er", er, 1.0, 20.0),
            RangeCheck("t/h", t_over_h, 0.0, 0.5),
            RangeCheck("f*h [GHz*mm]", fn, 0.0, 25.0),
        ]
    del model
    return c


def microstrip_hj(
    w: float, h: float, t: float, er: float, f: float = 0.0, *, legacy_dispersion: bool = False
) -> CFResult:
    """Hammerstad-Jensen microstrip with HJ thickness correction.

    With ``f > 0`` the Kirschning-Jansen eeff(f) and Jansen-Kirschning Z0(f) are applied.
    """
    u, th = w / h, t / h
    if th > 0.0:
        # skrf notes that the Qucs-doc form of this equation (11.22) is wrong; the HJ paper form
        # (with normalized u) is used here, tanh^2 == 1/coth^2.
        du1 = (
            th / math.pi * math.log(1.0 + 4.0 * math.e / th * math.tanh(math.sqrt(6.517 * u)) ** 2)
        )
    else:
        du1 = 0.0
    dur = 0.5 * (1.0 + 1.0 / math.cosh(math.sqrt(er - 1.0))) * du1
    u1, ur = u + du1, u + dur
    e_r = hj_eeff(ur, er)
    z0 = hj_z01(ur) / math.sqrt(e_r)
    eeff = e_r * (hj_z01(u1) / hj_z01(ur)) ** 2
    fn = f * h * 1e-6
    notes = ["Hammerstad-Jensen 1980 static; thickness by HJ effective width"]
    if f > 0.0:
        eeff_f = kj_eeff(u, fn, er, eeff)
        z0_f = kj_z0(u, fn, er, eeff, eeff_f, z0)
        notes.append("Kirschning-Jansen 1982 eeff(f), Jansen-Kirschning 1983 Z0(f)")
        z0, eeff = z0_f, eeff_f
    return CFResult(
        model="hammerstad_jensen_1980" + ("+kirschning_jansen" if f > 0.0 else ""),
        z0=z0,
        eeff=eeff,
        checks=tuple(_checks(u, er, th, fn, "hj")),
        notes=tuple(notes),
        details={"u": u, "w_eff_over_h": ur},
    )


def microstrip_wheeler(w: float, h: float, t: float, er: float) -> CFResult:
    """Wheeler 1977 microstrip (Wadell sec. 3.2 form). eeff from the same Z0 ratio as HJ."""
    if t > 0.0:
        dw = (
            t
            / math.pi
            * math.log(
                4.0 * math.e / math.sqrt((t / h) ** 2 + ((1 / math.pi) / (w / t + 1.1)) ** 2)
            )
        )
    else:
        dw = 0.0
    wp = w + dw * (1.0 + 1.0 / er) / 2.0
    x = 4.0 * h / wp
    a = (14.0 + 8.0 / er) / 11.0 * x
    z0 = (
        ETA0
        / (2.0 * math.pi * math.sqrt(2.0 * (1.0 + er)))
        * math.log(1.0 + x * (a + math.sqrt(a * a + math.pi**2 * (1.0 + 1.0 / er) / 2.0)))
    )
    # Wheeler gives Z0 for the filled line; the air line (er = 1) fixes eeff = (Z0air/Z0)^2.
    xa = 4.0 * h / (w + dw)
    aa = (14.0 + 8.0) / 11.0 * xa
    z_air = (
        ETA0
        / (2.0 * math.pi * math.sqrt(4.0))
        * math.log(1.0 + xa * (aa + math.sqrt(aa * aa + math.pi**2)))
    )
    return CFResult(
        model="wheeler_1977",
        z0=z0,
        eeff=(z_air / z0) ** 2,
        checks=(
            RangeCheck("W/h", w / h, 0.05, 20.0),
            RangeCheck("er", er, 1.0, 16.0),
            RangeCheck("t/h", t / h, 0.0, 0.5),
        ),
        notes=("Wheeler 1977 (Wadell 3.2); ~0.7 % vs numerical",),
    )


def embedded_microstrip(
    w: float, h: float, t: float, er: float, cover: float, exponent: float = 1.55
) -> CFResult:
    """Embedded (buried) microstrip: HJ microstrip with a same-er cover of height ``cover``.

    eeff_emb = eeff_ms * exp(-k d/h) + er * (1 - exp(-k d/h)), Z0_emb = Z0_ms(air)/sqrt(eeff_emb)
    with the exponent k = ``exponent``. The [verify] item of docs/research/02 sec. 1.6 (2 or 1.55)
    could not be traced to a primary source here; both were scored against the BEM on a 12-case
    grid (tests/test_embedded_exponent.py): mean |dZ0| 1.1 % for 1.55 and 1.2 % for 2, worst 2.9 %
    and 2.4 %. 1.55 is the default; the result is always flagged approximate (+-3 % Z0, +-6 % eeff).
    """
    base = microstrip_hj(w, h, t, er)
    assert base.z0 is not None and base.eeff is not None
    z_air = base.z0 * math.sqrt(base.eeff)
    e = math.exp(-exponent * cover / h)
    eeff = base.eeff * e + er * (1.0 - e)
    return CFResult(
        model="embedded_microstrip_cover_correction",
        z0=z_air / math.sqrt(eeff),
        eeff=eeff,
        checks=(*base.checks, RangeCheck("cover/h", cover / h, 0.0, 4.0)),
        notes=("approximate (+-3 %): cover assumed to have the substrate er; use BEM for mask",),
        details={"exponent": exponent},
    )
