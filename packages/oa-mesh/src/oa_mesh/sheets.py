# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``mesh_copper_sheets``: constrained triangle meshes of copper polygons with holes.

Each ``SheetPiece`` is cleaned with shapely, then meshed in its own gmsh model (``.geo`` kernel,
Frontal-Delaunay). Contacts are n-gon rings whose vertices are gmsh points and whose edges are
single transfinite elements, so the ring nodes are exactly the ring vertices (radial error at
floating-point level) and ``SheetMesh.contacts`` maps contact id -> node indices.

Size fields are gmsh ``Distance`` + ``Threshold`` fields combined with ``Min``; ``size`` at the
feature grows by ``grading`` per unit distance until ``h_default``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import shapely  # type: ignore[import-untyped]
from shapely.geometry import Point as ShPoint  # type: ignore[import-untyped]
from shapely.geometry import Polygon as ShPolygon
from shapely.ops import unary_union  # type: ignore[import-untyped]
from shapely.strtree import STRtree  # type: ignore[import-untyped]

from oa_mesh._clean import clean_polygons, polygonal_parts, ring_array
from oa_mesh._quality import quality_report, triangle_areas
from oa_mesh._session import gmsh_session, gmsh_version
from oa_mesh.errors import MeshError
from oa_mesh.types import (
    CleanOptions,
    Contact,
    ContactNodes,
    PhysicalGroup,
    SheetMesh,
    SheetPiece,
    SizeField,
)
from oa_model.arrays import NDArray, Points

__all__ = ["mesh_copper_sheets"]

_S = 1e6  # working unit: micrometres (keeps gmsh tolerances far below 1 nm)
_MAX_SAMPLES = 200_000
_MIN_SEGMENTS = 16
_MAX_SEGMENTS = 256


def _sample_polyline(xy: npt.NDArray[np.float64], spacing: float, path: str) -> npt.NDArray[Any]:
    ls = shapely.LineString(xy)
    n = math.ceil(ls.length / spacing) + 1
    if n > _MAX_SAMPLES:
        raise MeshError(
            f"size field would need {n} sample points (limit {_MAX_SAMPLES})",
            code="MESH_SIZE_FIELD",
            path=path,
            hint="increase the field size or restrict it with piece_ids",
        )
    d = np.linspace(0.0, ls.length, n)
    return np.asarray(shapely.get_coordinates(shapely.line_interpolate_point(ls, d)))


def _field_applies(sf: SizeField, piece_id: str) -> bool:
    return sf.piece_ids is None or piece_id in sf.piece_ids


def _contact_ring(c: Contact, n: int) -> npt.NDArray[np.float64]:
    th = 2.0 * np.pi * np.arange(n) / n
    return np.column_stack([c.x + c.radius * np.cos(th), c.y + c.radius * np.sin(th)])


def _auto_segments(c: Contact, piece: SheetPiece, fields: Sequence[SizeField], h: float) -> int:
    if c.segments is not None:
        return c.segments
    size = h
    for sf in fields:
        if (
            sf.kind == "contacts"
            and _field_applies(sf, piece.id)
            and (sf.contact_ids is None or c.id in sf.contact_ids)
        ):
            size = min(size, sf.size)
    n = math.ceil(2.0 * math.pi * c.radius / size)
    return max(_MIN_SEGMENTS, min(_MAX_SEGMENTS, n))


