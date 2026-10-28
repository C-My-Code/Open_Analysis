# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Via rules VI-001, VI-002."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import Field

from oa_erc.base import ErcContext, ErcRule, RuleConfig, mean_dk
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_physics.via import stub_resonance
from oa_results.violations import Violation

_REFS = (
    "docs/research/06-landscape-architecture.md section 3.2 (VI-001: f = c / (4 l sqrt(Er_eff)))",
    "Engineering default: stubs above about 10 mil (0.25 mm) matter above roughly 10 Gb/s "
    "(common high-speed design guides; configurable)",
)


class VI001Config(RuleConfig):
    max_stub_length: float = Field(
        default=0.25e-3, gt=0, description="Longest via stub [m] (10 mil)."
    )
    f_max: float | None = Field(
        default=None,
        gt=0,
        description="Frequency [Hz] used for the resonance check when the intent gives none; "
        "None disables the resonance check for nets without f_max.",
    )
    skip_terminal_vias: bool = Field(
        default=True,
        description="Ignore vias where only one track ends (a dead-end via is the termination "
        "of the route, not a transition).",
    )
    resonance_margin: float = Field(
        default=3.0,
        gt=1,
        description="The stub is also reported when its quarter-wave resonance is below "
        "resonance_margin * f_max.",
    )


class ViaStub(ErcRule):
    id = "VI-001"
    title = "Via stub length / stub resonance"
    category = "via"
    default_severity = "warning"
    references = _REFS
    Config = VI001Config
    explanation = (
        "The barrel beyond the last used layer is an open quarter-wave stub with resonance "
        "f0 = c / (4 l sqrt(Dk_eff)) (oa_physics.via.stub_resonance). Long stubs notch the "
        "channel near f0. Backdrilled barrels are measured after backdrilling."
    )

    def check(self, ctx: ErcContext, cfg: VI001Config) -> Iterable[Violation]:
        geo = ctx.geometry()
        vias = {v.id: v for v in ctx.board.vias}
        dk = mean_dk(ctx.board)
        for net in ctx.nets_in_scope():
            topo = geo.topology(net)
            for st in topo.stubs:
                if st.kind != "via_stub":
                    continue
                vid, _, layer = st.from_node.removeprefix("via:").rpartition("@")
                via = vias.get(vid)
                if via is None or st.length <= 0:
                    continue
                if cfg.skip_terminal_vias and _attached_tracks(topo, vid) < 2:
                    continue
                f0 = stub_resonance(st.length, dk)
                ni = ctx.net_intent(net)
                f_max = ni.f_max if ni is not None and ni.f_max else cfg.f_max
                too_long = st.length > cfg.max_stub_length
                resonant = f_max is not None and f0 < cfg.resonance_margin * f_max
                if not (too_long or resonant):
                    continue
                yield self.violation(
                    message=f"Via {vid} of {net} leaves a {st.length * 1e3:.2f} mm stub below "
                    f"{layer} (quarter-wave resonance {f0 / 1e9:.1f} GHz).",
                    location=via.position,
                    nets=(net,),
                    layers=(layer,),
                    items=(vid,),
                    measured=st.length,
                    limit=cfg.max_stub_length,
                    unit="m",
                    remediation=TypedRemediation(
                        summary=f"Backdrill via {vid} to leave at most "
                        f"{cfg.max_stub_length * 1e3:.2f} mm, or route {net} on the bottom layer.",
                        actions=(
                            TypedAction(
                                kind="backdrill",
                                description=f"Backdrill via {vid} below {layer}.",
                                targets=(vid,),
                                parameter="stub_length",
                                current_value=st.length,
                                suggested_value=cfg.max_stub_length,
                                unit="m",
                                applies_via="board_edit",
                                confidence="high",
                            ),
                            TypedAction(
                                kind="change_layer",
                                description=f"Move {net} to a layer that ends the via stub.",
                                targets=(net,),
                                applies_via="board_edit",
                                confidence="medium",
                            ),
                        ),
                        evidence={
                            "stub_resonance_hz": f0,
                            "dk_eff": dk,
                            "f_max_hz": f_max,
                        },
                    ),
                )


def _attached_tracks(topo: Any, via_id: str) -> int:
    nodes = {n.id for n in topo.nodes if n.kind == "via" and n.ref == via_id}
    return sum(1 for e in topo.edges if e.kind == "segment" and (e.a in nodes or e.b in nodes))


class VI002Config(RuleConfig):
    max_vias: int = Field(
        default=4, ge=1, description="Largest number of vias on one high-speed net."
    )


class ViaCount(ErcRule):
    id = "VI-002"
    title = "Via count per high-speed net"
    category = "via"
    default_severity = "info"
    references = (
        "docs/research/06-landscape-architecture.md section 3.2 (VI-002)",
        "Engineering default: more than 4 vias on a multi-Gb/s net is rarely justified",
    )
    Config = VI002Config
    explanation = (
        "Every via adds inductance, capacitance and a possible stub. The rule counts the "
        "vias of each routed net in scope (nets without tracks, such as via arrays joining "
        "zones, are skipped)."
    )

    def check(self, ctx: ErcContext, cfg: VI002Config) -> Iterable[Violation]:
        by_net: dict[str, list[object]] = {}
        for v in ctx.board.vias:
            if v.net is not None:
                by_net.setdefault(v.net, []).append(v)
        routed = {t.net for t in ctx.board.tracks}
        for net in ctx.nets_in_scope():
            vs = by_net.get(net, [])
            if net not in routed or len(vs) <= cfg.max_vias:
                continue
            vs.sort(key=lambda v: v.id)  # type: ignore[attr-defined]
            mid = vs[len(vs) // 2]
            yield self.violation(
                message=f"Net {net} has {len(vs)} vias (limit {cfg.max_vias}).",
                location=mid.position,  # type: ignore[attr-defined]
                nets=(net,),
                items=tuple(v.id for v in vs),  # type: ignore[attr-defined]
                measured=float(len(vs)),
                limit=float(cfg.max_vias),
                unit="count",
                remediation=TypedRemediation(
                    summary=f"Reduce the number of layer changes of {net} to {cfg.max_vias}.",
                    actions=(
                        TypedAction(
                            kind="reroute",
                            description=f"Reroute {net} with fewer layer changes.",
                            targets=(net,),
                            parameter="via_count",
                            current_value=float(len(vs)),
                            suggested_value=float(cfg.max_vias),
                            unit="count",
                            applies_via="board_edit",
                        ),
                    ),
                ),
            )
