# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``impedance.profile`` analysis: Z0 / Zdiff, eps_eff and delay along nets and pairs.

Flow (research 02 section 5.3):

- ``plan`` samples every selected net (``sample_net``), locates width / coupling / reference
  transitions by bisection, merges samples into runs, classifies the cause of each discontinuity
  and deduplicates the sections by ``CrossSection.key()``. It emits one ``layout`` task (echoes the
  runs and targets to ``reduce``) and one ``solve`` task per batch of unique keys.
- ``run_task`` (solve) sends the batch to the ``impedance`` service once (``solve_many``).
- ``reduce`` expands the solved sections back onto the runs, builds one ``ProfileResult`` per net,
  the deviation / discontinuity / section / layer-delay tables and the tuning-profile suggestion.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import numpy as np
from pydantic import Field

from oa_framework.analysis import Plan, PlanContext, RunContext, Task, TaskOutput
from oa_impedance.analysis import TOLERANCE_BASIS, _ulid
from oa_impedance.profile.causes import KeyFeature
from oa_impedance.profile.layout import NetLayout, Run, build_runs
from oa_impedance.profile.result import ImpedanceProfileResult, profile_stats
from oa_impedance.profile.tuning import build_tuning_profiles
from oa_impedance.service import SOLVER_VERSION, ImpedanceServiceImpl
from oa_model._base import OAModel
from oa_model.arrays import Floats, NDArray
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.setup.intent import NetSelector
from oa_model.xsection import CrossSection
from oa_results.bundle import ArtifactOnly, Provenance, ResultBundle, ResultItem
from oa_results.container import ValidityFlag
from oa_results.fields import FieldData
from oa_results.profile import ProfileEvent
from oa_results.scalars import Scalar, ScalarSet
from oa_results.tables import ColumnSpec, TableResult

__all__ = ["ImpedanceProfile", "ProfileConfig", "ProfilePayload"]

KIND = "impedance.profile"
MAX_TABLE_ROWS = 1000
_LOCAL = ImpedanceServiceImpl()
_EVENT_KIND = {
    "layer_change": "layer_change",
    "ref_change": "ref_change",
    "plane_edge": "plane_edge",
    "neckdown": "neckdown",
}


class ProfileConfig(OAModel):
    nets: NetSelector = Field(
        default_factory=NetSelector,
        description="Nets to profile. Empty = every signal net with a target impedance or in a "
        "differential pair. Nets of a pair are profiled together as one pair.",
    )
    ds: float | None = Field(
        default=None,
        gt=0,
        description="Sampling step along the net [m]; None = min(0.5 mm, 2 h) per segment.",
    )
    freqs: tuple[float, ...] = Field(
        default=(0.0,),
        description="Frequencies [Hz], ascending; the first one fills the plain quantities, "
        "others add 'name@f' quantities. 0 = static (lossless).",
    )
    method: Literal["auto", "closed_form", "bem", "fem"] = Field(
        default="auto", description="Solver: auto = closed form where valid, else BEM."
    )
    tolerance: float | None = Field(
        default=None,
        gt=0,
        lt=1,
        description="Impedance tolerance [fraction]; None = from the design intent (default 0.1).",
    )
    refine_tol: float = Field(
        default=25e-6,
        gt=0,
        description="Accuracy of the located transition positions [m].",
    )
    batch_size: int = Field(
        default=8, ge=1, description="Unique cross-sections per solve task [count]."
    )


class SolveItem(OAModel):
    key: str = Field(description="CrossSection.key().")
    xsection: CrossSection = Field(description="Section to solve (CrossSection).")


class ProfilePayload(OAModel):
    mode: Literal["layout", "solve"] = Field(description="Task type.")
    layouts: tuple[NetLayout, ...] = Field(default=(), description="Layouts (layout task).")
    features: dict[str, KeyFeature] = Field(default_factory=dict, description="Features by key.")
    notes: tuple[str, ...] = Field(default=(), description="Nets skipped by the sampler.")
    items: tuple[SolveItem, ...] = Field(default=(), description="Sections (solve task).")
    freqs: tuple[float, ...] = Field(default=(0.0,), description="Frequencies [Hz].")
    method: Literal["auto", "closed_form", "bem", "fem"] = Field(
        default="auto", description="Solver selection."
    )


