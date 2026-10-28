# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Meshes and field data (interfaces.md section 4.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import JsonValue

from oa_model._base import OAModel
from oa_model.arrays import NDArray, Points
from oa_model.common import LayerId, NetId, Vec2
from oa_results.container import PayloadBase, shape_of, value_span


class TriMesh2D(OAModel):
    nodes: Points  # (N, 2) board frame
    triangles: NDArray  # (M, 3) int64
    layer: LayerId | None = None
    z: float | None = None


class TetMesh3D(OAModel):  # M3+ (field dumps from Palace/Elmer)
    nodes: NDArray  # (N, 3)
    tets: NDArray  # (M, 4) int64


class RasterGrid2D(OAModel):  # thermal FVM and raster DC reference
    origin: Vec2
    dx: float
    dy: float
    nx: int
    ny: int
    layer: LayerId | None = None


class FieldData(OAModel):
    name: str  # "V", "dV", "J_mag", "J_vec", "T", "q"
    unit: str  # "V", "A/m^2", "K", "W/m^2"
    location: Literal["node", "cell"]
    values: NDArray  # (n,) or (n, k) real
    clim_hint: tuple[float, float] | None = None
    log_scale: bool = False


class FieldPiece(OAModel):
    mesh: TriMesh2D | TetMesh3D | RasterGrid2D
    fields: tuple[FieldData, ...]
    net: NetId | None = None


class FieldResult(PayloadBase):
    kind: Literal["field"] = "field"
    pieces: tuple[FieldPiece, ...]  # one per layer (2D) or one volume piece (3D)

    def _digest(self) -> str:
        names = sorted({f.name for p in self.pieces for f in p.fields})
        return f"{len(self.pieces)} field piece(s) with quantities {', '.join(names) or 'none'}."

    def _stats(self, max_items: int) -> dict[str, JsonValue]:
        pieces: list[JsonValue] = []
        for p in self.pieces[:max_items]:
            fields: list[JsonValue] = [
                {
                    "name": f.name,
                    "unit": f.unit,
                    "location": f.location,
                    "shape": shape_of(f.values),
                    "span": list(value_span(f.values) or []),
                }
                for f in p.fields[:max_items]
            ]
            pieces.append(
                {
                    "mesh": type(p.mesh).__name__,
                    "layer": getattr(p.mesh, "layer", None),
                    "net": p.net,
                    "fields": fields,
                }
            )
        return {"n_pieces": len(self.pieces), "pieces": pieces}
