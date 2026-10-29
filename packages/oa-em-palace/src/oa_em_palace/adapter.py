# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Palace ``SolverAdapter`` (interfaces.md section 5.3, ADR-0004).

``prepare`` meshes the problem (WP-206), writes ``config.json`` and returns one
``ExternalCommand`` for the ``palace`` tool (the ``oa-palace-run`` wrapper): Palace solves every
excitation in one run. ``collect`` reads ``postpro/port-S.csv``, runs the quality gate and writes
Touchstone plus a metadata sidecar. Palace is Apache-2.0 and runs as a separate process (WSL2,
Docker or native Linux); nothing here imports it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from pydantic import JsonValue

from oa_em_palace import discovery
from oa_em_palace.config import PalaceOptions, build_config
from oa_em_palace.parse import SParams, parse_footer, read_csv_table, read_port_s
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
from oa_results.io.touchstone import to_touchstone
from oa_results.network import NetworkResult, PortMapEntry
from oa_results.quality import QualityCriteria, QualityReport
from oa_results.tables import ColumnSpec, TableResult

Assessor = Callable[[Any], QualityReport]
"""``oa_network.quality.assess`` (interfaces.md section 4.5): ``skrf.Network -> QualityReport``."""

_MAX_TABLE_ROWS = 1000
CONFIG = "config.json"
MESH = "model.msh"
OUTPUT_DIR = "postpro"


def default_assessor() -> Assessor | None:
    """The WP-106 quality gate, or ``None`` when ``oa_network.quality`` is not available."""
    try:
        from oa_network.quality import assess  # type: ignore[import-not-found,unused-ignore]
    except Exception:
        return None
    return assess


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "problem"


def _json(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=float))


def _dofs(n_tets: int, n_faces: int, n_edges: int, order: int) -> int:
    """Nedelec (first kind) DOFs of a tetrahedral mesh at polynomial order ``order``."""
    p = order
    return n_edges * p + n_faces * p * (p - 1) + n_tets * p * (p - 1) * (p - 2) // 2


def _mesh(problem: EMProblem, path: Path) -> Any:
    """WP-206 mesh of ``problem`` as gmsh ``.msh`` **2.2** (MFEM's reader in Palace 0.18 does not
    read 4.1) plus ``<path>.groups.json``; returns ``MeshStats``."""
    from oa_mesh.occ3d import OccOptions, build_occ_model, write_msh

    opts = OccOptions().model_copy(update={"f_max": problem.frequency.f_max})
    thick = [s.z_top - s.z_bottom for s in problem.structure.slabs]
    model = build_occ_model(
        problem.structure,
        sheet_threshold=0.5 * min(thick) if thick else 0.0,
        ports=problem.ports,
        options=opts,
        boundaries=problem.boundaries,
    )
    return write_msh(model, path, sizes=problem.mesh, msh_version="2.2")


