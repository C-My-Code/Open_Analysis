# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Independent raster FVM reference solver for the DC network (WP-204 cross-check).

Same physical problem as ``oa_pi.dc.fem`` (it consumes the same ``DcNetwork``), different
discretization: every sheet is sampled on a square grid of cell size ``h``; two neighbouring
copper cells are joined by the conductance ``1/R_s`` (for square cells it is independent of
``h``); a cell is copper when its centre lies inside the copper polygons (staircase edges).

- Pins: the cells whose centre is inside the pad are one node.
- Barrels: the via contact is the smallest lattice disc around the nearest cell whose
  equivalent radius (from a local lattice solve, 0.1986 h for one cell) is at least the barrel
  radius; the excess spreading resistance ``R_s ln(r_eq / r_out) / (2 pi)`` is added in series.
  It makes the result converge as ``h -> 0`` without resolving the (sub-cell) via ring.
- Terminals and the regulator reuse ``oa_pi.dc.terminals`` (group merging, loads, Dirichlet,
  Thevenin, remote sense), which is plain circuit algebra and not a discretization.

Limits: contacts of different barrels must not share cells (via pitch > 2 cells), sheets thinner
than ``h`` are not resolved. Use it to verify the FEM, not to analyse boards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import cache

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp
import scipy.sparse.linalg as spla
import shapely
from shapely.ops import unary_union

from oa_physics import resistivity
from oa_pi.dc.network import DcNetwork, Pin
from oa_pi.dc.terminals import TerminalSolution, labels, solve_terminals
from oa_pi.errors import PiGeometryError

__all__ = ["RasterSolution", "solve_raster"]

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


@dataclass
class RasterSolution:
    h: float
    n_cells: int
    terminal: TerminalSolution

    def load_voltage(self, name: str) -> float:
        """Rail-to-return voltage of load ``name`` [V] (return = 0 V when there is no return)."""
        ts = self.terminal
        v = ts.group_voltage[f"load:{name}"]
        return v - ts.group_voltage.get(f"load_ret:{name}", 0.0)


_RL = 45  # radius of the local lattice problem [cells]


@cache
def _patch_req(rho2: int) -> float:
    """Equivalent radius [cells] of the cell patch ``{|v|^2 <= rho2}`` on the unit lattice.

    Solves a local lattice problem (patch at 1 V, a ring of radius ``_RL`` at 0 V) and inverts
    ``R = ln(R_out / r_eq) / (2 pi)``; the single cell gives 0.1986 (lattice Green function).
    """
    n = 2 * _RL + 3
    yy, xx = np.mgrid[0:n, 0:n] - (n - 1) // 2
    rr = np.hypot(xx, yy)
    inside = rr <= _RL
    idx = np.full((n, n), -1, dtype=np.int64)
    idx[inside] = np.arange(int(inside.sum()))
    ei: list[I64] = []
    ej: list[I64] = []
    for a, b in ((idx[:, :-1], idx[:, 1:]), (idx[:-1, :], idx[1:, :])):
        ok = (a >= 0) & (b >= 0)
        ei.append(a[ok])
        ej.append(b[ok])
    i, j = np.concatenate(ei), np.concatenate(ej)
    m = int(inside.sum())
    g = np.ones(len(i))
    k = sp.coo_matrix(
        (
            np.concatenate([g, g, -g, -g]),
            (np.concatenate([i, j, i, j]), np.concatenate([i, j, j, i])),
        ),
        shape=(m, m),
    ).tocsr()
    patch = inside & (xx**2 + yy**2 <= rho2)
    outer = inside & (rr > _RL - 1.0)
    fixed = np.zeros(m, dtype=bool)
    fixed[idx[patch]] = True
    fixed[idx[outer]] = True
    v = np.zeros(m)
    v[idx[patch]] = 1.0
    free = ~fixed
    v[free] = spla.spsolve(k[free][:, free].tocsc(), -k[free][:, fixed] @ v[fixed])
    current = float((k @ v)[idx[patch]].sum())
    return float((_RL - 0.5) * math.exp(-2.0 * math.pi / current))


