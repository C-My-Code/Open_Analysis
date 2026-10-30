# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""2D quasi-static BEM: Maxwell capacitance C(f), vacuum capacitance C0, L, R(f).

Formulation (all lengths SI, eps0 = 1 inside, charges are sigma~ = sigma / eps0):

- one unknown total (free + bound, vacuum-equivalent) line charge per boundary panel;
- conductor panels: ``phi(r_i) + c = V_k`` (``c`` = unknown additive constant of the 2D log
  kernel, fixed by the constraint that the free charges of all conductors sum to zero);
- dielectric-interface panels with media (e1 on the side of the normal, e2 behind it):
  ``(e1 + e2)/2 sigma~ + (e1 - e2) E_n,avg = 0``;
- free charge on a conductor panel is ``e1 sigma~ l`` (thick conductor) or
  ``((e1 + e2)/2 sigma~ + (e1 - e2) E_n,avg) l`` (zero-thickness ribbon between two media);
- L = mu0 C0^-1 with C0 the vacuum (all eps = 1) capacitance, which needs conductor panels only;
- R(f) from the Wheeler / surface-impedance perturbation using the vacuum current distribution.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.interpolate import CubicSpline  # type: ignore[import-untyped,unused-ignore]

import oa_physics as phys
from oa_impedance._dielectric import eps_at, is_frequency_independent
from oa_model.xsection import CrossSection
from oa_physics.constants import EPS0, MU0

from .geometry import (
    FACE_BOTTOM,
    FACE_TOP,
    KIND_INTERFACE,
    KIND_THICK,
    MeshParams,
    Problem,
    build_problem,
)
from .kernels import influence

C128 = npt.NDArray[np.complex128]
F64 = npt.NDArray[np.float64]

__all__ = ["BemGeometry", "BemSolution", "prepare", "solve_bem"]

_MAX_ANCHORS = 6

try:  # BLAS thread pools oversubscribe badly for these small (N ~ 300) dense systems
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped,unused-ignore]
except ImportError:  # pragma: no cover - optional
    threadpool_limits = None


@contextmanager
def _single_thread() -> Iterator[None]:
    ctx: Any = threadpool_limits(limits=1) if threadpool_limits is not None else nullcontext()
    with ctx:
        yield


@dataclass
class BemGeometry:
    """Frequency-independent part of a BEM problem (mesh + influence matrices)."""

    prob: Problem
    P: F64
    D: F64
    sig_idx: list[int]  # conductor indices of the matrix conductors (reference excluded)
    ref_idx: list[int]  # grounded conductors (or the pseudo-reference)
    float_idx: list[int]
    notes: list[str] = field(default_factory=list)

    @property
    def n_panels(self) -> int:
        return self.prob.panels.n


@dataclass
class BemSolution:
    freqs: F64
    C: C128  # (F, N, N) Maxwell, F/m, complex: C' - j C'' (G = -w imag)
    C0: F64  # (N, N) vacuum
    L_ext: F64  # (N, N) H/m
    R_ac: F64  # (F, N, N) ohm/m (AC part, skin/proximity)
    R_dc: F64  # (N,) ohm/m
    conductor_ids: tuple[str, ...]
    notes: tuple[str, ...]
    n_panels: int
    n_solves: int
    h_char: float


def prepare(xs: CrossSection, mesh: MeshParams | None = None) -> BemGeometry:
    prob = build_problem(xs, mesh or MeshParams())
    pan = prob.panels
    nrm = pan.normal
    # source "normal" in kernels is the panel normal; targets use their own normal
    p, d = influence(pan.mid, pan.normal, pan.a, pan.b, pan.tangent, nrm, pan.length)
    sig = [i for i, r in enumerate(prob.cond_roles) if r == "signal"]
    gnd = [i for i, r in enumerate(prob.cond_roles) if r == "ground"]
    flt = [i for i, r in enumerate(prob.cond_roles) if r == "floating"]
    notes: list[str] = []
    if not gnd:
        ref = sig[-1]
        gnd = [ref]
        sig = sig[:-1]
        notes.append(
            f"no ground conductor: signal {prob.cond_ids[ref]!r} is the reference; "
            "results are the N-1 mutual (differential-type) quantities"
        )
    return BemGeometry(prob, p, d, sig, gnd, flt, notes)


def _zone_eps(prob: Problem, f: float) -> C128:
    out = np.empty(len(prob.zone_models), dtype=np.complex128)
    for k, m in enumerate(prob.zone_models):
        out[k] = prob.background_dk if m is None else eps_at(m, f)
    return out