class PalaceAdapter:
    """Frequency-domain FEM adapter for AWS Palace 0.18.x.

    ``mpi_ranks`` is the number of MPI ranks (the wrapper clamps it to ``OA_PALACE_MAX_NP``, default
    2); ``gpu=True`` sets ``Solver.Device = "GPU"`` (needs a CUDA/HIP Palace build); ``memory_gb``
    is the hard limit of the whole process tree inside WSL/Linux. ``assessor`` overrides the
    quality gate (default: ``oa_network.quality.assess`` when importable).
    """

    name: ClassVar[str] = "palace"
    tool: ClassVar[str] = discovery.PROGRAM

    def __init__(
        self,
        *,
        mpi_ranks: int = 1,
        gpu: bool = False,
        memory_gb: float = 8.0,
        cpu_percent: int = 200,
        assessor: Assessor | None = None,
    ) -> None:
        if mpi_ranks < 1:
            raise ValueError("mpi_ranks must be >= 1")
        self.mpi_ranks = mpi_ranks
        self.gpu = gpu
        self.memory_gb = memory_gb
        self.cpu_percent = cpu_percent
        self._assessor = assessor

    # ------------------------------------------------------------------ capabilities / check
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            physics=frozenset({"fullwave_driven"}),
            ports=frozenset({"lumped", "wave", "coax"}),
            wave_port_mode="modal",
            boundaries=frozenset({"pec", "pmc", "absorbing"}),
            dispersive_dielectric="single_frequency_fit",
            surface_roughness=False,
            sheet_conductors=True,
            excitations_per_run="all",
            farfield=False,
            platforms=frozenset({"linux", "windows"}),  # Windows through WSL2 or Docker
        )

    def check(self, problem: EMProblem) -> tuple[Diagnostic, ...]:
        diags: list[Diagnostic] = []

        def err(code: str, message: str, hint: str | None = None) -> None:
            diags.append(
                Diagnostic(severity="error", code=code, message=message, hint=hint)
            )

        def warn(code: str, message: str, hint: str | None = None) -> None:
            diags.append(
                Diagnostic(severity="warning", code=code, message=message, hint=hint)
            )

        if not isinstance(problem.physics, FullWaveDriven):
            err(
                "PALACE.PHYSICS",
                f"physics {problem.physics.kind!r} is not supported by the Palace adapter",
                "Only fullwave_driven is implemented.",
            )
        if not any(p.excite for p in problem.ports):
            err("PALACE.NO_EXCITATION", "no port has excite=True", "Excite at least one port.")
        if problem.outputs.farfield is not None:
            err(
                "PALACE.FARFIELD",
                "far-field output is not implemented for Palace",
                "Remove outputs.farfield or use the openEMS adapter.",
            )
        if problem.outputs.field_dumps:
            warn("PALACE.FIELD_DUMPS", "field dumps are ignored by the Palace adapter")
        kinds = {p.kind for p in problem.ports}
        if "wave" in kinds and "lumped" in kinds:
            warn(
                "PALACE.MIXED_PORTS",
                "lumped and wave ports in one problem: cross-type S-parameters may be 180 deg out "
                "of phase (Palace polarity convention)",
                "Use one port type, or pin wave-port polarity (not done by this adapter).",
            )
        for p in problem.ports:
            if isinstance(p, EMWavePort) and p.z0 is not None:
                warn(
                    "PALACE.WAVE_Z0_IGNORED",
                    f"port {p.name}: Palace wave-port S-parameters are referenced to the modal "
                    "impedance; z0 is not applied",
                )
        if problem.frequency.f_min == 0:
            diags.append(
                Diagnostic(
                    severity="info",
                    code="PALACE.NO_DC",
                    message="Palace cannot solve at 0 Hz; the sweep starts above DC",
                )
            )
        return tuple(diags)

    # --------------------------------------------------------------------------- prepare
    def prepare(self, problem: EMProblem, workdir: Path, tool: ToolInfo) -> PreparedRun:
        errors = [d for d in self.check(problem) if d.severity == "error"]
        if errors:
            raise ValueError("; ".join(d.message for d in errors))
        options = PalaceOptions.from_expert(problem.mesh.expert)
        if self.gpu:
            options = PalaceOptions(**{**options.__dict__, "device": "GPU"})
        workdir.mkdir(parents=True, exist_ok=True)
        stats = _mesh(problem, workdir / MESH)
        groups = json.loads((workdir / "model.groups.json").read_text(encoding="utf-8"))
        config, info = build_config(
            problem, groups, mesh_file=MESH, output_dir=OUTPUT_DIR, options=options
        )
        (workdir / CONFIG).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        env = {
            "OA_PALACE_NP": str(self.mpi_ranks),
            "OA_PALACE_MEM_GB": f"{self.memory_gb:g}",
            "OA_PALACE_CPU_PERCENT": str(self.cpu_percent),
        }
        command = ExternalCommand(
            program=self.tool,
            args=(CONFIG,),
            workdir=workdir,
            env=env,
            timeout_s=problem.convergence.max_wall_s,
            progress_parser="palace",
            outputs=(f"{OUTPUT_DIR}/port-S.csv",),
        )
        order = problem.mesh.fem_order
        dofs = _dofs(stats.n_tets, stats.n_faces, stats.n_edges, order)
        mesh_stats: dict[str, JsonValue] = _json(
            {
                "adapter": self.name,
                "cells": stats.n_tets,
                "nodes": stats.n_nodes,
                "edges": stats.n_edges,
                "dofs": dofs,
                "order": order,
                "min_cell": stats.h_min,
                "max_cell": stats.h_max,
                "min_sicn": stats.min_sicn,
                "mesh_hash": stats.content_hash,
                "mpi_ranks": self.mpi_ranks,
                "memory_limit_gb": self.memory_gb,
                "est_memory_gb": round(0.5 + dofs * 6.0e-6, 2),
                "est_time_s": round(dofs * 2.0e-3 * problem.frequency.n_points),
                "eps_freq_hz": info.eps_freq,
                "dielectric_fit": info.dielectric_fit,
                "sweep_ghz": list(info.sweep_ghz),
                "diagnostics": [d.model_dump(mode="json") for d in info.diagnostics],
                "port_index": info.port_index,
            }
        )
        files = (CONFIG, MESH, "model.groups.json")
        return PreparedRun(
            workdir=workdir, commands=(command,), mesh_stats=mesh_stats, files=files
        )

    # --------------------------------------------------------------------------- collect
    def collect(
        self, problem: EMProblem, run: PreparedRun, results: Sequence[ExternalResult]
    ) -> EMSolveOutput:
        warnings: list[Diagnostic] = [
            Diagnostic.model_validate(d) for d in run.mesh_stats.get("diagnostics", [])  # type: ignore[union-attr]
        ]
        version = "unknown"

        def fail(code: str, message: str, hint: str) -> EMSolveOutput:
            warnings.append(Diagnostic(severity="error", code=code, message=message, hint=hint))
            return EMSolveOutput(
                network=None,
                port_impedance=None,
                farfield=None,
                convergence_log=None,
                solver_version=version,
                warnings=tuple(warnings),
            )

        if len(results) != len(run.commands):
            return fail(
                "PALACE.RESULT_COUNT",
                f"{len(results)} results for {len(run.commands)} commands",
                "Pass one ExternalResult per prepared command.",
            )
        res = results[0]
        footer = parse_footer(res.stdout_tail)
        version = footer.version or res.tool_version or "unknown"
        if res.returncode != 0:
            return fail(
                "PALACE.RUN_FAILED",
                f"palace exited with code {res.returncode}",
                "See logs/ in the job directory; stderr tail: " + res.stderr_tail[-300:],
            )
        csv_path = run.workdir / OUTPUT_DIR / "port-S.csv"
        if not csv_path.is_file():
            return fail(
                "PALACE.NO_OUTPUT",
                f"{OUTPUT_DIR}/port-S.csv not found",
                "The run finished without S-parameters; check the Palace log.",
            )
        try:
            sp = read_port_s(csv_path)
        except ValueError as exc:
            return fail("PALACE.BAD_OUTPUT", str(exc), "Palace output format changed?")
        ports = list(problem.ports)
        n = len(ports)
        if sp.s.shape[1] != n:
            return fail(
                "PALACE.PORT_COUNT",
                f"port-S.csv has {sp.s.shape[1]} ports, the problem has {n}",
                "The config and the problem are out of sync; re-run prepare.",
            )
        s, notes = self._complete(sp, ports)
        for note in notes:
            warnings.append(
                Diagnostic(severity="warning", code="PALACE.RECIPROCITY_FILL", message=note)
            )
        z0 = np.array(
            [
                float(p.z0) if not isinstance(p, EMWavePort) or p.z0 is not None else 50.0
                for p in ports
            ],
            dtype=np.float64,
        )
        names = tuple(p.name for p in ports)
        quality = self._assess(sp.freqs, s, z0)
        wall = footer.wall_s if footer.wall_s is not None else res.wall_s
        peak_mb = footer.peak_mem_kb / 1024.0 if footer.peak_mem_kb is not None else None
        network = NetworkResult(
            freqs=Floats(sp.freqs),
            s=NDArray(s),
            z0=NDArray(z0.astype(np.complex128)),
            port_names=names,
            port_map=tuple(self._port_map(problem)),
            quality=quality,
            touchstone=None,
            provenance=SolverProvenance(
                solver=self.name, solver_version=version, inputs_hash=problem.key()
            ),
        )
        out_dir = run.workdir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(problem.name)
        ts_path = out_dir / f"{stem}.s{n}p"
        to_touchstone(network, ts_path, version="1.1" if len(set(z0)) == 1 else "2.0")
        data = ts_path.read_bytes()
        ref = ArtifactRef(
            sha256=hashlib.sha256(data).hexdigest(),
            media_type="application/x-touchstone",
            name=ts_path.name,
            size=len(data),
        )
        network = network.model_copy(update={"touchstone": ref})
        meta: dict[str, Any] = {
            "schema": "oa-palace-result/1",
            "problem": problem.name,
            "problem_key": problem.key(),
            "structure_key": problem.structure.key(),
            "solver": self.name,
            "solver_version": version,
            "touchstone": ts_path.name,
            "ports": [
                {"name": nm, "z0_ohm": float(z), "number": i + 1}
                for i, (nm, z) in enumerate(zip(names, z0, strict=True))
            ],
            "freq_hz": [float(sp.freqs[0]), float(sp.freqs[-1])],
            "n_freqs": int(sp.freqs.size),
            "mesh_stats": run.mesh_stats,
            "wall_s": wall,
            "peak_mem_mb": peak_mb,
            "quality": quality.model_dump(mode="json"),
            "stackup_provenance": problem.structure.provenance.model_dump(mode="json"),
        }
        (out_dir / f"{stem}.json").write_text(
            json.dumps(_json(meta), indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        return EMSolveOutput(
            network=network,
            port_impedance=None,
            farfield=None,
            fields=(),
            convergence_log=self._convergence(run.workdir / OUTPUT_DIR),
            solver_version=version,
            warnings=tuple(warnings),
        )

    # ------------------------------------------------------------------------- helpers
    @staticmethod
    def _complete(sp: SParams, ports: Sequence[Any]) -> tuple[np.ndarray, list[str]]:
        """Fill columns of ports that were not excited by reciprocity (``S_ij = S_ji``)."""
        s = sp.s.copy()
        notes: list[str] = []
        n = s.shape[1]
        for j in range(n):
            if j in sp.excited:
                continue
            if not all(i in sp.excited for i in range(n) if i != j):
                raise ValueError(f"S column {j + 1} cannot be completed by reciprocity")
            for i in range(n):
                s[:, i, j] = s[:, j, i]
            notes.append(f"column {j + 1} ({ports[j].name}) filled by reciprocity")
        return s, notes

    def _assess(self, freqs: np.ndarray, s: np.ndarray, z0: np.ndarray) -> QualityReport:
        assessor = self._assessor or default_assessor()
        if assessor is not None:
            import skrf

            net = skrf.Network(
                frequency=skrf.Frequency.from_f(freqs, unit="hz"), s=s, z0=z0.astype(complex)
            )
            return assessor(net)
        sv = np.linalg.svd(s, compute_uv=False)
        recip = float(np.max(np.abs(s - np.transpose(s, (0, 2, 1)))))
        return QualityReport(
            verdict="fail",
            pqm=0.0,
            rqm=0.0,
            cqm=0.0,
            max_singular_value=float(sv.max()),
            worst_passivity_freq=float(freqs[int(np.argmax(sv[:, 0]))]),
            reciprocity_max_err=recip,
            dc="missing",
            f_min=float(freqs[0]),
            f_max=float(freqs[-1]),
            n_freqs=int(freqs.size),
            criteria=QualityCriteria(),
            notes=(
                "quality gate oa_network.quality.assess is not available; the network has NOT "
                "been assessed (verdict forced to fail)",
            ),
        )

    def _port_map(self, problem: EMProblem) -> list[PortMapEntry]:
        st = problem.structure
        by_id = {c.id: c for c in st.conductors}
        out: list[PortMapEntry] = []
        for p in problem.ports:
            if isinstance(p, EMWavePort):
                nets = tuple(
                    dict.fromkeys(
                        n
                        for cid in p.signal_conductors
                        if cid in by_id and (n := by_id[cid].net) is not None
                    )
                )
                box = st.bbox
                x = box.xmin if p.face == "xmin" else box.xmax if p.face == "xmax" else None
                y = box.ymin if p.face == "ymin" else box.ymax if p.face == "ymax" else None
                mid = 0.5 * (p.rect[0] + p.rect[1])
                out.append(
                    PortMapEntry(
                        port=p.name,
                        nets=nets,
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
                        nets=nets,
                        terminals=("positive", "negative"),
                        location=p.positive.position,
                    )
                )
            elif isinstance(p, EMCoaxPort):
                out.append(PortMapEntry(port=p.name, location=p.center))
        return out

    @staticmethod
    def _convergence(post: Path) -> TableResult | None:
        """AMR history from ``error-indicators.csv`` (one row per adaptation pass), if any."""
        candidates = sorted(post.glob("**/error-indicators.csv"))
        if not candidates:
            return None
        try:
            header, body = read_csv_table(candidates[-1])
        except ValueError:
            return None
        cols = tuple(
            ColumnSpec(name=h.split("(")[0].strip() or f"c{i}", dtype="f8")
            for i, h in enumerate(header)
        )
        rows: list[tuple[JsonValue, ...]] = [tuple(float(v) for v in r) for r in body[:200]]
        return TableResult(columns=cols, inline=tuple(rows))


__all__ = ["PalaceAdapter", "default_assessor"]
