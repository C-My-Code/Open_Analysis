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
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import numpy.typing as npt
from scipy.interpolate import CubicSpline  # type: ignore[import-untyped,unused-ignore]

import oa_physics as phys
from oa_accel.errors import AccelError, ErrorInfo
from oa_impedance._dielectric import eps_at, is_frequency_independent
from oa_impedance.errors import ImpedanceUnsupported, SectionTooLarge
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
    "batch",
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
_BLOCK = os.environ.get("OA_BEM_BLOCK", "1").strip() != "0"  # "0": factor the whole system (oracle)
_PIVOT_RATIO = 1e-12  # smallest |U_ii| / largest |U_ii| of the conductor block that is trusted

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


_ST_LOCK = threading.Lock()
_ST_DEPTH = 0
_ST_CTX: Any = None
_BATCH_DEPTH = 0
_SMALL_BATCH_CPU = (
    0  # open ``batch(small=True)`` contexts: a few sections do not repay GPU start-up
)
_SMALL_BATCH_N = 1500  # panels below which a small batch stays on the host (device 'auto' only)
_BATCH_DEV: dict[int, Any] = {}  # backends whose pool free is deferred to the end of a batch


def _blas_threads() -> int:
    """BLAS threads of one solve (``OA_IMPEDANCE_BLAS_THREADS``, default 1): the LU of concurrent
    sections does not run in parallel across Python threads, so a batch can set more here."""
    try:
        return max(1, int(os.environ.get("OA_IMPEDANCE_BLAS_THREADS", "1")))
    except ValueError:
        return 1


def _enter_single_thread() -> None:
    global _ST_DEPTH, _ST_CTX
    with _ST_LOCK:
        _ST_DEPTH += 1
        if _ST_DEPTH == 1 and threadpool_limits is not None:
            _ST_CTX = threadpool_limits(limits=_blas_threads())


def _exit_single_thread() -> None:
    global _ST_DEPTH, _ST_CTX
    with _ST_LOCK:
        _ST_DEPTH -= 1
        if _ST_DEPTH == 0 and _ST_CTX is not None:
            _ST_CTX.restore_original_limits()
            _ST_CTX = None


@contextmanager
def _single_thread() -> Iterator[None]:
    """BLAS limited to one thread. Re-entrant and shared by concurrent solves: the (slow,
    process-global) limit is set by the first one in and restored by the last one out."""
    _enter_single_thread()
    try:
        yield
    finally:
        _exit_single_thread()


@contextmanager
def batch(*, small: bool = False) -> Iterator[None]:
    """Many solves in a row (``solve_many``): the BLAS limit is set once, and the device memory
    pool is returned to the driver once at the end instead of after every section. ``small``:
    the batch has only a few sections, so with ``OA_DEVICE=auto`` the sections of fewer than
    ``_SMALL_BATCH_N`` panels stay on the host (the first CUDA call costs about 0.8 s)."""
    global _BATCH_DEPTH, _SMALL_BATCH_CPU
    _enter_single_thread()
    with _ST_LOCK:
        _BATCH_DEPTH += 1
        _SMALL_BATCH_CPU += small
    try:
        yield
    finally:
        with _ST_LOCK:
            _BATCH_DEPTH -= 1
            _SMALL_BATCH_CPU -= small
            pending = list(_BATCH_DEV.values()) if _BATCH_DEPTH == 0 else []
            if pending:
                _BATCH_DEV.clear()
        for be in pending:
            be.xp.get_default_memory_pool().free_all_blocks()
        _exit_single_thread()


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
    fac: Any = None  # LU of the conductor block of P, shared by C0 and every C(f)

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
    spec = os.environ.get("OA_DEVICE", "auto").strip().lower()
    if spec == "cpu" or (spec == "auto" and _SMALL_BATCH_CPU > 0 and n < _SMALL_BATCH_N):
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


_KERNEL_ENTRIES = 1 << 22  # matrix entries per influence launch (about 5 ms on the reference GPU)


