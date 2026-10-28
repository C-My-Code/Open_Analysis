# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``mesh_cross_section``: P1/P2 triangle mesh of a ``CrossSection`` for 2D FEM.

Method
------
1. All shapes become shapely geometry in scaled working units (so gmsh tolerances stay far below the
   thinnest feature) and are snapped to a grid; conductors, dielectric boundaries and the outer
   box are noded together and polygonized into a planar subdivision (no OCC booleans).
2. Faces get a region (dielectric index, air, conductor). Faces that cannot be reached from a
   signal conductor without crossing a conductor (the outside of a coax shield, the space beyond
   an infinite ground plane) are dropped.
3. Every subdivision edge is discretized in Python with a graded size function (corner points,
   conductor edges, grading ``g``); each piece is a single-element gmsh line, so the boundary mesh
   is exactly the graded one and the interior follows by Frontal-Delaunay.
4. Physical groups: ``dielectric:<i>``, ``air``, ``conductor:<id>`` (boundary curves),
   ``conductor_interior:<id>`` (optional), ``outer``.

Zero-thickness conductors (``XsPlane`` with thickness 0) are curves: they split the domain when
they span it and are embedded curves when they end inside it.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any

import numpy as np
import numpy.typing as npt
import shapely  # type: ignore[import-untyped]
from shapely.geometry import LineString, Point  # type: ignore[import-untyped]
from shapely.geometry import Polygon as ShPolygon
from shapely.geometry import box as sh_box
from shapely.geometry.polygon import orient  # type: ignore[import-untyped]
from shapely.ops import polygonize_full, unary_union  # type: ignore[import-untyped]
from shapely.strtree import STRtree  # type: ignore[import-untyped]

from oa_mesh._clean import polygonal_parts, ring_array, to_shapely
from oa_mesh._quality import quality_report, triangle_areas
from oa_mesh._session import gmsh_session, gmsh_version
from oa_mesh.errors import MeshError
from oa_mesh.types import EdgeSet, PhysicalGroup, XsMesh
from oa_model.arrays import NDArray, Points
from oa_model.xsection import (
    CrossSection,
    XsCircle,
    XsPlane,
    XsPolygon,
    XsSlab,
    XsTrapezoid,
)

__all__ = ["mesh_cross_section"]

_CORNER_TURN_DEG = 15.0
_MAX_ELEMENTS = 3_000_000

Seg = tuple[tuple[float, float], tuple[float, float]]


def _key(x: float, y: float) -> tuple[float, float]:
    return (round(x, 6), round(y, 6))


def _seg_key(a: tuple[float, float], b: tuple[float, float]) -> Seg:
    return (a, b) if a <= b else (b, a)


