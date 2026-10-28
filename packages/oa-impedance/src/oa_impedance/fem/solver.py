# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""FEM reference solver: electrostatic C/G/L plus eddy-current R(f), L_int(f).

``solve_fem`` returns an ``ImpedanceResult`` (``method="fem"``). ``solve_loss`` runs only the
eddy-current solve; ``with_fem_loss`` swaps its R(f)/L(f) into a BEM (or any) result, replacing the
surface-impedance perturbation, and flags the result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from oa_impedance import _physics_compat as phys
from oa_impedance.fem._mesh import FemMesh, build
from oa_impedance.fem.eddy import EddySolution, characteristic_thickness, solve_eddy
from oa_impedance.fem.electrostatic import (
    ElectroSolution,
    FemSetup,
    prepare_electro,
    solve_electro,
)
from oa_impedance.modal import modal_decomposition
from oa_impedance.service import SOLVER_VERSION
from oa_mesh import mesh_cross_section
from oa_model.arrays import Floats, NDArray
from oa_model.xsection import CrossSection, XsPlane
from oa_results.container import ValidityFlag
from oa_results.rlgc import RLGC, ImpedanceResult, LineParams

F64 = npt.NDArray[np.float64]

__all__ = [
    "FEM_VERSION",
    "TOLERANCE_BASIS",
    "FemParams",
    "FemSolution",
    "solve_fem",
    "solve_fem_raw",
    "solve_loss",
    "with_fem_loss",
]

_MIN_VOLUME_T = 1e-6  # [m] conductors thinner than this are always impedance sheets
FEM_VERSION = "oa-fem 0.1"
TOLERANCE_BASIS = "golden:impedance_fem (coax 1e-3, wire/Cohn/CPW 3e-3 vs exact; 5e-3 vs BEM)"


@dataclass(frozen=True)
class FemParams:
    """Numerical controls of the FEM reference solver."""

    h_max: float | None = None  # coarsest element size of the first level [m]; None = Lref / 4
    corner_refine: float = 0.1  # corner element size as a fraction of h_max [1]
    box_factor: float = 20.0  # outer box = box_factor * Lref [1]
    rtol: float = 1e-3  # refinement stops when the energy (C, L) changes less than this [1]
    max_levels: int = 3  # maximum refinement levels after the first mesh [1]
    ibc_ratio: float = 4.0  # surface impedance BC where delta < t / ibc_ratio [1]
    skin_cells: float = 4.0  # conductor element size in skin depths (volume eddy mesh) [1]
    loss: Literal["eddy", "none"] = "eddy"  # eddy-current R(f) / L_int(f), or lossless


@dataclass
class FemSolution:
    es: ElectroSolution
    eddy: EddySolution | None
    L_int: F64 | None  # (F, N, N) H/m, mesh-matched L_total - L_ext
    converged: bool
    levels: list[dict[str, float]]  # per refinement level: h_max, n_dofs, c11, l11
    notes: list[str]
    h_char: float


def _mesh(xs: CrossSection, h: float, p: FemParams, *, conductors: bool) -> FemMesh:
    m = mesh_cross_section(
        xs,
        h_max=h,
        order=1,
        corner_refine=p.corner_refine,
        box_factor=p.box_factor,
        mesh_conductors=conductors,
    )
    return build(m)


def _lref(xs: CrossSection, p: FemParams) -> float:
    m = mesh_cross_section(xs, h_max=None, order=1, box_factor=p.box_factor)
    b = m.box
    return max(b[2] - b[0], b[3] - b[1]) / (p.box_factor + 1.0)


def _converged(prev: ElectroSolution | None, cur: ElectroSolution, rtol: float) -> float:
    if prev is None:
        return math.inf
    d = max(
        float(np.max(np.abs(cur.C0 - prev.C0)) / np.max(np.abs(cur.C0))),
        float(np.max(np.abs(cur.C.real - prev.C.real)) / np.max(np.abs(cur.C.real))),
    )
    return d


def solve_fem_raw(xs: CrossSection, freqs: F64, params: FemParams | None = None) -> FemSolution:
    """Mesh-refinement loop on the energy (C, vacuum L), then the eddy-current solve."""
    p = params or FemParams()
    fs = np.asarray(freqs, dtype=float)
    h = p.h_max if p.h_max is not None else _lref(xs, p) / 4.0
    h0 = h
    notes: list[str] = []
    levels: list[dict[str, float]] = []
    prev: ElectroSolution | None = None
    setup: FemSetup | None = None
    es: ElectroSolution | None = None
    ok = False
    for _lev in range(p.max_levels + 1):
        fm = _mesh(xs, h, p, conductors=False)
        setup = prepare_electro(fm, xs)
        es = solve_electro(setup, fs)
        err = _converged(prev, es, p.rtol)
        levels.append(
            {
                "h_max": h,
                "n_dofs": float(es.n_dofs),
                "c11": float(es.C[0, 0, 0].real),
                "l11": float(es.L_ext[0, 0]),
                "change": err,
            }
        )
        if err <= p.rtol:
            ok = True
            break
        prev = es
        h /= math.sqrt(2.0)
        p = FemParams(**{**p.__dict__, "corner_refine": p.corner_refine * 0.8})
    assert es is not None and setup is not None
    if not ok:
        notes.append(f"refinement did not reach rtol {p.rtol:g} in {p.max_levels} levels")
    notes.extend(es.notes)
    eddy: EddySolution | None = None
    l_int: F64 | None = None
    if p.loss == "eddy":
        eddy, l_int, enotes = _run_eddy(xs, fs, setup, es, p, h0)
        notes.extend(enotes)
    return FemSolution(es, eddy, l_int, ok, levels, notes, h0)


