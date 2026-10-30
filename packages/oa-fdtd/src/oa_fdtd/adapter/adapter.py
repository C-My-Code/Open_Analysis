# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The oa-fdtd ``SolverAdapter`` (interfaces.md section 5.3, ADR-0010, WP-507).

``prepare`` writes ``problem.json`` (the ``EMProblem``), ``grid.npz`` (the mesh lines, for
provenance) and returns **one** ``ExternalCommand``: all excitations run as one batch on the GPU
(``excitations_per_run = "all"``). The command is ``python -m oa_fdtd.adapter.worker`` through the
built-in ``python`` tool, so the job system needs no extra tool configuration. ``collect`` reads
``ports.npz`` and ``run.json``, passes the S-matrix through the quality gate and writes Touchstone
plus a metadata sidecar. Nothing in this module imports CuPy.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from oa_fdtd.adapter.touchstone import write_touchstone
from oa_fdtd.model import ModelError
from oa_framework.em import AdapterCapabilities, EMSolveOutput, PreparedRun
from oa_framework.external import ExternalCommand, ExternalResult, ToolInfo
from oa_model.arrays import Floats, NDArray
from oa_model.common import Diagnostic
from oa_model.em import (
    EMCoaxPort,
    EMLumpedPort,
    EMProblem,
    EMWavePort,
    FullWaveDriven,
)
from oa_results.artifact import ArtifactRef
from oa_results.container import SolverProvenance
from oa_results.network import NetworkResult, PortMapEntry
from oa_results.quality import QualityCriteria, QualityReport
from oa_results.tables import ColumnSpec, TableResult

Assessor = Callable[[Any], QualityReport]
"""``oa_network.quality.assess``: ``skrf.Network -> QualityReport``."""

__all__ = ["FdtdAdapter", "OaFdtdAdapter", "default_assessor"]

VERSION = "oa-fdtd 0.1.0"
_GCELL_STEPS_PER_S = 1.2e9  # measured PCB-like (ADE + SIBC) cell-steps/s per batch member
_STEPS_PER_TRANSIT = 22.0  # steps ~ this x (L sqrt(eps) / (c dt)): B-EM-01/02/03 calibration
_C0 = 299_792_458.0


def default_assessor() -> Assessor | None:
    try:
        from oa_network.quality import assess  # type: ignore[import-not-found,unused-ignore]
    except Exception:
        return None
    return assess


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "problem"


def _json(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=float))


