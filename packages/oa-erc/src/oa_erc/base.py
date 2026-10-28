# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rule base class, rule context and small helpers shared by all rules."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from oa_erc.remediation import TypedRemediation
from oa_framework.rules import RuleCategory
from oa_framework.services import DictServices, Services
from oa_geometry import BoardGeometry
from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import NetId, Vec2
from oa_model.geometry import ArcSeg, PathSeg
from oa_model.setup import ResolvedIntent
from oa_model.setup.intent import NetSelector
from oa_results.violations import Severity, Violation

if TYPE_CHECKING:
    from oa_model.setup.intent import NetIntentResolved

C0 = 299_792_458.0
DEFAULT_F_MAX = 1.0e9
"""Frequency [Hz] used when neither the intent nor the rule gives one."""


class RuleConfig(OAModel):
    """Base of every rule ``Config``: a net selector narrowing the rule's scope."""

    nets: NetSelector = Field(
        default=NetSelector(),
        description="Restrict the rule to these nets (empty selector = all nets in scope).",
    )


class ErcContext:
    """Concrete ``RuleContext`` (interfaces.md section 3.5).

    ``nets_in_scope`` returns the high-speed signal nets: those the intent marks ``high_speed``;
    when the intent marks none at all, every net of kind ``signal`` is assumed high-speed (a
    documented fallback so a bare board still gets checked). ``selector`` (the rule's
    ``Config.nets``), when non-empty, narrows the result.
    """

    def __init__(
        self,
        board: Board,
        intent: ResolvedIntent,
        services: Services | None = None,
        *,
        geometry: BoardGeometry | None = None,
        selector: NetSelector | None = None,
    ) -> None:
        self.board = board
        self.intent = intent
        self._services: Services = services if services is not None else DictServices()
        self._geometry = geometry
        self._selector = selector
        self._index = intent.index()
        self._memo: dict[Any, Any] = {}

    def geometry(self) -> BoardGeometry:
        if self._geometry is None:
            self._geometry = BoardGeometry.build(self.board)
        return self._geometry

    def services(self) -> Services:
        return self._services

    def memo(self, key: Any, fn: Any) -> Any:
        """Per-context cache shared by the rules of one run (e.g. the reference analysis)."""
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def net_intent(self, net: NetId) -> NetIntentResolved | None:
        return self._index.get(net)

    def net_kind(self, net: str | None) -> str:
        if net is None:
            return "unknown"
        i = self._index.get(net)
        return i.kind if i is not None else "unknown"

    def nets_in_scope(self) -> tuple[NetId, ...]:
        flagged = [n for n, i in self.intent.by_net if i.high_speed and i.kind != "ground"]
        if not flagged:
            flagged = [n for n, i in self.intent.by_net if i.kind == "signal"]
        sel = self._selector
        if sel is not None and (sel.names or sel.regex or sel.net_classes or sel.diff_pairs):
            chosen = _select_nets(self.board, self.intent, sel)
            flagged = [n for n in flagged if n in chosen]
        return tuple(sorted(flagged))

    def f_max(self, net: NetId, default: float = DEFAULT_F_MAX) -> float:
        i = self._index.get(net)
        if i is not None and i.f_max is not None:
            return float(i.f_max)
        return default


def _select_nets(board: Board, intent: ResolvedIntent, sel: NetSelector) -> set[str]:
    import fnmatch

    out: set[str] = set()
    rx = [re.compile(r) for r in sel.regex]
    pairs = {d.id: d for d in board.diff_pairs}
    for net in board.nets:
        if (
            any(fnmatch.fnmatchcase(net.id, p) for p in sel.names)
            or any(r.fullmatch(net.id) for r in rx)
            or (net.net_class is not None and net.net_class in sel.net_classes)
        ):
            out.add(net.id)
    for pid in sel.diff_pairs:
        if pid in pairs:
            out.update((pairs[pid].p_net, pairs[pid].n_net))
    return {n for n in out if not any(fnmatch.fnmatchcase(n, p) for p in sel.exclude)}


