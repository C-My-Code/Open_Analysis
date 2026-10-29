# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Seeded problem generators shared by the oa-accel tests."""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp


def random_spd(n: int, seed: int = 0, cond_shift: float = 1.0) -> np.ndarray:
    """Dense SPD matrix with condition number well below 1e6."""
    rng = np.random.default_rng(seed)
    m = rng.standard_normal((n, n))
    return m @ m.T / n + cond_shift * np.eye(n)


def random_general(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, n)) + n**0.5 * np.eye(n)


def grid_laplacian(nx: int, ny: int, seed: int = 0) -> sp.csr_matrix:
    """SPD resistor-grid matrix with random positive conductances and a grounded diagonal."""
    rng = np.random.default_rng(seed)
    n = nx * ny
    idx = np.arange(n).reshape(nx, ny)
    rows, cols, vals = [], [], []
    for a, b in ((idx[:-1, :], idx[1:, :]), (idx[:, :-1], idx[:, 1:])):
        g = rng.uniform(0.5, 2.0, size=a.size)
        rows += [a.ravel(), b.ravel()]
        cols += [b.ravel(), a.ravel()]
        vals += [-g, -g]
    off = sp.csr_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)
    )
    diag = -np.asarray(off.sum(axis=1)).ravel() + 0.01  # small shunt keeps it definite
    return (off + sp.diags(diag)).tocsr()
