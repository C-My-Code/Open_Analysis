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

Device path (WP-504): when ``oa_accel`` selects a GPU for the section (``OA_DEVICE``; ``auto`` above
the measured dense crossover), the influence matrices are evaluated on the device, the system is
assembled there and factored by cuSOLVER (float64/complex128). Memory is bounded by the device
budget, so sections that exceed the host section budget stay dense on the GPU. Any device failure
falls back to the host path.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from scipy.interpolate import CubicSpline  # type: ignore[import-untyped,unused-ignore]

from oa_accel.errors import AccelError
from oa_impedance import _physics_compat as phys
from oa_impedance.errors import ImpedanceUnsupported, SectionTooLarge
from oa_model.xsection import CrossSection

from .geometry import (
    FACE_BOTTOM,
    FACE_TOP,
    KIND_INTERFACE,
    KIND_THICK,
    MeshParams,
    Problem,
    build_problem,
)
from .iterative import EpsOperator, gmres_lockstep
from .kernels import influence_blocks

if TYPE_CHECKING:
    from oa_accel import Backend

C128 = npt.NDArray[np.complex128]
F64 = npt.NDArray[np.float64]

__all__ = [
    "DEFAULT_BUDGET_BYTES",
    "ITERATIVE_MAX_PANELS",
    "BemGeometry",
    "BemSolution",
    "dense_peak_bytes",
    "prepare",
    "solve_bem",
]

DEFAULT_BUDGET_BYTES = 512 * 2**20  # per section: influence matrices + system matrix + LU
GATE_BYTES = int(float(os.environ.get("OA_IMPEDANCE_TOTAL_MB", "1536")) * 2**20)
_MAX_COARSEN = 3  # at most 1.25**3 = 1.95x coarser than the requested mesh
_COARSEN_STEP = 1.25
ITERATIVE_MAX_PANELS = 20_000  # beyond this the matrix-free sweeps are too slow to be useful

_MAX_ANCHORS = 6

try:  # BLAS thread pools oversubscribe badly for these small (N ~ 300) dense systems
    from threadpoolctl import threadpool_limits  # type: ignore[import-untyped,unused-ignore]
except ImportError:  # pragma: no cover - optional
    threadpool_limits = None


