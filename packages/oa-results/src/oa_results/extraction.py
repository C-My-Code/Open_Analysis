# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quasi-static extraction and far-field payloads (interfaces.md section 4.1, M2)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_model.arrays import Floats, NDArray
from oa_model.common import Frequency
from oa_results.artifact import ArtifactRef
from oa_results.container import PayloadBase, as_np, shape_of, value_span


class MatrixPassivity(OAModel):
    """Passivity/consistency check of an extracted C, R, L set."""

    ok: bool
    fixes_applied: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class ExtractionResult(PayloadBase):
    """Quasi-static 3D (Q3D-class) extraction."""

    kind: Literal["extraction"] = "extraction"
    terminals: tuple[str, ...]  # net/terminal names, matrix order
    C: NDArray  # (T, T) Maxwell capacitance, F
    G: NDArray | None
    freqs: Floats | None  # for R(f), L(f)
    R: NDArray | None
    L: NDArray | None
    basis: Literal["port_loop", "partial"]
    validity_fmax: Frequency  # REQUIRED
    passivity: MatrixPassivity
    spice: ArtifactRef | None = None

    def _digest(self) -> str:
        return (
            f"Quasi-static extraction of {len(self.terminals)} terminal(s), basis {self.basis}, "
            f"valid up to {self.validity_fmax:.4g} Hz, "
            f"passivity {'ok' if self.passivity.ok else 'violated'}."
        )

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return {
            "terminals": list(self.terminals[:max_items]),
            "basis": self.basis,
            "validity_fmax_hz": self.validity_fmax,
            "C_shape": shape_of(self.C),
            "C_span_f": list(value_span(self.C) or []),
            "has_R": self.R is not None,
            "has_L": self.L is not None,
            "n_freqs": int(as_np(self.freqs).size) if self.freqs is not None else 0,
            "passivity_ok": self.passivity.ok,
            "spice": self.spice.name if self.spice else None,
        }


class FarFieldResult(PayloadBase):
    """openEMS nf2ff (M2), Palace/own transform (M3)."""

    kind: Literal["farfield"] = "farfield"
    freqs: Floats
    theta: Floats
    phi: Floats
    e_theta: NDArray  # (F, T, P) complex, V at r = 1 m normalization
    e_phi: NDArray
    p_rad: Floats
    p_acc: Floats | None = None
    directivity_max: Floats
    efficiency: Floats | None = None

    def _digest(self) -> str:
        d = value_span(self.directivity_max)
        dtxt = f", max directivity {d[1]:.3g}" if d else ""
        return f"Far field at {int(as_np(self.freqs).size)} frequency point(s){dtxt}."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        return {
            "n_freqs": int(as_np(self.freqs).size),
            "freq_span_hz": list(value_span(self.freqs) or []),
            "grid": [int(as_np(self.theta).size), int(as_np(self.phi).size)],
            "p_rad_span_w": list(value_span(self.p_rad) or []),
            "directivity_max_span": list(value_span(self.directivity_max) or []),
        }
