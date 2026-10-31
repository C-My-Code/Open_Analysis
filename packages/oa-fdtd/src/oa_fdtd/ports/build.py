# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile a :class:`~oa_fdtd.model.ModePort` into edge lists, loading and probe weights.

The port plane is a sheet of in-plane E edges. Each edge ``e`` carries the loading conductivity
``sigma_e = kappa sqrt(eps_e)`` (a local admittance that follows the dielectric under the mode)
and the source field ``V_s(t) t_e`` where ``t_e`` is the modal template [1/m]. ``kappa`` makes the
sheet dissipate ``V^2 / Z`` for a modal voltage ``V`` (Thevenin source with ``Z`` the modal
impedance), and the voltage probe projects the plane field with the same weights, so
``V = V_s - Z I`` holds exactly as for a lumped port.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.grid import Grid
from oa_fdtd.ports.modal import ModeSet, solve_modes

if TYPE_CHECKING:
    from oa_fdtd.model import Model, ModePort

FloatArray = npt.NDArray[np.float64]


def _plane_index(g: Grid, port: ModePort) -> int:
    """Node index of the port plane along ``port.axis``."""
    line = g.lines[port.axis]
    return int(np.argmin(np.abs(line - port.position)))


def _in_box(
    co_u: FloatArray, co_v: FloatArray, lo: tuple[float, float], hi: tuple[float, float], tol: float
) -> npt.NDArray[np.bool_]:
    mu = (co_u >= lo[0] - tol) & (co_u <= hi[0] + tol)
    mv = (co_v >= lo[1] - tol) & (co_v <= hi[1] + tol)
    return np.asarray(mu[:, None] & mv[None, :])


def _overlap(
    lo: tuple[float, float],
    hi: tuple[float, float],
    sig: tuple[tuple[float, float], tuple[float, float]],
    tol: float,
) -> bool:
    """Whether the rectangle ``lo..hi`` and the signal rectangle share an interior point."""
    (slo, shi) = sig
    return all(lo[a] < shi[a] - tol and slo[a] < hi[a] - tol for a in (0, 1))


def _physical_extent(g: Grid, c: Any, tol: float) -> tuple[list[float], list[float]]:
    """``(lo, hi)`` of a conductor box with its x/y faces moved out to the physical edge:
    ``e + edge_frac (e' - e)`` with ``e'`` the next mesh line outward (no move on the mesh ends)."""
    lo, hi = list(c.lo), list(c.hi)
    frac = float(getattr(c, "edge_frac", 0.0))
    if frac <= 0.0:
        return lo, hi
    for a in (0, 1):
        lines = g.lines[a]
        for ext, step in ((lo, -1), (hi, 1)):
            i = int(np.argmin(np.abs(lines - ext[a])))
            if abs(lines[i] - ext[a]) > tol or not 0 <= i + step < lines.size:
                continue
            ext[a] = float(lines[i] + frac * (lines[i + step] - lines[i]))
    return lo, hi


def _refine(
    base: FloatArray, extra: list[float], tol: float
) -> tuple[FloatArray, npt.NDArray[np.intp]]:
    """``base`` lines plus the ``extra`` ones strictly inside its span; returns the lines and the
    index of every base line in them."""
    inner = [x for x in extra if base[0] + tol < x < base[-1] - tol]
    # geometric grading into every edge: the static potential is singular there
    graded = list(inner)
    for x in inner:
        h = float(np.min(np.abs(base - x)[np.abs(base - x) > tol], initial=np.inf))
        h = min(h, float(np.min(np.diff(base))) if base.size > 1 else h)
        for k in range(1, 5):
            graded += [x - h / 3.0**k, x + h / 3.0**k]
    inner = [x for x in graded if base[0] + tol < x < base[-1] - tol]
    keep = [x for x in inner if np.min(np.abs(base - x)) > tol]
    lines = np.unique(np.concatenate([base, np.asarray(keep, dtype=np.float64)]))
    if lines.size > 1:  # merge extras that nearly coincide
        lines = lines[np.concatenate([[True], np.diff(lines) > tol])]
    idx = np.array([int(np.argmin(np.abs(lines - b))) for b in base], dtype=np.intp)
    return lines, idx


