# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``impedance`` service (interfaces.md section 3.4): solve / solve_many / quick."""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
from collections import OrderedDict
from collections.abc import MutableMapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import asdict
from typing import Literal

import numpy as np

from oa_impedance import methods as _methods
from oa_impedance import topology as topo_mod
from oa_impedance._dielectric import eps_at
from oa_impedance.bem import MeshParams, solve_bem
from oa_impedance.bem import meshpool as _meshpool
from oa_impedance.bem.geometry import Problem
from oa_impedance.bem.solver import batch as _bem_batch
from oa_impedance.errors import ImpedanceUnsupported
from oa_impedance.modal import modal_decomposition
from oa_impedance.screening import BOUND_FLAG, screening_bound
from oa_model.arrays import Floats, NDArray
from oa_model.xsection import CrossSection
from oa_physics.constants import C0
from oa_results.container import ValidityFlag
from oa_results.rlgc import RLGC, ImpedanceResult, LineParams

__all__ = [
    "BEM_VERSION",
    "SOLVER_VERSION",
    "ImpedanceServiceImpl",
    "bem_to_result",
    "default_service",
]

BEM_VERSION = "oa-bem 0.1"
SOLVER_VERSION = "oa-impedance 0.1.0"
Method = Literal["auto", "closed_form", "bem", "fem"]


def _default_workers() -> int:
    """Threads of ``solve_many``: ``OA_IMPEDANCE_WORKERS``, else ``OA_NUM_THREADS``, else the CPU
    count (at most 8). The dense factorisations release the GIL, so they scale with threads."""
    for key in ("OA_IMPEDANCE_WORKERS", "OA_NUM_THREADS"):
        try:
            n = int(os.environ.get(key, "0") or 0)
        except ValueError:
            n = 0
        if n > 0:
            return min(n, 32)
    return max(1, min(os.cpu_count() or 1, 8))


def _prob_of(meshed: list[Future[Problem | None]] | None, i: int) -> Future[Problem | None] | None:
    return None if meshed is None else meshed[i]


def _wait_mesh(fut: Future[Problem | None] | None) -> Problem | None:
    """The mesh a pool worker built, or None (no pool, or the worker failed on this section:
    the in-process path then meshes it again and meets the same error)."""
    if fut is None:
        return None
    try:
        return fut.result()
    except Exception:
        return None


def _freqs(freqs: Sequence[float] | None) -> tuple[float, ...]:
    if freqs is None or len(freqs) == 0:
        return (0.0,)
    out = tuple(float(f) for f in freqs)
    if any(f < 0 or not math.isfinite(f) for f in out):
        raise ImpedanceUnsupported("frequencies must be finite and >= 0", path="freqs")
    if list(out) != sorted(out):
        raise ImpedanceUnsupported("frequencies must be ascending", path="freqs")
    return out


def _quasi_static_fmax(xs: CrossSection, h_char: float) -> float:
    er_max = max([xs.background_dk] + [eps_at(d.model, 0.0).real for d in xs.dielectrics])
    return 0.1 * C0 / (math.sqrt(er_max) * h_char)


