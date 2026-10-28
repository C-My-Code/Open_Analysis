# SPDX-License-Identifier: GPL-3.0-or-later
"""Vias, backdrills and via protection (board-model.md section 8)."""

from typing import Literal

from pydantic import Field

from oa_model._base import OAModel
from oa_model.common import ItemId, LayerId, Length, NetId, SourceRef, Vec2


class Backdrill(OAModel):
    side: Literal["top", "bottom"] = Field(description="Side the drill enters from.")
    diameter: Length = Field(description="Backdrill diameter, metres.")
    last_removed_layer: LayerId = Field(
        description="Deepest conductor layer whose barrel copper is removed."
    )
    must_not_cut: LayerId | None = Field(
        default=None, description="First layer that must keep its barrel (next layer inward)."
    )
    depth: Length | None = Field(
        default=None, description="Explicit depth from the entry surface, metres, if known."
    )


class ViaProtection(OAModel):
    """IPC-4761 style protection. Affects thermal and DC models."""

    tented_top: bool = Field(default=False, description="Top side tented.")
    tented_bottom: bool = Field(default=False, description="Bottom side tented.")
    plugged: bool = Field(default=False, description="Barrel plugged.")
    filled: Literal["none", "conductive", "non_conductive", "unknown"] = Field(
        default="none", description="Barrel fill type."
    )
    fill_material: str | None = Field(
        default=None, description="Material name when conductive fill."
    )
    capped: bool = Field(default=False, description="Capped (VIPPO).")


class Via(OAModel):
    id: ItemId
    padstack: ItemId = Field(description="Id of the Padstack.")
    position: Vec2 = Field(description="Board-frame (x, y), metres.")
    net: NetId | None = Field(description="Net name, or None.")
    kind: Literal["through", "blind", "buried", "micro"] = Field(description="Via type.")
    start_layer: LayerId = Field(description="Upper conductor layer of the span (smaller index).")
    end_layer: LayerId = Field(description="Lower conductor layer of the span.")
    backdrills: tuple[Backdrill, ...] = Field(
        default=(), description="Backdrills, at most one per side."
    )
    protection: ViaProtection = Field(default=ViaProtection(), description="IPC-4761 protection.")
    source: SourceRef | None = Field(default=None, description="Originating item.")
