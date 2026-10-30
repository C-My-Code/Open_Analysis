# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Edge classification of a copper box: PEC interior and surface-impedance face edges.

Convention shared with the E update: the edge ``E_c`` at storage index ``p`` is circulated by
``H_b`` at ``p`` and ``p - s_a`` and by ``H_a`` at ``p`` and ``p - s_b``, with ``(c, a, b)`` cyclic.
With the outward normal ``n`` (from the metal into the dielectric), ``E_t = Zs n x H_t`` gives

- face normal ``+a``: ``E_c = +Zs H_b[p]``;  face normal ``-a``: ``E_c = -Zs H_b[p - s_a]``;
- face normal ``+b``: ``E_c = -Zs H_a[p]``;  face normal ``-b``: ``E_c = +Zs H_a[p - s_b]``.

An edge on two faces (a box edge line along the current) averages the two outside samples.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import Grid


@dataclass
class ConductorEdges:
    """Edges of one box, as ``(k, j, i)`` index arrays of the ``(nz + 1, ny + 1, nx + 1)`` edge
    lattice (the lattice the PEC masks use)."""

    interior: list[tuple[npt.NDArray[np.intp], ...]]  # per component c
    surface: list[tuple[npt.NDArray[np.intp], ...]]
    # per component: face flags (+1 / -1 / 0) of the transverse axes a and b, per surface edge
    face_a: list[npt.NDArray[np.int8]]
    face_b: list[npt.NDArray[np.int8]]


def classify_box(g: Grid, lo: tuple[float, ...], hi: tuple[float, ...]) -> ConductorEdges:
    """Split the E edges inside the closed box into PEC interior and face (surface) edges."""
    interior: list[tuple[npt.NDArray[np.intp], ...]] = []
    surface: list[tuple[npt.NDArray[np.intp], ...]] = []
    fa_all: list[npt.NDArray[np.int8]] = []
    fb_all: list[npt.NDArray[np.int8]] = []
    for c in range(3):
        a, b = (c + 1) % 3, (c + 2) % 3
        ids: dict[int, npt.NDArray[np.intp]] = {}
        flag: dict[int, npt.NDArray[np.int8]] = {}
        for ax in range(3):
            node = ax != c
            co = g.coords(ax, node)
            tol = 1e-6 * float(np.min(g.d(ax)))
            sel = np.flatnonzero((co >= lo[ax] - tol) & (co <= hi[ax] + tol))
            ids[ax] = sel
            f = np.zeros(sel.size, dtype=np.int8)
            if node:
                f[np.abs(co[sel] - hi[ax]) <= tol] = 1
                f[np.abs(co[sel] - lo[ax]) <= tol] = -1
            flag[ax] = f
        if min(v.size for v in ids.values()) == 0:
            empty = tuple(np.zeros(0, np.intp) for _ in range(3))
            interior.append(empty)
            surface.append(empty)
            fa_all.append(np.zeros(0, np.int8))
            fb_all.append(np.zeros(0, np.int8))
            continue
        kk, jj, ii = np.meshgrid(ids[2], ids[1], ids[0], indexing="ij")
        fl = np.meshgrid(flag[2], flag[1], flag[0], indexing="ij")
        fa = fl[2 - a].ravel()
        fb = fl[2 - b].ravel()
        on_face = (fa != 0) | (fb != 0)
        idx = (kk.ravel(), jj.ravel(), ii.ravel())
        interior.append(tuple(v[~on_face] for v in idx))
        surface.append(tuple(v[on_face] for v in idx))
        fa_all.append(fa[on_face])
        fb_all.append(fb[on_face])
    return ConductorEdges(interior, surface, fa_all, fb_all)


def strides(g: Grid) -> tuple[int, int, int]:
    """Flat storage strides of x, y, z."""
    return (1, g.shape[2], g.shape[2] * g.shape[1])


def outside_samples(
    g: Grid,
    c: int,
    k: npt.NDArray[np.intp],
    j: npt.NDArray[np.intp],
    i: npt.NDArray[np.intp],
    fa: npt.NDArray[np.int8],
    fb: npt.NDArray[np.int8],
) -> tuple[
    npt.NDArray[np.int64], npt.NDArray[np.int32], npt.NDArray[np.int64], npt.NDArray[np.float64]
]:
    """Flat edge index, H components ``(ne, 2)``, flat H indices ``(ne, 2)`` and signs ``(ne, 2)``."""  # noqa: E501
    a, b = (c + 1) % 3, (c + 2) % 3
    st = strides(g)
    p = np.ravel_multi_index((k + 1, j + 1, i + 1), g.shape).astype(np.int64)
    n = p.size
    hcomp = np.zeros((n, 2), np.int32)
    hflat = np.zeros((n, 2), np.int64)
    hsign = np.zeros((n, 2), np.float64)
    count = ((fa != 0).astype(int) + (fb != 0).astype(int)).astype(np.float64)
    # slot 0: face along a -> H_b ; slot 1: face along b -> H_a
    m0 = fa != 0
    hcomp[:, 0] = b
    hflat[:, 0] = np.where(fa > 0, p, p - st[a])
    hsign[:, 0] = np.where(m0, np.where(fa > 0, 1.0, -1.0) / np.maximum(count, 1), 0.0)
    m1 = fb != 0
    hcomp[:, 1] = a
    hflat[:, 1] = np.where(fb > 0, p, p - st[b])
    hsign[:, 1] = np.where(m1, np.where(fb > 0, -1.0, 1.0) / np.maximum(count, 1), 0.0)
    return p, hcomp, hflat, hsign
