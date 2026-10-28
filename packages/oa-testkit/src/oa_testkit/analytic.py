# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure-Python reference formulas for transmission-line cross-sections.

No third-party dependencies. Exact formulas (coax, wire over plane, two-wire line, Cohn
zero-thickness stripline, edge-coupled stripline and zero-thickness CPW) are evaluated with the
arithmetic-geometric mean, so complete elliptic integrals are accurate to machine precision.

The Hammerstad-Jensen microstrip and Wheeler stripline thickness formulas are published
*approximations*; values from them are formula-locks, not physical truth.

All lengths in metres, impedances in ohm. ``er`` is the relative permittivity of a homogeneous
(or, for CPW with a substrate, the stated) dielectric.
"""

from __future__ import annotations

import math

MU0 = 1.25663706212e-6  # H/m, CODATA 2018
C0 = 299_792_458.0  # m/s
EPS0 = 1.0 / (MU0 * C0 * C0)
ETA0 = MU0 * C0  # 376.730313... ohm, free-space wave impedance


def agm(a: float, b: float) -> float:
    """Arithmetic-geometric mean of two positive numbers."""
    for _ in range(64):
        if abs(a - b) <= 1e-16 * max(a, b):
            break
        a, b = 0.5 * (a + b), math.sqrt(a * b)
    return 0.5 * (a + b)


def ellipk(k: float, kp: float | None = None) -> float:
    """Complete elliptic integral of the first kind K(k), modulus convention.

    Pass the complementary modulus ``kp`` when it is known more accurately than sqrt(1-k^2).
    """
    if kp is None:
        kp = math.sqrt((1.0 - k) * (1.0 + k))
    return math.pi / (2.0 * agm(1.0, kp))


def k_ratio(k: float, kp: float | None = None) -> float:
    """K(k) / K(k'), with k' = sqrt(1 - k^2) unless given."""
    if kp is None:
        kp = math.sqrt((1.0 - k) * (1.0 + k))
    return agm(1.0, k) / agm(1.0, kp)


# ---------------------------------------------------------------------------------------------
# Exact: round conductors
# ---------------------------------------------------------------------------------------------


def coax_z0(b_over_a: float, er: float = 1.0) -> float:
    """Coax: Z0 = eta0 / (2 pi sqrt(er)) ln(b/a)."""
    return ETA0 / (2.0 * math.pi * math.sqrt(er)) * math.log(b_over_a)


def wire_over_plane_z0(h_over_r: float, er: float = 1.0) -> float:
    """Round wire (radius r, height h) over an infinite plane: eta0/(2 pi sqrt(er)) acosh(h/r)."""
    return ETA0 / (2.0 * math.pi * math.sqrt(er)) * math.acosh(h_over_r)


def two_wire_z0(d_over_diam: float, er: float = 1.0) -> float:
    """Balanced two-wire line (centre spacing D, wire diameter d): eta0/(pi sqrt(er)) acosh(D/d)."""
    return ETA0 / (math.pi * math.sqrt(er)) * math.acosh(d_over_diam)


# ---------------------------------------------------------------------------------------------
# Exact: Cohn stripline (zero thickness, conformal mapping)
# ---------------------------------------------------------------------------------------------


def cohn_stripline_z0(w_over_b: float, er: float = 1.0) -> float:
    """Zero-thickness symmetric stripline (Cohn 1954): eta0/(4 sqrt(er)) K(k)/K(k').

    The modulus is k = sech(pi W / 2b).
    """
    x = math.pi * w_over_b / 2.0
    return ETA0 / (4.0 * math.sqrt(er)) * k_ratio(1.0 / math.cosh(x), math.tanh(x))


def cohn_edge_coupled_stripline(
    w_over_b: float, s_over_b: float, er: float = 1.0
) -> dict[str, float]:
    """Edge-coupled zero-thickness centred stripline pair (Cohn 1955).

    Returns even/odd single-line impedances plus ``zdiff = 2 Z0o`` and ``zcomm = Z0e / 2``.
    """
    a = math.pi * w_over_b / 2.0
    c = math.pi * (w_over_b + s_over_b) / 2.0
    ke = math.tanh(a) * math.tanh(c)
    ko = math.tanh(a) / math.tanh(c)
    pref = ETA0 / (4.0 * math.sqrt(er))
    z0e = pref / k_ratio(ke)  # K(ke')/K(ke)
    z0o = pref / k_ratio(ko)
    return {"z0e": z0e, "z0o": z0o, "zdiff": 2.0 * z0o, "zcomm": 0.5 * z0e}


def wheeler_stripline_z0(w_over_b: float, t_over_b: float, er: float = 1.0) -> float:
    """Symmetric stripline with thickness (Wheeler 1978, Wadell eq. 3.5.1), ~0.5 % accurate."""
    if t_over_b <= 0.0:
        return cohn_stripline_z0(w_over_b, er)
    x = t_over_b
    m = 2.0 / (1.0 + (2.0 / 3.0) * x / (1.0 - x))
    dw = (
        x
        / (math.pi * (1.0 - x))
        * (1.0 - 0.5 * math.log((x / (2.0 - x)) ** 2 + (0.0796 * x / (w_over_b + 1.1 * x)) ** m))
    )
    wp = w_over_b / (1.0 - x) + dw  # W'/(b-t)
    q = 4.0 / (math.pi * wp)
    return (
        ETA0
        / (4.0 * math.pi * math.sqrt(er))
        * math.log(1.0 + q * (2.0 * q + math.sqrt((2.0 * q) ** 2 + 6.27)))
    )


# ---------------------------------------------------------------------------------------------
# Coplanar waveguide (zero thickness, infinite lateral ground)
# ---------------------------------------------------------------------------------------------


def cpw_z0_eeff(s: float, g: float, er: float = 1.0, h: float | None = None) -> tuple[float, float]:
    """Zero-thickness CPW (strip S, gaps G) on a substrate of height h with air above and no
    bottom ground (``h=None``: infinite half-space, eeff = (er+1)/2). Returns ``(z0, eeff)``.

    Conformal-mapping solution (Ghione & Naldi 1987; Wadell sec. 3.7). Exact for infinitely wide
    side grounds and zero conductor thickness.
    """
    k0 = s / (s + 2.0 * g)
    k0p = math.sqrt((1.0 - k0) * (1.0 + k0))
    r0 = k_ratio(k0p, k0)  # K(k0')/K(k0)
    if h is None:
        eeff = (er + 1.0) / 2.0
    else:
        k1 = math.sinh(math.pi * s / (4.0 * h)) / math.sinh(math.pi * (s + 2.0 * g) / (4.0 * h))
        eeff = 1.0 + (er - 1.0) / 2.0 * k_ratio(k1) * r0
    z0 = 30.0 * math.pi / math.sqrt(eeff) * r0
    return z0, eeff


# ---------------------------------------------------------------------------------------------
# Microstrip: Hammerstad-Jensen 1980 (published approximation)
# ---------------------------------------------------------------------------------------------


def _hj_z01(u: float) -> float:
    f = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-((30.666 / u) ** 0.7528))
    return ETA0 / (2.0 * math.pi) * math.log(f / u + math.sqrt(1.0 + (2.0 / u) ** 2))


def _hj_eeff(u: float, er: float) -> float:
    a = (
        1.0
        + math.log((u**4 + (u / 52.0) ** 2) / (u**4 + 0.432)) / 49.0
        + math.log(1.0 + (u / 18.1) ** 3) / 18.7
    )
    b = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    return (er + 1.0) / 2.0 + (er - 1.0) / 2.0 * (1.0 + 10.0 / u) ** (-a * b)


def hammerstad_jensen(u: float, er: float, t_over_h: float = 0.0) -> tuple[float, float]:
    """Microstrip Z0 and static eeff, u = W/h (Hammerstad & Jensen 1980). Returns ``(z0, eeff)``."""
    if t_over_h <= 0.0:
        eeff = _hj_eeff(u, er)
        return _hj_z01(u) / math.sqrt(eeff), eeff
    coth2 = 1.0 / math.tanh(math.sqrt(6.517 * u)) ** 2
    du1 = t_over_h / math.pi * math.log(1.0 + 4.0 * math.e / (t_over_h * coth2))
    dur = 0.5 * (1.0 + 1.0 / math.cosh(math.sqrt(er - 1.0))) * du1
    u1, ur = u + du1, u + dur
    eeff_r = _hj_eeff(ur, er)
    z0 = _hj_z01(ur) / math.sqrt(eeff_r)
    eeff_t = eeff_r * (_hj_z01(u1) / _hj_z01(ur)) ** 2
    return z0, eeff_t
