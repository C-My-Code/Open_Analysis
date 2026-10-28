# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Field-solved per-layer propagation delay, shaped for a later KiCad time-domain tuning export.

Data structure only: nothing here writes to KiCad. The JSON layout (``oa-tuning-profile/1``) is
ours; the exact KiCad 10 tuning-profile file format is not pinned down yet (see the WP-203
change request), so an exporter will map these fields when it is written.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from oa_model._base import OAModel

__all__ = ["LayerDelay", "TuningProfile", "TuningProfileSet", "build_tuning_profiles"]

PS_PER_MM_PER_S_PER_M = 1e9  # 1 s/m = 1e9 ps/mm


class LayerDelay(OAModel):
    layer: str = Field(description="Copper layer id.")
    delay_ps_per_mm: float = Field(description="Length-weighted propagation delay [ps/mm].")
    delay_min_ps_per_mm: float = Field(description="Smallest section delay [ps/mm].")
    delay_max_ps_per_mm: float = Field(description="Largest section delay [ps/mm].")
    eps_eff: float = Field(description="Effective permittivity equivalent to the delay [1].")
    length_m: float = Field(description="Routed length the average is based on [m].")
    n_nets: int = Field(description="Nets contributing on this layer [count].")


class TuningProfile(OAModel):
    name: str = Field(description="Profile name: the net class, or 'default'.")
    mode: Literal["single", "diff"] = Field(
        description="'single' = single-ended delay, 'diff' = odd-mode delay of coupled pairs."
    )
    layers: tuple[LayerDelay, ...] = Field(description="Per-layer delays, ordered by layer id.")
    accuracy_class: str = Field(description="Accuracy class of the solved sections.")
    notes: tuple[str, ...] = Field(default=(), description="Caveats.")


class TuningProfileSet(OAModel):
    schema_id: Literal["oa-tuning-profile/1"] = Field(
        default="oa-tuning-profile/1", description="Layout tag of this suggestion."
    )
    board_hash: str = Field(description="Content hash of the analysed board.")
    profiles: tuple[TuningProfile, ...] = Field(description="One profile per class and mode.")

    def to_json(self) -> str:
        """Canonical JSON text of the suggestion (what a file export would contain)."""
        return self.model_dump_json(indent=2)


class _Acc:
    __slots__ = ("dlen", "dmax", "dmin", "length", "nets")

    def __init__(self) -> None:
        self.length = 0.0
        self.dlen = 0.0
        self.dmin = float("inf")
        self.dmax = 0.0
        self.nets: set[str] = set()


def build_tuning_profiles(
    pieces: Iterable[tuple[str, str, str, str, float, float]],
    *,
    board_hash: str,
    accuracy_class: str,
) -> TuningProfileSet:
    """`pieces` = (group, mode, layer, net, length [m], delay [s/m]) for every run."""
    acc: dict[tuple[str, str], dict[str, _Acc]] = {}
    for group, mode, layer, net, length, delay in pieces:
        if length <= 0:
            continue
        a = acc.setdefault((group, mode), {}).setdefault(layer, _Acc())
        a.length += length
        a.dlen += length * delay
        a.dmin = min(a.dmin, delay)
        a.dmax = max(a.dmax, delay)
        a.nets.add(net)
    out: list[TuningProfile] = []
    for (group, mode), layers in sorted(acc.items()):
        rows = []
        for layer, a in sorted(layers.items()):
            d = a.dlen / a.length
            rows.append(
                LayerDelay(
                    layer=layer,
                    delay_ps_per_mm=d * PS_PER_MM_PER_S_PER_M,
                    delay_min_ps_per_mm=a.dmin * PS_PER_MM_PER_S_PER_M,
                    delay_max_ps_per_mm=a.dmax * PS_PER_MM_PER_S_PER_M,
                    eps_eff=(d * 299792458.0) ** 2,
                    length_m=a.length,
                    n_nets=len(a.nets),
                )
            )
        out.append(
            TuningProfile(
                name=group,
                mode="diff" if mode == "diff" else "single",
                layers=tuple(rows),
                accuracy_class=accuracy_class,
                notes=("via delay is not included", "static (lossless) delay unless freqs given"),
            )
        )
    return TuningProfileSet(board_hash=board_hash, profiles=tuple(out))
