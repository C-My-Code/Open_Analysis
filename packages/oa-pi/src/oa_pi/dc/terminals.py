# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="import-untyped,no-any-return,arg-type,return-value"
# mypy: disable-error-code="assignment,attr-defined"
"""Regulator and load terminals on a assembled conductance matrix (shared by FEM and raster).

Given the matrix ``K_A`` over "level A" unknowns (one per ring/pin/cell) and the unknown of every
pin, this merges the pins of each group into one node (exact equipotential constraint), applies the
loads, and solves for the regulator:

- ideal regulator: Dirichlet ``V_out = vset`` (return pins at 0 V);
- Thevenin ``r_out > 0``: Norton equivalent, still symmetric positive definite;
- remote sense: ``V(sense+) - V(sense-) = vset`` with the regulator current as an extra MNA
  unknown (saddle-point system, solved with SuperLU).

Without a return net the return is an ideal 0 V reference.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

from oa_pi.dc.network import DcNetwork, Pin
from oa_pi.dc.solvers import kcl_residual, solve_general, solve_spd

__all__ = ["TerminalSolution", "labels", "solve_terminals"]

F64 = npt.NDArray[np.float64]
I64 = npt.NDArray[np.int64]


def labels(n: int, groups: list[I64]) -> tuple[I64, int]:
    """Connected components of the chains ``g[0]-g[1]-...``: ``(label per index, count)``."""
    ea = [g[:-1] for g in groups if len(g) > 1]
    eb = [g[1:] for g in groups if len(g) > 1]
    a = np.concatenate(ea) if ea else np.zeros(0, np.int64)
    b = np.concatenate(eb) if eb else np.zeros(0, np.int64)
    g = sp.coo_matrix((np.ones(len(a)), (a, b)), shape=(n, n))
    k, lab = connected_components(g, directed=False)
    return lab.astype(np.int64), int(k)


@dataclass
class PinRecord:
    pin: Pin
    role: str  # "vrm", "vrm_ret", "load", "load_ret"
    group: str
    voltage: float
    current: float  # along the intended flow; < 0 = back-flow [A]


@dataclass
class TerminalSolution:
    v_a: F64  # voltage of every level-A unknown
    i_source: float
    v_source: float
    residual: float
    solver: str
    n_unknowns: int
    pins: list[PinRecord]
    group_voltage: dict[str, float]


def solve_terminals(
    net: DcNetwork, k_a: sp.csr_matrix, pin_dof: dict[str, int], solver: str = "auto"
) -> TerminalSolution:
    """Apply groups, loads and the regulator to ``k_a`` and solve."""
    cfg = net.cfg
    n_a = k_a.shape[0]

    def dof_a(pins: list[Pin]) -> I64:
        return np.unique(np.array([pin_dof[p.id] for p in pins], dtype=np.int64))

    gset: list[tuple[str, str, list[Pin]]] = [("vrm_p", "vrm", net.vrm_p)]
    if net.vrm_n:
        gset.append(("vrm_n", "vrm_ret", net.vrm_n))
    for ld in net.loads:
        if ld.cfg.model == "pin_group":
            gset.append((f"load:{ld.name}", "load", ld.rail_pins))
            if ld.ret_pins:
                gset.append((f"load_ret:{ld.name}", "load_ret", ld.ret_pins))
    lab_b, n_b = labels(n_a, [dof_a(pins) for _, _, pins in gset])
    pmat = sp.csr_matrix((np.ones(n_a), (np.arange(n_a), lab_b)), shape=(n_a, n_b))
    k_b = (pmat.T @ k_a @ pmat).tocsr()

    def dof_b(pins: list[Pin]) -> int:
        return int(lab_b[dof_a(pins)[0]])

    f = np.zeros(n_b)
    for ld in net.loads:
        cur = ld.cfg.current
        if ld.cfg.model == "pin_group":
            f[dof_b(ld.rail_pins)] -= cur
            if ld.ret_pins:
                f[dof_b(ld.ret_pins)] += cur
        else:
            for p in ld.rail_pins:
                f[dof_b([p])] -= cur / len(ld.rail_pins)
            for p in ld.ret_pins:
                f[dof_b([p])] += cur / len(ld.ret_pins)

    vset, r_out = cfg.vrm.vset, cfg.vrm.r_out
    d_p = dof_b(net.vrm_p)
    d_n = dof_b(net.vrm_n) if net.vrm_n else None
    v = np.zeros(n_b)
    all_dofs = np.arange(n_b)
    if not net.sense_p:
        if r_out == 0.0:  # ideal source: Dirichlet at the output (and ground at the return)
            fixed = {d_p: vset} | ({d_n: 0.0} if d_n is not None else {})
            fd = np.array(list(fixed))
            xd = np.array(list(fixed.values()))
            free = np.setdiff1d(all_dofs, fd)
            kff = k_b[free][:, free]
            rhs = f[free] - k_b[free][:, fd] @ xd
            x, used = solve_spd(kff.tocsr(), rhs, solver)
            v[free], v[fd] = x, xd
            i_s = float((k_b @ v)[d_p] - f[d_p])
            resid = kcl_residual(kff, x, rhs)
        else:  # Thevenin source -> Norton equivalent (stays SPD)
            free = all_dofs if d_n is None else np.setdiff1d(all_dofs, [d_n])
            kn = k_b + sp.csr_matrix(([1.0 / r_out], ([d_p], [d_p])), shape=(n_b, n_b))
            rhs_full = f.copy()
            rhs_full[d_p] += vset / r_out
            kff = kn[free][:, free]
            x, used = solve_spd(kff.tocsr(), rhs_full[free], solver)
            v[free] = x
            i_s = (vset - (v[d_p] - (v[d_n] if d_n is not None else 0.0))) / r_out
            resid = kcl_residual(kff, x, rhs_full[free])
    else:  # remote sense: V(sense+) - V(sense-) = vset with an extra MNA unknown I_s
        sp_dof = dof_b(net.sense_p)
        sn_dof = dof_b(net.sense_n) if net.sense_n else d_n
        free = all_dofs if d_n is None else np.setdiff1d(all_dofs, [d_n])
        pos = {int(k): i for i, k in enumerate(free)}
        bcol = np.zeros(len(free))
        bcol[pos[d_p]] = -1.0  # K V - B I_s = f with B = e_P (N is the ground reference)
        crow = np.zeros(len(free))
        crow[pos[sp_dof]] += 1.0
        if sn_dof is not None and sn_dof in pos:
            crow[pos[sn_dof]] -= 1.0
        kff = k_b[free][:, free]
        mat = sp.vstack(
            [
                sp.hstack([kff, sp.csr_matrix(bcol.reshape(-1, 1))]),
                sp.hstack([sp.csr_matrix(crow.reshape(1, -1)), sp.csr_matrix((1, 1))]),
            ]
        ).tocsr()
        rhs = np.concatenate([f[free], [vset]])
        x, used = solve_general(mat, rhs)
        v[free] = x[:-1]
        i_s = float(x[-1])
        resid = kcl_residual(mat, x, rhs)

    v_a = np.asarray(pmat @ v, dtype=np.float64)
    inj = k_a @ v_a  # current injected into the copper at every level-A unknown
    sign = {"vrm": 1.0, "vrm_ret": -1.0, "load": -1.0, "load_ret": 1.0}
    pin_sets = list(gset)
    for ld in net.loads:
        if ld.cfg.model == "equal_split":
            pin_sets.append((f"load:{ld.name}", "load", ld.rail_pins))
            if ld.ret_pins:
                pin_sets.append((f"load_ret:{ld.name}", "load_ret", ld.ret_pins))
    pins: list[PinRecord] = []
    gv: dict[str, float] = {}
    for name, role, plist in pin_sets:
        gv[name] = float(np.mean([v_a[pin_dof[p.id]] for p in plist]))
        for p in plist:
            d = pin_dof[p.id]
            pins.append(PinRecord(p, role, name, float(v_a[d]), float(sign[role] * inj[d])))
    return TerminalSolution(
        v_a=v_a,
        i_source=i_s,
        v_source=float(v[d_p] - (v[d_n] if d_n is not None else 0.0)),
        residual=resid,
        solver=used,
        n_unknowns=len(free),
        pins=pins,
        group_voltage=gv,
    )
