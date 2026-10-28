# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The openEMS ``SolverAdapter`` (interfaces.md section 5.3, ADR-0004).

``prepare`` writes one job directory per excitation (``exc_<port>/scene.json`` + ``sim.py``) and
returns one ``ExternalCommand`` each; the job system runs them out-of-process through an
``ExternalRunner``. ``collect`` reads the raw port voltages/currents the script left behind,
assembles and renormalizes the S-matrix, passes it through the quality gate and writes Touchstone
plus a metadata sidecar. Nothing here imports openEMS or CSXCAD.
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

from oa_em_openems import discovery
from oa_em_openems.postprocess import (
    RunRaw,
    assemble_s,
    line_impedance_reference,
    load_run,
    parse_convergence,
)
from oa_em_openems.scene import Options, Scene, build_scene, check_problem
from oa_em_openems.script import SIM_SCRIPT
from oa_em_openems.touchstone import write_touchstone
from oa_framework.em import AdapterCapabilities, EMSolveOutput, PreparedRun
from oa_framework.external import ExternalCommand, ExternalResult, ToolInfo
from oa_model.arrays import Floats, NDArray
from oa_model.common import Diagnostic
from oa_model.em import EMLumpedPort, EMProblem, EMWavePort
from oa_results.artifact import ArtifactRef
from oa_results.container import SolverProvenance
from oa_results.extraction import FarFieldResult
from oa_results.network import NetworkResult, PortMapEntry
from oa_results.quality import QualityCriteria, QualityReport
from oa_results.tables import ColumnSpec, TableResult

Assessor = Callable[[Any], QualityReport]
"""``oa_network.quality.assess`` (interfaces.md section 4.5): ``skrf.Network -> QualityReport``."""

_MAX_TABLE_ROWS = 1000


def default_assessor() -> Assessor | None:
    """The WP-106 quality gate, or ``None`` when ``oa_network.quality`` is not available."""
    try:
        from oa_network.quality import assess  # type: ignore[import-not-found,unused-ignore]
    except Exception:
        return None
    return assess  # type: ignore[no-any-return]


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "problem"


def _json(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=float))