def _service(ctx: PlanContext | RunContext) -> Any:
    svc = ctx.services()
    return svc.get("impedance") if svc.has("impedance") else _LOCAL


def _select(board: Board, intent: Any, sel: NetSelector) -> list[str]:
    idx = intent.index()
    if not any((sel.names, sel.regex, sel.net_classes, sel.diff_pairs, sel.kinds)):
        return sorted(
            n
            for n, i in idx.items()
            if i.kind not in ("power", "ground")
            and (i.target_z_se is not None or i.target_z_diff is not None or i.diff_pair)
        )
    rx = [re.compile(r) for r in sel.regex]
    pairs = {d.id: d for d in board.diff_pairs}
    chosen: set[str] = set()
    for n in board.nets:
        k = idx[n.id].kind if n.id in idx else n.kind
        if (
            any(fnmatch.fnmatchcase(n.id, g) for g in sel.names)
            or any(r.fullmatch(n.id) for r in rx)
            or (n.net_class is not None and n.net_class in sel.net_classes)
            or k in sel.kinds
        ):
            chosen.add(n.id)
    for pid in sel.diff_pairs:
        if pid in pairs:
            chosen.update((pairs[pid].p_net, pairs[pid].n_net))
    return sorted(n for n in chosen if not any(fnmatch.fnmatchcase(n, g) for g in sel.exclude))


def _extract(res: Any) -> dict[str, Any]:
    p = res.params
    zc = p.zc_matrix.array
    eps, dly, loss = p.eps_eff_modal.array, p.delay_per_m.array, p.loss_db_per_m.array
    n = eps.shape[1]
    out: dict[str, Any] = {
        "n": n,
        "z_se": (p.z0.array if p.z0 is not None else np.abs(zc[:, 0, 0])).tolist(),
        "eps_se": eps[:, 0].tolist(),
        "delay_se": dly[:, 0].tolist(),
        "loss_se": loss[:, 0].tolist(),
        "method": res.method,
        "model": res.model_name,
        "validity": res.validity,
    }
    if n >= 2:
        zd = (
            p.z_diff.array
            if p.z_diff is not None
            else (zc[:, 0, 0] + zc[:, 1, 1] - zc[:, 0, 1] - zc[:, 1, 0]).real
        )
        out.update(
            z_diff=zd.tolist(),
            eps_diff=eps[:, 1].tolist(),
            delay_diff=dly[:, 1].tolist(),
            loss_diff=loss[:, 1].tolist(),
        )
    return out


def _vals(v: dict[str, Any], pair: bool) -> dict[str, list[float]]:
    """Z, eps_eff, delay and loss per frequency for a section in single or pair mode."""
    if pair and v["n"] >= 2:
        return {"z": v["z_diff"], "eps": v["eps_diff"], "d": v["delay_diff"], "l": v["loss_diff"]}
    if pair:  # uncoupled leg of a pair: two independent lines
        return {
            "z": [2.0 * z for z in v["z_se"]],
            "eps": v["eps_se"],
            "d": v["delay_se"],
            "l": [2.0 * a for a in v["loss_se"]],
        }
    return {"z": v["z_se"], "eps": v["eps_se"], "d": v["delay_se"], "l": v["loss_se"]}


