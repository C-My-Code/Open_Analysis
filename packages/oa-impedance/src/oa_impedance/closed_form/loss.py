# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wheeler incremental-inductance conductor loss for closed-form lines.

R = omega * dL where dL is the change of the (air-line) external inductance when every metal wall
recedes by delta/2 (Wheeler 1942). Valid for delta << t and delta << W; ``valid`` is False
otherwise. Dielectric loss uses the usual filling-factor form.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from oa_physics import skin_depth
from oa_physics.constants import C0, MU0

__all__ = ["dielectric_loss_np_per_m", "wheeler_conductor_loss_np_per_m"]


def wheeler_conductor_loss_np_per_m(
    z_air: Callable[[float, float, float], float],
    w: float,
    h: float,
    t: float,
    z0: float,
    f: float,
    sigma: float = 5.8e7,
    roughness_k: float = 1.0,
) -> tuple[float, bool]:
    """alpha_c [Np/m]. ``z_air(w, h, t)`` returns the air-line impedance of the geometry.

    Returns ``(alpha, valid)``; ``valid`` is False when delta is not << min(t, w).
    """
    delta = float(skin_depth(sigma, f)[0])
    rs = (1.0 / (sigma * delta)) * roughness_k
    z_a = z_air(w, h, t)
    z_r = z_air(w - delta, h + delta, max(t - delta, 0.0))
    d_l = (z_r - z_a) / C0
    # R = Rs/mu0 * dL/dn, dn = delta/2 (equals omega * dL for a smooth conductor)
    r = (rs / MU0) * d_l / (delta / 2.0)
    valid = delta < 0.25 * min(t if t > 0 else w, w)
    return r / (2.0 * z0), valid


def dielectric_loss_np_per_m(f: float, er: float, eeff: float, tand: float) -> float:
    """alpha_d [Np/m] for a quasi-TEM line with filling factor q = (eeff-1)/(er-1)."""
    if er <= 1.0:
        return 0.0
    return math.pi * f / C0 * (er / math.sqrt(eeff)) * (eeff - 1.0) / (er - 1.0) * tand