class _MemoryGate:
    """Caps the summed working-memory estimate of the BEM solves running at once (threads of
    ``solve_many``): a solve waits until its estimate fits, and runs alone if it never would."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.used = 0
        self._cv = threading.Condition()

    def acquire(self, nbytes: int) -> None:
        with self._cv:
            while self.used > 0 and self.used + nbytes > self.total:
                self._cv.wait()
            self.used += nbytes

    def release(self, nbytes: int) -> None:
        with self._cv:
            self.used -= nbytes
            self._cv.notify_all()


_GATE = _MemoryGate(GATE_BYTES)
_DEV_GATES: dict[int, _MemoryGate] = {}
_DEV_LOCK = threading.Lock()
_DEV_FRACTION = 0.8  # share of the device budget the concurrent BEM solves may hold


@contextmanager
def _single_thread() -> Iterator[None]:
    ctx: Any = threadpool_limits(limits=1) if threadpool_limits is not None else nullcontext()
    with ctx:
        yield


@dataclass
class BemGeometry:
    """Frequency-independent part of a BEM problem (mesh + influence matrices)."""

    prob: Problem
    P: F64 | None  # None in the iterative (matrix-free) mode
    D: F64 | None
    sig_idx: list[int]  # conductor indices of the matrix conductors (reference excluded)
    ref_idx: list[int]  # grounded conductors (or the pseudo-reference)
    float_idx: list[int]
    notes: list[str] = field(default_factory=list)
    mode: str = "dense"  # "dense" (LU) or "gmres" (matrix-free)
    peak_bytes: int = 0  # estimated peak working memory of the chosen mode
    iterations: int = 0
    reserved: int = 0  # bytes held in the memory gate (released by solve_bem)
    be: Backend | None = None  # GPU backend when P / D live on the device
    reserved_dev: int = 0  # bytes held in the device gate

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
    mode: str = "dense"
    peak_bytes: int = 0
    iterations: int = 0


def dense_peak_bytes(n: int, n_cond: int, *, complex_: bool = False) -> int:
    """Estimated peak working memory [B] of the dense path: both influence matrices, the system
    matrix (real or complex) factored in place, or the conductor-only vacuum system."""
    item = 16 if complex_ else 8
    return 16 * n * n + max(item * (n + 1) ** 2, 8 * n_cond * n_cond) + (8 << 20)


def dense_device_bytes(n: int, *, complex_: bool = False) -> int:
    """Estimated device peak [B] of the GPU dense path: P and D, the system matrix and the copy
    cuSOLVER factors."""
    item = 16 if complex_ else 8
    return 16 * n * n + 2 * item * (n + 1) ** 2 + (64 << 20)


def _device_backend(n: int, est: int) -> Backend | None:
    """The GPU backend when ``oa_accel`` selects it for a dense system of order ``n``."""
    if os.environ.get("OA_DEVICE", "auto").strip().lower() == "cpu":
        return None
    from oa_accel import DevicePolicy, Problem, select
    from oa_accel.solvers import THRESHOLDS

    pol = DevicePolicy(
        problem=Problem(kind="dense", n=n, estimate_bytes=est), thresholds=THRESHOLDS
    )
    try:
        be = select(pol)
    except Exception:  # device string or driver problems never fail a CPU-capable solve
        return None
    return None if be.name == "cpu" else be


def _dev_gate(be: Backend) -> _MemoryGate:
    with _DEV_LOCK:
        key = getattr(be, "device_id", 0)  # one gate per device, shared by every section
        if key not in _DEV_GATES:
            _DEV_GATES[key] = _MemoryGate(int(be.memory.gpu_bytes * _DEV_FRACTION))
        return _DEV_GATES[key]


def _influence_dev(xp: Any, geo_p: Problem, p: Any, d: Any) -> None:
    """``kernels.influence`` on the device, in row blocks, written into ``p`` and ``d``."""
    pan = geo_p.panels
    mid, nrm_t, a, tan, nrm, ln = (
        xp.asarray(v) for v in (pan.mid, pan.normal, pan.a, pan.tangent, pan.normal, pan.length)
    )
    n = pan.n
    inv2pi = 1.0 / (2.0 * np.pi)
    rows = max(8, (1 << 22) // n)
    for r0 in range(0, n, rows):
        r1 = min(n, r0 + rows)
        rx = mid[r0:r1, None, 0] - a[None, :, 0]
        ry = mid[r0:r1, None, 1] - a[None, :, 1]
        x0 = rx * tan[None, :, 0] + ry * tan[None, :, 1]
        y0 = rx * nrm[None, :, 0] + ry * nrm[None, :, 1]
        s1 = -x0
        s2 = ln[None, :] - x0
        y2 = y0 * y0
        l1 = xp.log(xp.maximum(s1 * s1 + y2, 1e-300))
        l2 = xp.log(xp.maximum(s2 * s2 + y2, 1e-300))
        ay = xp.abs(y0)
        f2 = 0.5 * s2 * l2 - s2 + ay * xp.arctan2(s2, ay)
        f1 = 0.5 * s1 * l1 - s1 + ay * xp.arctan2(s1, ay)
        p[r0:r1] = -inv2pi * (f2 - f1)
        ex = inv2pi * 0.5 * (l1 - l2)
        ey = inv2pi * xp.arctan2(y0 * (s2 - s1), y2 + s1 * s2)
        d[r0:r1] = ex * (
            tan[None, :, 0] * nrm_t[r0:r1, None, 0] + tan[None, :, 1] * nrm_t[r0:r1, None, 1]
        ) + ey * (nrm[None, :, 0] * nrm_t[r0:r1, None, 0] + nrm[None, :, 1] * nrm_t[r0:r1, None, 1])
    idx = xp.arange(n)
    d[idx, idx] = 0.0


def _assemble_dev(be: Backend, prob: Problem, dev_est: int) -> tuple[Any, Any]:
    """P and D on the device. Blocks (``acquire``) until the device gate has room."""
    xp = be.xp
    n = prob.panels.n
    gate = _dev_gate(be)
    gate.acquire(dev_est)
    try:
        p = xp.empty((n, n))
        d = xp.empty((n, n))
        _influence_dev(xp, prob, p, d)
    except BaseException:
        gate.release(dev_est)
        raise
    return p, d


def _empty_conductors(prob: Problem) -> list[str]:
    """Conductors that own no boundary panel: the meshing merged them into another conductor
    (coincident copper), so their row of the system is empty and the matrix is singular."""
    pan = prob.panels
    owned = np.unique(pan.cond[pan.kind != KIND_INTERFACE])
    return [cid for k, cid in enumerate(prob.cond_ids) if k not in set(owned.tolist())]


def _iterative_peak_bytes(n: int, n_rhs: int) -> int:
    # Krylov bases + preconditioner blocks + kernel temporaries
    return 8 * n * (60 + 2) * (n_rhs + 1) * 2 + 8 * n * 192 + (16 << 20)


def prepare(
    xs: CrossSection,
    mesh: MeshParams | None = None,
    budget: int | None = None,
    *,
    reserve: bool = False,
) -> BemGeometry:
    mesh = mesh or MeshParams()
    limit = DEFAULT_BUDGET_BYTES if budget is None else budget
    notes: list[str] = []
    n0 = 0
    for step in range(_MAX_COARSEN + 1):
        prob = build_problem(xs, mesh)
        pan = prob.panels
        n = pan.n
        n0 = n0 or n
        n_cond = int(np.count_nonzero(pan.kind != KIND_INTERFACE))
        cplx = not _freq_independent(prob)
        est = dense_peak_bytes(n, n_cond, complex_=cplx)
        dev_est = dense_device_bytes(n, complex_=cplx)
        be = _device_backend(n, dev_est)
        if be is not None and dev_est <= _dev_gate(be).total:
            break  # the device budget, not the host section budget, bounds this section
        be = None
        if est <= limit or step == _MAX_COARSEN:
            break
        # adaptive panelization: coarsen the whole grading (same shape, fewer panels) until the
        # dense system fits; the accuracy cost is noted in the result
        mesh = mesh.scaled(1.0 / _COARSEN_STEP)
    if step:
        notes.append(
            f"mesh coarsened {_COARSEN_STEP**step:.2f}x ({n0} -> {n} panels) to fit the "
            f"{limit / 2**20:.0f} MB section budget"
        )
    empty = _empty_conductors(prob)
    if empty:
        raise ImpedanceUnsupported(
            f"conductor(s) {empty!r} have no boundary of their own in this cross-section "
            "(coincident with another conductor), so the BEM system is singular",
            path="xsection.conductors",
            hint="Check the routing at this position: two tracks (for example both sides of a "
            "pair, or a track and its own copy) occupy the same copper.",
        )
    p: Any = None
    d: Any = None
    mode = "dense"
    peak = est
    if be is not None:
        peak = 16 << 20  # host side: the device holds the matrices
    elif est > limit:
        # the conductor-only vacuum system stays dense
        peak = max(_iterative_peak_bytes(n, 8), 8 * n_cond * n_cond + (8 << 20))
        if n > ITERATIVE_MAX_PANELS or peak > limit:
            raise SectionTooLarge(
                f"BEM section needs {n} panels (~{est / 2**20:.0f} MB dense, budget "
                f"{limit / 2**20:.0f} MB)",
                path="xsection",
                hint="Reduce the window (fewer aggressors / coplanar grounds), use a coarser "
                "MeshParams.alpha, or raise the section budget (OA_IMPEDANCE_SECTION_MB).",
            )
        mode = "gmres"
        notes.append(
            f"{n} panels: dense system ~{est / 2**20:.0f} MB exceeds the "
            f"{limit / 2**20:.0f} MB section budget; matrix-free GMRES with block-Jacobi used"
        )
    if be is not None:
        try:
            p, d = _assemble_dev(be, prob, dev_est)
        except Exception as exc:  # OOM or driver trouble: redo on the host (budget unchecked)
            be = None
            peak = est
            notes.append(f"GPU assembly failed ({type(exc).__name__}); host dense path used")
    dev_held = 0
    if be is not None:
        dev_held = dev_est
        notes.append(f"{n} panels: dense LU on {be.name} ({be.report().device_name})")
    if reserve:
        _GATE.acquire(peak)
    if mode == "dense" and be is None:
        p = np.empty((n, n))
        d = np.empty((n, n))
        # source "normal" in kernels is the panel normal; targets use their own normal
        influence_blocks(
            pan.mid, pan.normal, pan.a, pan.b, pan.tangent, pan.normal, pan.length, p, d, same=True
        )
    sig = [i for i, r in enumerate(prob.cond_roles) if r == "signal"]
    gnd = [i for i, r in enumerate(prob.cond_roles) if r == "ground"]
    flt = [i for i, r in enumerate(prob.cond_roles) if r == "floating"]
    if not gnd:
        ref = sig[-1]
        gnd = [ref]
        sig = sig[:-1]
        notes.append(
            f"no ground conductor: signal {prob.cond_ids[ref]!r} is the reference; "
            "results are the N-1 mutual (differential-type) quantities"
        )
    return BemGeometry(
        prob, p, d, sig, gnd, flt, notes, mode, peak, 0, peak if reserve else 0, be, dev_held
    )


def _zone_eps(prob: Problem, f: float) -> C128:
    out = np.empty(len(prob.zone_models), dtype=np.complex128)
    for k, m in enumerate(prob.zone_models):
        out[k] = prob.background_dk if m is None else phys.eps_complex(m, f)
    return out


def _freq_independent(prob: Problem) -> bool:
    return all(m is None or phys.is_frequency_independent(m) for m in prob.zone_models)


def _chunks(idx: npt.NDArray[np.intp], size: int = 256) -> Iterator[npt.NDArray[np.intp]]:
    for k in range(0, len(idx), size):
        yield idx[k : k + size]


def _lu_solve(a: Any, rhs: Any) -> Any:
    from scipy.linalg import solve as _solve  # type: ignore[import-untyped,unused-ignore]

    return _solve(a, rhs, overwrite_a=True, check_finite=False)


def _solve_eps(geo: BemGeometry, ez_in: C128) -> C128:
    """Maxwell C (in eps0 units) for the signal conductors with zone permittivities ``ez``."""
    prob, pan = geo.prob, geo.prob.panels
    n = pan.n
    kind, cond = pan.kind, pan.cond
    is_if = kind == KIND_INTERFACE
    nf = len(geo.float_idx)
    size = n + 1 + nf
    rhs: Any = np.zeros((size, len(geo.sig_idx)))
    for m, ci in enumerate(geo.sig_idx):
        rhs[:n][(cond == ci) & ~is_if, m] = 1.0
    if geo.P is None:
        return _solve_eps_gmres(geo, ez_in, rhs)
    assert geo.D is not None
    if geo.be is not None:
        return _solve_eps_dev(geo, ez_in, rhs)
    real = not np.any(ez_in.imag)
    ez: Any = ez_in.real if real else ez_in
    e1 = ez[pan.zone1]
    e2 = np.where(pan.zone2 >= 0, ez[np.maximum(pan.zone2, 0)], 0.0)
    avg = 0.5 * (e1 + e2)
    diff = e1 - e2
    # Fortran order: the LU then overwrites A instead of copying it
    A = np.zeros((size, size), dtype=np.float64 if real else np.complex128, order="F")
    for ch in _chunks(np.nonzero(~is_if)[0]):  # conductor rows
        A[ch, :n] = geo.P[ch]
    A[:n][~is_if, n] = 1.0
    ii = np.nonzero(is_if)[0]  # interface rows
    for ch in _chunks(ii):
        A[ch, :n] = diff[ch, None] * geo.D[ch]
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
    x: Any = _lu_solve(A, rhs.astype(A.dtype))
    return np.asarray(Q[geo.sig_idx] @ x[:n]).astype(np.complex128)


def _solve_eps_dev(geo: BemGeometry, ez_in: C128, rhs: Any) -> C128:
    """``_solve_eps`` with the system assembled on the device and factored by cuSOLVER."""
    be = geo.be
    assert be is not None
    xp = be.xp
    prob, pan = geo.prob, geo.prob.panels
    n = pan.n
    kind, cond = pan.kind, pan.cond
    is_if = kind == KIND_INTERFACE
    nf = len(geo.float_idx)
    size = n + 1 + nf
    real = not np.any(ez_in.imag)
    ez: Any = ez_in.real if real else ez_in
    e1 = ez[pan.zone1]
    e2 = np.where(pan.zone2 >= 0, ez[np.maximum(pan.zone2, 0)], 0.0)
    avg = 0.5 * (e1 + e2)
    diff = e1 - e2
    dt = np.float64 if real else np.complex128
    ci = np.nonzero(~is_if)[0]
    ii = np.nonzero(is_if)[0]
    ncnd = len(prob.cond_ids)
    P: Any = geo.P
    D: Any = geo.D
    a = xp.zeros((size, size), dtype=dt, order="F")
    a[xp.asarray(ci), :n] = P[xp.asarray(ci)]
    a[xp.asarray(ci), n] = 1.0
    a[xp.asarray(ii), :n] = xp.asarray(diff[ii])[:, None] * D[xp.asarray(ii)]
    a[xp.asarray(ii), xp.asarray(ii)] += xp.asarray(avg[ii])
    # free-charge functional per conductor: thick panels on the diagonal, ribbons via D rows
    q = xp.zeros((ncnd, n), dtype=dt)
    thick = np.nonzero(kind == KIND_THICK)[0]
    q[xp.asarray(cond[thick]), xp.asarray(thick)] = xp.asarray(e1[thick] * pan.length[thick])
    rib = np.nonzero((kind != KIND_THICK) & ~is_if)[0]
    if len(rib):
        sel = xp.zeros((ncnd, len(rib)), dtype=dt)
        sel[xp.asarray(cond[rib]), xp.arange(len(rib))] = xp.asarray(pan.length[rib] * diff[rib])
        q += sel @ D[xp.asarray(rib)]
        q[xp.asarray(cond[rib]), xp.asarray(rib)] += xp.asarray(pan.length[rib] * avg[rib])
    for j, fi in enumerate(geo.float_idx):
        rows = np.nonzero((cond == fi) & ~is_if)[0]
        a[xp.asarray(rows), n + 1 + j] = -1.0
        a[n + 1 + j, :n] = q[fi]
    a[n, :n] = q.sum(axis=0)
    x = be.dense_solve(a, xp.asarray(rhs.astype(dt)), host=False)
    xd = xp.asarray(x)
    sig = xp.asarray(np.asarray(geo.sig_idx, dtype=np.intp))
    return np.asarray(be.to_host(q[sig] @ xd[:n])).astype(np.complex128)


def _solve_eps_gmres(geo: BemGeometry, ez_in: C128, rhs: Any) -> C128:
    pan = geo.prob.panels
    op = EpsOperator(pan, len(geo.prob.cond_ids), geo.float_idx, ez_in)
    x, its, res = gmres_lockstep(op, rhs.astype(op.dtype), op.preconditioner())
    geo.iterations = max(geo.iterations, its)
    if res > 1e-6:
        raise SectionTooLarge(
            f"GMRES did not converge on a {pan.n}-panel section (residual {res:.2g})",
            path="xsection",
            hint="Use a coarser MeshParams.alpha or reduce the section window.",
        )
    q = op.charge_of(x)
    return np.asarray(q[geo.sig_idx]).astype(np.complex128)


def _vacuum(geo: BemGeometry) -> tuple[F64, F64, npt.NDArray[np.intp]]:
    """C0 (dimensionless, eps0 units) and conductor-panel charges sigma~ (Nc, Ns)."""
    prob, pan = geo.prob, geo.prob.panels
    cidx = np.nonzero(pan.kind != KIND_INTERFACE)[0]
    nc = len(cidx)
    nf = len(geo.float_idx)
    size = nc + 1 + nf
    A = np.zeros((size, size))
    if geo.P is not None and geo.be is not None:
        xp = geo.be.xp
        ci_d = xp.asarray(cidx)
        A[:nc, :nc] = geo.be.to_host(geo.P[ci_d[:, None], ci_d[None, :]])
    elif geo.P is not None:
        A[:nc, :nc] = geo.P[np.ix_(cidx, cidx)]
    else:
        p_blk = np.empty((nc, nc))
        influence_blocks(
            pan.mid[cidx],
            pan.normal[cidx],
            pan.a[cidx],
            pan.b[cidx],
            pan.tangent[cidx],
            pan.normal[cidx],
            pan.length[cidx],
            p_blk,
            np.empty((nc, nc)),
            same=True,
        )
        A[:nc, :nc] = p_blk
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
    # the loss factors depend on (conductor, face) only: evaluate once per class, then gather
    classes, inv = np.unique(cond.astype(np.int64) * 8 + face.astype(np.int64), return_inverse=True)
    c_cond = classes // 8
    c_face = classes % 8
    c_sigma = np.array([prob.cond_sigma[int(k)] for k in c_cond])
    for fi, f in enumerate(freqs):
        if f <= 0.0:
            continue
        rs_c = np.array([phys.surface_resistance(f, s) for s in c_sigma])
        k_c = np.ones(len(classes))
        for q in range(len(classes)):
            top, bot, side = prob.cond_rough[int(c_cond[q])]
            rough = top if c_face[q] == FACE_TOP else (bot if c_face[q] == FACE_BOTTOM else side)
            k_c[q] = phys.roughness_factor(rough, f, float(c_sigma[q]))
        w = (rs_c * k_c)[inv] * length
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
    budget: int | None = None,
) -> BemSolution:
    """Solve the cross-section at ``freqs`` [Hz] (0.0 = static, lossless).

    ``budget`` [B] is the per-section working-memory limit (default 512 MB): above it the
    matrix-free GMRES path is used, and above its limits ``SectionTooLarge`` is raised."""
    with _single_thread():
        return _solve_bem(xs, freqs, mesh, geo, budget)


def _singular(detail: str) -> ImpedanceUnsupported:
    return ImpedanceUnsupported(
        f"the BEM system is singular ({detail})",
        path="xsection",
        hint="A conductor may float without a reference or the geometry is degenerate "
        "(coincident conductors, zero-width features).",
    )


def _solve_bem(
    xs: CrossSection,
    freqs: list[float] | F64,
    mesh: MeshParams | None,
    geo: BemGeometry | None,
    budget: int | None,
) -> BemSolution:
    geo = geo or prepare(xs, mesh, budget, reserve=True)
    try:
        return _solve_prepared(geo, freqs)
    except np.linalg.LinAlgError as exc:
        raise _singular(str(exc)) from exc
    except AccelError as exc:
        if exc.info.code != "ACCEL_SINGULAR":
            raise
        raise _singular(exc.info.message) from exc
    finally:
        if geo.reserved:
            _GATE.release(geo.reserved)
            geo.reserved = 0
        if geo.reserved_dev and geo.be is not None:
            _dev_gate(geo.be).release(geo.reserved_dev)
            geo.reserved_dev = 0
            geo.P = geo.D = None  # free the device matrices
            # return the blocks to the driver: on Windows the CuPy pool counts as committed
            # memory of the process (a job-object cap then fails unrelated host allocations)
            geo.be.xp.get_default_memory_pool().free_all_blocks()


def _solve_prepared(geo: BemGeometry, freqs: list[float] | F64) -> BemSolution:
    prob = geo.prob
    fs = np.asarray(freqs, dtype=float)
    ns = len(geo.sig_idx)
    nf = len(fs)
    notes = list(prob.notes) + list(geo.notes)

    c0hat, sig0, cidx = _vacuum(geo)
    c0hat = 0.5 * (c0hat + c0hat.T)
    L_ext: npt.NDArray[Any] = phys.MU0 * np.linalg.inv(c0hat)  # NDArray[Any]: see _vacuum

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
    C = C * phys.EPS0
    C = 0.5 * (C + C.transpose(0, 2, 1))

    r_ac, r_dc = _loss(geo, sig0, cidx, c0hat, fs, True)
    return BemSolution(
        freqs=fs,
        C=C,
        C0=c0hat * phys.EPS0,
        L_ext=L_ext,
        R_ac=r_ac,
        R_dc=r_dc,
        conductor_ids=tuple(prob.cond_ids[i] for i in geo.sig_idx),
        notes=tuple(notes),
        n_panels=geo.n_panels,
        n_solves=n_solves,
        h_char=prob.h_char,
        mode=geo.mode,
        peak_bytes=geo.peak_bytes,
        iterations=geo.iterations,
    )
