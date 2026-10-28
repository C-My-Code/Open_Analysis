# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""The ``pi.dc`` analysis: DC IR drop on copper sheets coupled by via barrels (WP-204).

One task solves the whole rail (all layers and the return net are one linear system). The task
stores the field arrays as one ``.npz`` artifact and small JSON tables/scalars in
``TaskOutput.data``; ``reduce`` rebuilds the typed result items from both.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib
import io
from collections.abc import Mapping
from typing import Any, ClassVar

import numpy as np
from pydantic import JsonValue, ValidationError

from oa_framework.analysis import Plan, PlanContext, Resources, RunContext, Task, TaskOutput
from oa_model._base import OAModel
from oa_model.arrays import NDArray, Points
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_pi.dc.config import DcConfig
from oa_pi.dc.fem import solve_fem
from oa_pi.dc.network import build_network, check_setup
from oa_pi.dc.outputs import summarize
from oa_pi.errors import PiError
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.container import SolverProvenance, ValidityFlag
from oa_results.fields import FieldData, FieldPiece, FieldResult, TriMesh2D
from oa_results.scalars import Scalar, ScalarSet
from oa_results.tables import ColumnSpec, TableResult

__all__ = ["DcTaskPayload", "PiDcAnalysis"]

SOLVER = "oa-dc-fem-p1"
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ASSUMPTIONS = (
    "copper sheets at their stackup thickness and a uniform temperature",
    "zone copper exactly as filled in the board file",
    "component pads are ideal equipotential conductors",
    "via barrels are lumped resistors with the padstack plating thickness",
    "rail and return nets solved together",
)
TOLERANCE_REL = 0.005
TOLERANCE_BASIS = "golden:pi_dc analytic strip/annulus/via array + raster cross-check"


class DcTaskPayload(OAModel):
    config: dict[str, JsonValue]


def _diag_from(exc: PiError) -> Diagnostic:
    return Diagnostic(
        severity="error",
        code=exc.code,
        message=exc.message,
        path=getattr(exc, "path", None),
        hint=getattr(exc, "hint", None),
    )


def _ulid_like(seed: str) -> str:
    """Deterministic 26-character Crockford id (same inputs, same id; job service may replace)."""
    v = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:17], "big") >> 6
    return "".join(_CROCKFORD[(v >> (5 * i)) & 31] for i in range(25, -1, -1))


def _accuracy_extras() -> dict[str, Any]:
    """Keyword arguments for the structured ``Accuracy`` model once ``oa_results`` has it."""
    try:
        mod = importlib.import_module("oa_results.accuracy")
    except ImportError:
        return {}
    acc = mod.Accuracy(
        accuracy_class="2d_sheet",
        method=SOLVER,
        tolerance_rel=TOLERANCE_REL,
        tolerance_basis=TOLERANCE_BASIS,
        validity="in_range",
        converged=True,
        assumptions=ASSUMPTIONS,
    )
    return {"accuracy": acc} if "accuracy" in ResultItem.model_fields else {}