class _PreparedPiece:
    """A cleaned piece with contact geometry resolved (no gmsh needed)."""

    def __init__(
        self,
        piece: SheetPiece,
        clean: CleanOptions,
        fields: Sequence[SizeField],
        h_default: float,
    ) -> None:
        self.piece = piece
        path = f"pieces[{piece.id}]"
        polys, self.report = clean_polygons(piece.polygons, clean, path=f"{path}.polygons")
        cop = unary_union(polys)
        ids = [c.id for c in piece.contacts]
        if len(set(ids)) != len(ids):
            dup = sorted({i for i in ids if ids.count(i) > 1})
            raise MeshError(
                f"duplicate contact ids {dup}",
                code="MESH_CONTACT_OVERLAP",
                path=f"{path}.contacts",
                hint="contact ids must be unique within a piece",
            )
        self.rings: list[npt.NDArray[np.float64] | None] = []
        shapes: list[Any] = []
        for k, c in enumerate(piece.contacts):
            cpath = f"{path}.contacts[{k}]"
            if c.radius > 0:
                ring = _contact_ring(c, _auto_segments(c, piece, fields, h_default))
                shape = ShPolygon(ring)
                self.rings.append(ring)
            else:
                shape = ShPoint(c.x, c.y)
                self.rings.append(None)
            if not cop.buffer(clean.snap).covers(shape):
                raise MeshError(
                    f"contact '{c.id}' is not covered by the copper of piece '{piece.id}'",
                    code="MESH_CONTACT_OUTSIDE",
                    path=cpath,
                    hint=(
                        "the via/pad ring must lie inside copper of this net and layer; check "
                        "antipads, pad shape and the piece polygons"
                    ),
                )
            shapes.append(shape)
        if shapes:
            tree = STRtree(shapes)
            left, right = tree.query(shapes, predicate="intersects")
            for a, b in zip(left.tolist(), right.tolist(), strict=True):
                if a < b and (self.rings[a] is not None or self.rings[b] is not None):
                    raise MeshError(
                        f"contacts '{ids[a]}' and '{ids[b]}' overlap",
                        code="MESH_CONTACT_OVERLAP",
                        path=f"{path}.contacts",
                        hint="rings must be disjoint; shrink the radii or merge the contacts",
                    )
                if a < b:
                    raise MeshError(
                        f"point contacts '{ids[a]}' and '{ids[b]}' coincide",
                        code="MESH_CONTACT_OVERLAP",
                        path=f"{path}.contacts",
                        hint="merge duplicate point contacts",
                    )
        self.shapes = shapes
        for k, c in enumerate(piece.contacts):
            if c.mode == "hole" and c.radius > 0:
                cop = cop.difference(shapes[k])
        self.polys = sorted(polygonal_parts(cop), key=lambda q: (q.bounds, q.area))
        self.area = float(sum(p.area for p in self.polys))


def _key(x: float, y: float) -> tuple[float, float]:
    return (round(x * _S, 6), round(y * _S, 6))


def _boundary_buckets(
    prep: _PreparedPiece, pts: dict[tuple[float, float], int], h: float
) -> dict[float, list[int]]:
    """gmsh point tags of boundary vertices grouped by (quantized) local edge length < h."""
    out: dict[float, list[int]] = {}
    rings = [ring_array(r) for p in prep.polys for r in (p.exterior, *p.interiors)]
    rings += [r for r in prep.rings if r is not None]
    for a in rings:
        nxt = np.roll(a, -1, axis=0)
        el = np.hypot(*(nxt - a).T)
        loc = np.minimum(el, np.roll(el, 1))
        for (x, y), ls in zip(a.tolist(), loc.tolist(), strict=True):
            if ls < h:
                q = 2.0 ** (math.floor(2.0 * math.log2(ls)) / 2.0)
                tag = pts.get(_key(x, y))
                if tag is not None:
                    out.setdefault(q, []).append(tag)
    return dict(sorted(out.items()))