def _volume_set(xs: CrossSection, f: float, ratio: float) -> set[int]:
    out: set[int] = set()
    for k, c in enumerate(xs.conductors):
        if isinstance(c.shape, XsPlane) and c.shape.thickness == 0.0:
            continue
        t = characteristic_thickness(c.shape)
        if t < _MIN_VOLUME_T:  # thinner than 1 um: a sheet, not a meshable volume
            continue
        if f <= 0.0 or phys.skin_depth(f, c.conductivity) >= t / ratio:
            out.add(k)
    return out


def _run_eddy(
    xs: CrossSection, fs: F64, setup: FemSetup, es: ElectroSolution, p: FemParams, h0: float
) -> tuple[EddySolution, F64, list[str]]:
    notes: list[str] = []
    vol_any = [k for f in fs for k in _volume_set(xs, float(f), p.ibc_ratio)]
    setup_e = setup
    if vol_any:
        deltas = [
            phys.skin_depth(float(f), xs.conductors[k].conductivity)
            for f in fs
            if f > 0
            for k in _volume_set(xs, float(f), p.ibc_ratio)
        ]
        t_min = min(characteristic_thickness(xs.conductors[k].shape) for k in set(vol_any))
        h_v = min(h0, p.skin_cells * min(deltas) / 0.3) if deltas else min(h0, 3.3 * t_min)
        h_v = max(h_v, t_min / 4.0)
        fm = _mesh(xs, h_v, p, conductors=True)
        setup_e = prepare_electro(fm, xs)
        notes.append(f"eddy volume mesh: h_max {h_v:.3g} m, {fm.basis.N} P2 dofs")
    sol = solve_eddy(setup_e, fs, lambda f: _volume_set(xs, f, p.ibc_ratio))
    es_e = solve_electro(setup_e, np.array([0.0])) if setup_e is not setup else es
    l_int = sol.L_total - es_e.L_ext[None, :, :]
    surf = [
        c.id for c in xs.conductors if isinstance(c.shape, XsPlane) and c.shape.thickness == 0.0
    ]
    if surf:
        notes.append(
            f"zero-thickness conductors {surf} are surface-impedance sheets (no DC resistance)"
        )
    n_ibc = sum(1 for v in sol.volume if len(v) == 0)
    notes.append(
        f"eddy-current solve at {len(fs)} frequencies; {n_ibc} fully impedance-boundary, "
        f"ibc_ratio {p.ibc_ratio:g}"
    )
    return sol, l_int, notes


def _quasi_static_fmax(xs: CrossSection, h_char: float) -> float:
    er_max = max([xs.background_dk] + [phys.eps_complex(d.model, 0.0).real for d in xs.dielectrics])
    return 0.1 * phys.C0 / (math.sqrt(er_max) * h_char)


def _result(
    xs: CrossSection, fs: F64, sol: FemSolution, R: F64, L: F64, loss_note: str
) -> ImpedanceResult:
    es = sol.es
    nf, n = len(fs), len(es.conductor_ids)
    w = 2.0 * np.pi * fs
    C = es.C.real
    with np.errstate(all="ignore"):
        G = np.where(w[:, None, None] > 0, -w[:, None, None] * es.C.imag, 0.0)
    modal = modal_decomposition(fs, R, L, G, C)
    kw: dict[str, Floats] = {}
    for name in ("z0", "z_diff", "z_comm", "z_odd", "z_even"):
        arr = getattr(modal, name)
        if arr is not None:
            kw[name] = Floats(arr)
    params = LineParams(
        freqs=Floats(fs),
        zc_modal=NDArray(modal.zc_modal),
        eps_eff_modal=NDArray(modal.eps_eff),
        zc_matrix=NDArray(modal.zc_matrix),
        delay_per_m=NDArray(modal.delay),
        loss_db_per_m=NDArray(modal.loss_db),
        **kw,
    )
    notes = list(sol.notes)
    notes.append(loss_note)
    notes.append(f"{es.n_dofs} P2 dofs (electrostatic), {len(sol.levels)} mesh level(s)")
    f_qs = _quasi_static_fmax(xs, sol.h_char * 4.0)
    validity: Literal["in_range", "extrapolated", "invalid"] = (
        "extrapolated" if len(fs) and float(np.max(fs)) > f_qs else "in_range"
    )
    notes.append(
        f"quasi-static model above ~{f_qs / 1e9:.3g} GHz: dispersion not captured"
        if validity == "extrapolated"
        else f"quasi-static valid below ~{f_qs / 1e9:.3g} GHz"
    )
    rlgc = RLGC(
        freqs=Floats(fs),
        R=NDArray(R),
        L=NDArray(L),
        G=NDArray(G),
        C=NDArray(C),
        conductor_ids=es.conductor_ids,
    )
    flags = (
        ValidityFlag(
            code="accuracy",
            ok=sol.converged and validity == "in_range",
            message=f"accuracy_class=2d_field; method={FEM_VERSION}; converged={sol.converged}; "
            f"validity={validity}; tolerance_basis={TOLERANCE_BASIS}",
        ),
        ValidityFlag(
            code="quasi_static_fmax",
            ok=validity == "in_range",
            message=f"quasi-static limit ~{f_qs / 1e9:.3g} GHz",
        ),
    )
    _ = nf, n
    return ImpedanceResult(
        xs_key=xs.key(),
        method="fem",
        model_name=FEM_VERSION,
        rlgc=rlgc,
        params=params,
        validity=validity,
        notes=tuple(notes),
        solver_version=SOLVER_VERSION,
        accuracy_class="reference" if sol.converged else "validated",
        validity_flags=flags,
    )


