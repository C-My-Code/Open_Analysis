# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Impedance rules IM-001, IM-002, IM-003, IM-005."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, Literal

import numpy as np
from pydantic import Field

from oa_erc.base import C0, ErcContext, ErcRule, RuleConfig, eps_eff, seg_point, track_id
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_results.container import as_np
from oa_results.violations import Violation

_REFS = (
    "docs/research/06-landscape-architecture.md section 3.2 (IM-001, IM-002, IM-003, IM-005)",
    "IPC-2141A (controlled impedance), +-10 % default tolerance (intent.z_tolerance)",
)


class IM001Config(RuleConfig):
    rel_tol: float = Field(
        default=0.02, ge=0, description="Allowed relative width deviation [fraction]."
    )
    skip_default_class: bool = Field(
        default=True, description="Ignore nets of the catch-all 'Default' net class."
    )
    skip_if_impedance_target: bool = Field(
        default=True,
        description="Skip nets with an impedance target: IM-002 is then authoritative (a width "
        "that differs from the class but still meets the target is deliberate).",
    )


class WidthMismatch(ErcRule):
    id = "IM-001"
    title = "Width differs from net-class target width"
    category = "impedance"
    default_severity = "warning"
    references = _REFS
    Config = IM001Config
    explanation = (
        "Net classes carry the track width that was chosen for the stackup. Segments with another "
        "width change the impedance without the solver being involved. Nets that have an "
        "impedance target are judged by IM-002 on the computed impedance instead."
    )

    def check(self, ctx: ErcContext, cfg: IM001Config) -> Iterable[Violation]:
        classes = {c.name: c for c in ctx.board.net_classes}
        net_cls = {n.id: n.net_class for n in ctx.board.nets}
        geo = ctx.geometry()
        for net in ctx.nets_in_scope():
            cname = net_cls.get(net)
            cls = classes.get(cname) if cname else None
            if cls is None or (cfg.skip_default_class and cname == "Default"):
                continue
            ni = ctx.net_intent(net)
            if (
                cfg.skip_if_impedance_target
                and ni is not None
                and (ni.target_z_se is not None or ni.target_z_diff is not None)
            ):
                continue
            want = cls.dp_width if (ni and ni.diff_pair and cls.dp_width) else cls.track_width
            if want is None:
                continue
            groups: dict[tuple[str, float], list[Any]] = defaultdict(list)
            for seg in geo.segments(net):
                if abs(seg.width - want) > cfg.rel_tol * want:
                    groups[(seg.layer, round(seg.width, 9))].append(seg)
            for (layer, w), segs in sorted(groups.items()):
                first = segs[0]
                loc = seg_point(first.seg, first.length / 2)
                yield self.violation(
                    message=f"Net {net} is {w * 1e3:.3f} mm wide on {layer}; class {cname} "
                    f"specifies {want * 1e3:.3f} mm.",
                    location=loc,
                    nets=(net,),
                    layers=(layer,),
                    items=tuple(dict.fromkeys(track_id(s.id) for s in segs)),
                    measured=w,
                    limit=want,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Set the width of {net} on {layer} to {want * 1e3:.3f} mm.",
                        actions=(
                            TypedAction(
                                kind="widen" if want > w else "narrow",
                                description=f"Set the track width of {net} to the class width.",
                                targets=(net, layer),
                                parameter="width",
                                current_value=w,
                                suggested_value=want,
                                unit="m",
                                applies_via="board_edit",
                                confidence="high",
                            ),
                            TypedAction(
                                kind="change_rule",
                                description="Enforce the width with a KiCad track_width rule.",
                                targets=(cname or "",),
                                parameter="track_width",
                                suggested_value=want,
                                unit="m",
                                applies_via="dru_rule",
                            ),
                        ),
                        evidence={"net_class": cname},
                    ),
                )


class IM002Config(RuleConfig):
    method: Literal["auto", "closed_form", "bem"] = Field(
        default="bem", description="Impedance method (BEM = 2D field solve, 'validated')."
    )
    max_sections: int = Field(
        default=64,
        ge=1,
        description="Most distinct cross-sections solved per net; more are thinned evenly.",
    )
    max_samples_per_net: int = Field(
        default=12,
        ge=2,
        description="Cross-sections sampled along a net (the spacing is net length / this, at "
        "least min_ds); geometry events (width change, reference change) are always sampled.",
    )
    min_ds: float = Field(
        default=1e-3, gt=0, description="Smallest sample spacing [m] along a net."
    )
    default_tolerance: float = Field(
        default=0.10,
        gt=0,
        description="Tolerance [fraction] when the intent gives none (IPC +-10 %).",
    )