def bem_to_result(
    xs: CrossSection,
    freqs: tuple[float, ...],
    mesh: MeshParams | None = None,
    budget: int | None = None,
    prob: Problem | None = None,
) -> ImpedanceResult:
    sol = solve_bem(xs, list(freqs), mesh, budget=budget, prob=prob)
    fs = sol.freqs
    n = len(sol.conductor_ids)
    w = 2.0 * np.pi * fs
    C = sol.C.real
    with np.errstate(all="ignore"):
        G = np.where(w[:, None, None] > 0, -w[:, None, None] * sol.C.imag, 0.0)
    rac = sol.R_ac
    R = rac.copy()
    d = np.arange(n)
    R[:, d, d] = np.sqrt(sol.R_dc**2 + rac[:, d, d] ** 2)
    with np.errstate(all="ignore"):
        L = sol.L_ext[None] + np.where(w[:, None, None] > 0, rac / w[:, None, None], 0.0)
    modal = modal_decomposition(fs, R, L, G, C)
    params_kwargs: dict[str, Floats] = {}
    for name in ("z0", "z_diff", "z_comm", "z_odd", "z_even"):
        arr = getattr(modal, name)
        if arr is not None:
            params_kwargs[name] = Floats(arr)
    params = LineParams(
        freqs=Floats(fs),
        zc_modal=NDArray(modal.zc_modal),
        eps_eff_modal=NDArray(modal.eps_eff),
        zc_matrix=NDArray(modal.zc_matrix),
        delay_per_m=NDArray(modal.delay),
        loss_db_per_m=NDArray(modal.loss_db),
        **params_kwargs,
    )
    notes = list(sol.notes)
    kind = "dense" if sol.mode == "dense" else f"GMRES ({sol.iterations} it)"
    notes.append(f"{sol.n_panels} boundary panels, {sol.n_solves} {kind} solve(s)")
    validity: Literal["in_range", "extrapolated", "invalid"] = "in_range"
    f_qs = _quasi_static_fmax(xs, sol.h_char)
    if len(fs) and float(np.max(fs)) > f_qs:
        validity = "extrapolated"
        notes.append(f"quasi-static model above ~{f_qs / 1e9:.3g} GHz: dispersion not captured")
    else:
        notes.append(f"quasi-static valid below ~{f_qs / 1e9:.3g} GHz")
    if "no ground conductor" in " ".join(sol.notes) and n == 1:
        notes.append("single mode: z0 is the line (differential) impedance, V between conductors")
    rlgc = RLGC(
        freqs=Floats(fs),
        R=NDArray(R),
        L=NDArray(L),
        G=NDArray(G),
        C=NDArray(C),
        conductor_ids=sol.conductor_ids,
    )
    return ImpedanceResult(
        xs_key=xs.key(),
        method="bem",
        model_name=BEM_VERSION,
        rlgc=rlgc,
        params=params,
        validity=validity,
        notes=tuple(notes),
        solver_version=SOLVER_VERSION,
        accuracy_class="validated",
        validity_flags=(
            ValidityFlag(
                code="quasi_static_fmax",
                ok=validity == "in_range",
                message=f"quasi-static limit ~{f_qs / 1e9:.3g} GHz",
            ),
        ),
    )