class ErcRule:
    """Base class of the built-in rules. Subclasses set the ``Rule`` class attributes."""

    id: ClassVar[str]
    title: ClassVar[str]
    category: ClassVar[RuleCategory]
    aliases: ClassVar[tuple[str, ...]] = ()
    default_severity: ClassVar[Severity]
    requires: ClassVar[frozenset[str]] = frozenset()
    references: ClassVar[tuple[str, ...]]
    Config: ClassVar[type[OAModel]]
    explanation: ClassVar[str] = ""

    def check(self, ctx: Any, cfg: Any) -> Iterable[Violation]:
        raise NotImplementedError

    def violation(
        self,
        *,
        message: str,
        location: Vec2,
        remediation: TypedRemediation,
        nets: Iterable[str] = (),
        layers: Iterable[str] = (),
        items: Iterable[str] = (),
        measured: float | None = None,
        limit: float | None = None,
        unit: str | None = None,
        severity: Severity | None = None,
    ) -> Violation:
        items_t = tuple(dict.fromkeys(items))
        nets_t = tuple(nets)
        layers_t = tuple(layers)
        return Violation(
            rule_id=self.id,
            severity=severity or self.default_severity,
            message=message,
            nets=nets_t,
            layers=layers_t,
            items=items_t,
            location=(float(location[0]), float(location[1])),
            measured=None if measured is None else float(measured),
            limit=None if limit is None else float(limit),
            unit=unit,
            fix_hint=remediation.summary,
            remediation=remediation.to_model(),
            fingerprint=fingerprint(self.id, items_t or nets_t, location, layers_t),
        )


def fingerprint(
    rule_id: str, items: Iterable[str], location: Vec2, layers: Iterable[str] = ()
) -> str:
    """Stable hash of (rule id, sorted items, location rounded to 10 um, layers)."""
    key = "|".join(
        [
            rule_id,
            ",".join(sorted(items)),
            f"{round(location[0] / 1e-5)},{round(location[1] / 1e-5)}",
            ",".join(sorted(layers)),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


_SEG_SUFFIX = re.compile(r"(#\d+)?(\.\d+)*$")


def track_id(seg_id: str) -> str:
    """Board track id of a ``RoutedSegment.id`` (strips ``#k`` and ``.j`` suffixes)."""
    return _SEG_SUFFIX.sub("", seg_id)


def seg_point(seg: PathSeg, s: float) -> Vec2:
    """Point at arc length ``s`` along a line or arc (clamped)."""
    if isinstance(seg, ArcSeg):
        r = math.hypot(seg.start[0] - seg.center[0], seg.start[1] - seg.center[1])
        a0 = math.atan2(seg.start[1] - seg.center[1], seg.start[0] - seg.center[0])
        a1 = math.atan2(seg.end[1] - seg.center[1], seg.end[0] - seg.center[0])
        d = (a1 - a0) % (2 * math.pi) if seg.ccw else -((a0 - a1) % (2 * math.pi))
        length = abs(d) * r
        t = 0.0 if length == 0 else min(max(s / length, 0.0), 1.0)
        a = a0 + d * t
        return (seg.center[0] + r * math.cos(a), seg.center[1] + r * math.sin(a))
    dx, dy = seg.end[0] - seg.start[0], seg.end[1] - seg.start[1]
    length = math.hypot(dx, dy)
    t = 0.0 if length == 0 else min(max(s / length, 0.0), 1.0)
    return (seg.start[0] + dx * t, seg.start[1] + dy * t)


def mean_dk(board: Board, f: float = 1e9) -> float:
    """Average relative permittivity of the stack's dielectrics (thickness weighted)."""
    from oa_model.stackup import DielectricLayer

    mats = {m.name: m for m in board.stackup.materials}
    tot = 0.0
    acc = 0.0
    for layer in board.stackup.layers:
        if isinstance(layer, DielectricLayer):
            tot += layer.thickness
            acc += layer.thickness * material_dk(mats[layer.material], f)
    return acc / tot if tot > 0 else 4.0


def material_dk(mat: Any, f: float = 1e9) -> float:
    model = getattr(mat, "model", None)
    dk = getattr(model, "dk", None)
    if isinstance(dk, (int, float)):
        return float(dk)
    try:
        from oa_physics import eps_complex

        return float(eps_complex(model, f).real)  # type: ignore[arg-type]
    except Exception:  # pragma: no cover - dispersive models are rare in M1 boards
        return 4.0


def eps_eff(board: Board, layer: str, f: float = 1e9) -> float:
    """Effective permittivity estimate: Dk inside the stack, (Dk + 1) / 2 on an outer layer."""
    dk = mean_dk(board, f)
    ids = [lyr.id for lyr in board.stackup.layers if lyr.kind == "conductor"]
    if ids and layer in (ids[0], ids[-1]):
        return (dk + 1.0) / 2.0
    return dk