class _Geometry:
    """Cross-section shapes in working units."""

    def __init__(self, xs: CrossSection, box_factor: float, snap: float, circle_segments: int):
        self.xs = xs
        ext = _finite_extent(xs)
        sig_w = 0.0
        sig = [c for c in xs.conductors if c.role == "signal"]
        sig_u = [rg for c in sig if (rg := _u_range(c.shape)) is not None]
        if sig_u:
            sig_w = max(r[1] for r in sig_u) - min(r[0] for r in sig_u)
        self.lref = max(sig_w, ext[2] - ext[0], ext[3] - ext[1])
        if not self.lref > 0:
            raise MeshError(
                "cross-section has no finite extent", code="MESH_SETUP", path="xs.conductors"
            )
        self.scale = 10.0 ** (3 - math.floor(math.log10(self.lref)))
        s = self.scale
        self.grid = snap * s
        m = box_factor * self.lref / 2
        self.box_m = (ext[0] - m, ext[1] - m, ext[2] + m, ext[3] + m)
        b = self.box_m
        self.box = self._snap(sh_box(b[0] * s, b[1] * s, b[2] * s, b[3] * s))
        self.circle_segments = circle_segments

        # conductors: (id, geometry in working units, is_area)
        self.cond: list[tuple[str, Any, bool]] = []
        for c in xs.conductors:
            g = self._conductor_geom(c.shape)
            if g is None or g.is_empty:
                raise MeshError(
                    f"conductor '{c.id}' lies outside the meshing box or is empty",
                    code="MESH_SETUP",
                    path=f"xs.conductors[{c.id}]",
                )
            self.cond.append((c.id, g, g.geom_type != "LineString"))
        # dielectrics: (index, priority, polygon)
        self.diel: list[tuple[int, int, Any]] = []
        for i, d in enumerate(xs.dielectrics):
            r = d.region
            if isinstance(r, XsSlab):
                u0 = self.box_m[0] if r.u_min is None else r.u_min
                u1 = self.box_m[2] if r.u_max is None else r.u_max
                g = sh_box(u0 * s, r.z_bottom * s, u1 * s, r.z_top * s)
            else:
                g = ShPolygon(r.polygon.exterior.array * s, [h.array * s for h in r.polygon.holes])
                if not g.is_valid:
                    g = shapely.make_valid(g)
            g = self._snap(g).intersection(self.box)
            parts = polygonal_parts(g)
            if parts:
                self.diel.append((i, d.priority, unary_union(parts)))

    def _snap(self, g: Any) -> Any:
        return shapely.set_precision(g, self.grid)

    def _conductor_geom(self, shape: Any) -> Any:
        s = self.scale
        if isinstance(shape, XsTrapezoid):
            z0, z1 = shape.z_bottom, shape.z_bottom + shape.thickness
            hb, ht = shape.w_bottom / 2, shape.w_top / 2
            u = shape.u_center
            g = ShPolygon(np.array([(u - hb, z0), (u + hb, z0), (u + ht, z1), (u - ht, z1)]) * s)
        elif isinstance(shape, XsPolygon):
            g = to_shapely(shape.polygon)
            g = ShPolygon(
                np.asarray(g.exterior.coords) * s, [np.asarray(h.coords) * s for h in g.interiors]
            )
            if not g.is_valid:
                g = shapely.make_valid(g)
        elif isinstance(shape, XsCircle):
            g = Point(shape.u_center * s, shape.z_center * s).buffer(
                shape.radius * s, quad_segs=max(2, self.circle_segments // 4)
            )
        else:
            assert isinstance(shape, XsPlane)
            u0 = self.box_m[0] if shape.u_min is None else max(shape.u_min, self.box_m[0])
            u1 = self.box_m[2] if shape.u_max is None else min(shape.u_max, self.box_m[2])
            if not u1 > u0:
                return None
            if shape.thickness > 0:
                g = sh_box(
                    u0 * s, shape.z_bottom * s, u1 * s, (shape.z_bottom + shape.thickness) * s
                )
            else:
                g = LineString([(u0 * s, shape.z_bottom * s), (u1 * s, shape.z_bottom * s)])
        g = self._snap(g)
        g = g.intersection(self.box)
        if g.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection"):
            parts = polygonal_parts(g)
            return unary_union(parts) if parts else None
        return g


def _span(a: npt.NDArray[np.float64], col: int) -> list[float]:
    return [float(np.min(a[:, col])), float(np.max(a[:, col]))]


def _u_range(shape: Any) -> tuple[float, float] | None:
    if isinstance(shape, XsTrapezoid):
        h = max(shape.w_bottom, shape.w_top) / 2
        return shape.u_center - h, shape.u_center + h
    if isinstance(shape, XsPolygon):
        lo, hi = _span(shape.polygon.exterior.array, 0)
        return lo, hi
    if isinstance(shape, XsCircle):
        return shape.u_center - shape.radius, shape.u_center + shape.radius
    if shape.u_min is None or shape.u_max is None:
        return None
    return shape.u_min, shape.u_max


def _finite_extent(xs: CrossSection) -> tuple[float, float, float, float]:
    """(u_min, z_min, u_max, z_max) of everything finite in the section [m]."""
    us: list[float] = []
    zs: list[float] = []
    for c in xs.conductors:
        s = c.shape
        if isinstance(s, XsTrapezoid):
            h = max(s.w_bottom, s.w_top) / 2
            us += [s.u_center - h, s.u_center + h]
            zs += [s.z_bottom, s.z_bottom + s.thickness]
        elif isinstance(s, XsPolygon):
            a = s.polygon.exterior.array
            us += _span(a, 0)
            zs += _span(a, 1)
        elif isinstance(s, XsCircle):
            us += [s.u_center - s.radius, s.u_center + s.radius]
            zs += [s.z_center - s.radius, s.z_center + s.radius]
        else:
            us += [v for v in (s.u_min, s.u_max) if v is not None]
            zs += [s.z_bottom, s.z_bottom + s.thickness]
    for d in xs.dielectrics:
        r = d.region
        if isinstance(r, XsSlab):
            us += [v for v in (r.u_min, r.u_max) if v is not None]
            zs += [r.z_bottom, r.z_top]
        else:
            a = r.polygon.exterior.array
            us += _span(a, 0)
            zs += _span(a, 1)
    if not us:
        us = [0.0]
    return min(us), min(zs), max(us), max(zs)


def _corners(geom: _Geometry, h_corner: float) -> tuple[npt.NDArray[Any], npt.NDArray[Any]]:
    """Corner points (working units) and the size wanted at each."""
    pts: list[tuple[float, float]] = []
    sizes: list[float] = []
    inner = geom.box.buffer(-geom.grid * 4)
    for _cid, g, is_area in geom.cond:
        if not is_area:
            for p in (g.coords[0], g.coords[-1]):
                if inner.contains(Point(p)):
                    pts.append((p[0], p[1]))
                    sizes.append(min(h_corner, g.length))
            continue
        for poly in polygonal_parts(g):
            for ring in [poly.exterior, *poly.interiors]:
                a = ring_array(ring)
                n = len(a)
                prev = np.roll(a, 1, axis=0)
                nxt = np.roll(a, -1, axis=0)
                d0 = a - prev
                d1 = nxt - a
                l0 = np.hypot(d0[:, 0], d0[:, 1])
                l1 = np.hypot(d1[:, 0], d1[:, 1])
                cross = d0[:, 0] * d1[:, 1] - d0[:, 1] * d1[:, 0]
                dot = d0[:, 0] * d1[:, 0] + d0[:, 1] * d1[:, 1]
                turn = np.degrees(np.abs(np.arctan2(cross, dot)))
                for i in range(n):
                    if turn[i] >= _CORNER_TURN_DEG and inner.contains(Point(a[i])):
                        pts.append((float(a[i, 0]), float(a[i, 1])))
                        sizes.append(float(min(h_corner, l0[i], l1[i])))
    if not pts:
        return np.zeros((0, 2)), np.zeros(0)
    return np.array(pts), np.array(sizes)


def mesh_cross_section(
    xs: CrossSection,
    *,
    h_max: float | None = None,
    order: int = 2,
    corner_refine: float = 0.1,
    box_factor: float = 20.0,
    grading: float = 1.3,
    mesh_conductors: bool = False,
    snap: float = 1e-10,
    circle_segments: int = 128,
) -> XsMesh:
    """Mesh a cross-section.

    Parameters
    ----------
    xs:
        The section, used in its own (u, z) frame (not canonicalized) [m].
    h_max:
        Largest element size [m]; default is ``Lref / 4`` with ``Lref`` the larger of the signal
        width and the finite extent of the structure.
    order:
        1 (P1, 3-node) or 2 (P2, 6-node; gmsh order ``v0 v1 v2 m01 m12 m20``).
    corner_refine:
        Element size at conductor corners as a fraction of ``h_max`` (also bounded by the shortest
        edge at the corner so thin ribbons keep good angles).
    box_factor:
        Outer truncation box: the finite structure is padded by ``box_factor * Lref / 2`` on every
        side (sides closed off by an infinite ground plane are discarded automatically).
    grading:
        Maximum size growth per unit distance + 1 away from the refined features (1.2 to 1.3).
    mesh_conductors:
        Also mesh the conductor interiors (eddy-current R(f)); region tag ``-2 - conductor index``.
    snap:
        Coordinate snap grid [m].
    circle_segments:
        Polygon segments used for ``XsCircle`` conductors.

    Raises ``MeshError`` on failure. Call from a worker process (see ``oa_mesh._session``).
    """
    if order not in (1, 2):
        raise MeshError("order must be 1 or 2", code="MESH_SETUP", path="order")
    if not 1.0 < grading <= 2.0:
        raise MeshError("grading must be in (1, 2]", code="MESH_SETUP", path="grading")
    if not 0.0 < corner_refine <= 1.0:
        raise MeshError("corner_refine must be in (0, 1]", code="MESH_SETUP", path="corner_refine")
    if box_factor <= 0:
        raise MeshError("box_factor must be positive", code="MESH_SETUP", path="box_factor")
    geom = _Geometry(xs, box_factor, snap, circle_segments)
    s = geom.scale
    hmax = (h_max if h_max is not None else geom.lref / 4) * s
    if not hmax > 0:
        raise MeshError("h_max must be positive", code="MESH_SETUP", path="h_max")
    h_corner = corner_refine * hmax
    h_edge = min(hmax, 3.0 * h_corner)
    slope = grading - 1.0
    warnings: list[str] = []

    # --- planar subdivision -------------------------------------------------------------
    lines: list[Any] = [geom.box.exterior]
    cond_boundary: list[Any] = []
    for _cid, g, is_area in geom.cond:
        bd = g.boundary if is_area else g
        cond_boundary.append(bd)
        lines.append(bd)
    for _i, _p, g in geom.diel:
        lines.append(g.boundary)
    noded = unary_union(lines)
    faces_raw, cuts, dangles, _invalid = polygonize_full(noded)
    faces = [orient(f, 1.0) for f in polygonal_parts(faces_raw)]
    faces.sort(key=lambda f: (f.bounds, f.area))
    if not faces:
        raise MeshError("cross-section produced no faces", code="MESH_INVALID_GEOMETRY", path="xs")

    # --- classification -------------------------------------------------------------------
    cond_polys = [(i, g) for i, (_c, g, area) in enumerate(geom.cond) if area]
    for _i, g in cond_polys:
        shapely.prepare(g)
    diel_sorted = sorted(geom.diel, key=lambda t: (-t[1], t[0]))
    for _i, _p, g in diel_sorted:
        shapely.prepare(g)
    kind: list[tuple[str, int]] = []  # ("cond", ci) | ("diel", di) | ("air", -1)
    for f in faces:
        rp = f.representative_point()
        hit = next((ci for ci, g in cond_polys if g.contains(rp)), None)
        if hit is not None:
            kind.append(("cond", hit))
            continue
        d_hit = next((di for di, _p, g in diel_sorted if g.contains(rp)), None)
        kind.append(("diel", d_hit) if d_hit is not None else ("air", -1))

    # segment table: key -> (faces, conductor membership, on outer box)
    seg_faces: dict[Seg, list[int]] = defaultdict(list)
    for fi, f in enumerate(faces):
        for ring in [f.exterior, *f.interiors]:
            c = np.asarray(ring.coords)
            for k in range(len(c) - 1):
                seg_faces[_seg_key(_key(*c[k]), _key(*c[k + 1]))].append(fi)
    extra_segs: dict[Seg, None] = {}
    for dl in [*shapely.get_parts(dangles), *shapely.get_parts(cuts)]:
        c = np.asarray(dl.coords)
        for k in range(len(c) - 1):
            extra_segs[_seg_key(_key(*c[k]), _key(*c[k + 1]))] = None
    tol = geom.grid * 50
    all_keys = sorted({*seg_faces.keys(), *extra_segs.keys()})
    mids = np.array([((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in all_keys])
    mid_pts = shapely.points(mids)
    memb: dict[Seg, tuple[int, ...]] = {}
    on_box: dict[Seg, bool] = {}
    cond_members: list[list[int]] = [[] for _ in all_keys]
    for ci, bd in enumerate(cond_boundary):
        d = shapely.distance(mid_pts, bd)
        for k in np.nonzero(d < tol)[0].tolist():
            cond_members[k].append(ci)
    dbox = shapely.distance(mid_pts, geom.box.exterior)
    for k, sk in enumerate(all_keys):
        memb[sk] = tuple(cond_members[k])
        on_box[sk] = bool(dbox[k] < tol)

    signal_idx = {i for i, c in enumerate(xs.conductors) if c.role == "signal"}
    seeds: set[int] = set()
    for sk, fl in seg_faces.items():
        if any(m in signal_idx for m in memb[sk]):
            seeds.update(f for f in fl if kind[f][0] != "cond")
    keep: set[int] = set()
    if not seeds:
        warnings.append("no region touches a signal conductor; keeping every region")
        seeds = {i for i, k in enumerate(kind) if k[0] != "cond"}
    queue = deque(sorted(seeds))
    keep.update(seeds)
    nbrs: dict[int, set[int]] = defaultdict(set)
    for sk, fl in seg_faces.items():
        if memb[sk]:
            continue
        for a in fl:
            for b in fl:
                if a != b and kind[a][0] != "cond" and kind[b][0] != "cond":
                    nbrs[a].add(b)
    while queue:
        f = queue.popleft()
        for g_ in sorted(nbrs[f]):
            if g_ not in keep:
                keep.add(g_)
                queue.append(g_)
    if mesh_conductors:
        for fl in seg_faces.values():
            if any(f in keep for f in fl):
                keep.update(f for f in fl if kind[f][0] == "cond")
    dropped = len(faces) - len(keep)
    if dropped:
        warnings.append(f"{dropped} region(s) unreachable from the signal conductors were dropped")
    kept = sorted(keep)
    if not any(kind[f][0] != "cond" for f in kept):
        raise MeshError(
            "no dielectric region remains to be meshed",
            code="MESH_INVALID_GEOMETRY",
            path="xs",
            hint="check that the signal conductors are not fully enclosed by conductors",
        )

    # --- size function and edge discretization ---------------------------------------------
    cb_all = unary_union(cond_boundary)
    shapely.prepare(cb_all)
    cpts, csizes = _corners(geom, h_corner)

    def size_at(x: float, y: float) -> float:
        sz = min(hmax, h_edge + slope * float(shapely.distance(Point(x, y), cb_all)))
        if len(cpts):
            d = np.hypot(cpts[:, 0] - x, cpts[:, 1] - y)
            sz = min(sz, float(np.min(csizes + slope * d)))
        return sz

    sub_cache: dict[Seg, list[tuple[float, float]]] = {}

    def subdivide(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
        """Interior points of segment a -> b (canonical direction cached)."""
        ck = _seg_key(a, b)
        if ck not in sub_cache:
            p, q = ck
            length = math.hypot(q[0] - p[0], q[1] - p[1])
            out: list[tuple[float, float]] = []
            t = 0.0
            while True:
                mx = p[0] + (q[0] - p[0]) * (t / length)
                my = p[1] + (q[1] - p[1]) * (t / length)
                step = size_at(mx, my)
                nt = t + step
                if nt >= length - 0.5 * step:
                    break
                out.append(
                    (p[0] + (q[0] - p[0]) * (nt / length), p[1] + (q[1] - p[1]) * (nt / length))
                )
                t = nt
            sub_cache[ck] = out
        pts = sub_cache[ck]
        return pts if (a, b) == ck else pts[::-1]

    # --- gmsh ---------------------------------------------------------------------------------
    n_cond = len(geom.cond)
    with gmsh_session() as gmsh:
        geo = gmsh.model.geo
        pts: dict[tuple[float, float], int] = {}
        glines: dict[tuple[int, int], int] = {}
        line_membership: dict[int, tuple[tuple[int, ...], bool]] = {}

        def point(p: tuple[float, float]) -> int:
            k = _key(*p)
            t = pts.get(k)
            if t is None:
                t = int(geo.addPoint(p[0], p[1], 0.0))
                pts[k] = t
            return t

        def chain(a: tuple[float, float], b: tuple[float, float]) -> list[int]:
            """Signed gmsh line tags along macro segment a -> b."""
            sk = _seg_key(_key(*a), _key(*b))
            path = [a, *subdivide(a, b), b]
            ids = [point(p) for p in path]
            out: list[int] = []
            for i in range(len(ids) - 1):
                u, v = ids[i], ids[i + 1]
                key = (u, v) if u < v else (v, u)
                t = glines.get(key)
                if t is None:
                    t = int(geo.addLine(key[0], key[1]))
                    glines[key] = t
                    line_membership[t] = (memb.get(sk, ()), on_box.get(sk, False))
                out.append(t if u < v else -t)
            return out

        def loop(ring: Any) -> int:
            c = [_key(*xy) for xy in np.asarray(ring.coords)]
            tags: list[int] = []
            for i in range(len(c) - 1):
                tags.extend(chain(c[i], c[i + 1]))
            return int(geo.addCurveLoop(tags))

        surf_of: dict[int, int] = {}
        for fi in kept:
            f = faces[fi]
            loops = [loop(f.exterior), *[loop(r) for r in f.interiors]]
            surf_of[fi] = int(geo.addPlaneSurface(loops))
        face_tree = STRtree([faces[fi] for fi in kept])
        embed: dict[int, list[int]] = defaultdict(list)
        for sk in extra_segs:
            m = Point((sk[0][0] + sk[1][0]) / 2, (sk[0][1] + sk[1][1]) / 2)
            hits = [kept[j] for j in face_tree.query(m, predicate="within").tolist()]
            if hits:
                for t in chain(sk[0], sk[1]):
                    embed[surf_of[hits[0]]].append(abs(t))
        geo.synchronize()
        model = gmsh.model
        for t in glines.values():
            model.mesh.setTransfiniteCurve(t, 2)
        for surf, ls in embed.items():
            model.mesh.embed(1, sorted(set(ls)), 2, surf)

        groups: list[PhysicalGroup] = []

        def add_group(
            dim: int, tags: list[int], tag: int, name: str, kind_: str, src: str | None
        ) -> None:
            if not tags:
                return
            model.addPhysicalGroup(dim, sorted(set(tags)), tag, name)
            groups.append(PhysicalGroup(name=name, dim=dim, tag=tag, kind=kind_, source_id=src))

        by_region: dict[tuple[str, int], list[int]] = defaultdict(list)
        for fi in kept:
            by_region[kind[fi]].append(surf_of[fi])
        for di, _p, _g in sorted(geom.diel):
            add_group(
                2,
                by_region.get(("diel", di), []),
                10 + di,
                f"dielectric:{di}",
                "dielectric",
                str(di),
            )
        add_group(2, by_region.get(("air", -1), []), 1, "air", "air", None)
        for ci, (cid, _g, area) in enumerate(geom.cond):
            if area and mesh_conductors:
                add_group(
                    2,
                    by_region.get(("cond", ci), []),
                    1000 + ci,
                    f"conductor_interior:{cid}",
                    "conductor_interior",
                    cid,
                )
        cond_lines: list[list[int]] = [[] for _ in range(n_cond)]
        outer_lines: list[int] = []
        for t, (members, ob) in sorted(line_membership.items()):
            for ci in members:
                cond_lines[ci].append(t)
            if ob:
                outer_lines.append(t)
        # lines of dropped regions only: keep groups to lines that border a kept region
        used_lines = {abs(t) for t in glines.values()}
        for ci, (cid, _g, _a) in enumerate(geom.cond):
            tags = [t for t in cond_lines[ci] if t in used_lines]
            if not tags:
                warnings.append(f"conductor '{cid}' has no boundary in the meshed domain")
            add_group(1, tags, 100 + ci, f"conductor:{cid}", "conductor", cid)
        add_group(1, [t for t in outer_lines if t in used_lines], 1, "outer", "outer", None)

        # Corner background field: the boundary mesh is already graded; this makes the interior
        # follow the same grading around each corner (otherwise triangles next to a tiny cap edge
        # grow too fast and have poor angles).
        fld = model.mesh.field
        by_size: dict[float, list[int]] = defaultdict(list)
        for (px, py), hc in zip(cpts.tolist(), csizes.tolist(), strict=True):
            tag = pts.get(_key(px, py))
            if tag is not None:
                by_size[float(f"{hc:.3g}")].append(tag)
        thr: list[int] = []
        for hc, tags in sorted(by_size.items()):
            if hc >= hmax:
                continue
            d = fld.add("Distance")
            fld.setNumbers(d, "PointsList", tags)
            t = fld.add("Threshold")
            fld.setNumber(t, "InField", d)
            fld.setNumber(t, "SizeMin", hc)
            fld.setNumber(t, "SizeMax", hmax)
            fld.setNumber(t, "DistMin", 0.0)
            fld.setNumber(t, "DistMax", (hmax - hc) / slope)
            thr.append(t)
        if thr:
            mn = fld.add("Min")
            fld.setNumbers(mn, "FieldsList", thr)
            fld.setAsBackgroundMesh(mn)
        gmsh.option.setNumber("Mesh.MeshSizeMax", hmax)
        gmsh.option.setNumber("Mesh.MeshSizeMin", 0.0)
        try:
            model.mesh.generate(2)
            if order == 2:
                model.mesh.setOrder(2)
        except Exception as exc:
            raise MeshError(
                f"gmsh failed: {exc}",
                code="MESH_GMSH_FAILED",
                path="xs",
                hint="try a larger h_max or a different box_factor",
            ) from exc

        ntags, ncoord, _ = model.mesh.getNodes()
        srt = np.argsort(ntags)
        ntags = np.asarray(ntags)[srt]
        xyz = np.asarray(ncoord).reshape(-1, 3)[srt]
        npe = 3 * order
        etype = 2 if order == 1 else 9
        ltype = 1 if order == 1 else 8

        def read(dim: int, tag: int, want: int, width: int) -> npt.NDArray[np.int64]:
            out: list[npt.NDArray[np.int64]] = []
            for ent in model.getEntitiesForPhysicalGroup(dim, tag):
                types, _e, en = model.mesh.getElements(dim, ent)
                for t, nn in zip(types, en, strict=True):
                    if t == want:
                        out.append(np.asarray(nn, dtype=np.int64).reshape(-1, width))
            return np.concatenate(out) if out else np.zeros((0, width), dtype=np.int64)

        tri_parts: list[npt.NDArray[np.int64]] = []
        reg_parts: list[npt.NDArray[np.int64]] = []
        for g in groups:
            if g.dim != 2:
                continue
            elems = read(2, g.tag, etype, npe)
            tri_parts.append(elems)
            if g.kind == "dielectric":
                rv = int(g.source_id or 0)
            elif g.kind == "air":
                rv = -1
            else:
                rv = -2 - next(i for i, c in enumerate(geom.cond) if c[0] == g.source_id)
            reg_parts.append(np.full(len(elems), rv, dtype=np.int64))
        tri_tags = np.concatenate(tri_parts) if tri_parts else np.zeros((0, npe), dtype=np.int64)
        region = np.concatenate(reg_parts) if reg_parts else np.zeros(0, dtype=np.int64)
        used = np.unique(tri_tags)
        nodes = xyz[np.searchsorted(ntags, used), :2] / s
        tri = np.searchsorted(used, tri_tags).astype(np.int64)
        neg = triangle_areas(nodes, tri) < 0
        if neg.any():
            perm = [0, 2, 1] if order == 1 else [0, 2, 1, 5, 4, 3]
            tri[neg] = tri[neg][:, perm]

        def edges_of(name: str) -> npt.NDArray[np.int64]:
            for g in groups:
                if g.dim == 1 and g.name == name:
                    e = read(1, g.tag, ltype, order + 1)
                    return np.searchsorted(used, e).astype(np.int64)
            return np.zeros((0, order + 1), dtype=np.int64)

        cond_sets = tuple(
            EdgeSet(
                name=f"conductor:{cid}", source_id=cid, edges=NDArray(edges_of(f"conductor:{cid}"))
            )
            for cid, _g, _a in geom.cond
        )
        outer = EdgeSet(name="outer", source_id=None, edges=NDArray(edges_of("outer")))
        gvers = gmsh_version()

    quality = quality_report(nodes, tri)
    if quality.n_triangles > _MAX_ELEMENTS:
        raise MeshError(
            f"mesh has {quality.n_triangles} triangles",
            code="MESH_GMSH_FAILED",
            path="h_max",
            hint="increase h_max",
        )
    bx = geom.box_m
    return XsMesh(
        order=order,  # type: ignore[arg-type]
        nodes=Points(nodes),
        triangles=NDArray(tri),
        region=NDArray(region),
        conductor_edges=cond_sets,
        outer_edges=outer,
        groups=tuple(groups),
        quality=quality,
        gmsh_version=gvers,
        box=(bx[0], bx[1], bx[2], bx[3]),
        warnings=tuple(warnings),
    )