def _dev_gate(be: Backend) -> _MemoryGate:
    with _DEV_LOCK:
        key = getattr(be, "device_id", 0)  # one gate per device, shared by every section
        if key not in _DEV_GATES:
            _DEV_GATES[key] = _MemoryGate(int(be.memory.gpu_bytes * _DEV_FRACTION))
        return _DEV_GATES[key]


_INFLUENCE_BODY = """
    double rx = mx - ax, ry = my - ay;
    double x0 = rx * tx + ry * ty;
    double y0 = rx * nx + ry * ny;
    double s1 = -x0, s2 = ln - x0, y2 = y0 * y0;
    double l1 = log(fmax(s1 * s1 + y2, 1e-300));
    double l2 = log(fmax(s2 * s2 + y2, 1e-300));
    double aby = fabs(y0);
    double f2 = 0.5 * s2 * l2 - s2 + aby * atan2(s2, aby);
    double f1 = 0.5 * s1 * l1 - s1 + aby * atan2(s1, aby);
    p = -0.15915494309189535 * (f2 - f1);
    double ex = 0.15915494309189535 * 0.5 * (l1 - l2);
    double ey = 0.15915494309189535 * atan2(y0 * (s2 - s1), y2 + s1 * s2);
    d = ex * (tx * ntx + ty * nty) + ey * (nx * ntx + ny * nty);
"""
_INFLUENCE_KERNELS: dict[int, Any] = {}


def _influence_kernel(xp: Any) -> Any:
    """One fused kernel for P and D: the same expressions as ``kernels.influence``, no temporaries
    (the CuPy pool is per stream, so the former row-block temporaries multiplied by the number of
    sections in flight), one launch instead of about 200."""
    key = id(xp)
    if key not in _INFLUENCE_KERNELS:
        _INFLUENCE_KERNELS[key] = xp.ElementwiseKernel(
            "float64 mx, float64 my, float64 ntx, float64 nty, float64 ax, float64 ay, "
            "float64 tx, float64 ty, float64 nx, float64 ny, float64 ln",
            "float64 p, float64 d",
            _INFLUENCE_BODY,
            "oa_bem_influence",
        )
    return _INFLUENCE_KERNELS[key]


def _influence_dev(xp: Any, geo_p: Problem, p: Any, d: Any) -> None:
    """``kernels.influence`` on the device, written into ``p`` and ``d``. Row blocks keep every
    launch short (the Windows watchdog kills a kernel that runs for about 2 s)."""
    pan = geo_p.panels
    n = pan.n
    row = [xp.asarray(np.ascontiguousarray(v))[:, None] for v in (*pan.mid.T, *pan.normal.T)]
    col = [
        xp.asarray(np.ascontiguousarray(v))[None, :]
        for v in (*pan.a.T, *pan.tangent.T, *pan.normal.T, pan.length)
    ]
    kern = _influence_kernel(xp)
    rows = max(64, _KERNEL_ENTRIES // n)
    for r0 in range(0, n, rows):
        r1 = min(n, r0 + rows)
        kern(*(v[r0:r1] for v in row), *col, p[r0:r1], d[r0:r1])
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
    prob0: Problem | None = None,
) -> BemGeometry:
    """Mesh (or take the ``prob0`` meshed elsewhere with the same ``mesh``) and assemble."""
    mesh = mesh or MeshParams()
    limit = DEFAULT_BUDGET_BYTES if budget is None else budget
    notes: list[str] = []
    n0 = 0
    for step in range(_MAX_COARSEN + 1):
        prob = prob0 if (step == 0 and prob0 is not None) else build_problem(xs, mesh)
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
        out[k] = prob.background_dk if m is None else eps_at(m, f)
    return out


def _freq_independent(prob: Problem) -> bool:
    return all(m is None or is_frequency_independent(m) for m in prob.zone_models)


def _chunks(idx: npt.NDArray[np.intp], size: int = 256) -> Iterator[npt.NDArray[np.intp]]:
    for k in range(0, len(idx), size):
        yield idx[k : k + size]