def _mesh_piece(
    gmsh: Any, prep: _PreparedPiece, fields: Sequence[SizeField], h: float, grading: float
) -> SheetMesh:
    piece = prep.piece
    geo = gmsh.model.geo
    pts: dict[tuple[float, float], int] = {}
    lines: dict[tuple[int, int], int] = {}

    def point(x: float, y: float) -> int:
        k = _key(x, y)
        t = pts.get(k)
        if t is None:
            t = int(geo.addPoint(x * _S, y * _S, 0.0))
            pts[k] = t
        return t

    def line(a: int, b: int) -> int:
        """Signed line tag running a -> b (one geometric line per point pair)."""
        if a == b:
            raise MeshError(
                "degenerate zero-length edge after snapping",
                code="MESH_INVALID_GEOMETRY",
                path=f"pieces[{piece.id}]",
                hint="raise CleanOptions.snap or min_feature",
            )
        key = (a, b) if a < b else (b, a)
        t = lines.get(key)
        if t is None:
            t = int(geo.addLine(key[0], key[1]))
            lines[key] = t
        return t if a < b else -t

    def loop(coords: npt.NDArray[np.float64]) -> int:
        ids = [point(float(x), float(y)) for x, y in coords]
        return int(
            geo.addCurveLoop([line(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))])
        )

    # Ring vertices first so hole-mode rings and embedded rings share point tags.
    ring_pts: list[list[int] | None] = []
    ring_lines: list[list[int]] = []
    for ring in prep.rings:
        if ring is None:
            ring_pts.append(None)
            ring_lines.append([])
            continue
        ids = [point(float(x), float(y)) for x, y in ring]
        ring_pts.append(ids)
        ring_lines.append([abs(line(ids[i], ids[(i + 1) % len(ids)])) for i in range(len(ids))])
    point_ids: list[int | None] = []
    for k, c in enumerate(piece.contacts):
        point_ids.append(point(c.x, c.y) if prep.rings[k] is None else None)

    surfs: list[int] = []
    for poly in prep.polys:
        loops = [loop(ring_array(poly.exterior))]
        loops += [loop(ring_array(r)) for r in poly.interiors]
        surfs.append(int(geo.addPlaneSurface(loops)))

    # Free points that carry the size fields.
    field_pts: list[tuple[SizeField, list[int]]] = []
    for fi, sf in enumerate(fields):
        if not _field_applies(sf, piece.id) or sf.size >= h:
            continue
        fpath = f"size_fields[{fi}]"
        tags: list[int] = []
        if sf.kind == "contacts":
            for k, c in enumerate(piece.contacts):
                if sf.contact_ids is not None and c.id not in sf.contact_ids:
                    continue
                rp = ring_pts[k]
                tags.extend(rp if rp is not None else [int(point_ids[k] or 0)])
        elif sf.kind == "points":
            if sf.points is None:
                raise MeshError(
                    "'points' size field needs points", code="MESH_SIZE_FIELD", path=fpath
                )
            tags = [int(geo.addPoint(x * _S, y * _S, 0.0)) for x, y in sf.points.array]
        elif sf.kind == "polyline":
            if sf.points is None or len(sf.points) < 2:
                raise MeshError(
                    "'polyline' size field needs >= 2 points", code="MESH_SIZE_FIELD", path=fpath
                )
            xy = _sample_polyline(sf.points.array, sf.size / 2, fpath)
            tags = [int(geo.addPoint(x * _S, y * _S, 0.0)) for x, y in xy]
        else:  # boundary
            for poly in prep.polys:
                for r in [poly.exterior, *poly.interiors]:
                    xy = _sample_polyline(np.asarray(r.coords), sf.size / 2, fpath)
                    tags.extend(int(geo.addPoint(x * _S, y * _S, 0.0)) for x, y in xy)
        if tags:
            field_pts.append((sf, tags))

    geo.synchronize()
    model = gmsh.model

    # Ring edges are single elements so ring nodes == ring vertices.
    for ls in ring_lines:
        for t in ls:
            model.mesh.setTransfiniteCurve(t, 2)

    # Embed contacts into the polygon that contains them.
    if surfs:
        tree = STRtree(prep.polys)
        for k, c in enumerate(piece.contacts):
            if c.mode == "hole" and prep.rings[k] is not None:
                continue
            hits = tree.query(prep.shapes[k], predicate="intersects").tolist()
            if not hits:
                raise MeshError(
                    f"contact '{c.id}' fell outside the cleaned copper",
                    code="MESH_CONTACT_OUTSIDE",
                    path=f"pieces[{piece.id}].contacts[{k}]",
                    hint="cleaning removed the copper under the contact; lower min_feature",
                )
            s = surfs[min(hits)]
            if prep.rings[k] is not None:
                model.mesh.embed(1, ring_lines[k], 2, s)
            else:
                model.mesh.embed(0, [point_ids[k]], 2, s)

    groups: list[PhysicalGroup] = []
    model.addPhysicalGroup(2, surfs, 1, f"piece:{piece.id}")
    groups.append(
        PhysicalGroup(name=f"piece:{piece.id}", dim=2, tag=1, kind="copper", source_id=piece.id)
    )
    for k, c in enumerate(piece.contacts):
        if prep.rings[k] is not None:
            model.addPhysicalGroup(1, ring_lines[k], 100 + k, f"contact:{c.id}")
            groups.append(
                PhysicalGroup(
                    name=f"contact:{c.id}", dim=1, tag=100 + k, kind="contact", source_id=c.id
                )
            )
        else:
            model.addPhysicalGroup(0, [point_ids[k]], 100 + k, f"contact:{c.id}")
            groups.append(
                PhysicalGroup(
                    name=f"contact:{c.id}", dim=0, tag=100 + k, kind="contact", source_id=c.id
                )
            )

    # Size fields. The boundary is meshed one element per polygon edge where the edge is shorter
    # than h; the interior size follows from graded fields around those short edges (gmsh's own
    # extension from the boundary is not grading-limited and over-refines large planes).
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    gmsh.option.setNumber("Mesh.MeshSizeMax", h * _S)
    gmsh.option.setNumber("Mesh.MeshSizeMin", 0.0)
    fld = model.mesh.field
    thr: list[int] = []
    for size, tags in _boundary_buckets(prep, pts, h).items():
        d = fld.add("Distance")
        fld.setNumbers(d, "PointsList", tags)
        t = fld.add("Threshold")
        fld.setNumber(t, "InField", d)
        fld.setNumber(t, "SizeMin", size * _S)
        fld.setNumber(t, "SizeMax", h * _S)
        fld.setNumber(t, "DistMin", 0.0)
        fld.setNumber(t, "DistMax", (h - size) * _S / (grading - 1.0))
        thr.append(t)
    for sf, tags in field_pts:
        d = fld.add("Distance")
        fld.setNumbers(d, "PointsList", tags)
        t = fld.add("Threshold")
        fld.setNumber(t, "InField", d)
        fld.setNumber(t, "SizeMin", sf.size * _S)
        fld.setNumber(t, "SizeMax", h * _S)
        fld.setNumber(t, "DistMin", 0.0)
        fld.setNumber(t, "DistMax", (h - sf.size) * _S / (sf.grading - 1.0))
        thr.append(t)
    # Narrow copper (traces): cap the element size at a third of the inscribed-circle diameter so
    # a 100 um trace is not meshed with 1 mm triangles.
    for poly, surf in zip(prep.polys, surfs, strict=True):
        r = float(shapely.maximum_inscribed_circle(poly, tolerance=h / 20).length)
        h_poly = 2.0 * r / 3.0
        if 0 < h_poly < h:
            cf = fld.add("Constant")
            fld.setNumber(cf, "VIn", h_poly * _S)
            fld.setNumber(cf, "VOut", h * _S)
            fld.setNumber(cf, "IncludeBoundary", 1)
            fld.setNumbers(cf, "SurfacesList", [surf])
            thr.append(cf)
    if thr:
        m = fld.add("Min")
        fld.setNumbers(m, "FieldsList", thr)
        fld.setAsBackgroundMesh(m)

    try:
        model.mesh.generate(2)
    except Exception as exc:  # gmsh raises bare Exception
        raise MeshError(
            f"gmsh failed on piece '{piece.id}': {exc}",
            code="MESH_GMSH_FAILED",
            path=f"pieces[{piece.id}]",
            hint="inspect the polygons with CleanOptions.min_feature raised",
        ) from exc

    ntags, ncoord, _ = model.mesh.getNodes()
    order = np.argsort(ntags)
    ntags = np.asarray(ntags)[order]
    xyz = np.asarray(ncoord).reshape(-1, 3)[order]
    etypes, _etags, enodes = model.mesh.getElements(2)
    tri = np.concatenate(
        [
            np.asarray(n, dtype=np.int64).reshape(-1, 3)
            for t, n in zip(etypes, enodes, strict=True)
            if t == 2
        ]
        or [np.zeros((0, 3), dtype=np.int64)]
    )
    used = np.unique(tri)
    nodes = xyz[np.searchsorted(ntags, used), :2] / _S
    tri = np.searchsorted(used, tri).astype(np.int64)
    neg = triangle_areas(nodes, tri) < 0
    tri[neg] = tri[neg][:, [0, 2, 1]]

    cnodes: list[ContactNodes] = []
    for k, c in enumerate(piece.contacts):
        if prep.rings[k] is not None:
            rp = ring_pts[k]
            assert rp is not None
            tags_k = [int(model.mesh.getNodes(0, p)[0][0]) for p in rp]
            idx = np.searchsorted(used, tags_k).astype(np.int64)
            rad = np.hypot(nodes[idx, 0] - c.x, nodes[idx, 1] - c.y)
            err = float(np.abs(rad - c.radius).max())
        else:
            tag = int(model.mesh.getNodes(0, point_ids[k])[0][0])
            idx = np.searchsorted(used, [tag]).astype(np.int64)
            err = 0.0
        cnodes.append(ContactNodes(id=c.id, nodes=NDArray(idx), radial_error=err))

    quality = quality_report(nodes, tri)
    if abs(quality.area - prep.area) > 1e-6 * prep.area:
        raise MeshError(
            f"mesh area {quality.area:.9g} differs from polygon area {prep.area:.9g}",
            code="MESH_GMSH_FAILED",
            path=f"pieces[{piece.id}]",
            hint="the polygon is probably self-touching; raise CleanOptions.min_feature",
        )
    return SheetMesh(
        nodes=Points(nodes),
        triangles=NDArray(tri),
        layer=piece.layer,
        z=piece.z,
        piece_id=piece.id,
        net=piece.net,
        contacts=tuple(cnodes),
        groups=tuple(groups),
        quality=quality,
        cleaning=prep.report,
        gmsh_version=gmsh_version(),
    )


