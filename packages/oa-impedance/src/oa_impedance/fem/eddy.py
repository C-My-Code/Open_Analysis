# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""2D magnetoquasistatic eddy-current solve: loop impedance matrix Z(f) = R(f) + j w L(f).

Formulation (longitudinal vector potential A, e^{j w t}): in a conductor
``-div(nu grad A) + j w sigma A = sigma e_g`` where ``e_g`` is the (unknown) voltage-drop per unit
length of the *group* the conductor belongs to, and every group carries a prescribed current
``I_g = e_g S_g - j w b_g . A``. Groups: each non-reference signal conductor is its own group, all
ground conductors share one (the return, ``sum I = 0``), each floating conductor is its own group
with ``I = 0``. When there is no ground conductor the last signal is the reference group.
``Z[k, m] = e_k - e_ref`` for a unit current in signal ``m`` is the loop impedance matrix.

Each conductor is treated either as a *volume* (interior meshed, full skin-effect solve) or as a
*surface* (Leontovich impedance boundary condition, interior not meshed):

- surface: ``E_z = Zs K`` with ``Zs = (1 + j)/(sigma delta) * K_rough`` and, on smooth curved
  boundaries, the second-order curvature correction ``Zs (1 + (1 - j) delta kappa / 4)`` (kappa > 0
  for a convex conductor; exact for a round wire to O((delta/a)^2)). The weak form adds
  ``j w Y M`` and ``Y b`` terms with ``Y = 1/Zs`` on the conductor facets, one term per domain side
  (a zero-thickness plane has two).
- volume: conductors thicker than ``ibc_ratio`` skin depths (``delta >= t / ibc_ratio``).
  Zero-thickness conductors are always surfaces.

The open boundary is ``A = 0`` (no ground, zero net current) or the dipole Robin condition
``d(A)/dn = -A/r`` (grounded sections), the same as the electrostatic solve, so
``L_int = L - L_ext``
cancels the truncation consistently.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp  # type: ignore[import-untyped,unused-ignore]
from scipy.sparse.linalg import splu  # type: ignore[import-untyped,unused-ignore]
from skfem import Basis, BilinearForm, ElementTriP2, FacetBasis, LinearForm  # type: ignore[import-untyped,unused-ignore]
from skfem.helpers import dot, grad  # type: ignore[import-untyped,unused-ignore]

from oa_impedance import _physics_compat as phys
from oa_impedance.fem._mesh import FemMesh
from oa_impedance.fem.electrostatic import FemSetup
from oa_model.xsection import XsCircle, XsPlane, XsPolygon, XsTrapezoid

C128 = npt.NDArray[np.complex128]
F64 = npt.NDArray[np.float64]

__all__ = ["EddySolution", "characteristic_thickness", "solve_eddy"]

_NU = 1.0 / phys.MU0
_SMOOTH = 0.26  # max normal change (rad, ~15 deg) between facets for a "smooth" boundary


@BilinearForm
def _stiff(u, v, _):  # type: ignore[no-untyped-def]
    return dot(grad(u), grad(v))


@BilinearForm
def _mass(u, v, _):  # type: ignore[no-untyped-def]
    return u * v


@LinearForm
def _load(v, _):  # type: ignore[no-untyped-def]
    return v


@BilinearForm
def _fmass(u, v, w):  # type: ignore[no-untyped-def]
    return w["y"] * u * v


@LinearForm
def _fload(v, w):  # type: ignore[no-untyped-def]
    return w["y"] * v


def characteristic_thickness(shape: object) -> float:
    """``t = 2 A / P`` (strip thickness; radius of a round wire); 0 for zero-thickness sheets."""
    if isinstance(shape, XsTrapezoid):
        return min(shape.thickness, 0.5 * (shape.w_bottom + shape.w_top))
    if isinstance(shape, XsCircle):
        return shape.radius
    if isinstance(shape, XsPlane):
        return shape.thickness
    if isinstance(shape, XsPolygon):
        from shapely.geometry import Polygon as ShPolygon  # type: ignore[import-untyped,unused-ignore]

        p = ShPolygon(shape.polygon.exterior.array)
        return 2.0 * p.area / p.length if p.length > 0 else 0.0
    return 0.0


@dataclass
class EddySolution:
    freqs: F64
    Z: C128  # (F, N, N) loop impedance, ohm/m
    L_total: F64  # (F, N, N) Im(Z)/w (energy-based at DC), H/m
    volume: list[tuple[str, ...]]  # per frequency: ids of conductors solved as volumes
    n_dofs: tuple[int, int]  # (volume-mesh dofs, max over frequencies of active dofs)
    conductor_ids: tuple[str, ...]
    notes: list[str] = field(default_factory=list)

    @property
    def R(self) -> F64:
        return np.asarray(self.Z.real)


