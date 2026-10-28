# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Return-path rules RP-001, RP-002, RP-003, RP-004, RP-007."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np
from pydantic import Field

from oa_erc.base import ErcContext, ErcRule, RuleConfig, track_id
from oa_erc.refpath import Run, net_runs
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_results.violations import Violation

_HUBING = (
    "Hubing et al., Clemson CVEL PCB EMC expert system rules, EMC'97 "
    "(https://cecas.clemson.edu/cvel/pdf/EMCS97-444.pdf)",
    "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E2, E3, E4)",
    "docs/research/06-landscape-architecture.md section 3.2 (RP-xxx)",
)


class _CovConfig(RuleConfig):
    cov_ok: float = Field(
        default=0.8,
        ge=0.05,
        le=1.0,
        description="Fraction [0..1] of the footprint (W + 2*3*h) the primary reference plane "
        "must cover for the span to count as referenced.",
    )


class RP001Config(_CovConfig):
    max_gap_length: float = Field(
        default=5e-3,
        gt=0,
        description="Longest unreferenced run [m] reported as a crossing; longer runs are RP-007.",
    )
    min_crossing_length: float = Field(
        default=5e-5, ge=0, description="Shortest unreferenced run [m] that counts as a crossing."
    )


class SplitCrossing(ErcRule):
    id = "RP-001"
    title = "Trace crosses reference-plane split/gap"
    category = "return_path"
    aliases = ("E2",)
    default_severity = "error"
    references = _HUBING
    Config = RP001Config
    explanation = (
        "Return current follows the trace on the adjacent plane. Where the plane has a split, "
        "slot or gap the current must detour, which enlarges the loop and radiates (E2, Hubing "
        "et al. 1997). The rule reports a run of the trace shorter than `max_gap_length` that has "
        "no reference on the adjacent plane layer but is referenced on both sides. A gap between "
        "two different reference nets is reported by RP-003 instead."
    )

    def check(self, ctx: ErcContext, cfg: RP001Config) -> Iterable[Violation]:
        for net in ctx.nets_in_scope():
            for run in net_runs(ctx, net, cfg.cov_ok):
                if run.status != "void" or run.prev is None or run.next is None:
                    continue
                if run.length < cfg.min_crossing_length or run.length >= cfg.max_gap_length:
                    continue
                if not (run.prev.ref_set & run.next.ref_set):
                    continue
                loc = run.midpoint()
                yield self.violation(
                    message=f"Net {net} crosses a {run.length * 1e3:.2f} mm gap in its reference "
                    f"plane ({', '.join(sorted(run.prev.ref_set))}) on {run.layer}.",
                    location=loc,
                    nets=(net,),
                    layers=(run.layer,),
                    items=_items(run),
                    measured=run.length,
                    limit=cfg.min_crossing_length,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Reroute {net} so it does not cross the reference-plane gap, "
                        "or close the gap / add a stitching capacitor at the crossing.",
                        actions=(
                            TypedAction(
                                kind="reroute",
                                description=f"Reroute {net} around the plane gap.",
                                targets=(net, run.layer),
                                applies_via="board_edit",
                                confidence="high",
                            ),
                            TypedAction(
                                kind="change_layer",
                                description=f"Move {net} to a layer with a continuous plane.",
                                targets=(net,),
                                applies_via="board_edit",
                                confidence="medium",
                            ),
                        ),
                        evidence={
                            "gap_length_m": run.length,
                            "reference_nets": [*sorted(run.prev.ref_set)],
                        },
                    ),
                )


def _items(run: Run) -> tuple[str, ...]:
    return tuple(dict.fromkeys(track_id(s) for s in run.seg_ids))


class RP007Config(_CovConfig):
    min_length: float = Field(
        default=5e-3,
        gt=0,
        description="Shortest run [m] with no adjacent reference plane that is reported.",
    )