class ImpedanceProfile:
    kind: ClassVar[str] = KIND
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "Impedance and delay profile along nets"
    Config: ClassVar[type[OAModel]] = ProfileConfig
    TaskPayload: ClassVar[type[OAModel]] = ProfilePayload

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        assert isinstance(cfg, ProfileConfig)
        out: list[Diagnostic] = []
        if list(cfg.freqs) != sorted(cfg.freqs) or any(f < 0 for f in cfg.freqs) or not cfg.freqs:
            out.append(
                Diagnostic(
                    severity="error",
                    code="IMPEDANCE.BAD_FREQS",
                    message="freqs must be a non-empty ascending list >= 0",
                    path="/config/freqs",
                    hint="Sort the frequency list and remove negative values.",
                )
            )
        if not _select(ctx.board, ctx.intent, cfg.nets):
            out.append(
                Diagnostic(
                    severity="warning",
                    code="IMPEDANCE.NO_NETS",
                    message="the net selection matches no net",
                    path="/config/nets",
                    hint="Select nets by name, net class or diff pair, or give targets in a "
                    "net class name such as '50ohm'.",
                )
            )
        return tuple(out)

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, ProfileConfig)
        geo = ctx.geometry()
        idx = ctx.intent.index()
        board = ctx.board
        classes = {n.id: n.net_class for n in board.nets}
        pairs = {d.id: d for d in board.diff_pairs}
        done: set[str] = set()
        xs_store: dict[str, CrossSection] = {}
        feat_store: dict[str, KeyFeature] = {}
        layouts: list[NetLayout] = []
        notes: list[str] = []
        for name in _select(board, ctx.intent, cfg.nets):
            if name in done:
                continue
            pid = idx[name].diff_pair if name in idx else None
            pair = pairs.get(pid) if pid else None
            nets = (pair.p_net, pair.n_net) if pair is not None else (name,)
            done.update(nets)
            runs = build_runs(
                geo,
                nets,
                ds=cfg.ds,
                refine_tol=cfg.refine_tol,
                xs_store=xs_store,
                feat_store=feat_store,
            )
            if not runs:
                notes.append(" / ".join(nets))
                continue
            ni = idx.get(nets[0])
            target = None
            if ni is not None:
                target = ni.target_z_diff if len(nets) == 2 else ni.target_z_se
            tol = cfg.tolerance or (ni.z_tolerance if ni and ni.z_tolerance else 0.1)
            layouts.append(
                NetLayout(
                    name="/".join(nets),
                    nets=nets,
                    net_class=classes.get(nets[0]),
                    target=target,
                    tolerance=tol,
                    runs=runs,
                )
            )
        used = sorted({r.key for lay in layouts for r in lay.runs})
        tasks = [
            Task(
                id="layout",
                analysis=KIND,
                payload=ProfilePayload(
                    mode="layout",
                    layouts=tuple(layouts),
                    features={k: feat_store[k] for k in used},
                    notes=tuple(notes),
                ).model_dump(mode="json"),
                cache=False,
            )
        ]
        for i in range(0, len(used), cfg.batch_size):
            keys = used[i : i + cfg.batch_size]
            tag = hashlib.sha256("".join(keys).encode()).hexdigest()[:12]
            tasks.append(
                Task(
                    id=f"solve:{tag}",
                    analysis=KIND,
                    payload=ProfilePayload(
                        mode="solve",
                        items=tuple(SolveItem(key=k, xsection=xs_store[k]) for k in keys),
                        freqs=cfg.freqs,
                        method=cfg.method,
                    ).model_dump(mode="json"),
                )
            )
        return Plan(tasks=tuple(tasks))

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, ProfilePayload)
        if payload.mode == "layout":
            return TaskOutput(
                task_id=ctx.task.id,
                data={"payload": payload.model_dump(mode="json")},
            )
        t0 = time.perf_counter()
        results = _service(ctx).solve_many(
            [it.xsection for it in payload.items], payload.freqs, method=payload.method
        )
        return TaskOutput(
            task_id=ctx.task.id,
            data={
                "values": {
                    it.key: _extract(r) for it, r in zip(payload.items, results, strict=True)
                },
                "elapsed_s": time.perf_counter() - t0,
            },
        )

    # ------------------------------------------------------------------ reduce
    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        assert isinstance(cfg, ProfileConfig)
        t0 = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        lay = ProfilePayload.model_validate(outputs["layout"].data["payload"])
        values: dict[str, dict[str, Any]] = {}
        solve_s = 0.0
        for tid, o in sorted(outputs.items()):
            if tid != "layout":
                values.update(o.data["values"])  # type: ignore[arg-type]
                solve_s += float(o.data.get("elapsed_s", 0.0))  # type: ignore[arg-type]
        feats = lay.features
        n_methods: dict[str, int] = {}
        worst_validity = "in_range"
        rank = {"in_range": 0, "extrapolated": 1, "invalid": 2}
        for v in values.values():
            n_methods[v["method"]] = n_methods.get(v["method"], 0) + 1
            if rank[v["validity"]] > rank[worst_validity]:
                worst_validity = v["validity"]
        any_bem = any(m != "closed_form" for m in n_methods)
        cls: Literal["validated", "screening"] = "validated" if any_bem else "screening"
        accuracy = "2d_field" if any_bem else "closed_form"
        basis = TOLERANCE_BASIS["bem" if any_bem else "closed_form"]
        flags = (
            ValidityFlag(
                code="accuracy",
                ok=worst_validity == "in_range",
                message=f"accuracy_class={accuracy}; methods="
                + ",".join(f"{k}:{n}" for k, n in sorted(n_methods.items()))
                + f"; validity={worst_validity}; tolerance_basis={basis}",
            ),
        )

        items: list[ResultItem] = []
        dev_rows: list[tuple[Any, ...]] = []
        seg_rows: list[tuple[Any, ...]] = []
        pieces: list[tuple[str, str, str, str, float, float]] = []
        key_len: dict[str, float] = {}
        key_runs: dict[str, int] = {}
        key_layer: dict[str, str] = {}
        n_nodes = 0
        for nl in lay.layouts:
            res_item, row, segs, pcs = self._net(nl, values, feats, cfg.freqs, cls, flags)
            items.append(res_item)
            dev_rows.append(row)
            seg_rows += segs
            pieces += pcs
            for r in nl.runs:
                key_len[r.key] = key_len.get(r.key, 0.0) + (r.s1 - r.s0)
                key_runs[r.key] = key_runs.get(r.key, 0) + 1
                key_layer[r.key] = r.layer
                n_nodes += 1

        diags: list[Diagnostic] = []
        if lay.notes:
            diags.append(
                Diagnostic(
                    severity="info",
                    code="IMPEDANCE.NO_ROUTING",
                    message="no routed copper to sample on: " + ", ".join(lay.notes[:10]),
                    hint="The nets have no tracks (pads or vias only).",
                )
            )
        if worst_validity != "in_range":
            bad = sorted(k for k, v in values.items() if v["validity"] != "in_range")
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="RESULT.INVALID_INPUT_RANGE"
                    if worst_validity == "invalid"
                    else "IMPEDANCE.EXTRAPOLATED",
                    message=f"{len(bad)} cross-section(s) are outside the validated range of "
                    "their method",
                    hint="Re-run with method='bem'.",
                )
            )
        trunc = len(seg_rows) > MAX_TABLE_ROWS
        if trunc:
            seg_rows.sort(key=lambda r: -abs(r[8] or 0.0))
            seg_rows = sorted(seg_rows[:MAX_TABLE_ROWS], key=lambda r: (r[0], r[1]))
            diags.append(
                Diagnostic(
                    severity="warning",
                    code="RESULT.TRUNCATED",
                    message=f"discontinuity table cut to the {MAX_TABLE_ROWS} largest deviations",
                    hint="Select fewer nets.",
                )
            )

        items += [
            ResultItem(
                id="deviation",
                title="Impedance versus target, per net",
                kind="table",
                data=TableResult(
                    columns=_DEV_COLS,
                    inline=tuple(dev_rows),
                    accuracy_class=cls,
                    validity_flags=flags,
                ),
            ),
            ResultItem(
                id="discontinuities",
                title="Discontinuity segments and causes",
                kind="table",
                data=TableResult(
                    columns=_SEG_COLS,
                    inline=tuple(seg_rows),
                    accuracy_class=cls,
                    validity_flags=flags,
                ),
            ),
        ]
        sec_rows = []
        for k in sorted(key_len):
            v = values[k]
            f = feats[k]
            sec_rows.append(
                (
                    k,
                    f.topology,
                    key_layer[k],
                    f.n_signals,
                    v["z_se"][0],
                    v["z_diff"][0] if "z_diff" in v else None,
                    v["eps_se"][0],
                    v["method"],
                    v["validity"],
                    key_runs[k],
                    key_len[k],
                )
            )
        items.append(
            ResultItem(
                id="sections",
                title="Unique cross-sections solved",
                kind="table",
                data=TableResult(
                    columns=_SEC_COLS,
                    inline=tuple(sec_rows[:MAX_TABLE_ROWS]),
                    accuracy_class=cls,
                    validity_flags=flags,
                ),
            )
        )
        tuning = build_tuning_profiles(pieces, board_hash=_board_hash(ctx), accuracy_class=accuracy)
        items.append(
            ResultItem(
                id="layer_delays",
                title="Field-solved delay per layer (tuning-profile suggestion)",
                kind="table",
                data=TableResult(
                    columns=_DLY_COLS,
                    inline=tuple(
                        (
                            p.name,
                            p.mode,
                            la.layer,
                            la.delay_ps_per_mm,
                            la.delay_min_ps_per_mm,
                            la.delay_max_ps_per_mm,
                            la.eps_eff,
                            la.length_m,
                            la.n_nets,
                        )
                        for p in tuning.profiles
                        for la in p.layers
                    ),
                    accuracy_class=cls,
                    validity_flags=flags,
                ),
            )
        )
        ref = ctx.artifacts().put_bytes(
            tuning.to_json().encode(),
            name="kicad_tuning_profile.json",
            media_type="application/json",
        )
        items.append(
            ResultItem(
                id="tuning_profile",
                title="KiCad time-domain tuning profile suggestion (JSON, not applied)",
                kind="artifact",
                data=ArtifactOnly(artifact=ref, accuracy_class=cls, validity_flags=flags),
            )
        )
        n_fail = sum(1 for r in dev_rows if r[13] == "fail")
        scalars = ScalarSet(
            values=(
                Scalar(name="nets", value=float(len(lay.layouts)), unit=""),
                Scalar(name="samples_runs", value=float(n_nodes), unit=""),
                Scalar(name="unique_sections", value=float(len(values)), unit=""),
                Scalar(name="solve_seconds", value=solve_s, unit="s"),
                Scalar(
                    name="nets_out_of_tolerance",
                    value=float(n_fail),
                    unit="",
                    status="fail" if n_fail else "pass",
                ),
            ),
            accuracy_class=cls,
            validity_flags=flags,
        )
        items.append(ResultItem(id="stats", title="Run statistics", kind="scalars", data=scalars))

        summary = (
            f"{len(lay.layouts)} net(s)/pair(s), {n_nodes} runs, {len(values)} unique "
            f"cross-section(s) ({', '.join(f'{k}:{n}' for k, n in sorted(n_methods.items()))}); "
            f"{n_fail} out of tolerance; accuracy {accuracy}."
        )
        cfg_json = cfg.model_dump_json()
        board_hash = _board_hash(ctx)
        bundle = ResultBundle(
            id=_ulid(),
            status="ok" if worst_validity == "in_range" else "warning",
            summary=summary,
            items=tuple(items),
            diagnostics=tuple(diags),
            provenance=Provenance(
                analysis=KIND,
                analysis_version=self.version,
                board_hash=board_hash,
                physics_hash=None,
                setup_hash="sha256:" + hashlib.sha256(cfg_json.encode()).hexdigest(),
                config=cfg.model_dump(mode="json"),
                solver=",".join(sorted(n_methods)) or None,
                solver_version=SOLVER_VERSION,
                started_utc=t0,
                finished_utc=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                inputs_hash="sha256:"
                + hashlib.sha256((",".join(sorted(values)) + cfg_json).encode()).hexdigest(),
            ),
        )
        return bundle

    def _net(
        self,
        nl: NetLayout,
        values: dict[str, dict[str, Any]],
        feats: dict[str, KeyFeature],
        freqs: tuple[float, ...],
        cls: str,
        flags: tuple[ValidityFlag, ...],
    ) -> tuple[ResultItem, tuple[Any, ...], list[tuple[Any, ...]], list[Any]]:
        pair = nl.pair
        nf = len(freqs)
        s: list[float] = []
        cols: dict[str, list[float]] = {k: [] for k in ("z", "eps", "d", "l")}
        extra: dict[tuple[str, int], list[float]] = {}
        zname = "Zdiff" if pair else "Z0"

        def emit(r: Run, s_at: float) -> None:
            v = _vals(values[r.key], pair)
            s.append(s_at)
            for k in cols:
                cols[k].append(v[k][0])
            for j in range(1, nf):
                for k in ("z", "eps", "l"):
                    extra.setdefault((k, j), []).append(v[k][j])

        prev: Run | None = None
        for r in nl.runs:
            if prev is not None and abs(r.s0 - prev.s1) > 1e-7:
                emit(prev, prev.s1)
            emit(r, r.s0)
            prev = r
        assert prev is not None
        emit(prev, prev.s1)

        sv = np.asarray(s)
        ln = np.maximum(np.diff(sv, append=sv[-1]), 0.0)
        delay_cum = np.concatenate(([0.0], np.cumsum(np.asarray(cols["d"]) * ln)))[:-1]
        qs = [
            FieldData(
                name=zname, unit="ohm", location="node", values=NDArray(np.asarray(cols["z"]))
            ),
            FieldData(
                name="eps_eff", unit="", location="node", values=NDArray(np.asarray(cols["eps"]))
            ),
            FieldData(
                name="delay_per_m",
                unit="s/m",
                location="node",
                values=NDArray(np.asarray(cols["d"])),
            ),
            FieldData(name="delay_s", unit="s", location="node", values=NDArray(delay_cum)),
            FieldData(
                name="loss_db_per_m",
                unit="dB/m",
                location="node",
                values=NDArray(np.asarray(cols["l"])),
            ),
        ]
        if nl.target:
            dev_arr = (np.asarray(cols["z"]) - nl.target) / nl.target
            qs.append(
                FieldData(name="deviation", unit="", location="node", values=NDArray(dev_arr))
            )
        for (k, j), arr in sorted(extra.items()):
            nm = {"z": zname, "eps": "eps_eff", "l": "loss_db_per_m"}[k]
            unit = {"z": "ohm", "eps": "", "l": "dB/m"}[k]
            qs.append(
                FieldData(
                    name=f"{nm}@{freqs[j]:g}Hz",
                    unit=unit,
                    location="node",
                    values=NDArray(np.asarray(arr)),
                )
            )

        events: list[ProfileEvent] = []
        seg_rows: list[tuple[Any, ...]] = []
        pieces: list[Any] = []
        prev_z: float | None = None
        for r in nl.runs:
            v = _vals(values[r.key], pair)
            zr = v["z"][0]
            dev = (zr - nl.target) / nl.target if nl.target else None
            oot = dev is not None and abs(dev) > nl.tolerance
            if r.flagged:
                first = r.causes[0]
                step = f"{prev_z:.4g} -> " if prev_z is not None else ""
                events.append(
                    ProfileEvent(
                        kind=_EVENT_KIND.get(first, "other"),  # type: ignore[arg-type]
                        s=r.s0,
                        item=r.seg,
                        message=f"causes={','.join(r.causes)}; Z {step}{zr:.4g} ohm"
                        + (f" ({dev * 100:+.1f} % vs target)" if dev is not None else ""),
                    )
                )
            if r.flagged or oot:
                f = feats[r.key]
                seg_rows.append(
                    (
                        nl.name,
                        r.s0,
                        r.s1,
                        r.layer,
                        f.topology,
                        zr,
                        v["eps"][0],
                        v["d"][0],
                        None if dev is None else dev * 100.0,
                        ",".join(r.causes),
                        r.flagged,
                        oot,
                    )
                )
            pieces.append(
                (
                    nl.net_class or "default",
                    "diff" if pair else "single",
                    r.layer,
                    nl.name,
                    r.s1 - r.s0,
                    v["d"][0],
                )
            )
            prev_z = zr

        data = ImpedanceProfileResult(
            net=(nl.nets[0], nl.nets[1]) if pair else nl.nets[0],
            s=Floats(sv),
            quantities=tuple(qs),
            events=tuple(events),
            target=nl.target,
            tolerance=nl.tolerance if nl.target else None,
            accuracy_class=cls,  # type: ignore[arg-type]
            validity_flags=flags,
        )
        st = profile_stats(sv, np.asarray(cols["z"]), nl.target, nl.tolerance)
        devs = (
            [
                ((r.s0, (_vals(values[r.key], pair)["z"][0] - nl.target) / nl.target, r.causes))
                for r in nl.runs
            ]
            if nl.target
            else []
        )
        worst = max(devs, key=lambda d: abs(d[1])) if devs else None
        status = "info" if not nl.target else ("fail" if (st["oot_m"] or 0) > 0 else "pass")
        row = (
            nl.name,
            "diff" if pair else "single",
            nl.target,
            nl.tolerance if nl.target else None,
            st["z_min"],
            st["z_max"],
            st["z_mean"],
            st["length_m"],
            st["oot_m"],
            None if worst is None else worst[1] * 100.0,
            None if worst is None else worst[0],
            None if worst is None else (",".join(worst[2]) or "uniform"),
            len({r.key for r in nl.runs}),
            status,
        )
        item = ResultItem(
            id=f"profile:{nl.name}",
            title=f"{zname} profile of {nl.name}",
            kind="profile",
            data=data,
        )
        return item, row, seg_rows, pieces


