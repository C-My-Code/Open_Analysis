# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``em.sparams`` analysis: board region -> ``EMProblem`` -> solver adapter -> S-parameters.

Tasks: one ``prepare`` task (adapter check + mesh statistics), one ``run`` task per adapter
command (an excitation for FDTD, everything for FEM) and, when ``refinement_check`` is on, the
same again on a finer mesh. Every task rebuilds the (deterministic) ``EMProblem`` from the
board and the config, so tasks stay JSON-sized and cacheable. ``reduce`` re-creates the run
directory from the stored artifacts, calls ``adapter.collect``, applies the quality gate and
writes Touchstone plus a JSON sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import skrf

from oa_em.adapters import load_adapter, resolve_tool
from oa_em.checks import (
    closed_form_z0,
    energy_convergence,
    measured_z0,
    mesh_delta,
    s_points_text,
    z0_check,
)
from oa_em.config import EmSparamsConfig, TaskPayload
from oa_em.problem import Built, build_problem
from oa_framework.analysis import Plan, PlanContext, Resources, RunContext, Task, TaskOutput
from oa_framework.em import EMSolveOutput, PreparedRun, SolverAdapter
from oa_framework.external import ExternalResult, ToolInfo
from oa_model._base import OAModel
from oa_model.arrays import Floats, NDArray
from oa_model.common import Diagnostic
from oa_model.em import EMProblem
from oa_model.errors import SetupError
from oa_network import gate, se2gmm
from oa_results.bundle import ArtifactOnly, Provenance, ResultBundle, ResultItem
from oa_results.container import SolverProvenance, ValidityFlag
from oa_results.io import to_touchstone
from oa_results.network import NetworkResult, PortMapEntry
from oa_results.quality import QualityReport

__all__ = ["EmSparams"]

KIND = "em.sparams"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_PRESET_CPW = {"coarse": 10.0, "medium": 20.0, "fine": 30.0, "expert": 20.0}
# The openEMS adapter's est_time_s is about 15x too pessimistic (316 s estimated vs 21 s on the
# via transition, WP-108 follow-up). Calibrated against the WP-205 runs; refreshed from measured
# wall times (see the package README). Used only for the pre-flight budget estimate.
_EST_SCALE = {"openems": 0.1}
TOLERANCE_BASIS = "WP-205 acceptance: microstrip thru vs analytic line; via transition plausibility"


def _ulid() -> str:
    n = int(time.time() * 1000)
    ts = ""
    for _ in range(10):
        ts = _CROCKFORD[n % 32] + ts
        n //= 32
    rnd = int.from_bytes(os.urandom(10), "big")
    body = ""
    for _ in range(16):
        body += _CROCKFORD[rnd % 32]
        rnd //= 32
    return ts + body


def _utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _fine(problem: EMProblem, cfg: EmSparamsConfig) -> EMProblem:
    base = problem.mesh.cells_per_wavelength or _PRESET_CPW[problem.mesh.preset]
    mesh = problem.mesh.model_copy(update={"cells_per_wavelength": base * cfg.refinement_factor})
    return problem.model_copy(update={"mesh": mesh})


