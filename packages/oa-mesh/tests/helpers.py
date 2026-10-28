# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared builders for the oa_mesh tests."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from oa_mesh import Contact, SheetPiece
from oa_model.arrays import Points
from oa_model.geometry import Polygon


def rect(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def circle(
    cx: float, cy: float, r: float, n: int = 24, cw: bool = False
) -> list[tuple[float, float]]:
    th = 2 * np.pi * np.arange(n) / n
    pts = [(cx + r * np.cos(t), cy + r * np.sin(t)) for t in th]
    return pts[::-1] if cw else pts


def poly(ext: Sequence[tuple[float, float]], *holes: Sequence[tuple[float, float]]) -> Polygon:
    return Polygon(exterior=Points(ext), holes=tuple(Points(h) for h in holes))


def piece(
    polygons: Sequence[Polygon], contacts: Sequence[Contact] = (), pid: str = "L2:NET"
) -> SheetPiece:
    return SheetPiece(
        id=pid, layer="L2", net="NET", polygons=tuple(polygons), contacts=tuple(contacts)
    )


def random_centres(
    n: int, w: float, h: float, margin: float, spacing: float, seed: int = 1
) -> list[tuple[float, float]]:
    """``n`` random points in the rectangle, at least ``spacing`` apart (grid-bucketed)."""
    rng = np.random.default_rng(seed)
    out: list[tuple[float, float]] = []
    buckets: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for _ in range(200 * n):
        if len(out) >= n:
            return out
        c = (float(rng.uniform(margin, w - margin)), float(rng.uniform(margin, h - margin)))
        bi, bj = int(c[0] / spacing), int(c[1] / spacing)
        near = [
            o for di in (-1, 0, 1) for dj in (-1, 0, 1) for o in buckets.get((bi + di, bj + dj), [])
        ]
        if all(np.hypot(c[0] - o[0], c[1] - o[1]) >= spacing for o in near):
            out.append(c)
            buckets.setdefault((bi, bj), []).append(c)
    if len(out) >= n:
        return out
    raise RuntimeError("could not place points; relax spacing")
