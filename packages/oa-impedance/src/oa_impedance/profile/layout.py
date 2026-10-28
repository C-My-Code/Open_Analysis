# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sampling a net into runs of identical cross-sections (the "layout" of a profile).

``sample_net`` gives cross-sections on a grid plus exact reference events. Between two grid
samples with different keys the transition is located by bisection (``refine_tol``), so the
position of a width / coupling / coplanar change is accurate to ``refine_tol`` and not to ``ds``.
Consecutive samples with the same key (and the same reference nets) are merged into one run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field

from oa_impedance.profile.causes import KeyFeature, classify, features_of, static_causes
from oa_model._base import OAModel
from oa_model.xsection import CrossSection

__all__ = ["NetLayout", "Run", "build_runs"]

_MIN_LEN = 1e-9


class Run(OAModel):
    s0: float = Field(description="Start path length [m].")
    s1: float = Field(description="End path length [m].")
    seg: str = Field(description="Id of the routed segment the run starts on.")
    layer: str = Field(description="Conductor layer of the run.")
    key: str = Field(description="CrossSection.key() of the run.")
    x: float = Field(description="Start point x, board frame [m].")
    y: float = Field(description="Start point y, board frame [m].")
    causes: tuple[str, ...] = Field(
        default=(), description="Causes of the discontinuity that starts this run."
    )
    flagged: bool = Field(default=False, description="The run starts a discontinuity.")


class NetLayout(OAModel):
    name: str = Field(description="Net name, or 'P/N' for a differential pair.")
    nets: tuple[str, ...] = Field(description="Net(s): one name, or (P, N).")
    net_class: str | None = Field(default=None, description="Net class of the (first) net.")
    target: float | None = Field(default=None, description="Target impedance [ohm].")
    tolerance: float = Field(default=0.1, description="Impedance tolerance [fraction].")
    runs: tuple[Run, ...] = Field(description="Runs in path order.")

    @property
    def pair(self) -> bool:
        return len(self.nets) == 2


@dataclass
class _Node:
    s0: float
    seg: str
    layer: str
    xs: CrossSection
    key: str
    tag: str
    xy: tuple[float, float]


def _ident(f: KeyFeature, key: str) -> tuple[Any, ...]:
    return (key, f.refs)


def build_runs(
    geo: Any,
    nets: tuple[str, ...],
    *,
    ds: float | None,
    refine_tol: float,
    xs_store: dict[str, CrossSection],
    feat_store: dict[str, KeyFeature],
) -> tuple[Run, ...]:
    """Runs of `nets` (one net or a P/N pair); fills `xs_store` / `feat_store` by key."""
    from oa_geometry.sampling import cross_section_at

    pair = len(nets) == 2
    target: Any = nets if pair else nets[0]
    primary = nets[0]
    partner = nets[1] if pair else None
    samples = geo.sample_net(target, ds=ds)
    if not samples:
        return ()
    segs = {s.id: s for s in geo.segments(primary)}

    def key_of(xs: CrossSection) -> str:
        k = xs.key()
        xs_store.setdefault(k, xs)
        if k not in feat_store:
            feat_store[k] = features_of(xs)
        return k

    # group by segment, keep order
    groups: dict[str, list[Any]] = {}
    for smp in samples:
        groups.setdefault(smp.seg_id, []).append(smp)

    nodes: list[_Node] = []
    ends: dict[str, float] = {}
    for sid, grp in groups.items():
        seg = segs[sid]
        ends[sid] = seg.s_start + seg.length
        prev: Any = None
        prev_key = ""
        for smp in grp:
            k = key_of(smp.xs)
            if prev is not None and smp.event == "regular" and k != prev_key:
                lo, hi = prev.s - seg.s_start, smp.s - seg.s_start
                xs_hi = smp.xs
                while hi - lo > refine_tol:
                    mid = 0.5 * (lo + hi)
                    xm = cross_section_at(geo, seg, mid, partner=partner)
                    if key_of(xm) == prev_key:
                        lo = mid
                    else:
                        hi, xs_hi = mid, xm
                if hi < smp.s - seg.s_start - _MIN_LEN:
                    f = (hi + seg.s_start - prev.s) / max(smp.s - prev.s, 1e-15)
                    xy = (
                        prev.position[0] + f * (smp.position[0] - prev.position[0]),
                        prev.position[1] + f * (smp.position[1] - prev.position[1]),
                    )
                    nodes.append(
                        _Node(seg.s_start + hi, sid, smp.layer, xs_hi, key_of(xs_hi), "regular", xy)
                    )
            nodes.append(_Node(smp.s, sid, smp.layer, smp.xs, k, smp.event, smp.position))
            prev, prev_key = smp, k

    runs: list[Run] = []
    prev_feat: KeyFeature | None = None
    cur: _Node | None = None
    cur_end = 0.0
    cur_causes: tuple[str, ...] = ()

    def flush() -> None:
        nonlocal cur
        if cur is not None and cur_end - cur.s0 > _MIN_LEN:
            runs.append(
                Run(
                    s0=cur.s0,
                    s1=cur_end,
                    seg=cur.seg,
                    layer=cur.layer,
                    key=cur.key,
                    x=cur.xy[0],
                    y=cur.xy[1],
                    causes=cur_causes,
                    flagged=bool(cur_causes),
                )
            )
        cur = None

    for i, nd in enumerate(nodes):
        nxt = nodes[i + 1] if i + 1 < len(nodes) and nodes[i + 1].seg == nd.seg else None
        end = nxt.s0 if nxt is not None else ends[nd.seg]
        f = feat_store[nd.key]
        if (
            cur is not None
            and _ident(feat_store[cur.key], cur.key) == _ident(f, nd.key)
            and abs(nd.s0 - cur_end) <= 1e-7
        ):
            cur_end = end
            continue
        flush()
        causes = (
            classify(prev_feat, f, nd.tag, pair)
            if prev_feat is not None
            else static_causes(f, pair)
        )
        if prev_feat is not None and causes:
            causes = tuple(dict.fromkeys((*causes, *static_causes(f, False))))
        cur, cur_end, cur_causes = nd, end, causes
        prev_feat = f
    flush()
    return tuple(runs)