def closed_form_result(
    topo: topo_mod.Topology, xs: CrossSection, freqs: tuple[float, ...]
) -> ImpedanceResult:
    n = 2 if topo.kind.startswith("edge_coupled") else 1
    nf = len(freqs)
    zc_modal = np.zeros((nf, n), dtype=complex)
    ee = np.zeros((nf, n))
    zc_mat = np.zeros((nf, n, n), dtype=complex)
    loss = np.zeros((nf, n))
    z0 = np.zeros(nf)
    zdiff = np.zeros(nf)
    zcomm = np.zeros(nf)
    zodd = np.zeros(nf)
    zeven = np.zeros(nf)
    worst = "in_range"
    rank = {"in_range": 0, "extrapolated": 1, "invalid": 2}
    msgs: list[str] = []
    model = ""
    flags: list[ValidityFlag] = []
    notes: list[str] = list(topo.notes)
    for i, f in enumerate(freqs):
        r = topo_mod.evaluate(topo, xs, f)
        model = r.model
        if rank[r.validity] > rank[worst]:
            worst = r.validity
        for m in r.violations:
            if m not in msgs:
                msgs.append(m)
        for nt in r.notes:
            if nt not in notes:
                notes.append(nt)
        a = topo_mod.loss_db_per_m(topo, xs, f, r)
        if n == 1:
            assert r.z0 is not None and r.eeff is not None
            zc_modal[i, 0] = r.z0
            zc_mat[i, 0, 0] = r.z0
            ee[i, 0] = r.eeff
            z0[i] = r.z0
            loss[i, 0] = a
        else:
            assert r.z0e is not None and r.z0o is not None
            assert r.eeff_e is not None and r.eeff_o is not None
            zc_modal[i] = (r.z0e, r.z0o)
            ee[i] = (r.eeff_e, r.eeff_o)
            zc_mat[i] = [
                [(r.z0e + r.z0o) / 2, (r.z0e - r.z0o) / 2],
                [(r.z0e - r.z0o) / 2, (r.z0e + r.z0o) / 2],
            ]
            zdiff[i], zcomm[i], zodd[i], zeven[i] = 2 * r.z0o, r.z0e / 2, r.z0o, r.z0e
            loss[i] = a
    notes += [f"validity: {m}" for m in msgs]
    if worst != "in_range":
        flags.append(ValidityFlag(code="formula_range", ok=False, message="; ".join(msgs)))
    else:
        flags.append(ValidityFlag(code="formula_range", ok=True, message="inside published range"))
    if all(f == 0.0 for f in freqs) and worst != "invalid":
        bound, why = screening_bound(topo, xs, extrapolated=worst == "extrapolated")
        if bound is not None:
            flags.append(ValidityFlag(code=BOUND_FLAG, ok=True, message=f"{bound:.4g}; {why}"))
    delay = np.sqrt(ee) / C0
    kw: dict[str, Floats] = {}
    if n == 1:
        kw["z0"] = Floats(z0)
    else:
        kw.update(
            z_diff=Floats(zdiff), z_comm=Floats(zcomm), z_odd=Floats(zodd), z_even=Floats(zeven)
        )
    params = LineParams(
        freqs=Floats(np.asarray(freqs)),
        zc_modal=NDArray(zc_modal),
        eps_eff_modal=NDArray(ee),
        zc_matrix=NDArray(zc_mat),
        delay_per_m=NDArray(delay),
        loss_db_per_m=NDArray(loss),
        **kw,
    )
    return ImpedanceResult(
        xs_key=xs.key(),
        method="closed_form",
        model_name=model,
        rlgc=None,
        params=params,
        validity=worst,  # type: ignore[arg-type]
        notes=tuple(notes),
        solver_version=SOLVER_VERSION,
        accuracy_class="screening",
        validity_flags=tuple(flags),
    )


_SMALL_BATCH = 8  # fewer uncached sections than this: no GPU start-up for small sections


