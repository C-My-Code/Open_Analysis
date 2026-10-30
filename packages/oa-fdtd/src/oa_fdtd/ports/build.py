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
    labels = -np.ones((su.size, sv.size), dtype=np.int64)
    # ground: PEC regions and conductor boxes crossing the plane, PEC walls on the rectangle edges
    boxes: list[tuple[Any, Any]] = [(c.lo, c.hi) for c in model.conductors] + [
        (r.lo, r.hi) for r in model.regions if r.pec
    ]
    pos = float(g.lines[n][m])
    for lo, hi in boxes:
        if lo[n] - tol <= pos <= hi[n] + tol:
            labels[_in_box(u, v, (lo[u_ax], lo[v_ax]), (hi[u_ax], hi[v_ax]), tol)] = 0
    for a, ax_lines, sl in ((u_ax, u, 0), (v_ax, v, 1)):
        for side in (0, 1):
            if model.walls[2 * a + side] == "pec":
                wall = g.lines[a][0 if side == 0 else -1]
                hit = np.abs(ax_lines - wall) <= tol
                if sl == 0:
                    labels[hit, :] = 0
                else:
                    labels[:, hit] = 0
    for k, (lo2, hi2) in enumerate(port.signals):
        labels[_in_box(u, v, lo2, hi2, tol)] = k + 1
    for k in range(len(port.signals)):
        if not np.any(labels == k + 1):
            raise ValueError(f"mode port {port.name}: signal {k} touches no node of the plane")
    modes = solve_modes(u, v, eps_cell, labels, len(port.signals))
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
    s_e = np.sqrt(np.maximum(np.concatenate(eps_e), 1.0))
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