def _freq_independent(prob: Problem) -> bool:
    return all(m is None or is_frequency_independent(m) for m in prob.zone_models)


def _solve_eps(geo: BemGeometry, ez_in: C128) -> C128:
    """Maxwell C (in eps0 units) for the signal conductors with zone permittivities ``ez``."""
    prob, pan = geo.prob, geo.prob.panels
    n = pan.n
    kind, cond = pan.kind, pan.cond
    real = not np.any(ez_in.imag)
    ez: Any = ez_in.real if real else ez_in
    e1 = ez[pan.zone1]
    e2 = np.where(pan.zone2 >= 0, ez[np.maximum(pan.zone2, 0)], 0.0)
    avg = 0.5 * (e1 + e2)
    diff = e1 - e2
    nf = len(geo.float_idx)
    size = n + 1 + nf
    A = np.zeros((size, size), dtype=np.float64 if real else np.complex128)
    is_if = kind == KIND_INTERFACE
    # conductor rows
    A[:n][~is_if, :n] = geo.P[~is_if]
    A[:n][~is_if, n] = 1.0
    # interface rows
    ii = np.nonzero(is_if)[0]
    A[ii, :n] = diff[ii, None] * geo.D[ii]
    A[ii, ii] += avg[ii]
    # free charge functional per conductor
    ncnd = len(prob.cond_ids)
    Q = np.zeros((ncnd, n), dtype=A.dtype)
    for i in np.nonzero(~is_if)[0]:
        k = int(cond[i])
        if kind[i] == KIND_THICK:
            Q[k, i] += e1[i] * pan.length[i]
        else:
            Q[k] += pan.length[i] * diff[i] * geo.D[i]
            Q[k, i] += pan.length[i] * avg[i]
    for j, fi in enumerate(geo.float_idx):
        rows = np.nonzero((cond == fi) & ~is_if)[0]
        A[rows, n + 1 + j] = -1.0
        A[n + 1 + j, :n] = Q[fi]
    A[n, :n] = Q.sum(axis=0)
    rhs = np.zeros((size, len(geo.sig_idx)), dtype=A.dtype)
    for m, ci in enumerate(geo.sig_idx):
        rhs[:n][(cond == ci) & ~is_if, m] = 1.0
    x = np.linalg.solve(A, rhs)
    return np.asarray(Q[geo.sig_idx] @ x[:n]).astype(np.complex128)


def _vacuum(geo: BemGeometry) -> tuple[F64, F64, npt.NDArray[np.intp]]:
    """C0 (dimensionless, eps0 units) and conductor-panel charges sigma~ (Nc, Ns)."""
    prob, pan = geo.prob, geo.prob.panels
    cidx = np.nonzero(pan.kind != KIND_INTERFACE)[0]
    nc = len(cidx)
    nf = len(geo.float_idx)
    size = nc + 1 + nf
    A = np.zeros((size, size))
    A[:nc, :nc] = geo.P[np.ix_(cidx, cidx)]
    A[:nc, nc] = 1.0
    cond = pan.cond[cidx]
    ncnd = len(prob.cond_ids)
    Q = np.zeros((ncnd, nc))
    for k in range(ncnd):
        Q[k, cond == k] = pan.length[cidx][cond == k]
    for j, fi in enumerate(geo.float_idx):
        A[:nc][cond == fi, nc + 1 + j] = -1.0
        A[nc + 1 + j, :nc] = Q[fi]
    A[nc, :nc] = Q.sum(axis=0)
    rhs = np.zeros((size, len(geo.sig_idx)))
    for m, ci in enumerate(geo.sig_idx):
        rhs[:nc][cond == ci, m] = 1.0
    # NDArray[Any]: numpy < 2.5 (resolved on Python 3.11) types linalg results as floating[Any]
    x: npt.NDArray[Any] = np.linalg.solve(A, rhs)
    sig = x[:nc]
    return Q[geo.sig_idx] @ sig, sig, cidx