def _lu_solve(a: Any, rhs: Any) -> Any:
    """``a^-1 rhs``. LAPACK getrf / getrs through ``lu_factor``: unlike ``scipy.linalg.solve``
    (which holds the GIL in SciPy >= 1.17) it lets the threads of ``solve_many`` run in parallel."""
    from scipy.linalg import lu_factor, lu_solve  # type: ignore[import-untyped,unused-ignore]

    fac = lu_factor(a, overwrite_a=True, check_finite=False)
    _check_pivots(fac[0])
    return lu_solve(fac, rhs, check_finite=False)


def _check_pivots(lu: Any) -> None:
    if not np.all(np.diagonal(lu) != 0.0):
        raise np.linalg.LinAlgError("Matrix is singular.")


def _solve_eps(geo: BemGeometry, ez_in: C128) -> C128:
    """Maxwell C (in eps0 units) for the signal conductors with zone permittivities ``ez``."""
    pan = geo.prob.panels
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
        return _solve_eps_block(geo, ez_in)
    if _BLOCK and _cond_factor(geo).ok:
        try:
            return _solve_eps_block(geo, ez_in)
        except np.linalg.LinAlgError:  # a singular Schur complement: the whole system decides
            pass
    return _solve_eps_full(geo, ez_in, rhs)


def _solve_eps_full(geo: BemGeometry, ez_in: C128, rhs: Any) -> C128:
    """The whole (n + 1 + nf) system factored at once: the reference for ``_solve_eps_block``."""
    prob, pan = geo.prob, geo.prob.panels
    n = pan.n
    kind, cond = pan.kind, pan.cond
    is_if = kind == KIND_INTERFACE
    nf = len(geo.float_idx)
    size = n + 1 + nf
    assert geo.P is not None and geo.D is not None
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


@dataclass
class _CondFactor:
    """LU of the conductor block ``P_cc`` of the influence matrix (on the host or the device).

    The conductor rows of the BEM system do not depend on the permittivities, so ``P_cc`` is
    factored once per section: it solves the vacuum system (C0) and, as the leading block of a
    block elimination, every ``C(f)`` solve (THEORY.md 2.2.1)."""

    cidx: npt.NDArray[np.intp]  # conductor (non-interface) panels
    iidx: npt.NDArray[np.intp]  # dielectric-interface panels
    lu: Any
    piv: Any
    ok: bool  # the pivots are healthy; otherwise the host callers use the whole-system path
    pivots: Any = None  # device: (min, max) of |diag U|, read with the first host transfer


def _lu_funcs(geo: BemGeometry) -> tuple[Any, Any]:
    """``lu_factor`` / ``lu_solve`` of SciPy (host) or CuPy (device)."""
    from oa_accel.solvers.lu import lu_functions

    return lu_functions(geo.be)


def _cond_factor(geo: BemGeometry) -> _CondFactor:
    if geo.fac is None:
        be = geo.be
        xp = be.xp if be is not None else np
        lu_factor, _ = _lu_funcs(geo)
        assert geo.P is not None
        is_if = geo.prob.panels.kind == KIND_INTERFACE
        ci, ii = np.nonzero(~is_if)[0], np.nonzero(is_if)[0]
        cx = xp.asarray(ci)
        a = geo.P[cx[:, None], cx[None, :]]
        if be is None:
            a = np.asfortranarray(a)
        lu, piv = lu_factor(a, overwrite_a=True, check_finite=False)
        d = xp.abs(xp.diagonal(lu))
        pair = xp.stack([d.min(), d.max()])
        if be is None:  # on the device the check waits for the first transfer: no sync here
            geo.fac = _CondFactor(ci, ii, lu, piv, _pivots_ok(pair, len(ci)))
        else:
            geo.fac = _CondFactor(ci, ii, lu, piv, True, pair)
    fac: _CondFactor = geo.fac
    return fac


def _pivots_ok(pair: Any, n: int) -> bool:
    lo_hi = np.asarray(pair)
    return bool(n and np.all(np.isfinite(lo_hi)) and lo_hi[0] > _PIVOT_RATIO * lo_hi[1])


def _singular_block(what: str) -> AccelError:
    return AccelError(
        ErrorInfo(
            code="ACCEL_SINGULAR",
            message=f"block elimination: {what} is singular or the result is not finite.",
            path="bem.solve",
            hint="Check the model for coincident conductors or floating conductors.",
        )
    )