def _facet_geometry(
    fm: FemMesh, k: int, active: npt.NDArray[np.bool_]
) -> tuple[npt.NDArray[np.int64], npt.NDArray[np.float64], list[list[tuple[int, float]]]]:
    """Facets of conductor ``k`` that touch the domain, with curvature and (class, side) data.

    Returns (facets, kappa, sides) with ``sides[i] = [(face_class, weight_placeholder)]`` where the
    class is 0 top / 1 bottom / 2 side.
    """
    mesh = fm.mesh
    fac = fm.cond_facets[k]
    x = mesh.p
    cent = x[:, mesh.t].mean(axis=1)  # (2, nelem)
    sides: list[list[tuple[int, float]]] = []
    normals: list[npt.NDArray[np.float64] | None] = []
    keep: list[int] = []
    for f in fac:
        a, b = mesh.facets[:, f]
        t = x[:, b] - x[:, a]
        ln = float(np.hypot(*t))
        t = t / ln
        nrm = np.array([t[1], -t[0]])
        mid = 0.5 * (x[:, a] + x[:, b])
        side: list[tuple[int, float]] = []
        n_first: npt.NDArray[np.float64] | None = None
        for e in mesh.f2t[:, f]:
            if e < 0 or not active[e]:
                continue
            m = nrm if float(np.dot(cent[:, e] - mid, nrm)) > 0 else -nrm
            cls = 0 if m[1] > 0.7071 else (1 if m[1] < -0.7071 else 2)
            side.append((cls, 0.0))
            if n_first is None:
                n_first = m
        if side:
            keep.append(int(f))
            sides.append(side)
            normals.append(n_first if len(side) == 1 else None)
    facets = np.array(keep, dtype=np.int64)
    # curvature from the chain of facets
    node_f: dict[int, list[int]] = {}
    pos = {int(f): i for i, f in enumerate(facets)}
    for f in facets:
        for v in mesh.facets[:, f]:
            node_f.setdefault(int(v), []).append(int(f))
    kappa: npt.NDArray[np.float64] = np.zeros(len(facets))
    for i, f in enumerate(facets):
        m0 = normals[i]
        if m0 is None:
            continue
        a, b = (int(v) for v in mesh.facets[:, f])
        t = x[:, b] - x[:, a]
        ln = float(np.hypot(*t))
        t = t / ln
        ks: list[float] = []
        for node, fwd in ((b, True), (a, False)):
            nb = [g for g in node_f.get(node, []) if g != int(f)]
            if len(nb) != 1:
                break
            g = nb[0]
            mg = normals[pos[g]]
            if mg is None:
                break
            ga, gb = mesh.facets[:, g]
            lg = float(np.hypot(*(x[:, gb] - x[:, ga])))
            dm = mg - m0 if fwd else m0 - mg
            if float(np.hypot(*dm)) > _SMOOTH:
                break
            ks.append(float(np.dot(dm, t)) / (0.5 * (ln + lg)))
        else:
            kappa[i] = 0.5 * (ks[0] + ks[1])
    return facets, kappa, sides