class ImpedanceOutOfTolerance(ErcRule):
    id = "IM-002"
    title = "Computed Z0/Zdiff out of tolerance"
    category = "impedance"
    default_severity = "error"
    requires = frozenset({"impedance"})
    references = _REFS
    Config = IM002Config
    explanation = (
        "The cross-section at each sample along nets with an impedance target is solved with the "
        "`impedance` service and compared with target * (1 +- tolerance). Consecutive failing "
        "samples are one violation; `measured` is the worst impedance of the run and `limit` the "
        "violated bound. The accuracy class of the solver is in the remediation evidence."
    )

    def check(self, ctx: ErcContext, cfg: IM002Config) -> Iterable[Violation]:
        svc = ctx.services().get("impedance")
        geo = ctx.geometry()
        scope = set(ctx.nets_in_scope())
        done: set[str] = set()
        for net in sorted(scope):
            ni = ctx.net_intent(net)
            if ni is None:
                continue
            tol = ni.z_tolerance if ni.z_tolerance is not None else cfg.default_tolerance
            if ni.diff_pair is not None and ni.target_z_diff is not None:
                pair = next((d for d in ctx.board.diff_pairs if d.id == ni.diff_pair), None)
                if pair is None:
                    pair_nets = tuple(
                        sorted(n for n, i in ctx.intent.by_net if i.diff_pair == ni.diff_pair)
                    )
                    if len(pair_nets) != 2:
                        continue
                    p_net, n_net = pair_nets
                else:
                    p_net, n_net = pair.p_net, pair.n_net
                if ni.diff_pair in done:
                    continue
                done.add(ni.diff_pair)
                samples = geo.sample_net((p_net, n_net), ds=self._ds(geo, (p_net,), cfg))
                yield from self._runs(
                    svc,
                    cfg,
                    samples,
                    (p_net, n_net),
                    ni.target_z_diff,
                    tol,
                    diff=True,
                )
            elif ni.target_z_se is not None and ni.diff_pair is None:
                yield from self._runs(
                    svc,
                    cfg,
                    geo.sample_net(net, ds=self._ds(geo, (net,), cfg)),
                    (net,),
                    ni.target_z_se,
                    tol,
                    diff=False,
                )

    @staticmethod
    def _ds(geo: Any, nets: tuple[str, ...], cfg: IM002Config) -> float:
        total = sum(sg.length for n in nets for sg in geo.segments(n))
        return float(max(cfg.min_ds, total / cfg.max_samples_per_net))

    def _runs(
        self,
        svc: Any,
        cfg: IM002Config,
        samples: Any,
        nets: tuple[str, ...],
        target: float,
        tol: float,
        *,
        diff: bool,
    ) -> Iterable[Violation]:
        if diff:
            samples = tuple(
                s for s in samples if sum(c.role == "signal" for c in s.xs.conductors) >= 2
            )
        if not samples:
            return
        sample_keys = [s.xs.key() for s in samples]  # key() hashes the section: once each
        by_key: dict[str, Any] = {}
        for k, s in zip(sample_keys, samples, strict=True):
            by_key.setdefault(k, s.xs)
        keys = sorted(by_key)
        if len(keys) > cfg.max_sections:
            step = len(keys) / cfg.max_sections
            keys = [keys[int(i * step)] for i in range(cfg.max_sections)]
        results = dict(
            zip(
                keys,
                svc.solve_many([by_key[k] for k in keys], None, method=cfg.method),
                strict=True,
            )
        )
        solved = [i for i, k in enumerate(sample_keys) if k in results]
        zs: list[float | None] = []
        for idx, s in enumerate(samples):
            r = results.get(sample_keys[idx])
            if r is None and solved:  # thinned: reuse the nearest solved section
                near = min(solved, key=lambda j: abs(j - idx))
                r = results[sample_keys[near]]
            if r is None:
                zs.append(None)
                continue
            arr = r.params.z_diff if diff else r.params.z0
            zs.append(float(np.abs(as_np(arr)).ravel()[0]) if arr is not None else None)
        lo, hi = target * (1 - tol), target * (1 + tol)
        run: list[int] = []
        for i, z in enumerate([*zs, 0.5 * (lo + hi)]):  # sentinel in tolerance flushes the run
            bad = z is not None and not (lo <= z <= hi)
            if bad:
                run.append(i)
                continue
            if run:
                yield self._report(samples, zs, run, results, nets, target, tol, (lo, hi), diff)
                run = []

    def _report(
        self,
        samples: Any,
        zs: list[float | None],
        run: list[int],
        results: dict[str, Any],
        nets: tuple[str, ...],
        target: float,
        tol: float,
        bounds: tuple[float, float],
        diff: bool,
    ) -> Violation:
        vals = [(abs(zs[i] - target), i) for i in run if zs[i] is not None]  # type: ignore[operator]
        _, worst_i = max(vals)
        worst = float(zs[worst_i])  # type: ignore[arg-type]
        limit = bounds[1] if worst > target else bounds[0]
        mid = samples[run[len(run) // 2]]
        res = results.get(samples[worst_i].xs.key()) or next(iter(results.values()))
        layers = tuple(dict.fromkeys(samples[i].layer for i in run))
        items = tuple(dict.fromkeys(track_id(samples[i].seg_id) for i in run))
        kind = "Zdiff" if diff else "Z0"
        w_now = None
        for c in samples[worst_i].xs.conductors:
            if c.role == "signal":
                w_now = _conductor_width(c)
                break
        suggested = w_now * worst / target if (w_now and not diff) else None
        actions = [
            TypedAction(
                kind="widen" if worst > target else "narrow",
                description=f"{'Widen' if worst > target else 'Narrow'} {nets[0]} on "
                f"{layers[0]} to bring {kind} to {target:g} ohm.",
                targets=(*nets, layers[0]),
                parameter="width",
                current_value=w_now,
                suggested_value=suggested,
                unit="m",
                applies_via="board_edit",
                confidence="low" if suggested is not None else "medium",
            ),
            TypedAction(
                kind="change_stackup",
                description="Change the dielectric height under the trace.",
                targets=(layers[0],),
                applies_via="stackup",
                confidence="low",
            ),
        ]
        return self.violation(
            message=f"{kind} of {'/'.join(nets)} on {layers[0]} is {worst:.1f} ohm; target "
            f"{target:g} ohm +-{tol:.0%}.",
            location=mid.position,
            nets=nets,
            layers=layers,
            items=items,
            measured=worst,
            limit=limit,
            unit="ohm",
            remediation=TypedRemediation(
                summary=f"Change the geometry of {'/'.join(nets)} so {kind} is within "
                f"{lo_hi(bounds)} ohm.",
                actions=tuple(actions),
                evidence={
                    "z_ohm": worst,
                    "target_ohm": target,
                    "tolerance": tol,
                    "method": res.method,
                    "model": res.model_name,
                    "accuracy_class": res.accuracy_class,
                    "validity": res.validity,
                    "n_samples": len(run),
                },
            ),
        )


def lo_hi(b: tuple[float, float]) -> str:
    return f"{b[0]:.1f}..{b[1]:.1f}"


def _conductor_width(c: Any) -> float | None:
    outline = getattr(c, "outline", None)
    if outline is None:
        return None
    try:
        pts = np.asarray(getattr(outline, "array", outline))
        return float(pts[:, 0].max() - pts[:, 0].min())
    except Exception:
        return None


class IM003Config(RuleConfig):
    max_length: float = Field(
        default=5e-3, gt=0, description="Longest allowed neck-down [m] (pin-field escapes)."
    )
    neck_ratio: float = Field(
        default=0.8,
        gt=0,
        lt=1,
        description="A segment narrower than this fraction of the nominal width is a neck-down.",
    )


class Neckdown(ErcRule):
    id = "IM-003"
    title = "Neckdown longer than max"
    category = "impedance"
    default_severity = "warning"
    references = _REFS
    Config = IM003Config
    explanation = (
        "A narrow section raises the impedance and resistance. Short neck-downs through pin "
        "fields are normal; the rule reports neck-downs longer than `max_length`. The nominal "
        "width is the widest segment of the net."
    )

    def check(self, ctx: ErcContext, cfg: IM003Config) -> Iterable[Violation]:
        geo = ctx.geometry()
        for net in ctx.nets_in_scope():
            segs = geo.segments(net)
            if not segs:
                continue
            nominal = max(s.width for s in segs)  # a neck-down is narrower than the rest of the net
            groups: list[list[Any]] = []
            cur: list[Any] = []
            for s in segs:
                if s.width < cfg.neck_ratio * nominal:
                    if cur and (cur[-1].layer != s.layer):
                        groups.append(cur)
                        cur = []
                    cur.append(s)
                elif cur:
                    groups.append(cur)
                    cur = []
            if cur:
                groups.append(cur)
            for g in groups:
                length = sum(s.length for s in g)
                if length <= cfg.max_length:
                    continue
                w = min(s.width for s in g)
                yield self.violation(
                    message=f"Net {net} is necked down to {w * 1e3:.3f} mm for "
                    f"{length * 1e3:.1f} mm on {g[0].layer} (nominal {nominal * 1e3:.3f} mm).",
                    location=seg_point(g[len(g) // 2].seg, g[len(g) // 2].length / 2),
                    nets=(net,),
                    layers=(g[0].layer,),
                    items=tuple(dict.fromkeys(track_id(s.id) for s in g)),
                    measured=length,
                    limit=cfg.max_length,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Shorten the neck-down on {net} to {cfg.max_length * 1e3:.1f} mm "
                        "or widen it.",
                        actions=(
                            TypedAction(
                                kind="widen",
                                description=f"Widen the necked section of {net}.",
                                targets=(net, g[0].layer),
                                parameter="width",
                                current_value=w,
                                suggested_value=nominal,
                                unit="m",
                                applies_via="board_edit",
                            ),
                        ),
                        evidence={"nominal_width_m": nominal},
                    ),
                )


class IM005Config(RuleConfig):
    max_stub_length: float = Field(
        default=3e-3,
        gt=0,
        description="Longest stub [m] (branch or dead-end pad stub) allowed; also limited to "
        "t_rise * v / 10 when the intent gives a rise time.",
    )


class LongStub(ErcRule):
    id = "IM-005"
    title = "Stub longer than max"
    category = "impedance"
    default_severity = "warning"
    references = _REFS
    Config = IM005Config
    explanation = (
        "Open stubs (T-branches, test points, dead-end pads) reflect energy. A stub is "
        "electrically short when its round trip is below a tenth of the rise time, "
        "l < t_rise * v / 10; `max_stub_length` caps it when no rise time is known. Via stubs "
        "are handled by VI-001."
    )

    def check(self, ctx: ErcContext, cfg: IM005Config) -> Iterable[Violation]:
        geo = ctx.geometry()
        for net in ctx.nets_in_scope():
            topo = geo.topology(net)
            nodes = {n.id: n for n in topo.nodes}
            ni = ctx.net_intent(net)
            limit = cfg.max_stub_length
            if ni is not None and ni.rise_time:
                layer = next(iter(s.layer for s in geo.segments(net)), "L1")
                v = C0 / math.sqrt(eps_eff(ctx.board, layer))
                limit = min(limit, ni.rise_time * v / 10)
            for st in topo.stubs:
                if st.kind == "via_stub" or st.length <= limit:
                    continue
                origin = nodes.get(st.from_node)
                if origin is not None and origin.kind == "end":
                    continue  # the whole route (no pads at either end), not a stub
                node = nodes.get(st.end_node)
                loc = node.position if node else (0.0, 0.0)
                yield self.violation(
                    message=f"Net {net} has a {st.length * 1e3:.1f} mm stub (limit "
                    f"{limit * 1e3:.1f} mm).",
                    location=loc,
                    nets=(net,),
                    layers=(node.layer,) if node and node.layer else (),
                    items=tuple(
                        dict.fromkeys(track_id(e) for e in st.edges if not e.startswith("barrel"))
                    ),
                    measured=st.length,
                    limit=limit,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Remove or shorten the stub on {net} to {limit * 1e3:.1f} mm.",
                        actions=(
                            TypedAction(
                                kind="reroute",
                                description=f"Remove the stub branch on {net}.",
                                targets=(net,),
                                parameter="stub_length",
                                current_value=st.length,
                                suggested_value=limit,
                                unit="m",
                                applies_via="board_edit",
                            ),
                        ),
                        evidence={"stub_kind": st.kind},
                    ),
                )
