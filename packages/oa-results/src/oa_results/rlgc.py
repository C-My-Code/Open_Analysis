# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Impedance service output (interfaces.md section 1.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_model.arrays import Floats, NDArray
from oa_results.container import PayloadBase, ResultContainer, as_np, value_span


class RLGC(OAModel):
    """Per-unit-length matrices on a frequency grid. N = number of signal conductors."""

    freqs: Floats  # Hz, ascending; may include 0.0 (DC) as first point
    R: NDArray  # (F, N, N) ohm/m
    L: NDArray  # (F, N, N) H/m
    G: NDArray  # (F, N, N) S/m
    C: NDArray  # (F, N, N) F/m (Maxwell form, C_ij <= 0 off-diagonal)
    conductor_ids: tuple[str, ...]


class LineParams(OAModel):
    """Derived characteristic values at each frequency (computed by oa_impedance)."""

    freqs: Floats
    zc_modal: NDArray  # (F, N) complex
    eps_eff_modal: NDArray  # (F, N) real
    zc_matrix: NDArray  # (F, N, N) complex
    z0: Floats | None = None  # N == 1: |Zc|
    z_diff: Floats | None = None  # N == 2
    z_comm: Floats | None = None
    z_odd: Floats | None = None
    z_even: Floats | None = None
    delay_per_m: NDArray  # (F, N) s/m per mode
    loss_db_per_m: NDArray  # (F, N)


class ImpedanceResult(PayloadBase):
    xs_key: str
    method: Literal["closed_form", "bem", "fem"]
    model_name: str  # e.g. "hammerstad_jensen_1980", "oa-bem 0.2"
    rlgc: RLGC | None
    params: LineParams
    validity: Literal["in_range", "extrapolated", "invalid"]
    notes: tuple[str, ...] = ()
    solver_version: str

    summary_kind = "impedance"

    def _digest(self) -> str:
        p = self.params
        parts = [f"{self.method} impedance ({self.model_name}), validity {self.validity}"]
        for label, arr in (("Z0", p.z0), ("Zdiff", p.z_diff)):
            if arr is not None:
                span = value_span(arr)
                if span:
                    parts.append(f"{label} {span[0]:.4g}..{span[1]:.4g} ohm")
        return "; ".join(parts) + "."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        p = self.params
        stats: dict[str, JsonValue] = {
            "xs_key": self.xs_key,
            "method": self.method,
            "model_name": self.model_name,
            "validity": self.validity,
            "n_freqs": int(as_np(p.freqs).size),
            "n_conductors": int(as_np(p.zc_modal).shape[-1]) if as_np(p.zc_modal).ndim else 0,
            "freq_span_hz": list(value_span(p.freqs) or []),
            "has_rlgc": self.rlgc is not None,
            "notes": list(self.notes[:max_items]),
            "solver_version": self.solver_version,
        }
        for name in ("z0", "z_diff", "z_comm", "z_odd", "z_even"):
            arr = getattr(p, name)
            if arr is not None:
                stats[f"{name}_span_ohm"] = list(value_span(arr) or [])
        return stats


class RLGCResultSet(PayloadBase):
    kind: Literal["rlgc"] = "rlgc"
    results: tuple[ImpedanceResult, ...]

    def _digest(self) -> str:
        n = len(self.results)
        methods = sorted({r.method for r in self.results})
        bad = sum(1 for r in self.results if r.validity != "in_range")
        return (
            f"{n} impedance result(s), methods {', '.join(methods) or 'none'}; {bad} not in range."
        )

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return {
            "n_results": len(self.results),
            "results": [
                {"xs_key": r.xs_key, "method": r.method, "validity": r.validity}
                for r in self.results[:max_items]
            ],
        }


__all__ = ["RLGC", "ImpedanceResult", "LineParams", "RLGCResultSet", "ResultContainer"]