def _disc_rho2(radius_cells: float) -> int:
    """Smallest squared lattice radius whose patch has an equivalent radius >= ``radius_cells``."""
    cand = sorted({i * i + j * j for i in range(0, 30) for j in range(0, 30)})
    for r2 in cand:
        if _patch_req(r2) >= radius_cells:
            return r2
    # very large contact: equal-area radius minus the boundary-layer shift of the lattice
    return round((radius_cells + 0.31) ** 2)


def _disc_offsets(radius_cells: float) -> I64:
    r2 = _disc_rho2(radius_cells)
    r = math.isqrt(r2) + 1
    ii, jj = np.meshgrid(np.arange(-r, r + 1), np.arange(-r, r + 1))
    keep = ii**2 + jj**2 <= r2
    return np.column_stack([ii[keep], jj[keep]]).astype(np.int64)


def _disc_req(radius_cells: float) -> float:
    """Equivalent radius [cells] of the contact patch chosen for ``radius_cells``."""
    r2 = _disc_rho2(radius_cells)
    return _patch_req(r2) if r2 < 900 else radius_cells


def solve_raster(net: DcNetwork, h: float) -> RasterSolution:
    """Solve ``net`` on a square grid of cell size ``h`` [m]."""
    allg = unary_union([p for s in net.sheets for p in s.polygons])
    x0, y0, x1, y1 = allg.bounds
    nx, ny = math.ceil((x1 - x0) / h) + 1, math.ceil((y1 - y0) / h) + 1
    xs = x0 + (np.arange(nx) + 0.5) * h
    ys = y0 + (np.arange(ny) + 0.5) * h
    cx, cy = np.meshgrid(xs, ys)  # (ny, nx)
    xy = np.column_stack([cx.ravel(), cy.ravel()])

    n_cells = 0
    index: dict[str, I64] = {}  # sheet key -> (ny*nx,) global cell index or -1
    rho_cell: dict[str, F64] = {}
    rows: list[I64] = []
    cols: list[I64] = []
    vals: list[F64] = []
    for s in net.sheets:
        mask = shapely.contains_xy(unary_union(s.polygons), xy[:, 0], xy[:, 1])
        idx = np.full(nx * ny, -1, dtype=np.int64)
        idx[mask] = n_cells + np.arange(int(mask.sum()))
        n_cells += int(mask.sum())
        index[s.key] = idx
        t = (
            np.asarray(net.temperature(s.layer, xy), dtype=np.float64)
            if callable(net.temperature)
            else np.full(len(xy), float(net.temperature))
        )
        rho_cell[s.key] = np.asarray(resistivity(s.material, t), dtype=np.float64)
        rs = rho_cell[s.key] / s.thickness
        grid = idx.reshape(ny, nx)
        rsg = rs.reshape(ny, nx)
        for a, b, ra, rb in (
            (grid[:, :-1], grid[:, 1:], rsg[:, :-1], rsg[:, 1:]),
            (grid[:-1, :], grid[1:, :], rsg[:-1, :], rsg[1:, :]),
        ):
            ok = (a >= 0) & (b >= 0)
            g = 2.0 / (ra[ok] + rb[ok])  # series of two half cells
            rows.append(a[ok])
            cols.append(b[ok])
            vals.append(g)

    def cells_in(key: str, geom: shapely.Geometry) -> I64:
        idx = index[key]
        inside = shapely.contains_xy(geom.buffer(1e-9), xy[:, 0], xy[:, 1])
        return idx[inside & (idx >= 0)]

    groups: list[I64] = []
    pins: dict[str, Pin] = {p.id: p for p in net.all_pins()}
    pin_cells: dict[str, I64] = {}
    for pid, p in pins.items():
        key = f"{p.layer}:{p.net}"
        c = cells_in(key, p.region) if key in index else np.zeros(0, np.int64)
        if len(c) == 0 and key in index:  # pad smaller than a cell: take the nearest copper cell
            ok = np.flatnonzero(index[key] >= 0)
            d = np.hypot(*(xy[ok] - np.array(p.region.centroid.coords[0])).T)
            c = index[key][ok[[int(np.argmin(d))]]]
        pin_cells[pid] = c
        if len(c):
            groups.append(c)

    # barrel nodes: one extra unknown per (barrel, layer) joined to its contact by R_corr
    extra = 0
    contact_of: dict[tuple[str, str], tuple[int, float]] = {}  # -> (cell index, R_corr)
    contact_sets: dict[tuple[str, str], I64] = {}
    for b in net.barrels:
        for lid in b.layers:
            if lid in b.pin_ends:
                continue
            key = f"{lid}:{b.net}"
            idx = index[key]
            ok = np.flatnonzero(idx >= 0)
            d = np.hypot(*(xy[ok] - np.array(b.xy)).T)
            c0 = int(ok[int(np.argmin(d))])  # patch centre: the copper cell nearest the via
            iy0, ix0 = divmod(c0, nx)
            offs = _disc_offsets(b.r_out / h)
            iy, ix = iy0 + offs[:, 1], ix0 + offs[:, 0]
            good = (iy >= 0) & (iy < ny) & (ix >= 0) & (ix < nx)
            flat = (iy * nx + ix)[good]
            cells = idx[flat][idx[flat] >= 0]
            sheet = net.sheet(key)
            rs_loc = float(rho_cell[key][c0] / sheet.thickness)
            r_corr = rs_loc / (2.0 * math.pi) * math.log(_disc_req(b.r_out / h) * h / b.r_out)
            contact_sets[(b.id, lid)] = cells
            contact_of[(b.id, lid)] = (n_cells + extra, max(r_corr, 0.0))
            extra += 1
            groups.append(cells)
    n_tot = n_cells + extra + len(pins)
    pin_virtual = {pid: n_cells + extra + k for k, pid in enumerate(sorted(pins))}
    for pid, c in pin_cells.items():
        groups.append(np.concatenate([[pin_virtual[pid]], c]).astype(np.int64))
    lab, n_a = labels(n_tot, groups)

    # barrel stamps (level-A labels)
    br: list[int] = []
    bc: list[int] = []
    bg: list[float] = []
    for b in net.barrels:
        nodes: list[int] = []
        for lid in b.layers:
            if lid in b.pin_ends:
                nodes.append(int(lab[pin_virtual[b.pin_ends[lid]]]))
                continue
            node, r_corr = contact_of[(b.id, lid)]
            cell_dof = int(lab[contact_sets[(b.id, lid)][0]])
            if r_corr > 0:
                br.append(int(lab[node]))
                bc.append(cell_dof)
                bg.append(1.0 / r_corr)
                nodes.append(int(lab[node]))
            else:
                nodes.append(cell_dof)
        for k, r in enumerate(b.seg_r):
            br.append(nodes[k])
            bc.append(nodes[k + 1])
            bg.append(1.0 / r)

    i = np.concatenate([lab[np.concatenate(rows)], np.array(br, dtype=np.int64)])
    j = np.concatenate([lab[np.concatenate(cols)], np.array(bc, dtype=np.int64)])
    g = np.concatenate([np.concatenate(vals), np.array(bg)])
    k_a = sp.coo_matrix(
        (
            np.concatenate([g, g, -g, -g]),
            (np.concatenate([i, j, i, j]), np.concatenate([i, j, j, i])),
        ),
        shape=(n_a, n_a),
    ).tocsr()
    pin_dof = {pid: int(lab[v]) for pid, v in pin_virtual.items()}
    for pid, d in pin_dof.items():
        if k_a.indptr[d + 1] == k_a.indptr[d]:
            raise PiGeometryError(
                f"pin {pins[pid].refdes}.{pins[pid].number} touches no raster cell",
                path=pid,
                hint="Use a finer raster (smaller h).",
            )
    return RasterSolution(h, n_cells, solve_terminals(net, k_a, pin_dof, net.cfg.mesh.solver))