class EmSparams:
    kind: ClassVar[str] = KIND
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "Full-wave S-parameters of a board region"
    Config: ClassVar[type[OAModel]] = EmSparamsConfig
    TaskPayload: ClassVar[type[OAModel]] = TaskPayload

    def __init__(self, adapter: SolverAdapter | None = None, tool: ToolInfo | None = None) -> None:
        """``adapter`` and ``tool`` override the entry-point lookup and the tool configuration
        (tests, embedding); jobs construct the analysis without arguments."""
        self._adapter = adapter
        self._tool = tool

    # ------------------------------------------------------------------ helpers
    def _adapter_for(self, cfg: EmSparamsConfig) -> SolverAdapter:
        return self._adapter if self._adapter is not None else load_adapter(cfg.solver)

    def _tool_for(self, ctx: Any, adapter: SolverAdapter) -> ToolInfo:
        return self._tool if self._tool is not None else resolve_tool(ctx, adapter)

    def _build(self, ctx: Any, cfg: EmSparamsConfig, variant: str = "base") -> Built:
        built = build_problem(ctx.geometry(), cfg)
        if variant == "fine":
            built.problem = _fine(built.problem, cfg)
        return built

    def _n_commands(self, adapter: SolverAdapter, problem: EMProblem) -> int:
        if adapter.capabilities().excitations_per_run == "all":
            return 1
        return sum(1 for p in problem.ports if p.excite)

    # ------------------------------------------------------------------ protocol
    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        assert isinstance(cfg, EmSparamsConfig)
        try:
            adapter = self._adapter_for(cfg)
        except Exception as exc:
            return (
                Diagnostic(
                    severity="error",
                    code="EM.NO_ADAPTER",
                    message=str(exc),
                    hint="Install the solver adapter or set config.solver.",
                ),
            )
        try:
            built = build_problem(ctx.geometry(), cfg)
        except SetupError as exc:
            return tuple(getattr(exc, "diagnostics", ())) or (
                Diagnostic(severity="error", code="EM.SETUP", message=str(exc)),
            )
        diags = list(built.diagnostics)
        caps = adapter.capabilities()
        kinds = {"wave": "wave", "lumped": "lumped", "coax": "coax"}
        for p in built.problem.ports:
            if kinds[p.kind] not in caps.ports:
                diags.append(
                    Diagnostic(
                        severity="error",
                        code="EM.ADAPTER_PORT",
                        message=f"solver {adapter.name!r} does not support {p.kind} ports "
                        f"(port {p.name})",
                        item=p.name,
                        hint="Choose another solver or port type.",
                    )
                )
        if not any(d.severity == "error" for d in diags):
            diags.extend(adapter.check(built.problem))
        for info in built.ports:
            if info.kind == "wave" and info.z0_cfg is not None:
                d = z0_check(
                    info,
                    closed_form_z0(ctx, ctx.geometry(), info),
                    info.z0_cfg,
                    cfg.z0_tolerance,
                    "configured",
                )
                if d:
                    diags.append(d)
        return tuple(diags)

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, EmSparamsConfig)
        adapter = self._adapter_for(cfg)
        built = build_problem(ctx.geometry(), cfg)
        problem = built.problem
        n = self._n_commands(adapter, problem)
        tmp = Path(tempfile.mkdtemp(prefix="oa_em_plan_"))
        try:
            stats = adapter.prepare(problem, tmp, self._tool_for(ctx, adapter)).mesh_stats
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        scale = _EST_SCALE.get(adapter.name, 1.0) * cfg.estimate_scale
        est = float(stats.get("est_time_s", 60.0)) * scale  # type: ignore[arg-type]
        mem = float(stats.get("est_memory_gb", 1.0)) + 0.3  # type: ignore[arg-type]
        needs = (adapter.tool,)
        cfg_json = json.loads(cfg.model_dump_json())
        tasks = [
            Task(
                id="prepare",
                analysis=KIND,
                payload=TaskPayload(role="prepare", config=cfg_json).model_dump(mode="json"),
                resources=Resources(est_seconds=2.0 + est * 0.05, memory_gb=0.5),
            )
        ]
        variants: list[Any] = ["base"] + (["fine"] if cfg.refinement_check else [])
        for v in variants:
            f = cfg.refinement_factor**4 if v == "fine" else 1.0
            for i in range(n):
                tasks.append(
                    Task(
                        id=f"run:{v}:{i}",
                        analysis=KIND,
                        payload=TaskPayload(
                            role="run",
                            config=cfg_json,
                            variant=v,
                            index=i,
                        ).model_dump(mode="json"),
                        depends_on=("prepare",),
                        resources=Resources(
                            est_seconds=est * f, memory_gb=mem * (f**0.75), needs=needs
                        ),
                    )
                )
        return Plan(tasks=tuple(tasks))

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, TaskPayload)
        cfg = EmSparamsConfig.model_validate(payload.config)
        adapter = self._adapter_for(cfg)
        built = self._build(ctx, cfg, payload.variant)
        problem = built.problem
        diags = list(adapter.check(problem))
        errors = [d for d in diags if d.severity == "error"]
        if errors:
            raise SetupError("; ".join(f"{d.code}: {d.message}" for d in errors), diags)
        work = ctx.scratch / "work"
        prep = adapter.prepare(problem, work, self._tool_for(ctx, adapter))
        if payload.role == "prepare":
            return TaskOutput(
                task_id=ctx.task.id,
                data={
                    "problem_key": problem.key(),
                    "n_commands": len(prep.commands),
                    "mesh_stats": prep.mesh_stats,
                },
                diagnostics=tuple(diags),
            )
        cmd = prep.commands[payload.index]
        t0 = time.perf_counter()
        res = ctx.run_external(cmd)
        wall = time.perf_counter() - t0
        rel_dir = cmd.workdir.relative_to(prep.workdir)
        refs = []
        for rel in cmd.outputs:
            f = cmd.workdir / rel
            if f.is_file():
                refs.append(
                    ctx.artifacts().put_file(
                        f, name=(rel_dir / rel).as_posix(), media_type="application/octet-stream"
                    )
                )
        return TaskOutput(
            task_id=ctx.task.id,
            data={
                "result": json.loads(res.model_dump_json()),
                "wall_s": wall,
                "workdir": rel_dir.as_posix(),
            },
            artifacts=tuple(refs),
        )

    # ------------------------------------------------------------------ reduce
    def _collect(
        self,
        ctx: RunContext,
        cfg: EmSparamsConfig,
        adapter: SolverAdapter,
        variant: str,
        outputs: Mapping[str, TaskOutput],
    ) -> tuple[Built, PreparedRun, EMSolveOutput, float]:
        built = self._build(ctx, cfg, variant)
        work = ctx.scratch / f"work_{variant}"
        prep = adapter.prepare(built.problem, work, self._tool_for(ctx, adapter))
        results: list[ExternalResult] = []
        wall = 0.0
        for i in range(len(prep.commands)):
            out = outputs[f"run:{variant}:{i}"]
            for ref in out.artifacts:
                dest = work / ref.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ctx.artifacts().path(ref), dest)
            results.append(ExternalResult.model_validate(out.data["result"]))
            wall += float(out.data["wall_s"])  # type: ignore[arg-type]
        return built, prep, adapter.collect(built.problem, prep, results), wall

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        assert isinstance(cfg, EmSparamsConfig)
        t0 = _utc()
        adapter = self._adapter_for(cfg)
        built, prep, out, wall = self._collect(ctx, cfg, adapter, "base", outputs)
        diags: list[Diagnostic] = [*built.diagnostics, *out.warnings]
        board = ctx.board()
        cfg_json = cfg.model_dump_json()
        prov = Provenance(
            analysis=KIND,
            analysis_version=self.version,
            board_hash=board.content_hash(),
            physics_hash=None,
            setup_hash=_sha(cfg_json),
            config=json.loads(cfg_json),
            solver=adapter.name,
            solver_version=out.solver_version,
            started_utc=t0,
            finished_utc=t0,
            inputs_hash=_sha(built.problem.key() + cfg_json),
        )
        if out.network is None:
            msgs = "; ".join(d.message for d in out.warnings if d.severity == "error")
            return ResultBundle(
                id=_ulid(),
                status="error",
                summary=f"{KIND}: solver {adapter.name} produced no S-parameters. {msgs}"[:1400],
                items=(),
                diagnostics=tuple(diags),
                provenance=prov.model_copy(update={"finished_utc": _utc()}),
            )

        # ---- convergence ------------------------------------------------------------------
        e_ok, e_db = energy_convergence(
            out.convergence_log, cfg.convergence.energy_decay_db, cfg.energy_margin_db
        )
        if e_ok is False:
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="EM.NOT_CONVERGED",
                    message=f"FDTD energy ended at {e_db:.1f} dB, above the "
                    f"{cfg.convergence.energy_decay_db:g} dB end criterion",
                    hint="Raise convergence.max_timesteps; closed regions decay slowly.",
                )
            )
        net0 = out.network
        s_base = np.asarray(net0.s.array)
        mesh_ok: bool | None = None
        d_s: float | None = None
        fine_wall = 0.0
        if cfg.refinement_check:
            _b, _p, out_f, fine_wall = self._collect(ctx, cfg, adapter, "fine", outputs)
            if out_f.network is not None:
                d_s = mesh_delta(s_base, np.asarray(out_f.network.s.array))
                mesh_ok = d_s <= cfg.convergence.delta_s
            if mesh_ok is not True:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        code="EM.MESH_NOT_CONVERGED",
                        message=f"mesh refinement changes S by max |dS| = "
                        f"{'n/a' if d_s is None else f'{d_s:.3f}'} "
                        f"(limit {cfg.convergence.delta_s:g})",
                        hint="Use mesh.preset='fine' or a larger cells_per_wavelength.",
                    )
                )
        converged_parts = [x for x in (e_ok, mesh_ok) if x is not None]
        converged = all(converged_parts) if converged_parts else None

        # ---- port impedance sanity check ----------------------------------------------------
        for info in built.ports:
            if info.kind != "wave":
                continue
            d = z0_check(
                info,
                closed_form_z0(ctx, ctx.geometry(), info),
                measured_z0(out.port_impedance, info.em_name),
                cfg.z0_tolerance,
                "solver",
            )
            if d:
                diags.append(d)

        # ---- quality gate -------------------------------------------------------------------
        net_in = net0.to_skrf()
        sym_fix: str | None = None
        if cfg.fix_quality:
            # PCB structures are reciprocal: remove solver noise in S12 - S21 before the gate
            # (the gate itself never touches reciprocity). Recorded as a fix.
            s_raw = np.asarray(net_in.s, dtype=complex)
            err = float(np.max(np.abs(s_raw - np.transpose(s_raw, (0, 2, 1)))))
            if err > 1e-6:
                net_in = net_in.copy()
                net_in.s = 0.5 * (s_raw + np.transpose(s_raw, (0, 2, 1)))
                sym_fix = f"reciprocity_symmetrized(max |Sij-Sji| = {err:.3g})"
        net_g, rep = gate(net_in, fix=cfg.fix_quality)
        if sym_fix is not None:
            rep = rep.model_copy(
                update={
                    "fixes_applied": (sym_fix, *rep.fixes_applied),
                    "verdict": "warn" if rep.verdict == "pass" else rep.verdict,
                }
            )
        pairs = built.pairs
        se = self._network(ctx, built, net_g, rep, adapter, out, pairs, "sparams", converged)
        items = [ResultItem(id="sparams", title="S-parameters", kind="network", data=se)]
        reports = {"sparams": rep}
        if built.want_mixed_mode:
            try:
                mm = se2gmm(net_g)
                mm_g, rep_mm = gate(mm, fix=False)
                names = [f"D_{built.ports[i].setup_name}" for i, _j in pairs] + [
                    f"C_{built.ports[i].setup_name}" for i, _j in pairs
                ]
                mm_res = NetworkResult(
                    freqs=Floats(np.asarray(mm_g.f, dtype=float)),
                    s=NDArray(np.asarray(mm_g.s, dtype=complex)),
                    z0=NDArray(np.asarray(mm_g.z0[0], dtype=complex)),
                    port_names=tuple(names),
                    port_map=(),
                    quality=rep_mm,
                    provenance=SolverProvenance(
                        solver=adapter.name,
                        solver_version=out.solver_version,
                        inputs_hash=prov.inputs_hash,
                    ),
                    accuracy_class="estimate",
                    validity_flags=self._flags(rep_mm, converged, net_g),
                )
                items.append(
                    ResultItem(
                        id="sparams_mixed_mode",
                        title="Mixed-mode S-parameters",
                        kind="network",
                        data=mm_res,
                    )
                )
                reports["sparams_mixed_mode"] = rep_mm
            except Exception as exc:  # conversion is an add-on; keep the SE result
                diags.append(
                    Diagnostic(severity="warning", code="EM.MIXED_MODE_FAILED", message=str(exc))
                )
        if out.port_impedance is not None:
            items.append(
                ResultItem(
                    id="port_z", title="Port impedance", kind="table", data=out.port_impedance
                )
            )
        if out.convergence_log is not None:
            items.append(
                ResultItem(
                    id="convergence",
                    title="FDTD energy convergence",
                    kind="table",
                    data=out.convergence_log,
                )
            )

        # ---- sidecar and mesh preview -------------------------------------------------------
        st = built.problem.structure
        total_wall = wall + fine_wall
        sidecar: dict[str, Any] = {
            "schema": "oa-em-sidecar/1",
            "solver": adapter.name,
            "solver_version": out.solver_version,
            "problem_key": built.problem.key(),
            "structure_key": st.key(),
            "stackup_hash": _sha(board.stackup.model_dump_json()),
            "region": st.provenance.region,
            "bbox": [
                st.bbox.xmin,
                st.bbox.ymin,
                st.bbox.zmin,
                st.bbox.xmax,
                st.bbox.ymax,
                st.bbox.zmax,
            ],
            "boundaries": built.problem.boundaries.model_dump(mode="json"),
            "port_map": [
                {
                    "port": p.em_name,
                    "setup_port": p.setup_name,
                    "kind": p.kind,
                    "nets": list(p.nets),
                    "face": p.face,
                    "layer": p.layer,
                    "position": list(p.position),
                    "extended_to_face": p.extended,
                }
                for p in built.ports
            ],
            "mesh_stats": prep.mesh_stats,
            "convergence": {
                "energy_final_db": e_db,
                "energy_target_db": cfg.convergence.energy_decay_db,
                "energy_converged": e_ok,
                "mesh_refinement_checked": cfg.refinement_check,
                "mesh_max_delta_s": d_s,
                "mesh_converged": mesh_ok,
                "converged": converged,
            },
            "quality": {k: r.model_dump(mode="json") for k, r in reports.items()},
            "runtime_s": {
                "solver_wall_s": total_wall,
                "base_wall_s": wall,
                "fine_wall_s": fine_wall,
            },
        }
        art = ctx.artifacts()
        side_ref = art.put_bytes(
            json.dumps(sidecar, indent=1, sort_keys=True, default=str).encode(),
            name=f"{prov.analysis}.json",
            media_type="application/json",
        )
        items.append(
            ResultItem(
                id="sidecar",
                title="Run metadata",
                kind="artifact",
                data=ArtifactOnly(artifact=side_ref),
            )
        )
        preview = {
            "bbox": sidecar["bbox"],
            "boundaries": sidecar["boundaries"],
            "mesh_stats": prep.mesh_stats,
            "ports": sidecar["port_map"],
        }
        prev_ref = art.put_bytes(
            json.dumps(preview, indent=1, sort_keys=True, default=str).encode(),
            name="mesh_preview.json",
            media_type="application/json",
        )
        items.append(
            ResultItem(
                id="mesh_preview",
                title="Mesh preview",
                kind="artifact",
                data=ArtifactOnly(artifact=prev_ref),
            )
        )

        # ---- status and summary -------------------------------------------------------------
        verdict = rep.verdict
        warn_codes = [d for d in diags if d.severity == "warning" and d.code.startswith("EM.")]
        if verdict == "fail":
            status = "failed_quality"
        elif verdict == "warn" or converged is False or warn_codes:
            status = "warning"
        else:
            status = "ok"
        diags.append(
            Diagnostic(
                severity="info",
                code="EM.RUNTIME",
                message=f"solver wall time {total_wall:.1f} s ({adapter.name})",
            )
        )
        f = np.asarray(net_g.f, dtype=float)
        text = (
            f"{len(built.ports)}-port S-parameters {f[0] / 1e9:.3g}..{f[-1] / 1e9:.3g} GHz "
            f"from {adapter.name} {out.solver_version}: quality {verdict} "
            f"(PQM {rep.pqm:.1f}, RQM {rep.rqm:.1f}); "
            f"{s_points_text(f, np.asarray(net_g.s))}; "
            f"converged {converged}; runtime {total_wall:.0f} s."
        )
        return ResultBundle(
            id=_ulid(),
            status=status,  # type: ignore[arg-type]
            summary=text[:1400],
            items=tuple(items),
            diagnostics=tuple(diags),
            provenance=prov.model_copy(update={"finished_utc": _utc()}),
        )

    def _flags(
        self, rep: QualityReport, converged: bool | None, net: skrf.Network
    ) -> tuple[ValidityFlag, ...]:
        f = np.asarray(net.f, dtype=float)
        msg = (
            "accuracy_class=3d_full_wave; method=openems-fdtd/adapter; validity=in_range; "
            f"quality={rep.verdict}; converged={converged}; "
            f"f_min={f[0]:.6g}; f_max={f[-1]:.6g}; tolerance_basis={TOLERANCE_BASIS}"
        )
        return (
            ValidityFlag(code="accuracy", ok=rep.verdict != "fail", message=msg),
            ValidityFlag(
                code="quality_gate", ok=rep.verdict == "pass", message=f"verdict={rep.verdict}"
            ),
            ValidityFlag(
                code="converged", ok=converged is not False, message=f"converged={converged}"
            ),
        )

    def _network(
        self,
        ctx: RunContext,
        built: Built,
        net: skrf.Network,
        rep: QualityReport,
        adapter: SolverAdapter,
        out: EMSolveOutput,
        pairs: tuple[tuple[int, int], ...],
        name: str,
        converged: bool | None,
    ) -> NetworkResult:
        res = NetworkResult(
            freqs=Floats(np.asarray(net.f, dtype=float)),
            s=NDArray(np.asarray(net.s, dtype=complex)),
            z0=NDArray(np.asarray(net.z0[0], dtype=complex)),
            port_names=tuple(p.em_name for p in built.ports),
            port_map=tuple(
                PortMapEntry(port=p.em_name, nets=p.nets, location=p.position) for p in built.ports
            ),
            mixed_mode_pairs=pairs if built.want_mixed_mode else (),
            validity_fmax=None,
            quality=rep,
            provenance=SolverProvenance(
                solver=adapter.name,
                solver_version=out.solver_version,
                inputs_hash=_sha(built.problem.key()),
            ),
            accuracy_class="validated"
            if rep.verdict == "pass" and converged is not False
            else "estimate",
            validity_flags=self._flags(rep, converged, net),
        )
        path = ctx.scratch / "out" / f"{name}.s{len(res.port_names)}p"
        path.parent.mkdir(parents=True, exist_ok=True)
        to_touchstone(res, path)
        ref = ctx.artifacts().put_file(path, name=path.name, media_type="application/x-touchstone")
        return res.model_copy(update={"touchstone": ref})