def mesh_copper_sheets(
    pieces: Sequence[SheetPiece],
    *,
    h_default: float,
    size_fields: Sequence[SizeField] = (),
    clean: CleanOptions | None = None,
    grading: float = 1.3,
) -> tuple[SheetMesh, ...]:
    """Mesh copper pieces (polygons with holes) with embedded contact rings.

    Parameters
    ----------
    pieces:
        One mesh is returned per piece, in input order.
    h_default:
        Maximum element size on the plane [m].
    size_fields:
        Refinement constraints combined with ``min``; see ``SizeField``.
    clean:
        Shapely clean-up options; see ``CleanOptions``.
    grading:
        Growth of the element size away from short boundary edges (1.2 to 1.3 is typical).

    Returns ``SheetMesh`` objects (``TriMesh2D`` subclasses) carrying ``contacts`` (contact id ->
    node indices), quality, and the cleaning report. Raises ``MeshError``.

    The call owns the process-wide gmsh session (see ``oa_mesh._session``): call it from worker
    processes; threads are serialized.
    """
    if not h_default > 0:
        raise MeshError("h_default must be positive", code="MESH_SETUP", path="h_default")
    if not 1.0 < grading <= 2.0:
        raise MeshError("grading must be in (1, 2]", code="MESH_SETUP", path="grading")
    pids = [p.id for p in pieces]
    if len(set(pids)) != len(pids):
        raise MeshError("duplicate piece ids", code="MESH_SETUP", path="pieces")
    opts = clean or CleanOptions()
    prepared = [_PreparedPiece(p, opts, size_fields, h_default) for p in pieces]
    out: list[SheetMesh] = []
    with gmsh_session() as gmsh:
        for prep in prepared:
            gmsh.clear()
            gmsh.model.add(f"piece_{len(out)}")
            out.append(_mesh_piece(gmsh, prep, size_fields, h_default, grading))
    return tuple(out)
