# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Profile along a net (interfaces.md section 4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_model.arrays import Floats
from oa_model.common import ItemId, NetId
from oa_results.container import PayloadBase, as_np, value_span
from oa_results.fields import FieldData


class ProfileEvent(OAModel):
    kind: Literal["via", "layer_change", "ref_change", "neckdown", "plane_edge", "other"]
    s: float  # path length (m)
    item: ItemId | None = None
    message: str = ""


class ProfileResult(PayloadBase):
    """Impedance/delay versus distance along a net."""

    kind: Literal["profile"] = "profile"
    net: NetId | tuple[NetId, NetId]
    s: Floats  # path length (m)
    quantities: tuple[FieldData, ...]  # location="node" over s
    events: tuple[ProfileEvent, ...] = ()
    target: float | None = None
    tolerance: float | None = None

    def _digest(self) -> str:
        net = self.net if isinstance(self.net, str) else "/".join(self.net)
        length = value_span(self.s)
        extent = f"{length[1]:.4g} m" if length else "empty"
        return (
            f"Profile of {net} over {extent} with {len(self.events)} event(s); "
            f"quantities {', '.join(q.name for q in self.quantities) or 'none'}."
        )

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return {
            "net": self.net if isinstance(self.net, str) else list(self.net),
            "n_samples": int(as_np(self.s).size),
            "length_m": (value_span(self.s) or [0.0, 0.0])[1],
            "target": self.target,
            "tolerance": self.tolerance,
            "quantities": [
                {"name": q.name, "unit": q.unit, "span": list(value_span(q.values) or [])}
                for q in self.quantities[:max_items]
            ],
            "events": [{"kind": e.kind, "s": e.s, "item": e.item} for e in self.events[:max_items]],
        }