class ImpedanceServiceImpl:
    """``impedance`` service: closed forms, BEM and plug-in methods, with an LRU cache.

    ``job_cache`` (optional ``MutableMapping[str, str]``) is the hook for the job cache of
    interfaces.md section 4.4: results are stored as JSON under ``imp:<digest>``.
    """

    def __init__(
        self,
        *,
        cache_size: int = 512,
        mesh: MeshParams | None = None,
        job_cache: MutableMapping[str, str] | None = None,
        max_workers: int | None = None,
        section_budget_mb: float | None = None,
    ) -> None:
        self._lru: OrderedDict[str, ImpedanceResult] = OrderedDict()
        self._size = cache_size
        self.mesh = mesh or MeshParams()
        self.job_cache = job_cache
        self.max_workers = _default_workers() if max_workers is None else max_workers
        if section_budget_mb is None:
            section_budget_mb = float(os.environ.get("OA_IMPEDANCE_SECTION_MB", "512"))
        self.section_budget = int(section_budget_mb * 2**20)
        self._lock = threading.Lock()
        self.stats = {"bem": 0, "closed_form": 0, "plugin": 0, "hits": 0}

    # -- cache ------------------------------------------------------------------------
    def _ckey(self, xs: CrossSection, freqs: tuple[float, ...], method: str) -> str:
        payload = json.dumps(
            [xs.key(), freqs, method, asdict(self.mesh)], sort_keys=True, default=str
        )
        return "imp:" + hashlib.sha256(payload.encode()).hexdigest()[:32]

    def _get(self, key: str) -> ImpedanceResult | None:
        with self._lock:
            if key in self._lru:
                self._lru.move_to_end(key)
                self.stats["hits"] += 1
                return self._lru[key]
        if self.job_cache is not None and key in self.job_cache:
            res = ImpedanceResult.model_validate_json(self.job_cache[key])
            self._put(key, res, store=False)
            self.stats["hits"] += 1
            return res
        return None

    def _put(self, key: str, res: ImpedanceResult, *, store: bool = True) -> None:
        with self._lock:
            self._lru[key] = res
            self._lru.move_to_end(key)
            while len(self._lru) > self._size:
                self._lru.popitem(last=False)
        if store and self.job_cache is not None:
            self.job_cache[key] = res.model_dump_json()

    # -- protocol ---------------------------------------------------------------------
    def solve(
        self,
        xs: CrossSection,
        freqs: Sequence[float] | None = None,
        *,
        method: Method = "auto",
    ) -> ImpedanceResult:
        fs = _freqs(freqs)
        key = self._ckey(xs, fs, method)
        hit = self._get(key)
        if hit is not None:
            return hit
        res = self._compute(xs, fs, method)
        self._put(key, res)
        return res

    def solve_many(
        self,
        xss: Sequence[CrossSection],
        freqs: Sequence[float] | None = None,
        *,
        method: str = "auto",
    ) -> list[ImpedanceResult]:
        fs = _freqs(freqs)
        uniq: dict[str, CrossSection] = {}
        keys = []
        for xs in xss:
            k = self._ckey(xs, fs, method)
            keys.append(k)
            uniq.setdefault(k, xs)
        out: dict[str, ImpedanceResult] = {}
        todo = []
        for k, xs in uniq.items():
            hit = self._get(k)
            if hit is not None:
                out[k] = hit
            else:
                todo.append((k, xs))
        # BLAS limit and device-pool release once per batch, not per section
        with _bem_batch(small=len(todo) < _SMALL_BATCH) if todo else nullcontext():
            # a large BEM batch is meshed by worker processes while the threads below solve
            meshed: list[Future[Problem | None]] | None = None
            if method == "bem" and len(todo) > 1:
                meshed = _meshpool.mesh_many([xs for _, xs in todo], self.mesh)
            if len(todo) > 1 and self.max_workers > 1:
                with ThreadPoolExecutor(max_workers=min(self.max_workers, len(todo))) as ex:
                    futs = [
                        (k, ex.submit(self._compute, xs, fs, method, _prob_of(meshed, i)))
                        for i, (k, xs) in enumerate(todo)
                    ]
                    for k, fut in futs:
                        out[k] = fut.result()
                        self._put(k, out[k])
            else:
                for i, (k, xs) in enumerate(todo):
                    out[k] = self._compute(xs, fs, method, _prob_of(meshed, i))
                    self._put(k, out[k])
        return [out[k] for k in keys]

    def quick(self, xs: CrossSection) -> ImpedanceResult:
        """Closed form only (< 1 ms); raises ``ImpedanceUnsupported`` when no formula applies."""
        return self.solve(xs, None, method="closed_form")

    # -- dispatch ---------------------------------------------------------------------
    def _compute(
        self,
        xs: CrossSection,
        fs: tuple[float, ...],
        method: str,
        prob: Future[Problem | None] | None = None,
    ) -> ImpedanceResult:
        if method in ("auto", "closed_form"):
            topo = topo_mod.extract(xs)
            if topo is not None:
                res = closed_form_result(topo, xs, fs)
                if method == "closed_form" or res.validity != "invalid":
                    self.stats["closed_form"] += 1
                    return res
            if method == "closed_form":
                raise ImpedanceUnsupported(
                    f"no closed-form model for this cross-section (hint {xs.topology_hint!r})",
                    path="method",
                    hint="Use method='bem' (or 'auto'), or simplify the section to a canonical "
                    "microstrip / stripline / CPW / coupled pair.",
                )
            method = "bem"
        if method == "bem":
            self.stats["bem"] += 1
            mesh = _wait_mesh(prob)
            return bem_to_result(xs, fs, self.mesh, self.section_budget, mesh)
        solver = _methods.get(method)  # raises MethodNotAvailable for unknown / unregistered
        self.stats["plugin"] += 1
        return solver(xs, fs)


_DEFAULT: ImpedanceServiceImpl | None = None


def default_service() -> ImpedanceServiceImpl:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = ImpedanceServiceImpl()
    return _DEFAULT
