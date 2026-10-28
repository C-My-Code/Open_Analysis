# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shapely clean-up of real-world copper before it reaches gmsh.

Pipeline (all lengths in metres):

1. ``make_valid`` for self-intersecting / bow-tie / hole-crossing polygons, keeping polygonal parts.
2. ``unary_union`` (merges overlapping or duplicated copper).
3. Snap every vertex to a ``snap`` grid (near-coincident vertices and edges collapse).
4. Morphological opening (erode then dilate, mitre joins) by ``min_feature``/2: removes slivers,
   hair-thin spurs, specks, and resolves holes that touch the outline or each other at a point.
5. Morphological closing by the same amount: fills slits narrower than ``min_feature``.
6. Optional Douglas-Peucker ``simplify``.

Mitre joins keep straight edges and convex corners exact, so polygons without sub-``min_feature``
features come out unchanged (up to ~1e-12 relative area).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import shapely  # type: ignore[import-untyped]
from shapely.geometry import Polygon as ShPolygon  # type: ignore[import-untyped]
from shapely.geometry.polygon import orient  # type: ignore[import-untyped]
from shapely.ops import unary_union  # type: ignore[import-untyped]

from oa_mesh.errors import MeshError
from oa_mesh.types import CleanOptions, CleanReport
from oa_model.geometry import Polygon

__all__ = ["clean_polygons", "polygonal_parts", "to_shapely"]

_MITRE_LIMIT = 10.0


def to_shapely(poly: Polygon) -> Any:
    """Convert an ``oa_model`` Polygon to a (possibly invalid) shapely Polygon."""
    return ShPolygon(
        poly.exterior.array.copy(),
        [h.array.copy() for h in poly.holes if len(h) >= 3],
    )


def polygonal_parts(geom: Any) -> list[Any]:
    """All non-empty polygons of ``geom`` (drops lines, points, empties), in a stable order."""
    if geom is None or geom.is_empty:
        return []
    t = geom.geom_type
    if t == "Polygon":
        return [geom] if geom.area > 0 else []
    if t in ("MultiPolygon", "GeometryCollection"):
        out: list[Any] = []
        for g in geom.geoms:
            out.extend(polygonal_parts(g))
        return out
    return []


def _robust_polygon(poly: Polygon) -> tuple[Any, bool]:
    """Shell minus the union of its holes, each repaired on its own.

    ``make_valid`` on a polygon whose holes touch, cross or leave the shell guesses; subtracting
    the repaired holes from the repaired shell gives the intended copper. Returns (geometry,
    whether any repair was needed).
    """
    sp = to_shapely(poly)
    if sp.is_valid:
        return sp, False
    shell = ShPolygon(poly.exterior.array.copy())
    if not shell.is_valid:
        shell = shapely.make_valid(shell)
    holes = []
    for h in poly.holes:
        if len(h) < 3:
            continue
        hp = ShPolygon(h.array.copy())
        holes.append(hp if hp.is_valid else shapely.make_valid(hp))
    out = shell.difference(unary_union(holes)) if holes else shell
    return out, True


def _valid_union(parts: Sequence[Any]) -> Any:
    g = unary_union(list(parts))
    if not g.is_valid:
        g = shapely.make_valid(g)
    return unary_union(polygonal_parts(g)) if not g.is_empty else g


def clean_polygons(
    polys: Sequence[Polygon], opts: CleanOptions, *, path: str
) -> tuple[list[Any], CleanReport]:
    """Clean ``polys`` into disjoint, valid, CCW shapely polygons (holes CW).

    Raises ``MeshError`` (``MESH_EMPTY_GEOMETRY`` / ``MESH_INVALID_GEOMETRY``) with ``path`` when
    nothing usable remains or the result is still invalid.
    """
    warnings: list[str] = []
    parts: list[Any] = []
    n_repaired = 0
    n_dropped = 0
    for i, p in enumerate(polys):
        if len(p.exterior) < 3:
            n_dropped += 1
            warnings.append(f"polygon {i}: fewer than 3 vertices, dropped")
            continue
        sp, repaired = _robust_polygon(p)
        n_repaired += int(repaired)
        got = polygonal_parts(sp)
        if not got:
            n_dropped += 1
            warnings.append(f"polygon {i}: zero area, dropped")
        parts.extend(got)
    if not parts:
        raise MeshError(
            "no polygon with non-zero area in the piece",
            code="MESH_EMPTY_GEOMETRY",
            path=path,
            hint="check that the copper polygons have at least 3 non-collinear vertices",
        )
    g = _valid_union(parts)
    area_in = float(g.area)

    g = shapely.set_precision(g, opts.snap)
    g = _valid_union([g]) if not g.is_empty else g
    d = max(opts.min_feature, 4 * opts.snap) / 2
    base_area = float(g.area)
    opened = g.buffer(-d, join_style="mitre", mitre_limit=_MITRE_LIMIT)
    opened = opened.buffer(d, join_style="mitre", mitre_limit=_MITRE_LIMIT)
    opened = shapely.set_precision(opened, opts.snap)
    opened = _valid_union([opened]) if not opened.is_empty else opened
    removed = float(g.difference(opened).area) if not opened.is_empty else base_area
    closed = opened.buffer(d, join_style="mitre", mitre_limit=_MITRE_LIMIT)
    closed = closed.buffer(-d, join_style="mitre", mitre_limit=_MITRE_LIMIT)
    closed = shapely.set_precision(closed, opts.snap)
    closed = _valid_union([closed]) if not closed.is_empty else closed
    closed_area = float(closed.difference(opened).area) if not closed.is_empty else 0.0
    g = closed
    if opts.simplify > 0 and not g.is_empty:
        g = _valid_union([g.simplify(opts.simplify, preserve_topology=True)])

    result = polygonal_parts(g)
    if not result:
        raise MeshError(
            "nothing is left of the piece after cleaning (all features are thinner than "
            f"min_feature = {opts.min_feature:g} m)",
            code="MESH_EMPTY_GEOMETRY",
            path=path,
            hint="lower CleanOptions.min_feature or check the polygon units (metres)",
        )
    if removed > 0.01 * area_in:
        warnings.append(f"cleaning removed {100 * removed / area_in:.2f} % of the copper area")
    out = [orient(p, 1.0) for p in sorted(result, key=lambda q: (q.bounds, q.area))]
    bad = [i for i, p in enumerate(out) if not p.is_valid]
    if bad:
        raise MeshError(
            f"{len(bad)} polygon(s) are still invalid after repair",
            code="MESH_INVALID_GEOMETRY",
            path=path,
            hint="simplify the outline or raise CleanOptions.min_feature",
            details={"polygon_indices": bad},
        )
    rep = CleanReport(
        area_in=area_in,
        area_out=float(sum(p.area for p in out)),
        n_polygons=len(out),
        n_holes=int(sum(len(p.interiors) for p in out)),
        n_repaired=n_repaired,
        n_dropped=n_dropped,
        removed_area=removed,
        closed_area=closed_area,
        warnings=tuple(warnings),
    )
    return out, rep


def ring_array(ring: Any) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Coordinates of a shapely ring without the closing duplicate, as (N, 2) float64."""
    return np.asarray(ring.coords, dtype=np.float64)[:-1]
