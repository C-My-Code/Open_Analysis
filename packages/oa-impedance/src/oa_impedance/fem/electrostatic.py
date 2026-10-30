# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""P2 electrostatic FEM: Maxwell C(f) (complex, so G comes with it) and L from the vacuum C.

Conductors are holes of the mesh with equipotential boundaries; the outer truncation box is a
grounded Dirichlet boundary. Each conductor (and the box) gets one unit-voltage solve through the
Schur complement of the stiffness matrix, which yields the full Maxwell matrix ``K`` of all
conductors plus the box. Grounded conductors are then merged with the box (when a ground exists),
floating conductors are eliminated with zero net charge, and, as in the BEM, when no ground
conductor exists the last signal is the reference and the box floats with zero net charge.

Anisotropy (``dk_z_over_dk_xy``) is not modelled (noted in the result).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import scipy.sparse as sp  # type: ignore[import-untyped,unused-ignore]
from scipy.interpolate import CubicSpline  # type: ignore[import-untyped,unused-ignore]
from scipy.sparse.linalg import splu  # type: ignore[import-untyped,unused-ignore]
from skfem import Basis, BilinearForm, ElementTriP2, FacetBasis  # type: ignore[import-untyped,unused-ignore]
from skfem.helpers import dot, grad  # type: ignore[import-untyped,unused-ignore]

from oa_impedance._dielectric import eps_at, is_frequency_independent
from oa_impedance.fem._mesh import FemMesh
from oa_model.xsection import CrossSection
from oa_physics.constants import EPS0, MU0

C128 = npt.NDArray[np.complex128]
F64 = npt.NDArray[np.float64]

__all__ = ["ElectroSolution", "FemSetup", "prepare_electro", "solve_electro"]

_MAX_ANCHORS = 6


@BilinearForm
def _laplace(u, v, _):  # type: ignore[no-untyped-def]
    return dot(grad(u), grad(v))


@BilinearForm
def _robin(u, v, w):  # type: ignore[no-untyped-def]
    return u * v / np.sqrt((w.x[0] - w.c0) ** 2 + (w.x[1] - w.c1) ** 2)


@dataclass
class FemSetup:
    """Frequency-independent part of the electrostatic problem."""

    fm: FemMesh
    xs: CrossSection
    region_ids: list[int]
    stiff: dict[int, sp.csr_matrix]  # per region id, unit permittivity, N x N
    node_dofs: list[F64]  # (conductors..., box) boundary DOF sets
    free: npt.NDArray[np.int64]  # interior DOFs
    roles: list[str]  # per node (conductors..., box)
    robin: dict[int, sp.csr_matrix]  # per region id, 1/r outer-boundary term (grounded sections)
    robin_all: sp.csr_matrix | None  # same, unit weight over every outer facet
    robin_center: tuple[float, float]
    notes: list[str] = field(default_factory=list)


@dataclass
class ElectroSolution:
    freqs: F64
    C: C128  # (F, N, N) Maxwell F/m, complex (G = -w imag)
    C0: F64  # (N, N) vacuum F/m
    L_ext: F64  # (N, N) H/m
    conductor_ids: tuple[str, ...]
    ref_ids: tuple[str, ...]
    notes: tuple[str, ...]
    n_dofs: int
    n_solves: int