def _fetch(geo: BemGeometry, array: Any) -> Any:
    """A device array to the host. This is the only synchronisation of a section's solve step.
    The first transfer also checks the pivots of ``P_cc``; every transfer checks finiteness."""
    be = geo.be
    assert be is not None
    fac = _cond_factor(geo)
    xp = be.xp
    flat = array.ravel().astype(xp.complex128)
    pair = fac.pivots
    if pair is not None:
        flat = xp.concatenate([pair.astype(xp.complex128), flat])
    host = np.asarray(be.to_host(flat))
    if pair is not None:
        if not _pivots_ok(host[:2].real, len(fac.cidx)):
            raise _singular_block("the conductor block")
        fac.pivots = None
        host = host[2:]
    if not np.all(np.isfinite(host)):
        raise _singular_block("the system")
    out = host.reshape(array.shape)
    return out if np.iscomplexobj(array) else out.real


def _charge_q(
    xp: Any, pan: Any, e1: Any, avg: Any, diff: Any, d_mat: Any, ncnd: int, dt: Any
) -> Any:
    """Free-charge functional per conductor, ``(ncnd, n)``: thick panels on the diagonal, ribbons
    (zero-thickness, two media) through their D rows. ``e1``, ``avg`` and ``diff`` are host
    arrays; ``d_mat`` and the result live where ``xp`` does."""
    kind, cond = pan.kind, pan.cond
    q = xp.zeros((ncnd, pan.n), dtype=dt)
    thick = np.nonzero(kind == KIND_THICK)[0]
    q[xp.asarray(cond[thick]), xp.asarray(thick)] = xp.asarray(e1[thick] * pan.length[thick])
    rib = np.nonzero((kind != KIND_THICK) & (kind != KIND_INTERFACE))[0]
    if len(rib):
        sel = xp.zeros((ncnd, len(rib)), dtype=dt)
        sel[xp.asarray(cond[rib]), xp.arange(len(rib))] = xp.asarray(pan.length[rib] * diff[rib])
        q += sel @ d_mat[xp.asarray(rib)]
        q[xp.asarray(cond[rib]), xp.asarray(rib)] += xp.asarray(pan.length[rib] * avg[rib])
    return q


def _border_cols(geo: BemGeometry, cc: npt.NDArray[np.intp], m2: int, lead: int) -> F64:
    """Columns ``[.. | c | v_float ..]`` of the conductor rows (``lead`` columns precede)."""
    a = np.zeros((len(cc), m2))
    a[:, lead] = 1.0  # the additive constant of the log kernel
    for j, fi in enumerate(geo.float_idx):
        a[cc == fi, lead + 1 + j] = -1.0
    return a


def _eps_split(pan: Any, ez_in: C128) -> tuple[Any, Any, Any, Any]:
    """``(e1, e2, avg, diff)``: the permittivity on the two sides of every panel (real when the
    materials are lossless)."""
    real = not np.any(ez_in.imag)
    ez: Any = ez_in.real if real else ez_in
    e1 = ez[pan.zone1]
    e2 = np.where(pan.zone2 >= 0, ez[np.maximum(pan.zone2, 0)], 0.0)
    return e1, e2, 0.5 * (e1 + e2), e1 - e2


