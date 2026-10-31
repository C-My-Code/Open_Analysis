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

from oa_physics.constants import C0

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
    """All frequencies at once: batched LAPACK calls (one per quantity, not one per frequency)."""
    n = L.shape[1]
    f = np.asarray(freqs, dtype=float)
    w = 2.0 * np.pi * f
    static = f <= 0.0
    ws = np.where(static, 1.0, w)[:, None, None]  # static rows use the lossless product below
    z = np.where(static[:, None, None], 0.0, R + 1j * ws * L)
    y = np.where(static[:, None, None], C.astype(np.complex128), G + 1j * ws * C)
    m = np.where(static[:, None, None], L @ C, z @ y)
    lam, t = np.linalg.eig(m)
    gam = np.sqrt(lam.astype(np.complex128))  # static: gamma / (j w)
    dyn = np.where(gam.real < 0, -gam, gam)
    gam = np.where(static[:, None], gam, dyn)
    ti = np.linalg.inv(t)
    gm = (t * gam[:, None, :]) @ ti
    zc = gm @ np.linalg.inv(y)
    dly = np.where(static[:, None], gam.real, gam.imag / ws[:, :, 0])
    alpha = np.where(static[:, None], 0.0, gam.real)
    zc = 0.5 * (zc + zc.transpose(0, 2, 1))
    ee = (dly * C0) ** 2
    # modal impedance of each eigenvector
    zm = np.einsum("fik,fij,fjk->fk", t, zc, t) / np.einsum("fik,fik->fk", t, t)
    if n == 2:
        even_score = np.abs(t[:, 0, :] + t[:, 1, :]) - np.abs(t[:, 0, :] - t[:, 1, :])
        i_even = np.argmax(even_score, axis=1)
        order = np.stack([i_even, 1 - i_even], axis=1)
    else:
        order = np.argsort(ee, axis=1)
    zc_modal = np.take_along_axis(zm, order, axis=1)
    eps_eff = np.take_along_axis(ee, order, axis=1)
    delay = np.take_along_axis(dly, order, axis=1)
    loss = np.take_along_axis(alpha, order, axis=1) * _NEPER_TO_DB
    zc_mat = zc
    z0 = z_diff = z_comm = z_odd = z_even = None
    if n == 1:
        z0 = np.abs(zc_mat[:, 0, 0])
    if n == 2:
        z_diff = (zc_mat[:, 0, 0] + zc_mat[:, 1, 1] - zc_mat[:, 0, 1] - zc_mat[:, 1, 0]).real
        z_comm = (zc_mat[:, 0, 0] + zc_mat[:, 1, 1] + zc_mat[:, 0, 1] + zc_mat[:, 1, 0]).real / 4.0
        z_odd = z_diff / 2.0
        z_even = z_comm * 2.0
    return ModalResult(zc_modal, eps_eff, zc_mat, delay, loss, z0, z_diff, z_comm, z_odd, z_even)