class Unreferenced(ErcRule):
    id = "RP-007"
    title = "Unreferenced high-speed segment"
    category = "return_path"
    aliases = ("E3",)
    default_severity = "error"
    references = _HUBING
    Config = RP007Config
    explanation = (
        "A high-speed trace needs a continuous plane on an adjacent plane layer. Where the "
        "adjacent layer has no reference copper (2-layer boards, a large void, a layer not "
        "used as a plane) the return current takes an uncontrolled path (E3). Runs shorter than "
        "`min_length` are left to RP-001 or ignored as pad escapes."
    )

    def check(self, ctx: ErcContext, cfg: RP007Config) -> Iterable[Violation]:
        for net in ctx.nets_in_scope():
            for run in net_runs(ctx, net, cfg.cov_ok):
                if run.status != "void" or run.length < cfg.min_length:
                    continue
                yield self.violation(
                    message=f"Net {net} runs {run.length * 1e3:.1f} mm on {run.layer} without a "
                    "reference plane on the adjacent plane layer.",
                    location=run.midpoint(),
                    nets=(net,),
                    layers=(run.layer,),
                    items=_items(run),
                    measured=run.length,
                    limit=cfg.min_length,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Add a reference plane under {net} or move it to a layer "
                        "adjacent to a continuous plane.",
                        actions=(
                            TypedAction(
                                kind="change_layer",
                                description=f"Move {net} to a layer next to a continuous plane.",
                                targets=(net, run.layer),
                                applies_via="board_edit",
                            ),
                            TypedAction(
                                kind="change_stackup",
                                description="Make an adjacent layer a plane under the trace.",
                                targets=(run.layer,),
                                applies_via="stackup",
                                confidence="low",
                            ),
                        ),
                        evidence={"unreferenced_length_m": run.length},
                    ),
                )


class RP002Config(_CovConfig):
    min_voids: int = Field(
        default=3, ge=2, description="Number of coverage dips in a cluster that triggers the rule."
    )
    cluster_gap: float = Field(
        default=10e-3,
        gt=0,
        description="Largest good-coverage stretch [m] between two dips of one cluster.",
    )


class VoidCluster(ErcRule):
    id = "RP-002"
    title = "Trace over voided reference (antipad/slot cluster)"
    category = "return_path"
    default_severity = "warning"
    references = (
        "docs/research/06-landscape-architecture.md section 3.2 (RP-002, void fraction threshold)",
        "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E2: antipads merged)",
    )
    Config = RP002Config
    explanation = (
        "A line of antipads (BGA fields, via arrays) perforates the plane under the trace so the "
        "return current meanders between holes. The rule finds dips in plane coverage under the "
        "footprint bounded by good coverage and reports clusters of at least `min_voids` dips."
    )

    def check(self, ctx: ErcContext, cfg: RP002Config) -> Iterable[Violation]:
        for net in ctx.nets_in_scope():
            runs = net_runs(ctx, net, cfg.cov_ok)
            dips = [
                r
                for r in runs
                if r.status == "partial"
                and r.prev is not None
                and r.next is not None
                and r.prev.status == "good"
                and r.next.status == "good"
            ]
            cluster: list[Run] = []
            clusters: list[list[Run]] = []
            for d in dips:
                if cluster and not (
                    cluster[-1].next is not None
                    and cluster[-1].next.next is d
                    and cluster[-1].next.length < cfg.cluster_gap
                ):
                    clusters.append(cluster)
                    cluster = []
                cluster.append(d)
            if cluster:
                clusters.append(cluster)
            for c in clusters:
                if len(c) < cfg.min_voids:
                    continue
                covs = [i.ref.coverage for r in c for i in r.intervals if i.ref is not None]
                worst = 1.0 - min(covs)
                pts = np.array([r.midpoint() for r in c])
                loc = (float(pts[:, 0].mean()), float(pts[:, 1].mean()))
                items = tuple(dict.fromkeys(i for r in c for i in _items(r)))
                yield self.violation(
                    message=f"Net {net} passes over {len(c)} voids in its reference plane on "
                    f"{c[0].layer} (worst void fraction {worst:.0%}).",
                    location=loc,
                    nets=(net,),
                    layers=(c[0].layer,),
                    items=items,
                    measured=worst,
                    limit=1.0 - cfg.cov_ok,
                    unit="fraction",
                    remediation=TypedRemediation(
                        summary=f"Reroute {net} between the antipads or add reference copper "
                        "under the trace.",
                        actions=(
                            TypedAction(
                                kind="reroute",
                                description=f"Reroute {net} away from the antipad cluster.",
                                targets=(net, c[0].layer),
                                applies_via="board_edit",
                            ),
                            TypedAction(
                                kind="change_spacing",
                                description="Increase via pitch or shrink antipads under it.",
                                targets=(c[0].layer,),
                                applies_via="board_edit",
                                confidence="low",
                            ),
                        ),
                        evidence={"n_voids": len(c), "worst_void_fraction": worst},
                    ),
                )


