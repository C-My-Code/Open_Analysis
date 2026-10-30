# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""De-embedding: reference-plane shift from a measured gamma, and a 2x-thru hook (ADR-0010 s. 6)."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]

# ``hook(S, frequencies) -> S'``: for example an ``oa_network`` IEEE 370 2x-thru de-embedder.
DeembedHook = Callable[[ComplexArray, FloatArray], ComplexArray]


def gamma_from_planes(
    v1: ComplexArray, v2: ComplexArray, v3: ComplexArray, spacing: float
) -> ComplexArray:
    """Propagation constant [1/m] of a uniform line from the modal voltages at three planes spaced
    ``spacing`` [m] apart (``v2`` in the middle).

    ``V(z) = A e^{-g z} + B e^{g z}`` gives ``cosh(g d) = (V1 + V3) / (2 V2)`` whatever the
    reflection, so the matched termination assumption of a two-plane ratio is not needed. The
    branch has ``Re g >= 0``; the phase is unwrapped along frequency.
    """
    c = (v1 + v3) / (2.0 * v2)
    g = np.arccosh(c.astype(np.complex128)) / spacing
    g = np.where(g.real < 0, -g, g)
    # arccosh returns Im in [0, pi]: unwrap beta d along frequency
    beta = np.unwrap(g.imag * spacing) / spacing
    return np.asarray(g.real + 1j * beta, dtype=np.complex128)


def shift_reference(
    s: ComplexArray, gamma: Sequence[ComplexArray] | ComplexArray, lengths: Sequence[float]
) -> ComplexArray:
    """Move the reference plane of port ``i`` inwards by ``lengths[i]`` [m].

    ``S'_ij = S_ij exp(g_i L_i) exp(g_j L_j)`` (the line section between the old and the new plane
    is removed; the reference impedance is unchanged). ``gamma`` is one array for all ports or one
    per port; a negative length moves the plane outwards.
    """
    n = s.shape[0]
    gam = [np.asarray(gamma)] * n if np.ndim(gamma[0]) == 0 else [np.asarray(g) for g in gamma]  # type: ignore[arg-type]
    fac = [np.exp(gam[i] * lengths[i]) for i in range(n)]
    out = np.empty_like(s)
    for i in range(n):
        for j in range(n):
            out[i, j] = s[i, j] * fac[i] * fac[j]
    return out


def apply_hook(s: ComplexArray, freqs: FloatArray, hook: DeembedHook) -> ComplexArray:
    """Run a fixture de-embedder (2x-thru through ``oa_network``) on ``S[i, j, f]``."""
    return hook(s, freqs)
