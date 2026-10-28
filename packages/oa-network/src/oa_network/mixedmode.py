# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Single-ended to mixed-mode conversion."""

from __future__ import annotations

import numpy as np
import skrf

__all__ = ["mixed_mode_matrix", "se2gmm"]


def mixed_mode_matrix(n_pairs: int) -> np.ndarray:
    """Unitary wave transform ``b_mm = M a_se``.

    Single-ended ports are ordered by pair: ``(+1, -1, +2, -2, ...)``, i.e. ports ``2k`` and
    ``2k + 1`` form pair ``k``. Mixed-mode ports are ordered all differential first, then all
    common: ``(d1 .. dp, c1 .. cp)``, with ``d = (a+ - a-)/sqrt2`` and ``c = (a+ + a-)/sqrt2``.
    """
    p = n_pairs
    m = np.zeros((2 * p, 2 * p))
    r = 1.0 / np.sqrt(2.0)
    for k in range(p):
        m[k, 2 * k], m[k, 2 * k + 1] = r, -r
        m[p + k, 2 * k], m[p + k, 2 * k + 1] = r, r
    return m


def se2gmm(net: skrf.Network) -> skrf.Network:
    """Convert a ``2p``-port single-ended network to mixed mode (see :func:`mixed_mode_matrix`).

    The returned network is normalised to ``2 z0`` (differential) and ``z0 / 2`` (common) per pair,
    which is what the unitary transform implies for equal single-ended ``z0``.
    """
    n = net.nports
    if n % 2:
        raise ValueError("mixed-mode conversion needs an even number of ports")
    p = n // 2
    m = mixed_mode_matrix(p)
    s = m @ net.s @ m.T
    z0 = net.z0[0, :].real
    z_pair = z0[0::2]
    z_mm = np.concatenate([2.0 * z_pair, z_pair / 2.0])
    return skrf.Network(frequency=net.frequency, s=s, z0=z_mm, name=net.name)