def _sha256_file(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


class FdtdAdapter:
    """GPU FDTD solver adapter. ``assessor`` overrides the quality gate."""

    name: ClassVar[str] = "fdtd"
    tool: ClassVar[str] = "python"  # the interpreter of the job host (built-in tool entry)

    def __init__(self, *, assessor: Assessor | None = None) -> None:
        self._assessor = assessor
        self._cache: tuple[str, Any] | None = None

    # ------------------------------------------------------------------ capabilities / check
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            physics=frozenset({"fullwave_driven"}),
            ports=frozenset({"lumped", "wave"}),
            wave_port_mode="modal",
            boundaries=frozenset({"pec", "pmc", "absorbing", "pml"}),
            dispersive_dielectric="broadband",
            surface_roughness=True,
            sheet_conductors=True,
            excitations_per_run="all",
            farfield=False,
            platforms=frozenset({"windows", "linux"}),
        )

    def _built(self, problem: EMProblem) -> Any:
        from oa_fdtd.subcell.pcb import build_model

        key = problem.key()
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        built = build_model(
            problem, copper=str(problem.mesh.expert.get("fdtd.copper", "thick_sibc"))
        )
        self._cache = (key, built)
        return built

    def check(self, problem: EMProblem) -> tuple[Diagnostic, ...]:
        out: list[Diagnostic] = []

        def err(code: str, message: str, hint: str) -> None:
            out.append(Diagnostic(severity="error", code=code, message=message, hint=hint))

        if not isinstance(problem.physics, FullWaveDriven):
            err(
                "FDTD.PHYSICS",
                f"physics {problem.physics.kind!r} is not supported",
                "Use fullwave_driven.",
            )
        if not problem.ports:
            err(
                "FDTD.NO_PORTS", "the problem has no ports", "Add at least one lumped or wave port."
            )
        if problem.outputs.farfield is not None:
            err(
                "FDTD.FARFIELD",
                "far-field output is not implemented (NF2FF is WP-506 part B)",
                "Use the openEMS adapter for far fields.",
            )
        if problem.outputs.field_dumps:
            err("FDTD.FIELD_DUMPS", "field dumps are not implemented", "Remove the field dumps.")
        for p in problem.ports:
            if isinstance(p, EMCoaxPort):
                err(
                    "FDTD.PORT_COAX",
                    f"port {p.name}: coax ports are not implemented",
                    "Use a lumped or wave port.",
                )
            elif isinstance(p, EMWavePort) and getattr(problem.boundaries, p.face) not in (
                "absorbing",
                "pml",
                "pmc",
            ):
                err(
                    "FDTD.PORT_FACE_BOUNDARY",
                    f"port {p.name}: wave-port face {p.face} must be absorbing",
                    "Set the boundary of the port face to 'absorbing'.",
                )
        if any(d.severity == "error" for d in out):
            return tuple(out)
        try:
            built = self._built(problem)
        except ModelError as exc:
            out.append(
                Diagnostic(
                    severity="error",
                    code="FDTD.TRANSLATION",
                    message=exc.message,
                    hint=exc.hint or "Check port and conductor definitions against the structure.",
                )
            )
            return tuple(out)
        for text in built.diagnostics:
            code, _, msg = text.partition(": ")
            out.append(
                Diagnostic(
                    severity="warning",
                    code="FDTD." + code.removeprefix("FDTD_"),
                    message=msg or text,
                )
            )
        return tuple(out)

    # --------------------------------------------------------------------------- prepare
    def _estimate(self, built: Any, problem: EMProblem) -> dict[str, Any]:
        m = built.model
        g = m.grid
        cells = int(g.cells)
        n_exc = sum(1 for p in problem.ports if p.excite) or len(problem.ports)
        batch = max(len(m.ports), n_exc)
        dt = float(g.dt())
        extent = max(float(g.lines[a][-1] - g.lines[a][0]) for a in range(3))
        eps = 1.0
        for r in m.regions:
            if not r.pec:
                eps = max(eps, r.eps_r + sum(d for d, _ in r.poles))
        steps = int(_STEPS_PER_TRANSIT * extent * eps**0.5 / (_C0 * dt))
        steps = min(steps, problem.convergence.max_timesteps)
        itemsize = 4
        mem = (
            cells * (6 * itemsize * batch + 6) * 1.25 + cells * 8 * 0.1
        )  # fields + index + ADE/PML
        return {
            "cells": cells,
            "shape": list(g.n),
            "min_cell_m": float(built.meta["min_cell"]),
            "dt_s": dt,
            "est_steps": steps,
            "batch": batch,
            "est_memory_gb": mem / 1e9,
            "est_time_s": 2.0 + cells * steps * batch / (_GCELL_STEPS_PER_S * 2.0),
            "gpu": True,
        }

    def prepare(self, problem: EMProblem, workdir: Path, tool: ToolInfo) -> PreparedRun:
        built = self._built(problem)
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "problem.json").write_text(problem.model_dump_json(), encoding="utf-8")
        g = built.model.grid
        np.savez(workdir / "grid.npz", x=g.x, y=g.y, z=g.z)
        stats = _json(
            {
                **built.meta,
                **self._estimate(built, problem),
                "adapter": self.name,
                "version": VERSION,
            }
        )
        cmd = ExternalCommand(
            program=self.tool,
            args=("-m", "oa_fdtd.adapter.worker", "problem.json"),
            workdir=workdir,
            env={},
            timeout_s=problem.convergence.max_wall_s,
            progress_parser="generic",
            outputs=("ports.npz", "run.json"),
        )
        return PreparedRun(
            workdir=workdir,
            commands=(cmd,),
            mesh_stats=stats,
            files=("problem.json", "grid.npz"),
        )

    # --------------------------------------------------------------------------- collect
    def collect(
        self, problem: EMProblem, run: PreparedRun, results: Sequence[ExternalResult]
    ) -> EMSolveOutput:
        warnings: list[Diagnostic] = []

        def fail(code: str, message: str, hint: str) -> EMSolveOutput:
            warnings.append(Diagnostic(severity="error", code=code, message=message, hint=hint))
            return EMSolveOutput(
                network=None,
                port_impedance=None,
                farfield=None,
                convergence_log=None,
                solver_version=VERSION,
                warnings=tuple(warnings),
            )

        if len(results) != 1 or len(run.commands) != 1:
            return fail(
                "FDTD.RESULT_COUNT",
                f"{len(results)} results for {len(run.commands)} commands",
                "One command per problem.",
            )
        res, cmd = results[0], run.commands[0]
        if res.returncode != 0:
            detail = _error_line(res.stdout_tail) or res.stderr_tail[-300:]
            unstable = "FDTD.UNSTABLE" in detail
            code = "FDTD.UNSTABLE" if unstable else "FDTD.RUN_FAILED"
            return fail(
                code,
                f"oa-fdtd worker exited with code {res.returncode}: {detail}",
                "See logs in the job directory; lower the Courant number or refine the mesh.",
            )
        ports_npz = cmd.workdir / "ports.npz"
        if not ports_npz.is_file():
            return fail(
                "FDTD.NO_OUTPUT", "ports.npz not found", "The worker finished without results."
            )
        with np.load(ports_npz) as d:
            freqs = np.array(d["freqs"], dtype=np.float64)
            s = np.array(d["s"], dtype=np.complex128)
            z0 = np.array(d["z0"], dtype=np.float64)
            names = tuple(str(x) for x in d["names"])
        meta = json.loads((cmd.workdir / "run.json").read_text(encoding="utf-8"))
        version = str(meta.get("fdtd_version", VERSION))
        for text in meta.get("diagnostics", []):
            code, _, msg = str(text).partition(": ")
            warnings.append(
                Diagnostic(
                    severity="warning",
                    code="FDTD." + code.removeprefix("FDTD_"),
                    message=msg or text,
                )
            )
        if meta.get("stopped_by") == "max_steps":
            tail = meta.get("tail") or {}
            warnings.append(
                Diagnostic(
                    severity="warning",
                    code="FDTD.TAIL" if tail.get("applied") else "FDTD.NOT_CONVERGED",
                    message=(
                        f"run stopped at the step cap ({meta['steps']} steps, energy "
                        f"{meta['energy_db']:.1f} dB); "
                        + (
                            f"matrix-pencil tail extrapolation applied ({tail['fits']} fits, "
                            f"worst residual {tail['worst_residual']:.2g})"
                            if tail.get("applied")
                            else "S-parameters are truncated"
                        )
                    ),
                    hint="Raise convergence.max_timesteps or relax convergence.energy_decay_db.",
                )
            )
        if not np.all(np.isfinite(s)):
            return fail("FDTD.NONFINITE", "S-parameters contain NaN or Inf", "Check the run log.")
        quality = self._assess(freqs, s, z0)
        touch = self._write_files(problem, run, freqs, s, z0, names, quality, meta, version)
        network = NetworkResult(
            freqs=Floats(freqs),
            s=NDArray(s),
            z0=NDArray(z0.astype(np.complex128)),
            port_names=names,
            port_map=tuple(self._port_map(problem)),
            quality=quality,
            touchstone=touch,
            provenance=SolverProvenance(
                solver=self.name, solver_version=version, inputs_hash=problem.key()
            ),
        )
        return EMSolveOutput(
            network=network,
            port_impedance=self._port_impedance(freqs, z0, names)
            if problem.outputs.port_impedance
            else None,
            farfield=None,
            fields=(),
            convergence_log=self._convergence(meta),
            solver_version=version,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------------- helpers
    def _assess(self, freqs: np.ndarray, s: np.ndarray, z0: np.ndarray) -> QualityReport:
        assessor = self._assessor or default_assessor()
        if assessor is not None:
            import skrf

            net = skrf.Network(
                frequency=skrf.Frequency.from_f(freqs, unit="hz"), s=s, z0=z0.astype(complex)
            )
            return assessor(net)
        sv = np.linalg.svd(s, compute_uv=False)
        return QualityReport(
            verdict="fail",
            pqm=0.0,
            rqm=0.0,
            cqm=0.0,
            max_singular_value=float(sv.max()),
            worst_passivity_freq=float(freqs[int(np.argmax(sv[:, 0]))]),
            reciprocity_max_err=float(np.max(np.abs(s - np.transpose(s, (0, 2, 1))))),
            dc="missing",
            f_min=float(freqs[0]),
            f_max=float(freqs[-1]),
            n_freqs=int(freqs.size),
            criteria=QualityCriteria(),
            notes=("quality gate oa_network.quality.assess is not available; NOT assessed",),
        )

    def _port_map(self, problem: EMProblem) -> list[PortMapEntry]:
        st = problem.structure
        by_id = {c.id: c for c in st.conductors}
        out: list[PortMapEntry] = []
        for p in problem.ports:
            if isinstance(p, EMWavePort):
                nets = tuple(
                    by_id[cid].net
                    for cid in p.signal_conductors
                    if cid in by_id and by_id[cid].net is not None
                )
                box = st.bbox
                x = box.xmin if p.face == "xmin" else box.xmax if p.face == "xmax" else None
                y = box.ymin if p.face == "ymin" else box.ymax if p.face == "ymax" else None
                mid = 0.5 * (p.rect[0] + p.rect[1])
                out.append(
                    PortMapEntry(
                        port=p.name,
                        nets=tuple(n or "" for n in nets),
                        terminals=(p.face,),
                        location=(x if x is not None else mid, y if y is not None else mid),
                    )
                )
            elif isinstance(p, EMLumpedPort):
                nets = tuple(
                    dict.fromkeys(
                        c.net
                        for c in st.conductors
                        if c.net is not None and c.layer in (p.positive.layer, p.negative.layer)
                    )
                )
                out.append(
                    PortMapEntry(
                        port=p.name,
                        nets=tuple(n for n in nets if n is not None),
                        terminals=("positive", "negative"),
                        location=p.positive.position,
                    )
                )
            else:
                out.append(PortMapEntry(port=p.name))
        return out

    def _write_files(
        self,
        problem: EMProblem,
        run: PreparedRun,
        freqs: np.ndarray,
        s: np.ndarray,
        z0: np.ndarray,
        names: tuple[str, ...],
        quality: QualityReport,
        meta: dict[str, Any],
        version: str,
    ) -> ArtifactRef:
        out_dir = run.workdir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(problem.name)
        ts = out_dir / f"{stem}.s{len(names)}p"
        write_touchstone(
            ts,
            freqs,
            s,
            [float(z) for z in z0],
            names,
            comments=(
                f"Open_Analysis {version}; problem {problem.name}",
                f"EMProblem key {problem.key()}",
            ),
        )
        sha, size = _sha256_file(ts)
        sidecar: dict[str, Any] = {
            "schema": "oa-fdtd-result/1",
            "problem": problem.name,
            "problem_key": problem.key(),
            "structure_key": problem.structure.key(),
            "solver": self.name,
            "solver_version": version,
            "touchstone": ts.name,
            "ports": [
                {"name": nm, "z0_ohm": float(z), "number": i + 1}
                for i, (nm, z) in enumerate(zip(names, z0, strict=True))
            ],
            "freq_hz": [float(freqs[0]), float(freqs[-1])],
            "n_freqs": int(freqs.size),
            "mesh_stats": run.mesh_stats,
            "run": {k: v for k, v in meta.items() if k not in ("diagnostics",)},
            "quality": quality.model_dump(mode="json"),
            "stackup_provenance": problem.structure.provenance.model_dump(mode="json"),
        }
        (out_dir / f"{stem}.json").write_text(
            json.dumps(_json(sidecar), indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        return ArtifactRef(
            sha256=sha, media_type="application/x-touchstone", name=ts.name, size=size
        )

    def _port_impedance(
        self, freqs: np.ndarray, z0: np.ndarray, names: tuple[str, ...]
    ) -> TableResult:
        cols = [ColumnSpec(name="freq_hz", dtype="f8", unit="Hz")]
        for nm in names:
            cols.append(ColumnSpec(name=f"{nm}.z_re", dtype="f8", unit="ohm"))
            cols.append(ColumnSpec(name=f"{nm}.z_im", dtype="f8", unit="ohm"))
        idx = np.unique(np.linspace(0, freqs.size - 1, min(freqs.size, 200)).round().astype(int))
        rows = tuple((float(freqs[k]), *(v for z in z0 for v in (float(z), 0.0))) for k in idx)
        return TableResult(columns=tuple(cols), inline=rows)

    def _convergence(self, meta: dict[str, Any]) -> TableResult:
        cols = (
            ColumnSpec(name="run", dtype="str", role="id"),
            ColumnSpec(name="timestep", dtype="i8"),
            ColumnSpec(name="energy_db", dtype="f8", unit="dB"),
        )
        return TableResult(
            columns=cols, inline=(("batch", int(meta["steps"]), float(meta["energy_db"])),)
        )


OaFdtdAdapter = FdtdAdapter


def _error_line(tail: str) -> str:
    for line in reversed(tail.strip().splitlines()):
        if line.startswith("{") and '"code"' in line:
            try:
                d = json.loads(line)
                return f"{d.get('code')}: {d.get('message')}"
            except ValueError:
                continue
    return ""
