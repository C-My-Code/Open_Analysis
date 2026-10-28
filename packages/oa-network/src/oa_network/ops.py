# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Renormalisation and reference-plane shifting."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
import skrf

__all__ = ["renormalize", "shift_reference_plane"]


def renormalize(net: skrf.Network, z0: float | Sequence[float]) -> skrf.Network:
    """Return ``net`` renormalised to real port impedance(s) ``z0`` [ohm]."""
    out = net.copy()
    out.renormalize(float(z0) if np.isscalar(z0) else np.asarray(z0, dtype=float))
    return out


def shift_reference_plane(
    net: skrf.Network,
    delay: float | Sequence[float],
    *,
    mode: Literal["deembed", "extend"] = "deembed",
) -> skrf.Network:
    """Shift the reference planes by a lossless, matched line of ``delay`` seconds per port.

    ``S'_ij = S_ij exp(+/- j w (tau_i + tau_j))``; ``deembed`` moves the planes into the DUT
    (removes the lead-in, ``+``), ``extend`` moves them away (adds a lead-in, ``-``).
    """
    tau = np.broadcast_to(np.asarray(delay, dtype=float), (net.nports,))
    w = 2.0 * np.pi * net.f
    sign = 1.0 if mode == "deembed" else -1.0
    ph = np.exp(sign * 1j * w[:, None, None] * (tau[None, :, None] + tau[None, None, :]))
    return skrf.Network(frequency=net.frequency, s=net.s * ph, z0=net.z0[0, :], name=net.name)