def solve_port_modes(
    model: Model,
    g: Grid,
    port: ModePort,
    eps_cell_axis: FloatArray,
) -> tuple[ModeSet, dict[str, Any]]:
    """Solve the modes of ``port``'s rectangle; ``eps_cell_axis`` is the ``(nz, ny, nx)`` relative
    permittivity of the cells (real, at the band centre)."""
    n = port.axis
    u_ax, v_ax = [a for a in range(3) if a != n]
    m = _plane_index(g, port)
    tol = 1e-6 * float(min(np.min(g.d(a)) for a in range(3)))
    lines_u, lines_v = g.lines[u_ax], g.lines[v_ax]
    su = np.flatnonzero((lines_u >= port.lo[0] - tol) & (lines_u <= port.hi[0] + tol))
    sv = np.flatnonzero((lines_v >= port.lo[1] - tol) & (lines_v <= port.hi[1] + tol))
    if su.size < 2 or sv.size < 2:
        raise ValueError(f"mode port {port.name}: rectangle holds fewer than two lines per axis")
    u, v = lines_u[su], lines_v[sv]
    # cell layer next to the plane, on the side the structure lies
    cell_n = m if port.direction > 0 else m - 1
    cell_n = min(max(cell_n, 0), g.n[n] - 1)
    idx: list[Any] = [slice(None)] * 3  # storage-free (nz, ny, nx) array of cells
    idx[2 - n] = cell_n
    plane = eps_cell_axis[tuple(idx)]  # axes: the two remaining of (z, y, x), in that order
    arr = plane.T  # remaining array axes are (v, u); want (iu, iv)
    eps_cell = arr[np.ix_(su[:-1], sv[:-1])]
    pos = float(g.lines[n][m])
    # ground: PEC regions and conductor boxes crossing the plane, at their physical extent (a box
    # meshed with an edge rule ends ``edge_frac`` of a cell inside its edge), PEC walls
    boxes: list[tuple[list[float], list[float]]] = []
    for c in model.conductors:
        if c.lo[n] - tol <= pos <= c.hi[n] + tol:
            boxes.append(_physical_extent(g, c, tol))
    boxes += [(list(r.lo), list(r.hi)) for r in model.regions if r.pec]
    # the static solve runs on the port lattice refined by every physical edge in the rectangle
    # (nodal Q1 elements end the metal at its last node; the Yee field ends about a third of a
    # cell beyond it), and the potential is read back on the Yee nodes
    extra_u = [b[k][u_ax] for b in boxes for k in (0, 1)] + [
        q[k][0] for q in port.signals for k in (0, 1)
    ]
    extra_v = [b[k][v_ax] for b in boxes for k in (0, 1)] + [
        q[k][1] for q in port.signals for k in (0, 1)
    ]
    ua, iu = _refine(u, extra_u, tol)
    va, iv = _refine(v, extra_v, tol)
    cu = np.clip(np.searchsorted(u, 0.5 * (ua[1:] + ua[:-1])) - 1, 0, u.size - 2)
    cv = np.clip(np.searchsorted(v, 0.5 * (va[1:] + va[:-1])) - 1, 0, v.size - 2)
    eps_aug = eps_cell[np.ix_(cu, cv)]
    labels = -np.ones((ua.size, va.size), dtype=np.int64)
    sig_boxes: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
    for lo, hi in boxes:
        if lo[n] - tol <= pos <= hi[n] + tol:
            lo2, hi2 = (lo[u_ax], lo[v_ax]), (hi[u_ax], hi[v_ax])
            # a box that overlaps a signal conductor is part of it (its raster may reach past
            # the signal's bounding box by a cell), never ground next to it
            k_sig = next((k for k, q in enumerate(port.signals) if _overlap(lo2, hi2, q, tol)), -1)
            if k_sig >= 0:
                sig_boxes.append((k_sig, lo2, hi2))
            else:
                labels[_in_box(ua, va, lo2, hi2, tol)] = 0
    for a, ax_lines, sl in ((u_ax, ua, 0), (v_ax, va, 1)):
        for side in (0, 1):
            if model.walls[2 * a + side] == "pec":
                wall = g.lines[a][0 if side == 0 else -1]
                hit = np.abs(ax_lines - wall) <= tol
                if sl == 0:
                    labels[hit, :] = 0
                else:
                    labels[:, hit] = 0
    for k, (lo2, hi2) in enumerate(port.signals):
        labels[_in_box(ua, va, lo2, hi2, tol)] = k + 1
    for k, lo2, hi2 in sig_boxes:
        labels[_in_box(ua, va, lo2, hi2, tol)] = k + 1
    for k in range(len(port.signals)):
        if not np.any(labels[np.ix_(iu, iv)] == k + 1):
            raise ValueError(f"mode port {port.name}: signal {k} touches no node of the plane")
    full = solve_modes(ua, va, eps_aug, labels, len(port.signals))
    modes = replace(full, phi=full.phi[:, iu][:, :, iv])
    return modes, {"u_ax": u_ax, "v_ax": v_ax, "plane": m, "su": su, "sv": sv}


