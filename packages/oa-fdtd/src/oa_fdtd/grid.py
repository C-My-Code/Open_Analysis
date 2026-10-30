# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Non-uniform Yee grid and the storage layout shared by both engines (ADR-0010 section 2, 7).

Storage: one array per field component, shape ``(B, nz + 2, ny + 2, pitch)`` with C order and x
fastest; ``pitch`` is ``nx + 2`` rounded up to 32 elements (128 B for float32). Along each axis the
storage index ``I`` holds primal node ``i = I - 1`` for node-type components and dual cell
``i = I - 1`` for cell-type components; ``I = 0`` and ``I = n + 1`` are ghost layers used by PMC
mirror walls. ``E_a`` is cell-type along ``a`` and node-type along the other axes; ``H_a`` is
node-type along ``a`` and cell-type along the others.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

C0 = 299792458.0  # [m/s]
MU0 = 4e-7 * np.pi  # [H/m]
EPS0 = 1.0 / (MU0 * C0**2)  # [F/m]
ETA0 = MU0 * C0  # [ohm]
LANE = 32  # row padding [elements]

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Grid:
    """Mesh lines ``x, y, z`` [m], strictly increasing, each with at least two lines."""

    x: FloatArray
    y: FloatArray
    z: FloatArray

    def __post_init__(self) -> None:
        for name in "xyz":
            v = np.asarray(getattr(self, name), dtype=np.float64)
            if v.ndim != 1 or v.size < 2 or np.any(np.diff(v) <= 0):
                raise ValueError(f"grid lines {name} must be 1-D, increasing, with >= 2 entries")
            object.__setattr__(self, name, v)

    @property
    def lines(self) -> tuple[FloatArray, FloatArray, FloatArray]:
        return (self.x, self.y, self.z)

    @property
    def n(self) -> tuple[int, int, int]:
        """Cells per axis ``(nx, ny, nz)`` [-]."""
        return (self.x.size - 1, self.y.size - 1, self.z.size - 1)

    @property
    def cells(self) -> int:
        nx, ny, nz = self.n
        return nx * ny * nz

    @property
    def pitch(self) -> int:
        """Padded x extent of one row [elements]."""
        return -(-(self.n[0] + 2) // LANE) * LANE

    @property
    def shape(self) -> tuple[int, int, int]:
        """Storage shape ``(nz + 2, ny + 2, pitch)`` without the batch dimension."""
        return (self.n[2] + 2, self.n[1] + 2, self.pitch)

    def length(self, axis: int) -> int:
        """Storage extent along ``axis`` (0=x, 1=y, 2=z)."""
        return self.pitch if axis == 0 else self.n[axis] + 2

    def d(self, axis: int) -> FloatArray:
        """Primal cell lengths [m]."""
        return np.diff(self.lines[axis])

    def centers(self, axis: int) -> FloatArray:
        v = self.lines[axis]
        return 0.5 * (v[1:] + v[:-1])

    def coords(self, axis: int, node: bool) -> FloatArray:
        """Coordinates of the node-type or cell-type lattice along ``axis`` [m]."""
        return self.lines[axis] if node else self.centers(axis)

    def inv_primal(self, axis: int) -> FloatArray:
        """``1 / d_i`` at storage index ``I = i + 1`` (cells); zero elsewhere [1/m]."""
        out = np.zeros(self.length(axis))
        out[1 : self.n[axis] + 1] = 1.0 / self.d(axis)
        return out

    def inv_dual(self, axis: int) -> FloatArray:
        """``1 / dual spacing`` at node storage index ``I = i + 1``; zero elsewhere [1/m].

        The dual spacing is ``(d_{i-1} + d_i) / 2``; at a wall the missing cell is the mirror image.
        """
        d = self.d(axis)
        ext = np.concatenate([d[:1], d, d[-1:]])
        out = np.zeros(self.length(axis))
        out[1 : self.n[axis] + 2] = 2.0 / (ext[:-1] + ext[1:])
        return out

    def node_weights(self, axis: int) -> FloatArray:
        """Dual-cell length of each node (half at the walls) at storage index ``I`` [m]."""
        d = self.d(axis)
        ext = np.concatenate([[0.0], d, [0.0]])
        out = np.zeros(self.length(axis))
        out[1 : self.n[axis] + 2] = 0.5 * (ext[:-1] + ext[1:])
        return out

    def cell_weights(self, axis: int) -> FloatArray:
        out = np.zeros(self.length(axis))
        out[1 : self.n[axis] + 1] = self.d(axis)
        return out

    def dt(self, courant: float = 0.95, c_max: float = C0) -> float:
        """Courant limit for the non-uniform grid [s] (ADR-0010 section 2)."""
        s = sum(1.0 / float(self.d(a).min()) ** 2 for a in range(3))
        return courant / (c_max * s**0.5)

    def valid(self, kind: str, comp: int) -> tuple[tuple[int, int], ...]:
        """Storage index ranges ``[lo, hi)`` per axis (x, y, z) that carry real edges.

        ``kind`` is ``"E"`` or ``"H"``; the component axis is cell-type for E and node-type for H.
        """
        out = []
        for a in range(3):
            n = self.n[a]
            node_type = (a != comp) if kind == "E" else (a == comp)
            out.append((1, n + 2) if node_type else (1, n + 1))
        return tuple(out)

    def points(
        self, kind: str, comp: int, lo: tuple[float, float, float], hi: tuple[float, float, float]
    ) -> tuple[npt.NDArray[np.intp], tuple[int, int, int]]:
        """Flat storage indices of the lattice points of a component inside the closed box.

        Returns ``(flat, counts)``, ordered z, y, x; ``counts`` is the number of points per axis
        ``(nx, ny, nz)``. Raises ``ValueError`` when the box holds no point of an axis.
        """
        sel: list[npt.NDArray[np.intp]] = []
        for a in range(3):
            node_type = (a != comp) if kind == "E" else (a == comp)
            c = self.coords(a, node_type)
            tol = 1e-6 * float(self.d(a).min())
            idx = np.flatnonzero((c >= lo[a] - tol) & (c <= hi[a] + tol)) + 1
            if idx.size == 0 and hi[a] - lo[a] <= 2.0 * tol:  # point box: snap to the nearest
                idx = np.array([int(np.argmin(np.abs(c - 0.5 * (lo[a] + hi[a])))) + 1])
            if idx.size == 0:
                raise ValueError(f"box selects no {kind}{'xyz'[comp]} point along {'xyz'[a]}")
            sel.append(idx.astype(np.intp))
        kk, jj, ii = np.meshgrid(sel[2], sel[1], sel[0], indexing="ij")
        flat = np.ravel_multi_index((kk.ravel(), jj.ravel(), ii.ravel()), self.shape)
        return flat.astype(np.intp), (sel[0].size, sel[1].size, sel[2].size)