def _loss(
    geo: BemGeometry,
    sig: F64,
    cidx: npt.NDArray[np.intp],
    c0: F64,
    freqs: F64,
    rdc_on: bool,
) -> tuple[F64, F64]:
    """R_ac(f) (F, N, N) and R_dc (N,)."""
    prob, pan = geo.prob, geo.prob.panels
    ns = len(geo.sig_idx)
    cond = pan.cond[cidx]
    length = pan.length[cidx]
    face = pan.face[cidx]
    c0inv = np.linalg.inv(c0)
    r_ac = np.zeros((len(freqs), ns, ns))
    sigma = np.array([prob.cond_sigma[k] for k in cond])
    # roughness classes present
    for fi, f in enumerate(freqs):
        if f <= 0.0:
            continue
        rs = np.sqrt(np.pi * f * MU0 / sigma)  # surface resistance 1/(sigma delta) [ohm/sq]
        kfac = np.ones(len(cidx))
        for p in range(len(cidx)):
            top, bot, side = prob.cond_rough[cond[p]]
            rough = top if face[p] == FACE_TOP else (bot if face[p] == FACE_BOTTOM else side)
            kfac[p] = float(phys.roughness_factor(rough, f, float(sigma[p]))[0])
        w = rs * kfac * length
        s_mat = (sig * w[:, None]).T @ sig
        r_ac[fi] = c0inv @ s_mat @ c0inv
    r_dc = np.zeros(ns)
    if rdc_on:
        for m, ci in enumerate(geo.sig_idx):
            area = prob.cond_area[ci]
            r_dc[m] = 1.0 / (prob.cond_sigma[ci] * area) if area > 0 else 0.0
    return r_ac, r_dc


def solve_bem(
    xs: CrossSection,
    freqs: list[float] | F64,
    mesh: MeshParams | None = None,
    *,
    geo: BemGeometry | None = None,
) -> BemSolution:
    """Solve the cross-section at ``freqs`` [Hz] (0.0 = static, lossless)."""
    with _single_thread():
        return _solve_bem(xs, freqs, mesh, geo)


def _solve_bem(
    xs: CrossSection,
    freqs: list[float] | F64,
    mesh: MeshParams | None,
    geo: BemGeometry | None,
) -> BemSolution:
    geo = geo or prepare(xs, mesh)
    prob = geo.prob
    fs = np.asarray(freqs, dtype=float)
    ns = len(geo.sig_idx)
    nf = len(fs)
    notes = list(prob.notes) + list(geo.notes)

    c0hat, sig0, cidx = _vacuum(geo)
    c0hat = 0.5 * (c0hat + c0hat.T)
    L_ext: npt.NDArray[Any] = MU0 * np.linalg.inv(c0hat)  # NDArray[Any]: see _vacuum

    pos = fs[fs > 0.0]
    const = _freq_independent(prob)
    n_solves = 0
    C = np.zeros((nf, ns, ns), dtype=np.complex128)
    # static point and the frequency grid
    cache: dict[float, C128] = {}

    def at(f: float) -> C128:
        nonlocal n_solves
        if f not in cache:
            ez = _zone_eps(geo.prob, f)
            if f <= 0.0:
                ez = ez.real.astype(np.complex128)
            cache[f] = _solve_eps(geo, ez)
            n_solves += 1
        return cache[f]

    if const:
        base = at(float(pos[0])) if len(pos) else at(0.0)
        for i in range(nf):
            C[i] = base.real if fs[i] <= 0.0 else base
        n_solves = 1
    else:
        for i0 in np.nonzero(fs <= 0.0)[0]:
            C[i0] = at(0.0)
        if len(pos):
            lo, hi = float(pos.min()), float(pos.max())
            n_anchor = min(_MAX_ANCHORS, len(np.unique(pos)))
            if n_anchor <= 2 or hi <= lo:
                anchors = np.unique(pos)
            else:
                anchors = np.exp(np.linspace(np.log(lo), np.log(hi), n_anchor))
            vals = np.stack([at(float(a)) for a in anchors])
            if len(anchors) == 1:
                interp = np.repeat(vals, len(pos), axis=0)
            else:
                lg = np.log(anchors)
                flat = vals.reshape(len(anchors), -1)
                spl = CubicSpline(lg, flat.real, axis=0), CubicSpline(lg, flat.imag, axis=0)
                lp = np.log(pos)
                interp = (spl[0](lp) + 1j * spl[1](lp)).reshape(len(pos), ns, ns)
                if len(anchors) < len(pos):
                    notes.append(
                        f"C(f) solved at {len(anchors)} log-spaced frequencies and interpolated"
                    )
            C[fs > 0.0] = interp
    C = C * EPS0
    C = 0.5 * (C + C.transpose(0, 2, 1))

    r_ac, r_dc = _loss(geo, sig0, cidx, c0hat, fs, True)
    return BemSolution(
        freqs=fs,
        C=C,
        C0=c0hat * EPS0,
        L_ext=L_ext,
        R_ac=r_ac,
        R_dc=r_dc,
        conductor_ids=tuple(prob.cond_ids[i] for i in geo.sig_idx),
        notes=tuple(notes),
        n_panels=geo.n_panels,
        n_solves=n_solves,
        h_char=prob.h_char,
    )