class PiDcAnalysis:
    kind: ClassVar[str] = "pi.dc"
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "DC IR drop"
    Config: ClassVar[type[OAModel]] = DcConfig
    TaskPayload: ClassVar[type[OAModel]] = DcTaskPayload

    # ---- setup -----------------------------------------------------------------------------
    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        assert isinstance(cfg, DcConfig)
        try:
            return tuple(check_setup(ctx.board, cfg))
        except PiError as exc:
            return (_diag_from(exc),)

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, DcConfig)
        n_cu = sum(1 for lay in ctx.board.stackup.layers if lay.kind == "conductor")
        return Plan(
            tasks=(
                Task(
                    id="solve",
                    analysis=self.kind,
                    payload={"config": cfg.model_dump(mode="json", by_alias=True)},
                    resources=Resources(memory_gb=1.0 + 0.5 * n_cu, cpus=1),
                ),
            )
        )

    # ---- solve -----------------------------------------------------------------------------
    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, DcTaskPayload)
        try:
            cfg = DcConfig.model_validate(payload.config)
        except ValidationError as exc:  # pragma: no cover - plan() validated it already
            raise PiError(str(exc), code="SETUP_INVALID", path="/config") from exc
        started = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        board: Board = ctx.board()
        net = build_network(board, ctx.geometry(), cfg)
        ctx.progress(0.1, "network extracted")
        ctx.check_cancelled()
        nets = {net.rail, net.ret}
        sol = solve_fem(
            net,
            tracks=[t for t in board.tracks if t.net in nets],
            progress=lambda f, m: ctx.progress(0.1 + 0.8 * f, m),
        )
        data, arrays = summarize(sol)
        buf = io.BytesIO()
        np.savez_compressed(buf, **arrays)
        ref = ctx.artifacts().put_bytes(
            buf.getvalue(), name="pi_dc_fields.npz", media_type="application/x-npz"
        )
        data["started_utc"] = started
        data["finished_utc"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ctx.progress(1.0, "done")
        return TaskOutput(
            task_id=ctx.task.id,
            data=data,
            artifacts=(ref,),
            diagnostics=tuple(net.diagnostics),
        )

    # ---- results ---------------------------------------------------------------------------
    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        assert isinstance(cfg, DcConfig)
        out = outputs["solve"]
        data: dict[str, Any] = dict(out.data)
        with np.load(ctx.artifacts().path(out.artifacts[0])) as npz:
            arrays = {k: npz[k] for k in npz.files}
        cfg_json = cfg.model_dump(mode="json", by_alias=True)
        setup_hash = "sha256:" + hashlib.sha256(repr(sorted(cfg_json.items())).encode()).hexdigest()
        board_hash = ctx.board().content_hash()
        inputs_hash = "sha256:" + hashlib.sha256((board_hash + setup_hash).encode()).hexdigest()
        meta = data["meta"]
        flag = ValidityFlag(
            code="accuracy.2d_sheet",
            ok=True,
            message=f"2d_sheet; {SOLVER}; tolerance_rel {TOLERANCE_REL}",
        )
        common: dict[str, Any] = {
            "accuracy_class": "validated",
            "validity_flags": (flag,),
            "provenance": SolverProvenance(
                solver=SOLVER, solver_version=str(meta.get("gmsh_version")), inputs_hash=inputs_hash
            ),
        }
        extras = _accuracy_extras()
        diags: list[Diagnostic] = list(out.diagnostics)
        diags.append(
            Diagnostic(
                severity="info",
                code="PI.ACCURACY",
                message=(
                    f"accuracy_class 2d_sheet, method {SOLVER}, tolerance_rel {TOLERANCE_REL} "
                    f"({TOLERANCE_BASIS}). Assumptions: " + "; ".join(ASSUMPTIONS)
                ),
            )
        )

        pieces = []
        for i, p in enumerate(data["pieces"]):
            mesh = TriMesh2D(
                nodes=Points(arrays[f"nodes_{i}"]),
                triangles=NDArray(arrays[f"tris_{i}"]),
                layer=p["layer"],
                z=p["z"],
            )
            j = arrays[f"j_{i}"]
            pieces.append(
                FieldPiece(
                    mesh=mesh,
                    net=p["net"],
                    fields=(
                        FieldData(
                            name="V", unit="V", location="node", values=NDArray(arrays[f"v_{i}"])
                        ),
                        FieldData(
                            name="dV",
                            unit="V",
                            location="node",
                            values=NDArray(arrays[f"dv_{i}"]),
                        ),
                        FieldData(
                            name="J_mag",
                            unit="A/m^2",
                            location="cell",
                            values=NDArray(j),
                            clim_hint=(0.0, float(np.max(j))) if len(j) else None,
                            log_scale=True,
                        ),
                    ),
                )
            )
        items: list[ResultItem] = [
            ResultItem(
                id="fields",
                title="Voltage, IR drop and current density per layer",
                kind="field",
                data=FieldResult(pieces=tuple(pieces), **common),
                **extras,
            )
        ]
        titles = {
            "loads": "Load voltages and drop",
            "vias": "Via barrel currents",
            "pins": "Pin currents",
        }
        for name, t in data["tables"].items():
            cols = tuple(
                ColumnSpec(name=c[0], dtype=c[1], unit=c[2], role=c[3]) for c in t["columns"]
            )
            items.append(
                ResultItem(
                    id=name,
                    title=titles[name],
                    kind="table",
                    data=TableResult(
                        columns=cols, inline=tuple(tuple(r) for r in t["rows"]), **common
                    ),
                    **extras,
                )
            )
            if t["truncated"]:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        code="RESULT_TRUNCATED",
                        message=f"table {name!r}: {t['n_rows']} rows, worst {len(t['rows'])} kept",
                        hint="Use the VTU/CSV export for the full table.",
                    )
                )
        items.append(
            ResultItem(
                id="rail",
                title="Rail summary",
                kind="scalars",
                data=ScalarSet(
                    values=tuple(
                        Scalar(
                            name=s["name"],
                            value=s["value"],
                            unit=s.get("unit"),
                            status=s.get("status"),
                            limit=s.get("limit"),
                        )
                        for s in data["scalars"]
                    ),
                    **common,
                ),
                **extras,
            )
        )

        vset = cfg.vrm.vset
        for r in data["tables"]["loads"]["rows"]:
            if r[9] == "fail":
                diags.append(
                    Diagnostic(
                        severity="warning",
                        code="PI.LOAD_DROP_EXCEEDED",
                        message=(
                            f"load {r[0]}: drop {r[5] * 1e3:.2f} mV ({r[6]:.2%} of {vset:g} V) "
                            f"exceeds the limit {r[7] * 1e3:.2f} mV"
                        ),
                        item=r[0],
                        location=(r[10], r[11]),
                        hint="Add copper or vias between the regulator and the load, or raise "
                        "the limit if the load tolerates it.",
                    )
                )
        if meta["n_via_segments_failing"]:
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="PI.VIA_CURRENT_DENSITY",
                    message=f"{meta['n_via_segments_failing']} via segment(s) exceed "
                    f"{cfg.limits.via_current_density:.3g} A/m^2",
                    hint="Add parallel vias near the worst via listed in the 'vias' table.",
                )
            )

        w = data["worst"]
        wl = w["load"]
        text = (
            f"Worst load drop {wl['drop'] * 1e3:.2f} mV ({wl['fraction']:.2%} of {vset:g} V, "
            f"limit {cfg.limits.drop_fraction:.1%}) at {wl['name']}: {wl['status'].upper()}."
        )
        if w["via"] is not None:
            text += f" Worst via {w['via']['current']:.3g} A: {w['via']['status'].upper()}."
        status = (
            "warning"
            if any(d.severity == "warning" and d.code.startswith("PI.") for d in diags)
            else "ok"
        )
        prov = Provenance(
            analysis=self.kind,
            analysis_version=self.version,
            board_hash=board_hash,
            physics_hash=None,
            setup_hash=setup_hash,
            config=cfg_json,
            solver=SOLVER,
            solver_version=f"{meta['solver']}; gmsh {meta['gmsh_version']}",
            started_utc=data["started_utc"],
            finished_utc=data["finished_utc"],
            inputs_hash=inputs_hash,
        )
        return ResultBundle(
            id=_ulid_like(inputs_hash),
            status=status,
            summary=text[:200],
            items=tuple(items),
            diagnostics=tuple(diags),
            provenance=prov,
        )
