# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Arrow IPC encoders for board geometry and result tables (interfaces.md section 7).

Geometry is float32 in **millimetres** (deck.gl binary attributes). Net names are carried in the
schema metadata (``net_names``, a JSON list) and ``net_idx`` indexes it (-1 = no net).
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from itertools import pairwise
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from oa_model.board import Board
from oa_model.geometry import ArcSeg, LineSeg

__all__ = ["drills_ipc", "polygons_ipc", "rows_ipc", "tessellate", "tracks_ipc"]

MM = 1e3
ARC_TOL_M = 1e-5  # chord error of tessellated arcs [m]


def _ipc(table: pa.Table) -> bytes:
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, table.schema) as w:
        w.write_table(table)
    return bytes(sink.getvalue().to_pybytes())


def _net_table(nets: Iterable[str | None]) -> tuple[list[str], dict[str | None, int]]:
    names = sorted({n for n in nets if n is not None})
    idx: dict[str | None, int] = {n: i for i, n in enumerate(names)}
    idx[None] = -1
    return names, idx


def _meta(names: list[str], **extra: Any) -> dict[bytes, bytes]:
    meta = {b"net_names": json.dumps(names).encode(), b"unit": b"mm"}
    meta.update({k.encode(): str(v).encode() for k, v in extra.items()})
    return meta


def polygons_ipc(
    by_net: dict[str | None, Any], layer: str, only_nets: Sequence[str] | None = None
) -> bytes:
    """One row per copper polygon: ``net_idx``, ``item_idx``, ``ring_offsets``, ``xy`` (f32, mm).

    ``ring_offsets`` holds the start vertex of every ring in ``xy`` (first ring = exterior, the
    rest are holes). ``item_idx`` numbers the polygons of a net on the layer.
    """
    names, idx = _net_table(by_net)
    net_col: list[int] = []
    item_col: list[int] = []
    offs_col: list[list[int]] = []
    xy_col: list[list[float]] = []
    for net in sorted(by_net, key=lambda n: (n is None, n or "")):
        if only_nets is not None and net not in only_nets:
            continue
        geom = by_net[net]
        for k, p in enumerate(getattr(geom, "geoms", [geom])):
            if p.is_empty:
                continue
            offs, pts, n0 = [], [], 0
            for r in [p.exterior, *p.interiors]:
                c = np.asarray(r.coords, dtype=np.float64)[:-1]  # drop the closing vertex
                offs.append(n0)
                n0 += len(c)
                pts.append(c)
            net_col.append(idx[net])
            item_col.append(k)
            offs_col.append(offs)
            xy_col.append((np.concatenate(pts) * MM).astype(np.float32).ravel().tolist())
    schema = pa.schema(
        [
            ("net_idx", pa.int32()),
            ("item_idx", pa.int32()),
            ("ring_offsets", pa.list_(pa.int32())),
            ("xy", pa.list_(pa.float32())),
        ],
        metadata=_meta(names, layer=layer, geometry="polygons"),
    )
    table = pa.Table.from_arrays(
        [
            pa.array(net_col, pa.int32()),
            pa.array(item_col, pa.int32()),
            pa.array(offs_col, pa.list_(pa.int32())),
            pa.array(xy_col, pa.list_(pa.float32())),
        ],
        schema=schema,
    )
    return _ipc(table)


def tessellate(seg: LineSeg | ArcSeg, tol: float = ARC_TOL_M) -> list[tuple[float, float]]:
    """Vertices of a path segment (arcs split so the chord error is below ``tol`` [m])."""
    if isinstance(seg, LineSeg):
        return [seg.start, seg.end]
    cx, cy = seg.center
    r = math.hypot(seg.start[0] - cx, seg.start[1] - cy)
    a0 = math.atan2(seg.start[1] - cy, seg.start[0] - cx)
    a1 = math.atan2(seg.end[1] - cy, seg.end[0] - cx)
    sweep = (a1 - a0) % (2 * math.pi) if seg.ccw else -((a0 - a1) % (2 * math.pi))
    if sweep == 0.0:
        sweep = 2 * math.pi if seg.ccw else -2 * math.pi
    step = 2 * math.acos(max(0.0, 1.0 - tol / r)) if r > tol else math.pi / 2
    n = max(1, math.ceil(abs(sweep) / max(step, 1e-3)))
    pts = [
        (cx + r * math.cos(a0 + sweep * i / n), cy + r * math.sin(a0 + sweep * i / n))
        for i in range(n + 1)
    ]
    pts[0], pts[-1] = seg.start, seg.end
    return pts


def tracks_ipc(board: Board, layer: str) -> bytes:
    """Segments ``x0, y0, x1, y1, width, net_idx, item_idx`` (f32 mm), arcs tessellated."""
    names, idx = _net_table(t.net for t in board.tracks)
    cols: dict[str, list[float]] = {k: [] for k in ("x0", "y0", "x1", "y1", "width")}
    net_col: list[int] = []
    item_col: list[int] = []
    tracks = sorted((t for t in board.tracks if t.layer == layer), key=lambda t: t.id)
    for k, t in enumerate(tracks):
        for seg in t.path:
            pts = tessellate(seg)
            for (xa, ya), (xb, yb) in pairwise(pts):
                cols["x0"].append(xa * MM)
                cols["y0"].append(ya * MM)
                cols["x1"].append(xb * MM)
                cols["y1"].append(yb * MM)
                cols["width"].append(t.width * MM)
                net_col.append(idx[t.net])
                item_col.append(k)
    schema = pa.schema(
        [(c, pa.float32()) for c in cols] + [("net_idx", pa.int32()), ("item_idx", pa.int32())],
        metadata=_meta(names, layer=layer, geometry="tracks"),
    )
    arrays = [pa.array(v, pa.float32()) for v in cols.values()]
    arrays += [pa.array(net_col, pa.int32()), pa.array(item_col, pa.int32())]
    return _ipc(pa.Table.from_arrays(arrays, schema=schema))


def drills_ipc(drills: Sequence[Any]) -> bytes:
    """Holes ``x, y, d, kind, net_idx`` (f32 mm; kind 0 = plated, 1 = npth)."""
    names, idx = _net_table(d.net for d in drills)
    ds = sorted(drills, key=lambda d: d.id)
    schema = pa.schema(
        [
            ("x", pa.float32()),
            ("y", pa.float32()),
            ("d", pa.float32()),
            ("kind", pa.int8()),
            ("net_idx", pa.int32()),
        ],
        metadata=_meta(names, geometry="drills", kinds=json.dumps(["plated", "npth"])),
    )
    table = pa.Table.from_arrays(
        [
            pa.array([d.position[0] * MM for d in ds], pa.float32()),
            pa.array([d.position[1] * MM for d in ds], pa.float32()),
            pa.array([d.diameter * MM for d in ds], pa.float32()),
            pa.array([0 if d.kind == "plated" else 1 for d in ds], pa.int8()),
            pa.array([idx[d.net] for d in ds], pa.int32()),
        ],
        schema=schema,
    )
    return _ipc(table)


def rows_ipc(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    """A result table window as an Arrow IPC stream (column types inferred)."""
    arrays = []
    for i, _ in enumerate(columns):
        vals = [r[i] for r in rows]
        try:
            arrays.append(pa.array(vals))
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            arrays.append(pa.array([None if v is None else str(v) for v in vals]))
    return _ipc(pa.Table.from_arrays(arrays, names=list(columns)))