def _board_hash(ctx: RunContext) -> str:
    try:
        return str(ctx.board().content_hash())
    except Exception:
        return ""


_DEV_COLS = (
    ColumnSpec(name="net", dtype="str", role="net"),
    ColumnSpec(name="kind", dtype="str"),
    ColumnSpec(name="target", dtype="f8", unit="ohm"),
    ColumnSpec(name="tolerance", dtype="f8", unit="1"),
    ColumnSpec(name="z_min", dtype="f8", unit="ohm"),
    ColumnSpec(name="z_max", dtype="f8", unit="ohm"),
    ColumnSpec(name="z_mean", dtype="f8", unit="ohm"),
    ColumnSpec(name="length", dtype="f8", unit="m"),
    ColumnSpec(name="out_of_tol_length", dtype="f8", unit="m"),
    ColumnSpec(name="worst_dev_pct", dtype="f8", unit="%"),
    ColumnSpec(name="worst_s", dtype="f8", unit="m"),
    ColumnSpec(name="worst_cause", dtype="str"),
    ColumnSpec(name="n_sections", dtype="i8"),
    ColumnSpec(name="status", dtype="str", role="status"),
)
_SEG_COLS = (
    ColumnSpec(name="net", dtype="str", role="net"),
    ColumnSpec(name="s_start", dtype="f8", unit="m"),
    ColumnSpec(name="s_end", dtype="f8", unit="m"),
    ColumnSpec(name="layer", dtype="str", role="layer"),
    ColumnSpec(name="topology", dtype="str"),
    ColumnSpec(name="z", dtype="f8", unit="ohm"),
    ColumnSpec(name="eps_eff", dtype="f8"),
    ColumnSpec(name="delay_per_m", dtype="f8", unit="s/m"),
    ColumnSpec(name="dev_pct", dtype="f8", unit="%"),
    ColumnSpec(name="causes", dtype="str"),
    ColumnSpec(name="discontinuity", dtype="bool"),
    ColumnSpec(name="out_of_tolerance", dtype="bool"),
)
_SEC_COLS = (
    ColumnSpec(name="key", dtype="str", role="id"),
    ColumnSpec(name="topology", dtype="str"),
    ColumnSpec(name="layer", dtype="str", role="layer"),
    ColumnSpec(name="n_signals", dtype="i8"),
    ColumnSpec(name="z0", dtype="f8", unit="ohm"),
    ColumnSpec(name="zdiff", dtype="f8", unit="ohm"),
    ColumnSpec(name="eps_eff", dtype="f8"),
    ColumnSpec(name="method", dtype="str"),
    ColumnSpec(name="validity", dtype="str"),
    ColumnSpec(name="n_runs", dtype="i8"),
    ColumnSpec(name="length", dtype="f8", unit="m"),
)
_DLY_COLS = (
    ColumnSpec(name="profile", dtype="str"),
    ColumnSpec(name="mode", dtype="str"),
    ColumnSpec(name="layer", dtype="str", role="layer"),
    ColumnSpec(name="delay", dtype="f8", unit="ps/mm"),
    ColumnSpec(name="delay_min", dtype="f8", unit="ps/mm"),
    ColumnSpec(name="delay_max", dtype="f8", unit="ps/mm"),
    ColumnSpec(name="eps_eff", dtype="f8"),
    ColumnSpec(name="length", dtype="f8", unit="m"),
    ColumnSpec(name="n_nets", dtype="i8"),
)