class RP003Config(_CovConfig):
    ignore_ground_to_ground: bool = Field(
        default=True,
        description="Do not report a change between two ground-kind nets (e.g. GND to AGND).",
    )


class ReferenceChange(ErcRule):
    id = "RP-003"
    title = "Reference net changes along the route"
    category = "return_path"
    default_severity = "warning"
    references = (
        "docs/research/06-landscape-architecture.md section 3.2 (RP-003)",
        "docs/research/07-quasistatic-thermal-emi.md section 3.1 (E10)",
    )
    Config = RP003Config
    explanation = (
        "If the plane under a trace changes from one net to another (GND to a power rail) the "
        "return current has to cross between the planes, which needs a stitching capacitor or "
        "via nearby. The rule compares the primary reference net of consecutive referenced runs, "
        "skipping any gap between them."
    )

    def check(self, ctx: ErcContext, cfg: RP003Config) -> Iterable[Violation]:
        for net in ctx.nets_in_scope():
            ni = ctx.net_intent(net)
            allowed = set(ni.reference_nets) if ni is not None else set()
            for run in net_runs(ctx, net, cfg.cov_ok):
                if run.status == "void" or run.next is None:
                    continue
                nxt: Run | None = run.next
                void = None
                if nxt is not None and nxt.status == "void":
                    void = nxt
                    nxt = nxt.next
                if nxt is None or nxt.status == "void":
                    continue
                sa, sb = run.ref_set, nxt.ref_set
                if not sa or not sb or (sa & sb):
                    continue
                a, b = sorted(sa)[0], sorted(sb)[0]
                if cfg.ignore_ground_to_ground and (
                    ctx.net_kind(a) == "ground" and ctx.net_kind(b) == "ground"
                ):
                    continue
                if allowed and a in allowed and b in allowed:
                    continue
                loc = void.midpoint() if void is not None else run.p1
                yield self.violation(
                    message=f"Reference plane of {net} changes from {a} to {b} on {run.layer}.",
                    location=loc,
                    nets=(net,),
                    layers=(run.layer,),
                    items=tuple(dict.fromkeys((*_items(run), *_items(nxt)))),
                    remediation=TypedRemediation(
                        summary=f"Keep {net} over one reference net or add a stitching capacitor "
                        f"between {a} and {b} at the change.",
                        actions=(
                            TypedAction(
                                kind="reroute",
                                description=f"Route {net} over a single continuous reference.",
                                targets=(net, run.layer),
                                applies_via="board_edit",
                            ),
                            TypedAction(
                                kind="add_stitching_vias",
                                description=f"Add a stitching capacitor between {a} and {b} "
                                "next to the crossing.",
                                targets=(a, b),
                                applies_via="board_edit",
                                confidence="medium",
                            ),
                        ),
                        evidence={"from_net": a, "to_net": b},
                    ),
                )


class RP004Config(RuleConfig):
    return_via_radius: float = Field(
        default=2.5e-3,
        gt=0,
        description="Largest distance [m] from the signal via to a reference-net via "
        "(E4: 1-2 mm or lambda/20 at the knee frequency).",
    )
    stitch_cap_radius: float = Field(
        default=5e-3,
        gt=0,
        description="Largest distance [m] to a capacitor bridging two different reference nets.",
    )
    require_cap_when_nets_differ: bool = Field(
        default=False,
        description="When the reference nets differ, accept only a stitching capacitor (strict "
        "E4). Default: a return via of either reference net or a capacitor is accepted.",
    )