def solve_fem(
    xs: CrossSection, freqs: object = None, params: FemParams | None = None
) -> ImpedanceResult:
    """FEM ``ImpedanceResult``: C, G from the electrostatic solve, R, L from the eddy solve."""
    fs: F64 = np.asarray([0.0] if freqs is None or len(freqs) == 0 else freqs, dtype=np.float64)  # type: ignore[arg-type,var-annotated,unused-ignore]
    sol = solve_fem_raw(xs, fs, params)
    es = sol.es
    n = len(es.conductor_ids)
    R = np.zeros((len(fs), n, n))
    L = np.repeat(es.L_ext[None], len(fs), axis=0)
    note = "lossless: eddy-current solve disabled"
    if sol.eddy is not None and sol.L_int is not None:
        R = sol.eddy.R
        L = L + sol.L_int
        note = "R(f), L_int(f): 2D magnetoquasistatic eddy-current FEM (replaces surface impedance)"
    return _result(xs, fs, sol, R, L, note)


def solve_loss(
    xs: CrossSection, freqs: object, params: FemParams | None = None
) -> tuple[F64, F64, EddySolution]:
    """(R, L) per frequency, (F, N, N), from the eddy-current solve only (no refinement loop)."""
    p = params or FemParams()
    fs = np.asarray(freqs, dtype=float)
    h0 = p.h_max if p.h_max is not None else _lref(xs, p) / 4.0
    setup = prepare_electro(_mesh(xs, h0, p, conductors=False), xs)
    es = solve_electro(setup, np.array([0.0]))
    sol, l_int, _ = _run_eddy(xs, fs, setup, es, p, h0)
    return sol.R, np.asarray(es.L_ext[None] + l_int), sol


def with_fem_loss(
    base: ImpedanceResult, xs: CrossSection, params: FemParams | None = None
) -> ImpedanceResult:
    """Replace the R(f), L(f) of ``base`` (e.g. a BEM result) by the FEM eddy-current ones."""
    assert base.rlgc is not None
    fs = np.asarray(base.rlgc.freqs.array, dtype=float)
    R, L_fem, _ = solve_loss(xs, fs, params)
    r = base.rlgc
    n = len(r.conductor_ids)
    if R.shape[1] != n:
        raise ValueError("conductor count of the FEM loss solve does not match the base result")
    c, g = r.C.array, r.G.array
    modal = modal_decomposition(fs, R, L_fem, g, c)
    kw: dict[str, Floats] = {}
    for name in ("z0", "z_diff", "z_comm", "z_odd", "z_even"):
        arr = getattr(modal, name)
        if arr is not None:
            kw[name] = Floats(arr)
    params_new = LineParams(
        freqs=Floats(fs),
        zc_modal=NDArray(modal.zc_modal),
        eps_eff_modal=NDArray(modal.eps_eff),
        zc_matrix=NDArray(modal.zc_matrix),
        delay_per_m=NDArray(modal.delay),
        loss_db_per_m=NDArray(modal.loss_db),
        **kw,
    )
    flag = ValidityFlag(
        code="loss_model",
        ok=True,
        message=f"accuracy_class=2d_field; R(f), L(f) from {FEM_VERSION} eddy-current solve "
        f"replace the {base.method} surface-impedance perturbation; "
        f"tolerance_basis={TOLERANCE_BASIS}",
    )
    return base.model_copy(
        update={
            "rlgc": RLGC(
                freqs=Floats(fs),
                R=NDArray(R),
                L=NDArray(L_fem),
                G=r.G,
                C=r.C,
                conductor_ids=r.conductor_ids,
            ),
            "params": params_new,
            "notes": (*base.notes, "R(f), L(f) replaced by FEM eddy-current solve"),
            "validity_flags": (*base.validity_flags, flag),
            "accuracy_class": "reference",
        }
    )