def prepare_electro(fm: FemMesh, xs: CrossSection) -> FemSetup:
    mesh = fm.mesh
    region = fm.region
    ext = region >= -1
    stiff: dict[int, sp.csr_matrix] = {}
    rids = sorted({int(r) for r in region[ext]})
    for r in rids:
        b = Basis(mesh, ElementTriP2(), elements=np.nonzero(region == r)[0])
        stiff[r] = _laplace.assemble(b).tocsr()
    n = fm.basis.N
    ext_dofs = np.unique(fm.basis.element_dofs[:, ext])
    cond_dofs = [fm.dofs_of(f) for f in fm.cond_facets]
    taken = np.zeros(n, dtype=bool)
    node_dofs: list[F64] = []
    for d in cond_dofs:
        d = d[~taken[d]]
        taken[d] = True
        node_dofs.append(d)  # type: ignore[arg-type]
    has_ground = any(c.role == "ground" for c in xs.conductors)
    robin: dict[int, sp.csr_matrix] = {}
    robin_all: sp.csr_matrix | None = None
    center = (0.0, 0.0)
    roles: list[str] = [c.role for c in xs.conductors]
    notes = ["P2 electrostatic FEM; anisotropic dk_z_over_dk_xy is ignored"]
    if has_ground:
        # Open boundary: the leading far field of a grounded section is a dipole, phi ~ 1/r,
        # so the outer box carries the Robin condition d(phi)/dn = -phi/r (error ~ (L/D)^3
        # instead of the (L/D)^2 of a Dirichlet box).
        sigd = np.concatenate(
            [node_dofs[i] for i, c in enumerate(xs.conductors) if c.role == "signal"]
        ).astype(np.int64)
        c0, c1 = fm.basis.doflocs[:, sigd].mean(axis=1)
        center = (float(c0), float(c1))
        keep = sp.diags((~taken).astype(float))
        if len(fm.outer_facets):
            fb_all = FacetBasis(mesh, ElementTriP2(), facets=fm.outer_facets)
            rb_all = _robin.assemble(fb_all, c0=center[0], c1=center[1])
            robin_all = (keep @ rb_all @ keep).tocsr()
        else:  # fully enclosed (coax): no open boundary
            robin_all = sp.csr_matrix((n, n))
        owner = mesh.f2t[0, fm.outer_facets]
        for r in rids if len(fm.outer_facets) else []:
            sel = fm.outer_facets[region[owner] == r]
            if len(sel) == 0:
                continue
            fb = FacetBasis(mesh, ElementTriP2(), facets=sel)
            rb = _robin.assemble(fb, c0=float(c0), c1=float(c1)).tocsr()
            robin[r] = (keep @ rb @ keep).tocsr()
        notes.append("outer box: Robin 1/r open boundary")
    else:
        box = fm.dofs_of(fm.outer_facets)
        box = box[~taken[box]]
        taken[box] = True
        node_dofs.append(box)  # type: ignore[arg-type]
        roles.append("box")
        notes.append("outer box: floating Dirichlet (zero net charge)")
    mask = np.zeros(n, dtype=bool)
    mask[ext_dofs] = True
    mask &= ~taken
    return FemSetup(
        fm, xs, rids, stiff, node_dofs, np.nonzero(mask)[0], roles, robin, robin_all, center, notes
    )


def _eps_of_region(xs: CrossSection, r: int, f: float) -> complex:
    if r < 0:
        return complex(xs.background_dk)
    return eps_at(xs.dielectrics[r].model, f)


def _maxwell(setup: FemSetup, eps: dict[int, complex]) -> C128:
    """Full Maxwell matrix (eps0 units) of (conductors..., box) for permittivities ``eps``."""
    n = setup.fm.basis.N
    cplx = any(abs(e.imag) > 0 for e in eps.values())
    dt = np.complex128 if cplx else np.float64
    a = sp.csr_matrix((n, n), dtype=dt)
    for r in setup.region_ids:
        a = a + (eps[r] if cplx else eps[r].real) * setup.stiff[r]
        if r in setup.robin:
            a = a + (eps[r] if cplx else eps[r].real) * setup.robin[r]
    a = a.tocsr()
    free = setup.free
    nn = len(setup.node_dofs)
    rows = np.concatenate([np.asarray(d, dtype=np.int64) for d in setup.node_dofs])
    cols = np.concatenate(
        [np.full(len(d), i, dtype=np.int64) for i, d in enumerate(setup.node_dofs)]
    )
    p = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, nn))
    aii = a[free][:, free].tocsc()
    rhs = -(a[free] @ p)
    x = splu(aii).solve(np.asarray(rhs.todense(), dtype=dt))
    k = p.T @ a @ p + p.T @ a[:, free] @ x
    kc: C128 = np.asarray(k, dtype=np.complex128)
    return np.asarray(0.5 * (kc + kc.T), dtype=np.complex128)