class TransitionWithoutReturn(ErcRule):
    id = "RP-004"
    title = "Layer transition without return via / stitching cap"
    category = "return_path"
    aliases = ("E4",)
    default_severity = "error"
    references = _HUBING
    Config = RP004Config
    explanation = (
        "When a signal changes layers its return current must change reference planes too. If "
        "both references are the same net a nearby via of that net gives it a short path; if "
        "they are different nets a capacitor between them is needed (E4)."
    )

    def check(self, ctx: ErcContext, cfg: RP004Config) -> Iterable[Violation]:
        scope = set(ctx.nets_in_scope())
        rm = ctx.geometry().reference_map()
        pos = _via_positions(ctx)
        for vt in rm.via_transitions:
            if vt.net not in scope or vt.from_layer == vt.to_layer:
                continue
            refs = [r.net for r in (vt.from_ref, vt.to_ref) if r is not None]
            if not refs:
                continue
            d_via = _nearest(pos, refs, vt.position)
            d_cap = min((c.distance for c in vt.stitching_caps), default=math.inf)
            if d_cap <= cfg.stitch_cap_radius or d_via <= cfg.return_via_radius:
                continue
            if vt.ref_net_changes and cfg.require_cap_when_nets_differ:
                d, limit, what = d_cap, cfg.stitch_cap_radius, "a stitching capacitor"
                action = TypedAction(
                    kind="add_stitching_vias",
                    description=f"Add a capacitor between {refs[0]} and {refs[-1]} near the via.",
                    targets=tuple(dict.fromkeys(refs)),
                    parameter="distance",
                    current_value=d if math.isfinite(d) else None,
                    suggested_value=limit,
                    unit="m",
                    applies_via="board_edit",
                )
            else:
                d, limit, what = d_via, cfg.return_via_radius, "a return via"
                action = TypedAction(
                    kind="add_return_via",
                    description=f"Add a {refs[0]} via next to the signal via.",
                    targets=(vt.via, refs[0]),
                    parameter="distance",
                    current_value=d if math.isfinite(d) else None,
                    suggested_value=limit,
                    unit="m",
                    applies_via="board_edit",
                    confidence="high",
                )
            shown = d if math.isfinite(d) else 1.0
            yield self.violation(
                message=f"Via {vt.via} of {vt.net} ({vt.from_layer} to {vt.to_layer}) has no "
                f"return path ({what}) within {limit * 1e3:.1f} mm.",
                location=vt.position,
                nets=(vt.net,),
                layers=(vt.from_layer,),
                items=(vt.via,),
                measured=shown,
                limit=limit,
                unit="m",
                remediation=TypedRemediation(
                    summary=f"Add {what} within {limit * 1e3:.1f} mm of via {vt.via}.",
                    actions=(action,),
                    evidence={
                        "reference_nets": list(dict.fromkeys(refs)),
                        "reference_changes": vt.ref_net_changes,
                    },
                ),
            )


def _via_positions(ctx: ErcContext) -> dict[str, np.ndarray]:
    def build() -> dict[str, np.ndarray]:
        out: dict[str, list[tuple[float, float]]] = {}
        for v in ctx.board.vias:
            if v.net is not None:
                out.setdefault(v.net, []).append(v.position)
        return {k: np.array(p) for k, p in out.items()}

    result: dict[str, np.ndarray] = ctx.memo("via_pos", build)
    return result


def _nearest(pos: dict[str, np.ndarray], nets: list[str], p: tuple[float, float]) -> float:
    best = math.inf
    for n in dict.fromkeys(nets):
        arr = pos.get(n)
        if arr is None:
            continue
        d = np.hypot(arr[:, 0] - p[0], arr[:, 1] - p[1])
        d = d[d > 1e-9]
        if d.size:
            best = min(best, float(d.min()))
    return best