def _solve_eps_block(geo: BemGeometry, ez_in: C128) -> C128:
    """Maxwell C (eps0 units) by block elimination on the cached ``P_cc`` factor.

    Unknowns are ordered ``[sigma_c | sigma_i, c, v_float]``. The conductor rows are
    ``[P_cc | P_ci, 1, -1_float]``, so with ``Y = P_cc^-1 [P_ci, 1, -1_float | rhs]`` only the
    small Schur complement (interface + border unknowns) depends on the permittivities."""
    xp = geo.be.xp if geo.be is not None else np
    _, lu_solve = _lu_funcs(geo)
    prob, pan = geo.prob, geo.prob.panels
    fac = _cond_factor(geo)
    ci, ii = fac.cidx, fac.iidx
    nc, ni = len(ci), len(ii)
    m2 = ni + 1 + len(geo.float_idx)
    e1, _, avg, diff = _eps_split(pan, ez_in)
    dt = np.float64 if not np.any(ez_in.imag) else np.complex128
    d_mat: Any = geo.D
    p_mat: Any = geo.P
    cx, ix = xp.asarray(ci), xp.asarray(ii)
    cc = pan.cond[ci]
    a12 = xp.asarray(_border_cols(geo, cc, m2, ni))
    a12[:, :ni] = p_mat[cx[:, None], ix[None, :]]
    rhs_c = xp.asarray((cc[:, None] == np.asarray(geo.sig_idx)[None, :]).astype(np.float64))
    y = lu_solve((fac.lu, fac.piv), xp.concatenate([a12, rhs_c], axis=1), check_finite=False)
    ya, z = y[:, :m2], y[:, m2:]
    q = _charge_q(xp, pan, e1, avg, diff, d_mat, len(prob.cond_ids), dt)
    qsum = q.sum(axis=0)
    dif_i = xp.asarray(diff[ii])
    a21 = xp.empty((m2, nc), dtype=dt)
    a21[:ni] = dif_i[:, None] * d_mat[ix[:, None], cx[None, :]]
    a21[ni] = qsum[cx]
    a22 = xp.zeros((m2, m2), dtype=dt)
    a22[:ni, :ni] = dif_i[:, None] * d_mat[ix[:, None], ix[None, :]]
    a22[xp.arange(ni), xp.arange(ni)] += xp.asarray(avg[ii])
    a22[ni, :ni] = qsum[ix]
    for j, fi in enumerate(geo.float_idx):
        a21[ni + 1 + j] = q[fi][cx]
        a22[ni + 1 + j, :ni] = q[fi][ix]
    x2 = xp.linalg.solve(a22 - a21 @ ya, -(a21 @ z))
    x1 = z - ya @ x2
    qs = q[xp.asarray(np.asarray(geo.sig_idx, dtype=np.intp))]
    out = qs[:, cx] @ x1 + qs[:, ix] @ x2[:ni]
    host = _fetch(geo, out) if geo.be is not None else out
    return np.asarray(host).astype(np.complex128)


def _vacuum_block(geo: BemGeometry) -> tuple[F64, F64, npt.NDArray[np.intp]]:
    """``_vacuum`` on the cached ``P_cc`` factor (the vacuum system is ``P_cc`` plus its border)."""
    xp = geo.be.xp if geo.be is not None else np
    _, lu_solve = _lu_funcs(geo)
    prob, pan = geo.prob, geo.prob.panels
    fac = _cond_factor(geo)
    ci = fac.cidx
    nf = len(geo.float_idx)
    cc = pan.cond[ci]
    length = pan.length[ci]
    q = np.zeros((len(prob.cond_ids), len(ci)))
    for k in range(len(prob.cond_ids)):
        q[k, cc == k] = length[cc == k]
    a12 = _border_cols(geo, cc, 1 + nf, 0)
    a21 = np.zeros((1 + nf, len(ci)))
    a21[0] = q.sum(axis=0)
    for j, fi in enumerate(geo.float_idx):
        a21[1 + j] = q[fi]
    rhs_c = (cc[:, None] == np.asarray(geo.sig_idx)[None, :]).astype(np.float64)
    y = lu_solve(
        (fac.lu, fac.piv), xp.asarray(np.concatenate([a12, rhs_c], axis=1)), check_finite=False
    )
    ya, z = y[:, : 1 + nf], y[:, 1 + nf :]
    a21x = xp.asarray(a21)
    x2 = xp.linalg.solve(-(a21x @ ya), -(a21x @ z))
    sig = z - ya @ x2
    sig_h = np.asarray(_fetch(geo, sig) if geo.be is not None else sig)
    return q[geo.sig_idx] @ sig_h, sig_h, ci


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
    if geo.P is not None and geo.be is not None:
        return _vacuum_block(geo)
    if _BLOCK and geo.P is not None and _cond_factor(geo).ok:
        try:
            return _vacuum_block(geo)
        except np.linalg.LinAlgError:
            pass
    prob, pan = geo.prob, geo.prob.panels
    cidx = np.nonzero(pan.kind != KIND_INTERFACE)[0]
    nc = len(cidx)
    nf = len(geo.float_idx)
    size = nc + 1 + nf
    A = np.zeros((size, size))
    if geo.P is not None:
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
    live = np.nonzero(freqs > 0.0)[0]
    if len(live):
        fl = freqs[live]
        rs_c = np.sqrt(np.pi * fl[:, None] * MU0 / c_sigma[None, :])  # [ohm/sq], (F, classes)
        k_c = np.ones((len(live), len(classes)))
        for q in range(len(classes)):
            top, bot, side = prob.cond_rough[int(c_cond[q])]
            rough = top if c_face[q] == FACE_TOP else (bot if c_face[q] == FACE_BOTTOM else side)
            k_c[:, q] = phys.roughness_factor(rough, fl, float(c_sigma[q]))
        w = (rs_c * k_c)[:, inv] * length[None, :]  # (F, panels)
        s_mat = (sig[None] * w[:, :, None]).transpose(0, 2, 1) @ sig  # (F, ns, ns)
        r_ac[live] = c0inv @ s_mat @ c0inv
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
    prob: Problem | None = None,
) -> BemSolution:
    """Solve the cross-section at ``freqs`` [Hz] (0.0 = static, lossless).

    ``budget`` [B] is the per-section working-memory limit (default 512 MB): above it the
    matrix-free GMRES path is used, and above its limits ``SectionTooLarge`` is raised. ``prob``
    is the mesh of ``xs`` for ``mesh``, when it was built elsewhere (the meshing pool)."""
    with _single_thread():
        return _solve_bem(xs, freqs, mesh, geo, budget, prob)


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
    prob: Problem | None = None,
) -> BemSolution:
    geo = geo or prepare(xs, mesh, budget, reserve=True, prob0=prob)
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
            _release_device(geo)