def _reduce(setup: FemSetup, k: C128) -> tuple[C128, list[int], list[str]]:
    """Eliminate floating nodes / merge grounds; returns (C_ss, signal node idx, ref ids)."""
    roles = setup.roles
    ids = [c.id for c in setup.xs.conductors] + ["box"]
    sig = [i for i, r in enumerate(roles) if r == "signal"]
    gnd = [i for i, r in enumerate(roles) if r == "ground"]
    flt = [i for i, r in enumerate(roles) if r in ("floating", "box")]
    if not gnd:
        gnd = [sig[-1]]
        sig = sig[:-1]
    if flt:
        kff = k[np.ix_(flt, flt)]
        c = k[np.ix_(sig, sig)] - k[np.ix_(sig, flt)] @ np.linalg.solve(kff, k[np.ix_(flt, sig)])
    else:
        c = k[np.ix_(sig, sig)]
    return c, sig, [ids[i] for i in gnd]


def solve_electro(setup: FemSetup, freqs: F64) -> ElectroSolution:
    xs = setup.xs
    fs = np.asarray(freqs, dtype=float)
    n_solves = 0
    vac = {r: complex(1.0) for r in setup.region_ids}
    k0 = _maxwell(setup, vac).real
    c0c, sig, refs = _reduce(setup, k0.astype(np.complex128))
    c0: F64 = 0.5 * (c0c.real + c0c.real.T)
    n_solves += 1
    L_ext = MU0 * np.linalg.inv(c0)
    ns = len(sig)
    models = [xs.dielectrics[r].model for r in setup.region_ids if r >= 0]
    const = all(is_frequency_independent(m) for m in models)
    cache: dict[float, C128] = {}

    def at(f: float) -> C128:
        nonlocal n_solves
        if f not in cache:
            eps = {r: _eps_of_region(xs, r, f) for r in setup.region_ids}
            if f <= 0.0:
                eps = {r: complex(e.real) for r, e in eps.items()}
            cache[f] = _reduce(setup, _maxwell(setup, eps))[0]
            n_solves += 1
        return cache[f]

    nf = len(fs)
    C = np.zeros((nf, ns, ns), dtype=np.complex128)
    pos = fs[fs > 0.0]
    notes = list(setup.notes)
    if const:
        base = at(float(pos[0])) if len(pos) else at(0.0)
        for i in range(nf):
            C[i] = base.real if fs[i] <= 0.0 else base
    else:
        for i0 in np.nonzero(fs <= 0.0)[0]:
            C[i0] = at(0.0)
        if len(pos):
            lo, hi = float(pos.min()), float(pos.max())
            n_anchor = min(_MAX_ANCHORS, len(np.unique(pos)))
            anchors = (
                np.unique(pos)
                if n_anchor <= 2 or hi <= lo
                else np.exp(np.linspace(np.log(lo), np.log(hi), n_anchor))
            )
            vals = np.stack([at(float(a)) for a in anchors])
            if len(anchors) == 1:
                interp = np.repeat(vals, len(pos), axis=0)
            else:
                flat = vals.reshape(len(anchors), -1)
                lg = np.log(anchors)
                s_re, s_im = CubicSpline(lg, flat.real, axis=0), CubicSpline(lg, flat.imag, axis=0)
                lp = np.log(pos)
                interp = (s_re(lp) + 1j * s_im(lp)).reshape(len(pos), ns, ns)
                if len(anchors) < len(pos):
                    notes.append(
                        f"C(f) solved at {len(anchors)} log-spaced frequencies and interpolated"
                    )
            C[fs > 0.0] = interp
    C = C * EPS0
    C = 0.5 * (C + C.transpose(0, 2, 1))
    if not any(r == "ground" for r in setup.roles):
        notes.append(
            f"no ground conductor: signal {refs[0]!r} is the reference; results are the N-1 "
            "mutual (differential-type) quantities"
        )
    return ElectroSolution(
        freqs=fs,
        C=C,
        C0=c0 * EPS0,
        L_ext=L_ext,
        conductor_ids=tuple(setup.xs.conductors[i].id for i in sig),
        ref_ids=tuple(refs),
        notes=tuple(notes),
        n_dofs=int(setup.fm.basis.N),
        n_solves=n_solves,
    )
