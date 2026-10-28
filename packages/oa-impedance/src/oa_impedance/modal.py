# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Characteristic impedance, modal decomposition and mixed-mode quantities from RLGC.

Zc = (Z Y)^(1/2) Y^-1 with Z = R + jwL, Y = G + jwC (lossless Zc = (LC)^(1/2) C^-1 at f = 0).
Modes are the eigenvectors of Z Y. For N == 2 the modal order is ``[even-like, odd-like]``
(by the eigenvector pattern), otherwise ascending effective permittivity.
``zc_modal[m] = v_m^T Zc v_m / v_m^T v_m``; for a mirror-symmetric pair these are exactly
``Zeven = Zc11 + Zc12`` and ``Zodd = Zc11 - Zc12``.
Mixed-mode (research 02 sec. 3.4): ``Zdiff = Zc11 + Zc22 - Zc12 - Zc21``,
``Zcomm = (Zc11 + Zc22 + Zc12 + Zc21) / 4``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from oa_impedance._physics_compat import C0

C128 = npt.NDArray[np.complex128]
F64 = npt.NDArray[np.float64]

__all__ = ["ModalResult", "modal_decomposition"]

_NEPER_TO_DB = 20.0 / np.log(10.0)


@dataclass
class ModalResult:
    zc_modal: C128  # (F, N)
    eps_eff: F64  # (F, N)
    zc_matrix: C128  # (F, N, N)
    delay: F64  # (F, N) s/m
    loss_db: F64  # (F, N) dB/m
    z0: F64 | None
    z_diff: F64 | None
    z_comm: F64 | None
    z_odd: F64 | None
    z_even: F64 | None


def _sqrt_branch(lam: C128) -> C128:
    g = np.sqrt(lam.astype(np.complex128))
    return np.where(g.real < 0, -g, g)


def modal_decomposition(freqs: F64, R: F64, L: F64, G: F64, C: F64) -> ModalResult:
    nf, n, _ = L.shape
    zc_modal = np.zeros((nf, n), dtype=np.complex128)
    eps_eff = np.zeros((nf, n))
    zc_mat = np.zeros((nf, n, n), dtype=np.complex128)
    delay = np.zeros((nf, n))
    loss = np.zeros((nf, n))
    for i, f in enumerate(freqs):
        w = 2.0 * np.pi * f
        if f <= 0.0:
            m = L[i] @ C[i]
            y = C[i].astype(np.complex128)
            lam, t = np.linalg.eig(m)
            gam = np.sqrt(lam.astype(np.complex128))  # gamma / (j w)
            gm = t @ np.diag(gam) @ np.linalg.inv(t)
            zc = gm @ np.linalg.inv(y)
            dly = gam.real
            alpha = np.zeros(n)
        else:
            z = R[i] + 1j * w * L[i]
            y = G[i] + 1j * w * C[i]
            lam, t = np.linalg.eig(z @ y)
            gam = _sqrt_branch(lam)
            gm = t @ np.diag(gam) @ np.linalg.inv(t)
            zc = gm @ np.linalg.inv(y)
            dly = gam.imag / w
            alpha = gam.real
        zc = 0.5 * (zc + zc.T)
        ee = (dly * C0) ** 2
        # modal impedance of each eigenvector
        zm = np.empty(n, dtype=np.complex128)
        for k in range(n):
            v = t[:, k]
            zm[k] = (v @ zc @ v) / (v @ v)
        order = np.argsort(ee) if n != 2 else _even_odd_order(t)
        zc_modal[i] = zm[order]
        eps_eff[i] = ee[order]
        delay[i] = dly[order]
        loss[i] = (alpha[order] if f > 0 else alpha) * _NEPER_TO_DB
        zc_mat[i] = zc
    z0 = z_diff = z_comm = z_odd = z_even = None
    if n == 1:
        z0 = np.abs(zc_mat[:, 0, 0])
    if n == 2:
        z_diff = (zc_mat[:, 0, 0] + zc_mat[:, 1, 1] - zc_mat[:, 0, 1] - zc_mat[:, 1, 0]).real
        z_comm = (zc_mat[:, 0, 0] + zc_mat[:, 1, 1] + zc_mat[:, 0, 1] + zc_mat[:, 1, 0]).real / 4.0
        z_odd = z_diff / 2.0
        z_even = z_comm * 2.0
    return ModalResult(zc_modal, eps_eff, zc_mat, delay, loss, z0, z_diff, z_comm, z_odd, z_even)


def _even_odd_order(t: C128) -> npt.NDArray[np.intp]:
    """Index order [even-like, odd-like] for the eigenvector matrix of a 2-conductor line."""
    even_score = np.abs(t[0, :] + t[1, :]) - np.abs(t[0, :] - t[1, :])
    i_even = int(np.argmax(even_score))
    return np.array([i_even, 1 - i_even])