def _sha256_file(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _decimate(n: int, cap: int) -> list[int]:
    if n <= cap:
        return list(range(n))
    idx = np.unique(np.linspace(0, n - 1, cap).round().astype(int))
    return [int(i) for i in idx]


class OpenEMSAdapter:
    """FDTD solver adapter for openEMS >= 0.37.

    ``farfield`` is the far-field flag: with ``False`` (default) a problem that requests
    ``outputs.farfield`` is rejected by :meth:`check`; with ``True`` the script adds an NF2FF box
    and :meth:`collect` returns a ``FarFieldResult``. ``assessor`` overrides the quality gate
    (default: ``oa_network.quality.assess`` when importable).
    """

    name: ClassVar[str] = "openems"
    tool: ClassVar[str] = discovery.PROGRAM

    def __init__(self, *, farfield: bool = False, assessor: Assessor | None = None) -> None:
        self.farfield = farfield
        self._assessor = assessor

    # ------------------------------------------------------------------ capabilities / check
    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            physics=frozenset({"fullwave_driven"}),
            ports=frozenset({"lumped", "wave"}),
            wave_port_mode="tl_probe",
            boundaries=frozenset({"pec", "pmc", "absorbing", "pml"}),
            dispersive_dielectric="single_frequency_fit",
            surface_roughness=False,
            sheet_conductors=True,
            excitations_per_run="one",
            farfield=self.farfield,
            platforms=frozenset({"windows", "linux"}),
        )

    def check(self, problem: EMProblem) -> tuple[Diagnostic, ...]:
        diags = list(check_problem(problem, farfield_enabled=self.farfield))
        if not any(d.severity == "error" for d in diags):
            try:
                diags.extend(
                    d
                    for d in build_scene(
                        problem, Options.from_problem(problem, farfield=self.farfield)
                    ).diagnostics
                    if d not in diags
                )
            except ValueError as exc:
                diags.append(
                    Diagnostic(
                        severity="error",
                        code="OPENEMS.TRANSLATION",
                        message=str(exc),
                        hint="Check port and conductor definitions against the structure.",
                    )
                )
        return tuple(diags)

    # --------------------------------------------------------------------------- prepare
    def prepare(self, problem: EMProblem, workdir: Path, tool: ToolInfo) -> PreparedRun:
        options = Options.from_problem(problem, farfield=self.farfield)
        scene = build_scene(problem, options)
        workdir.mkdir(parents=True, exist_ok=True)
        env: dict[str, str] = {}
        if tool.runner == "local" and tool.path:
            home = discovery.find_home(Path(tool.path))
            if home is not None:
                env[discovery.ENV_DLL] = str(home)
        commands: list[ExternalCommand] = []
        files: list[str] = []
        outputs = ["ports.npz", "run.json"]
        if scene.data["nf2ff"]:
            outputs.append("farfield.npz")
        for idx, port in enumerate(problem.ports):
            if not port.excite:
                continue
            rel = f"exc_{_safe_name(port.name)}"
            run_dir = workdir / rel
            run_dir.mkdir(parents=True, exist_ok=True)
            data = dict(scene.data)
            data["excited"] = idx + 1
            (run_dir / "scene.json").write_text(
                json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8"
            )
            (run_dir / "sim.py").write_text(SIM_SCRIPT, encoding="utf-8")
            files.extend((f"{rel}/scene.json", f"{rel}/sim.py"))
            commands.append(
                ExternalCommand(
                    program=self.tool,
                    args=("sim.py",),
                    workdir=run_dir,
                    env=env,
                    timeout_s=problem.convergence.max_wall_s,
                    progress_parser="openems",
                    outputs=tuple(outputs),
                )
            )
        stats = _json({**scene.stats, "adapter": self.name, "options": options.__dict__})
        return PreparedRun(
            workdir=workdir, commands=tuple(commands), mesh_stats=stats, files=tuple(files)
        )

    # --------------------------------------------------------------------------- collect
    def collect(
        self, problem: EMProblem, run: PreparedRun, results: Sequence[ExternalResult]
    ) -> EMSolveOutput:
        warnings: list[Diagnostic] = []
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
                "OPENEMS.RESULT_COUNT",
                f"{len(results)} results for {len(run.commands)} commands",
                "Pass one ExternalResult per prepared command, in order.",
            )
        raws: list[RunRaw] = []
        conv_rows: list[tuple[str, int, float]] = []
        walls: list[float] = []
        for cmd, res in zip(run.commands, results, strict=True):
            tag = cmd.workdir.name
            if res.returncode != 0:
                return fail(
                    "OPENEMS.RUN_FAILED",
                    f"{tag}: openEMS script exited with code {res.returncode}",
                    "See logs in the job directory; stderr tail: " + res.stderr_tail[-300:],
                )
            raw_path = cmd.workdir / "ports.npz"
            if not raw_path.is_file():
                return fail(
                    "OPENEMS.NO_OUTPUT",
                    f"{tag}: ports.npz not found",
                    "The script finished without writing results; check the run log.",
                )
            raws.append(load_run(raw_path))
            walls.append(res.wall_s)
            conv_rows.extend((tag, step, db) for step, db in parse_convergence(res.stdout_tail))
            meta = cmd.workdir / "run.json"
            if meta.is_file():
                version = str(json.loads(meta.read_text(encoding="utf-8")).get("openems_version"))
            elif res.tool_version:
                version = res.tool_version
        freqs = raws[0].freqs
        for r in raws[1:]:
            if r.freqs.shape != freqs.shape or not np.allclose(r.freqs, freqs, rtol=0, atol=1e-3):
                return fail(
                    "OPENEMS.FREQ_MISMATCH",
                    "excitation runs used different frequency grids",
                    "Re-run all excitations from the same prepared problem.",
                )

        ports = list(problem.ports)
        numbers = list(range(1, len(ports) + 1))
        z0_rows: list[float] = []
        for nr, port in zip(numbers, ports, strict=True):
            fixed = port.z0 if not isinstance(port, EMWavePort) or port.z0 is not None else None
            z0_rows.append(
                float(fixed) if fixed is not None else line_impedance_reference(raws, nr)
            )
        z0 = np.array(z0_rows, dtype=np.float64)
        try:
            s, notes = assemble_s(raws, numbers, z0)
        except ValueError as exc:
            return fail("OPENEMS.S_UNDETERMINED", str(exc), "Set excite=True on all ports.")
        for n in notes:
            warnings.append(
                Diagnostic(severity="warning", code="OPENEMS.RECIPROCITY_FILL", message=n)
            )
        names = tuple(p.name for p in ports)

        quality = self._assess(freqs, s, z0)
        touch_ref = self._write_files(problem, run, freqs, s, z0, names, quality, walls, version)
        network = NetworkResult(
            freqs=Floats(freqs),
            s=NDArray(s),
            z0=NDArray(z0.astype(np.complex128)),
            port_names=names,
            port_map=tuple(self._port_map(problem)),
            quality=quality,
            touchstone=touch_ref,
            provenance=SolverProvenance(
                solver=self.name, solver_version=version, inputs_hash=problem.key()
            ),
        )
        return EMSolveOutput(
            network=network,
            port_impedance=self._port_impedance(raws, numbers, names, freqs)
            if problem.outputs.port_impedance
            else None,
            farfield=self._farfield(problem, run),
            fields=(),
            convergence_log=self._convergence_table(conv_rows),
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
        sv_max = float(sv.max())
        recip = float(np.max(np.abs(s - np.transpose(s, (0, 2, 1)))))
        return QualityReport(
            verdict="fail",
            pqm=0.0,
            rqm=0.0,
            cqm=0.0,
            max_singular_value=sv_max,
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
                    n
                    for cid in p.signal_conductors
                    if (n := by_id[cid].net) is not None
                    if cid in by_id
                )
                box = st.bbox
                x = box.xmin if p.face == "xmin" else box.xmax if p.face == "xmax" else None
                y = box.ymin if p.face == "ymin" else box.ymax if p.face == "ymax" else None
                loc = (
                    x if x is not None else 0.5 * (p.rect[0] + p.rect[1]),
                    y if y is not None else 0.5 * (p.rect[0] + p.rect[1]),
                )
                out.append(PortMapEntry(port=p.name, nets=nets, terminals=(p.face,), location=loc))
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
        walls: list[float],
        version: str,
    ) -> ArtifactRef:
        out_dir = run.workdir / "out"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(problem.name)
        n = len(names)
        ts_path = out_dir / f"{stem}.s{n}p"
        write_touchstone(
            ts_path,
            freqs,
            s,
            [float(z) for z in z0],
            names,
            comments=(
                f"Open_Analysis openEMS {version}; problem {problem.name}",
                f"EMProblem key {problem.key()}",
            ),
        )
        sha, size = _sha256_file(ts_path)
        meta: dict[str, Any] = {
            "schema": "oa-openems-result/1",
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
            "freq_hz": [float(freqs[0]), float(freqs[-1])],
            "n_freqs": int(freqs.size),
            "mesh_stats": run.mesh_stats,
            "wall_s_per_run": walls,
            "quality": quality.model_dump(mode="json"),
            "stackup_provenance": problem.structure.provenance.model_dump(mode="json"),
        }
        (out_dir / f"{stem}.json").write_text(
            json.dumps(_json(meta), indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        return ArtifactRef(
            sha256=sha, media_type="application/x-touchstone", name=ts_path.name, size=size
        )

    def _port_impedance(
        self, raws: list[RunRaw], numbers: list[int], names: tuple[str, ...], freqs: np.ndarray
    ) -> TableResult:
        cols = [ColumnSpec(name="freq_hz", dtype="f8", unit="Hz")]
        for nm in names:
            cols.append(ColumnSpec(name=f"{nm}.z_re", dtype="f8", unit="ohm"))
            cols.append(ColumnSpec(name=f"{nm}.z_im", dtype="f8", unit="ohm"))
        rows: list[tuple[JsonValue, ...]] = []
        for k in _decimate(freqs.size, _MAX_TABLE_ROWS):
            row: list[JsonValue] = [float(freqs[k])]
            for nr in numbers:
                z = raws[0].ports[nr].z_line[k]
                row.extend((float(z.real), float(z.imag)))
            rows.append(tuple(row))
        return TableResult(columns=tuple(cols), inline=tuple(rows))

    def _convergence_table(self, rows: list[tuple[str, int, float]]) -> TableResult | None:
        if not rows:
            return None
        cols = (
            ColumnSpec(name="run", dtype="str", role="id"),
            ColumnSpec(name="timestep", dtype="i8"),
            ColumnSpec(name="energy_db", dtype="f8", unit="dB"),
        )
        keep = _decimate(len(rows), 200)
        inline: tuple[tuple[JsonValue, ...], ...] = tuple(
            (rows[i][0], rows[i][1], rows[i][2]) for i in keep
        )
        return TableResult(columns=cols, inline=inline)

    def _farfield(self, problem: EMProblem, run: PreparedRun) -> FarFieldResult | None:
        if problem.outputs.farfield is None or not self.farfield:
            return None
        for cmd in run.commands:
            path = cmd.workdir / "farfield.npz"
            if path.is_file():
                with np.load(path) as d:
                    e_th = np.array(d["e_theta"], dtype=np.complex128)
                    e_ph = np.array(d["e_phi"], dtype=np.complex128)
                    return FarFieldResult(
                        freqs=Floats(d["freqs"]),
                        theta=Floats(d["theta"]),
                        phi=Floats(d["phi"]),
                        e_theta=NDArray(e_th),
                        e_phi=NDArray(e_ph),
                        p_rad=Floats(np.atleast_1d(d["p_rad"]).astype(float)),
                        directivity_max=Floats(np.atleast_1d(d["dmax"]).astype(float)),
                    )
        return None


def prepared_scene(problem: EMProblem, *, farfield: bool = False) -> Scene:
    """The translated scene (no files written); handy for inspection and tests."""
    return build_scene(problem, Options.from_problem(problem, farfield=farfield))