def build_mode_port(
    model: Model,
    g: Grid,
    port: ModePort,
    modes: ModeSet,
    geo: dict[str, Any],
    edge_eps: list[FloatArray],
    sigma_cb: Any = None,
) -> dict[str, Any]:
    """Per-edge arrays of one mode of ``port`` (see module docstring)."""
    del sigma_cb
    if not 0 <= port.mode < modes.z.size:
        raise ValueError(f"mode port {port.name}: mode {port.mode} of {modes.z.size}")
    n = port.axis
    u_ax, v_ax = geo["u_ax"], geo["v_ax"]
    m, su, sv = geo["plane"], geo["su"], geo["sv"]
    phi = modes.phi[port.mode]
    z = float(modes.z[port.mode])
    pos = float(g.lines[n][m])
    lo3 = [0.0, 0.0, 0.0]
    hi3 = [0.0, 0.0, 0.0]
    lo3[n] = hi3[n] = pos
    lo3[u_ax], hi3[u_ax] = float(g.lines[u_ax][su[0]]), float(g.lines[u_ax][su[-1]])
    lo3[v_ax], hi3[v_ax] = float(g.lines[v_ax][sv[0]]), float(g.lines[v_ax][sv[-1]])
    comps: list[npt.NDArray[np.int32]] = []
    flats: list[npt.NDArray[np.intp]] = []
    tmpl: list[FloatArray] = []
    vol: list[FloatArray] = []
    eps_e: list[FloatArray] = []
    for comp, other in ((u_ax, v_ax), (v_ax, u_ax)):
        flat, _ = g.points("E", comp, tuple(lo3), tuple(hi3))  # type: ignore[arg-type]
        k, j, i = np.unravel_index(flat, g.shape)
        ijk = (i, j, k)
        cell = ijk[comp] - 1  # cell index along the edge
        node = ijk[other] - 1  # node index across
        length = g.d(comp)[cell]
        # Q1 potential difference along the edge, relative lattice indices
        c_rel = cell - (su[0] if comp == u_ax else sv[0])
        n_rel = node - (sv[0] if comp == u_ax else su[0])
        if comp == u_ax:
            dphi = phi[c_rel + 1, n_rel] - phi[c_rel, n_rel]
        else:
            dphi = phi[n_rel, c_rel + 1] - phi[n_rel, c_rel]
        tmpl.append(-dphi / length)
        w_other = g.node_weights(other)[ijk[other]]
        w_n = g.node_weights(n)[ijk[n]]
        vol.append(length * w_other * w_n)
        eps_e.append(edge_eps[comp][k - 1, j - 1, i - 1])
        comps.append(np.full(flat.size, comp, np.int32))
        flats.append(flat)
    t_e = np.concatenate(tmpl)
    vol_e = np.concatenate(vol)
    s_e = np.maximum(np.concatenate(eps_e), 1.0)
    norm = float(np.sum(s_e * t_e**2 * vol_e))
    if norm <= 0:
        raise ValueError(f"mode port {port.name}: zero template (no field on the plane)")
    kappa = 1.0 / (z * norm)
    sigma = kappa * s_e
    weights = z * sigma * t_e * vol_e
    return {
        "comp": np.concatenate(comps),
        "flat": np.concatenate(flats),
        "split": [f.size for f in flats],
        "sigma": sigma,
        "template": t_e,
        "weights": weights,
        "z": z,
        "eps_eff": float(modes.eps_eff[port.mode]),
        "voltages": modes.voltages[port.mode],
    }
