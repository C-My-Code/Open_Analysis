# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process solve of an ``EMProblem`` (what the adapter's worker command runs).

``solve_problem`` builds the engine model (:func:`oa_fdtd.subcell.pcb.build_model`), adds modal
probe planes behind every mode port, runs all excitations as one batch and returns the power-wave
S-matrix with the run statistics. It is also the entry point of the verification benchmarks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from oa_fdtd.model import ModelError, ModePort
from oa_fdtd.ports import PlaneSet, add_wave_planes, wave_sparams
from oa_fdtd.run import RunResult, run, sparams
from oa_fdtd.subcell.pcb import BuiltModel, build_model
from oa_model.em import EMProblem

__all__ = ["SolveOptions", "SolveResult", "options_from_problem", "run_meta", "solve_problem"]

ComplexArray = npt.NDArray[np.complex128]
FloatArray = npt.NDArray[np.float64]
_PLANE_OFFSETS = (1.5e-3, 1.0e-3, 0.5e-3, 0.25e-3)  # [m] first that the mesh can host


@dataclass(frozen=True)
class SolveOptions:
    """Options; ``mesh.expert`` keys ``fdtd.engine|dtype|copper|plane_offset|pulse`` set these."""

    engine: str = "auto"
    dtype: str = "float32"
    copper: str = "thick_sibc"
    end_db: float = -50.0
    max_steps: int = 1_000_000
    plane_offset: float | None = None
    pulse: str = "gauss_mod"  # or "gauss" (DC content, better below 0.05 f_max)
    tail: bool = True  # matrix-pencil extrapolation when the run stops at max_steps


@dataclass
class SolveResult:
    freqs: FloatArray  # [Hz]
    s: ComplexArray  # (F, N, N)
    z0: FloatArray  # port reference impedances [ohm]
    port_names: tuple[str, ...]
    run: RunResult
    built: BuiltModel
    wall_build_s: float
    wall_run_s: float
    notes: list[str] = field(default_factory=list)


def options_from_problem(problem: EMProblem) -> SolveOptions:
    ex = problem.mesh.expert
    conv = problem.convergence
    off = ex.get("fdtd.plane_offset")
    return SolveOptions(
        engine=str(ex.get("fdtd.engine", "auto")),
        dtype=str(ex.get("fdtd.dtype", "float32")),
        copper=str(ex.get("fdtd.copper", "thick_sibc")),
        end_db=float(conv.energy_decay_db),
        max_steps=int(conv.max_timesteps),
        plane_offset=float(off) if isinstance(off, int | float) else None,
        pulse=str(ex.get("fdtd.pulse", "gauss_mod")),
    )


def _planes(built: BuiltModel, offset: float | None) -> tuple[list[PlaneSet], str | None]:
    """Wave planes behind every mode port, or ``([], note)`` when some port has no uniform line
    long enough for well-conditioned planes (then S comes from the port sheets)."""
    model = built.model
    names = [p.name for p in model.ports if isinstance(p, ModePort)]
    offsets = (offset,) if offset is not None else _PLANE_OFFSETS
    last: ModelError | None = None
    for off in offsets:
        n0 = len(model.mode_probes)
        try:
            return [add_wave_planes(model, nm, off) for nm in names], None
        except ModelError as exc:
            del model.mode_probes[n0:]
            last = exc
    assert last is not None
    if last.code != "FDTD_UNEQUAL_PLANES":
        raise last
    return [], f"FDTD.PORT_S: {last.message}; S from the port sheets (no wave-plane de-embedding)"


def solve_problem(problem: EMProblem, opts: SolveOptions | None = None) -> SolveResult:
    """Solve ``problem``; raises :class:`~oa_fdtd.model.ModelError` on unsupported input."""
    o = opts or options_from_problem(problem)
    t0 = time.perf_counter()
    built = build_model(problem, copper=o.copper, pulse=o.pulse)
    model = built.model
    all_mode = bool(model.ports) and all(isinstance(p, ModePort) for p in model.ports)
    planes, plane_note = _planes(built, o.plane_offset) if all_mode else ([], None)
    all_mode = all_mode and bool(planes)
    req = np.asarray(model.frequencies, dtype=np.float64)
    # Extraction (branch unwrapping, smooth gamma fit) runs on a fixed internal grid united with the
    # requested points, and the fit sees the internal grid only: S does not depend on how many
    # frequencies are requested.
    internal = np.linspace(req[-1] / 400.0, req[-1], 400)
    model.frequencies = np.unique(np.concatenate([req, internal]))
    fit_mask = np.isin(model.frequencies, internal)
    t1 = time.perf_counter()
    kw: dict[str, Any] = {"end_db": o.end_db, "max_steps": o.max_steps, "tail": o.tail}
    notes: list[str] = [plane_note] if plane_note else []
    if o.engine == "auto":  # GPU whenever there is one (the crossover policy is for small cases)
        try:
            res = run(model, engine="cuda", dtype=o.dtype, **kw)
        except Exception as exc:
            if getattr(exc, "code", "").startswith("FDTD"):
                raise
            notes.append(f"FDTD.CPU_FALLBACK: no usable GPU ({type(exc).__name__}); NumPy engine")
            res = run(model, engine="numpy", dtype="float64", **kw)
    else:
        res = run(model, engine=o.engine, dtype=o.dtype, **kw)
    t2 = time.perf_counter()
    s_port = sparams(model, res)
    if all_mode:
        s, _gam = wave_sparams(
            res.probes, planes, res.port_r, s_port, freqs=model.frequencies, fit_mask=fit_mask
        )
    else:
        s = s_port
    sel = np.flatnonzero(np.isin(model.frequencies, req))
    return SolveResult(
        freqs=np.asarray(model.frequencies, dtype=np.float64)[sel],
        s=np.ascontiguousarray(np.moveaxis(s, 2, 0))[sel],
        z0=np.asarray(res.port_r, dtype=np.float64),
        port_names=tuple(p.name for p in model.ports),
        run=res,
        built=built,
        wall_build_s=t1 - t0,
        wall_run_s=t2 - t1,
        notes=notes,
    )


def run_meta(r: SolveResult) -> dict[str, Any]:
    """JSON-able run statistics."""
    return {
        "engine": r.run.engine,
        "dtype": r.run.dtype,
        "steps": r.run.steps,
        "stopped_by": r.run.stopped_by,
        "energy_db": r.run.energy_db,
        "dt_s": r.run.dt,
        "peak_gpu_bytes": r.run.peak_bytes,
        "wall_build_s": r.wall_build_s,
        "wall_run_s": r.wall_run_s,
        "cells": r.built.meta["cells"],
        "diagnostics": [*r.built.diagnostics, *r.notes],
        "accel": r.run.report,
        "tail": r.run.tail,
    }
