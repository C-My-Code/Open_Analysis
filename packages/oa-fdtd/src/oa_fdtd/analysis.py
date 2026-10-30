# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Post-processing helpers: matrix-pencil mode extraction and power-wave S-parameters."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
ComplexArray = npt.NDArray[np.complex128]


def matrix_pencil(
    y: FloatArray, dt: float, order: int, decimate: int = 1, pencil: float = 0.4
) -> tuple[FloatArray, FloatArray]:
    """Frequencies [Hz] and damping rates [1/s] of ``order`` complex exponentials in ``y``.

    ``order`` counts complex poles, so a real sinusoid needs 2. Returns only the non-negative
    frequencies, sorted ascending.
    """
    x = np.asarray(y[::decimate], dtype=np.float64)
    h = dt * decimate
    n = x.size
    ell = int(pencil * n)
    rows = n - ell
    idx = np.arange(rows)[:, None] + np.arange(ell + 1)[None, :]
    hank = x[idx]
    _, _, vh = np.linalg.svd(hank, full_matrices=False)
    v = vh[:order].conj().T  # (ell + 1, order)
    v1, v2 = v[:-1], v[1:]
    z = np.linalg.eigvals(np.linalg.pinv(v1) @ v2)
    s = np.log(z.astype(np.complex128)) / h
    f = s.imag / (2.0 * np.pi)
    keep = f >= 0
    order_ix = np.argsort(f[keep])
    return f[keep][order_ix], (-s.real[keep])[order_ix]


def power_wave_s(v: ComplexArray, vs: ComplexArray, r_ohm: FloatArray) -> ComplexArray:
    """S-matrix from lumped-port voltages (ADR-0010 section 6).

    ``v[i, j, f]`` is the voltage DFT at port ``i`` when port ``j`` is driven with source
    spectrum ``vs[j, f]``; ``r_ohm[i]`` the port resistances. The incident wave on the driven
    port is ``Vs / (2 sqrt(R))`` and the outgoing wave ``V / sqrt(R) - a`` (so the result does
    not assume the other ports were matched). Returns ``S[i, j, f]``.
    """
    n = v.shape[0]
    s = np.zeros_like(v)
    for j in range(n):
        a_j = vs[j] / (2.0 * np.sqrt(r_ohm[j]))
        for i in range(n):
            b_i = v[i, j] / np.sqrt(r_ohm[i]) - (a_j if i == j else 0.0)
            s[i, j] = b_i / a_j
    return s