def solve_eddy(
    setup: FemSetup,
    freqs: F64,
    volume_fn: Callable[[float], set[int]],
) -> EddySolution:
    """Solve Z(f). ``volume_fn(f)`` returns the conductor indices treated as volumes at ``f``."""
    fm, xs = setup.fm, setup.xs
    mesh = fm.mesh
    region = fm.region
    basis = fm.basis
    n_all = basis.N
    ncond = len(xs.conductors)
    roles = [c.role for c in xs.conductors]
    sig = [k for k in range(ncond) if roles[k] == "signal"]
    gnd = [k for k in range(ncond) if roles[k] == "ground"]
    ref_k = None
    if not gnd:
        ref_k = sig[-1]
        gnd = [ref_k]
        sig = sig[:-1]
    group_of: dict[int, int] = {}
    ngroups = 0
    for k in sig:
        group_of[k] = ngroups
        ngroups += 1
    g_ground = ngroups
    for k in gnd:
        group_of[k] = g_ground
    ngroups += 1
    for k in range(ncond):
        if k not in group_of:
            group_of[k] = ngroups
            ngroups += 1
    ns = len(sig)
    notes: list[str] = []

    # element groups
    is_cond = region <= -2
    stiff_nc = _stiff.assemble(
        Basis(mesh, ElementTriP2(), elements=np.nonzero(~is_cond)[0])
    ).tocsr()
    cond_k = {k: np.nonzero(region == -2 - k)[0] for k in range(ncond)}
    stiff_c: dict[int, sp.csr_matrix] = {}
    mass_c: dict[int, sp.csr_matrix] = {}
    load_c: dict[int, F64] = {}
    for k, el in cond_k.items():
        if len(el) == 0:
            continue
        b = Basis(mesh, ElementTriP2(), elements=el)
        stiff_c[k] = _stiff.assemble(b).tocsr()
        mass_c[k] = _mass.assemble(b).tocsr()
        load_c[k] = np.asarray(_load.assemble(b))
    facet_data: dict[
        int, tuple[npt.NDArray[np.int64], npt.NDArray[np.float64], list[list[tuple[int, float]]]]
    ] = {}

    dirichlet = setup.robin_all is None
    box_dofs = fm.dofs_of(fm.outer_facets) if dirichlet else np.zeros(0, dtype=np.int64)

    fs = np.asarray(freqs, dtype=float)
    nf = len(fs)
    Z = np.zeros((nf, ns, ns), dtype=np.complex128)
    Lt = np.zeros((nf, ns, ns))
    vols: list[tuple[str, ...]] = []
    max_dofs = 0
    for i, f in enumerate(fs):
        vol = {k for k in volume_fn(f) if k in stiff_c}
        vols.append(tuple(xs.conductors[k].id for k in sorted(vol)))
        active = ~is_cond.copy()
        for k in vol:
            active[cond_k[k]] = True
        dofs = np.unique(basis.element_dofs[:, active])
        dofs = np.setdiff1d(dofs, box_dofs)
        idx = -np.ones(n_all, dtype=np.int64)
        idx[dofs] = np.arange(len(dofs))
        nd = len(dofs)
        max_dofs = max(max_dofs, nd)
        w = 2.0 * math.pi * f
        f_eff = f if f > 0 else 1.0  # surface sheets at DC: tiny Zs (ideal conductor)
        kmat = _NU * stiff_nc
        for k in vol:
            kmat = kmat + _NU * stiff_c[k]
        if setup.robin_all is not None:
            kmat = kmat + _NU * setup.robin_all
        mmat = sp.csr_matrix((n_all, n_all), dtype=np.complex128)
        bvec = np.zeros((ngroups, n_all), dtype=np.complex128)
        for k in vol:
            s = xs.conductors[k].conductivity
            mmat = mmat + s * mass_c[k]
            bvec[group_of[k]] += s * load_c[k]
        for k in range(ncond):
            if k in vol or len(fm.cond_facets[k]) == 0:
                continue
            if k not in facet_data:
                facet_data[k] = _facet_geometry(fm, k, ~is_cond)
            facets, kappa, sides = facet_data[k]
            if len(facets) == 0:
                continue
            cd = xs.conductors[k]
            sg = cd.conductivity
            delta = 1.0 / math.sqrt(math.pi * f_eff * phys.MU0 * sg)
            zs = (1 + 1j) / (sg * delta)
            y = np.zeros(len(facets), dtype=np.complex128)
            for j in range(len(facets)):
                for cls, _ in sides[j]:
                    rough = (cd.roughness_top, cd.roughness_bottom, cd.roughness_side)[cls]
                    kr = phys.roughness_factor(rough, f_eff, sg)
                    corr = 1.0 + (1 - 1j) * delta * kappa[j] / 4.0 if len(sides[j]) == 1 else 1.0
                    y[j] += 1.0 / (zs * kr * corr)
            fb = FacetBasis(mesh, ElementTriP2(), facets=facets)
            nq = fb.X.shape[1]
            yr = np.repeat(y.real[:, None], nq, axis=1)
            yi = np.repeat(y.imag[:, None], nq, axis=1)
            mmat = mmat + _fmass.assemble(fb, y=yr) + 1j * _fmass.assemble(fb, y=yi)
            bvec[group_of[k]] += np.asarray(_fload.assemble(fb, y=yr)) + 1j * np.asarray(
                _fload.assemble(fb, y=yi)
            )
        a_full = (kmat + 1j * w * mmat).tocsr()
        a_dd = a_full[dofs][:, dofs]
        bg = bvec[:, dofs]  # (G, nd)
        sg_tot = bvec.sum(axis=1)  # S_g = sum of b (partition of unity)
        lu = splu(a_dd.tocsc())
        y = lu.solve(np.asarray(bg.T))  # A^-1 b_g^T  (nd, G)
        small = 1j * w * (bg @ y) - np.diag(sg_tot)
        rhs = np.zeros((ngroups, ns), dtype=np.complex128)
        for m in range(ns):
            rhs[group_of[sig[m]], m] = -1.0
            rhs[g_ground, m] += 1.0
        e = np.linalg.solve(small, rhs)
        sol_a = y @ e  # (nd, ns)
        e_ref = e[g_ground]
        for m in range(ns):
            for kk in range(ns):
                Z[i, kk, m] = e[group_of[sig[kk]], m] - e_ref[m]
        if f > 0:
            Lt[i] = Z[i].imag / w
        else:
            # DC: total inductance from the field energy, L_mn = A_m^T K A_n
            a_sol = sol_a.real
            kk_d = kmat[dofs][:, dofs]
            Lt[i] = a_sol.T @ (kk_d @ a_sol)
            Z[i] = Z[i].real
    return EddySolution(
        freqs=fs,
        Z=Z,
        L_total=Lt,
        volume=vols,
        n_dofs=(n_all, max_dofs),
        conductor_ids=tuple(xs.conductors[k].id for k in sig),
        notes=notes,
    )