def _release_device(geo: BemGeometry) -> None:
    """Give the device gate and the device matrices of ``geo`` back."""
    be = geo.be
    assert be is not None
    _dev_gate(be).release(geo.reserved_dev)
    geo.reserved_dev = 0
    geo.P = geo.D = geo.fac = None  # free the device matrices and the factor
    # return the blocks to the driver: on Windows the CuPy pool counts as committed memory of the
    # process (a job-object cap then fails unrelated host allocations); inside a ``batch()`` this
    # happens once, when the batch ends
    with _ST_LOCK:
        deferred = _BATCH_DEPTH > 0
        if deferred:
            _BATCH_DEV[id(be)] = be
    if not deferred:
        be.xp.get_default_memory_pool().free_all_blocks()


def _to_host(geo: BemGeometry, reason: str) -> BemGeometry:
    """The device failed (out of memory, driver error): rebuild P and D on the host, which does
    not trust anything left on the device."""
    pan = geo.prob.panels
    n = pan.n
    p = np.empty((n, n))
    d = np.empty((n, n))
    influence_blocks(
        pan.mid, pan.normal, pan.a, pan.b, pan.tangent, pan.normal, pan.length, p, d, same=True
    )
    try:
        _release_device(geo)
    except Exception:  # a dead device may refuse even the clean-up
        geo.reserved_dev = 0
    geo.be, geo.P, geo.D, geo.fac, geo.mode = None, p, d, None, "dense"
    geo.notes.append(f"GPU solve failed ({reason}); host dense path used")
    return geo


def _solve_prepared(geo: BemGeometry, freqs: list[float] | F64) -> BemSolution:
    be = geo.be
    if be is None:
        return _solve_core(geo, freqs)
    from oa_accel.solvers.guarded import run_guarded

    return run_guarded(
        be,
        "bem_block",
        geo.reserved_dev,
        lambda: _solve_core(geo, freqs),
        lambda reason: _solve_core(_to_host(geo, reason), freqs),
    )


def _solve_core(geo: BemGeometry, freqs: list[float] | F64) -> BemSolution:
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
        mode=geo.mode,
        peak_bytes=geo.peak_bytes,
        iterations=geo.iterations,
    )
