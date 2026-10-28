# SPDX-License-Identifier: GPL-3.0-or-later
"""Design intent and `resolve_intent` (board-model.md section 12.1)."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import Fraction, Frequency, ItemId, NetId, Resistance, Time
from oa_model.nets import DiffPair

_GROUND_GLOBS = ("GND*", "VSS*", "AGND", "DGND", "PGND")
_Z_IN_NAME = re.compile(r"(\d+(?:\.\d+)?)\s*(?:ohm|r\b|Ω)", re.IGNORECASE)


class NetSelector(OAModel):
    """Selects nets by name glob, regex, net class or diff pair: the union of all criteria
    minus `exclude`. An empty selector selects nothing."""

    names: tuple[str, ...] = Field(default=(), description="Net name globs: 'USB_*'.")
    regex: tuple[str, ...] = Field(
        default=(), description="Regexes matched against the whole name."
    )
    net_classes: tuple[str, ...] = Field(default=(), description="Net class names.")
    diff_pairs: tuple[ItemId, ...] = Field(default=(), description="Diff pair ids.")
    kinds: tuple[Literal["signal", "power", "ground"], ...] = Field(
        default=(), description="Net kinds."
    )
    exclude: tuple[str, ...] = Field(default=(), description="Net name globs to remove.")


class SignalIntent(OAModel):
    select: NetSelector = Field(description="Nets this entry applies to.")
    high_speed: bool = Field(default=True, description="Treat as a high-speed net.")
    rise_time: Time | None = Field(default=None, gt=0, description="10-90 % rise time, seconds.")
    f_max: Frequency | None = Field(
        default=None, description="Highest frequency of interest, Hz; default 0.35 / rise_time."
    )
    target_z_se: Resistance | None = Field(default=None, description="Single-ended ohms.")
    target_z_diff: Resistance | None = Field(default=None, description="Differential ohms.")
    z_tolerance: Fraction = Field(default=0.10, description="Impedance tolerance fraction.")
    reference_nets: tuple[NetId, ...] = Field(
        default=(), description="Allowed reference nets; empty = any ground/power."
    )
    io: bool = Field(default=False, description="Leaves the board (EMI).")


class PowerIntent(OAModel):
    net: NetId = Field(description="Power net name.")
    return_net: NetId = Field(default="GND", description="Return net name.")
    v_nominal: float = Field(description="Nominal voltage, volts.")
    tolerance: Fraction = Field(default=0.05, description="Voltage tolerance fraction.")
    i_max: float | None = Field(default=None, description="Maximum current, amps.")


class DesignIntent(OAModel):
    schema: Literal["oa-intent/1"] = Field(  # type: ignore[assignment]
        default="oa-intent/1", description="Schema tag; always 'oa-intent/1'."
    )
    signals: tuple[SignalIntent, ...] = Field(
        default=(), description="Later entries override earlier ones per net."
    )
    power: tuple[PowerIntent, ...] = Field(default=(), description="Power rails.")
    ground_nets: tuple[NetId, ...] = Field(
        default=(), description="Explicit ground nets; default heuristic GND*, VSS*, AGND, ..."
    )
    diff_pairs_add: tuple[DiffPair, ...] = Field(default=(), description="Extra diff pairs.")
    diff_pairs_remove: tuple[ItemId, ...] = Field(
        default=(), description="Ids of board diff pairs to ignore."
    )


class NetIntentResolved(OAModel):
    net: NetId = Field(description="Net name.")
    kind: Literal["signal", "power", "ground", "unknown"] = Field(description="Resolved kind.")
    high_speed: bool = Field(description="High-speed net.")
    rise_time: Time | None = Field(default=None, description="Seconds.")
    f_max: Frequency | None = Field(default=None, description="Hz.")
    target_z_se: Resistance | None = Field(default=None, description="Single-ended ohms.")
    target_z_diff: Resistance | None = Field(default=None, description="Differential ohms.")
    z_tolerance: Fraction | None = Field(default=None, description="Tolerance fraction.")
    reference_nets: tuple[NetId, ...] = Field(default=(), description="Allowed reference nets.")
    io: bool = Field(default=False, description="Leaves the board.")
    diff_pair: ItemId | None = Field(default=None, description="Id of the pair it belongs to.")
    power: PowerIntent | None = Field(default=None, description="Rail definition, for power nets.")


class ResolvedIntent(OAModel):
    by_net: tuple[tuple[NetId, NetIntentResolved], ...] = Field(
        description="(net name, intent) for every board net, in board order."
    )

    def index(self) -> dict[str, NetIntentResolved]:
        """Lookup by net name."""
        return dict(self.by_net)


def load_intent(path: str | Path) -> DesignIntent:
    """Load and validate an intent YAML file."""
    return DesignIntent.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


def _glob_any(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def _select(
    sel: NetSelector,
    board: Board,
    kinds: dict[str, str],
    pairs: dict[str, DiffPair],
) -> set[str]:
    chosen: set[str] = set()
    compiled = [re.compile(r) for r in sel.regex]
    classes = set(sel.net_classes)
    for net in board.nets:
        name = net.id
        if (
            _glob_any(name, sel.names)
            or any(rx.fullmatch(name) for rx in compiled)
            or (net.net_class is not None and net.net_class in classes)
            or kinds[name] in sel.kinds
        ):
            chosen.add(name)
    for pid in sel.diff_pairs:
        if pid in pairs:
            chosen.update((pairs[pid].p_net, pairs[pid].n_net))
    return {n for n in chosen if not _glob_any(n, sel.exclude)}


def resolve_intent(board: Board, intent: DesignIntent) -> ResolvedIntent:
    """Per-net intent: net kinds, diff pairs, impedance targets and signal parameters.

    Precedence (lowest to highest): net class values (or `90ohm` / `100R_diff` parsed from the
    class name), diff pair target, `signals` entries in order.
    """
    removed = set(intent.diff_pairs_remove)
    pairs: dict[str, DiffPair] = {d.id: d for d in board.diff_pairs if d.id not in removed}
    for d in intent.diff_pairs_add:
        pairs[d.id] = d
    pair_of: dict[str, DiffPair] = {}
    for d in pairs.values():
        pair_of[d.p_net] = d
        pair_of[d.n_net] = d

    power = {p.net: p for p in intent.power}
    kinds: dict[str, str] = {}
    for n in board.nets:
        kind = n.kind
        if n.id in power:
            kind = "power"
        elif n.id in intent.ground_nets or (
            kind == "unknown" and _glob_any(n.id.upper(), _GROUND_GLOBS)
        ):
            kind = "ground"
        kinds[n.id] = kind

    classes = {c.name: c for c in board.net_classes}
    state: dict[str, dict[str, object]] = {}
    for n in board.nets:
        s: dict[str, object] = {"high_speed": False, "reference_nets": (), "io": False}
        in_pair = n.id in pair_of
        cls = classes.get(n.net_class) if n.net_class else None
        if cls is not None:
            z_se, z_diff = cls.target_z_se, cls.target_z_diff
            if z_se is None and z_diff is None:
                m = _Z_IN_NAME.search(cls.name)
                if m:
                    z = float(m.group(1))
                    if "diff" in cls.name.lower() or in_pair:
                        z_diff = z
                    else:
                        z_se = z
            if z_se is not None:
                s["target_z_se"] = z_se
            if z_diff is not None:
                s["target_z_diff"] = z_diff
            if cls.target_tolerance is not None:
                s["z_tolerance"] = cls.target_tolerance
        if in_pair and pair_of[n.id].target_z_diff is not None:
            s["target_z_diff"] = pair_of[n.id].target_z_diff
        state[n.id] = s

    for sig in intent.signals:
        for name in _select(sig.select, board, kinds, pairs):
            s = state[name]
            s["high_speed"] = sig.high_speed
            s["io"] = sig.io
            s["z_tolerance"] = sig.z_tolerance
            s["reference_nets"] = sig.reference_nets
            for f in ("rise_time", "f_max", "target_z_se", "target_z_diff"):
                v = getattr(sig, f)
                if v is not None:
                    s[f] = v

    out: list[tuple[str, NetIntentResolved]] = []
    for n in board.nets:
        s = state[n.id]
        if "f_max" not in s and isinstance(s.get("rise_time"), float):
            s["f_max"] = 0.35 / s["rise_time"]  # type: ignore[operator]
        pair = pair_of.get(n.id)
        out.append(
            (
                n.id,
                NetIntentResolved.model_validate(
                    {
                        "net": n.id,
                        "kind": kinds[n.id],
                        "diff_pair": pair.id if pair else None,
                        "power": power.get(n.id),
                        **s,
                    }
                ),
            )
        )
    return ResolvedIntent(by_net=tuple(out))
