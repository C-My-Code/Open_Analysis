# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""S-parameter payload (interfaces.md section 4.1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_model.arrays import Floats, NDArray
from oa_model.common import Frequency, NetId, Vec2
from oa_results.artifact import ArtifactRef
from oa_results.container import PayloadBase, as_np, shape_of, value_span
from oa_results.quality import QualityReport

if TYPE_CHECKING:
    import skrf


class PortMapEntry(OAModel):
    port: str
    nets: tuple[NetId, ...] = ()
    terminals: tuple[str, ...] = ()
    location: Vec2 | None = None


class NetworkResult(PayloadBase):
    kind: Literal["network"] = "network"
    freqs: Floats  # Hz
    s: NDArray  # (F, P, P) complex128
    z0: NDArray  # (P,) or (F, P) complex
    port_names: tuple[str, ...]
    port_map: tuple[PortMapEntry, ...]
    mixed_mode_pairs: tuple[tuple[int, int], ...] = ()
    validity_fmax: Frequency | None = None  # None = full band
    quality: QualityReport  # REQUIRED (gate, section 4.5)
    touchstone: ArtifactRef | None = None

    def to_skrf(self) -> skrf.Network:
        import skrf

        f = as_np(self.freqs)
        net = skrf.Network(
            frequency=skrf.Frequency.from_f(f, unit="hz"),
            s=as_np(self.s),
            z0=as_np(self.z0),
        )
        net.port_names = list(self.port_names)
        return net

    def _digest(self) -> str:
        span = value_span(self.freqs)
        band = f"{span[0]:.4g}..{span[1]:.4g} Hz" if span else "no frequencies"
        return (
            f"{len(self.port_names)}-port S-parameters, {band}, quality {self.quality.verdict}"
            f" (PQM {self.quality.pqm:.1f}, RQM {self.quality.rqm:.1f},"
            f" CQM {self.quality.cqm:.1f})."
        )

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        q = self.quality
        return {
            "n_ports": len(self.port_names),
            "port_names": list(self.port_names[:max_items]),
            "n_freqs": int(as_np(self.freqs).size),
            "freq_span_hz": list(value_span(self.freqs) or []),
            "s_shape": list(shape_of(self.s)),
            "s_magnitude_span": list(value_span(self.s) or []),
            "validity_fmax_hz": self.validity_fmax,
            "touchstone": self.touchstone.name if self.touchstone else None,
            "quality": {
                "verdict": q.verdict,
                "pqm": q.pqm,
                "rqm": q.rqm,
                "cqm": q.cqm,
                "max_singular_value": q.max_singular_value,
                "dc": q.dc,
                "fixes_applied": list(q.fixes_applied[:max_items]),
            },
        }
