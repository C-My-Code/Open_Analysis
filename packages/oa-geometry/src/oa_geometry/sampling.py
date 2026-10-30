# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Cross-sections along routed nets: ``cross_section_at`` and ``sample_net``.

A cross-section is cut perpendicular to the segment at ``s``. ``u`` runs along the left normal
(rotate the direction of travel by +90 degrees), ``z`` is the board z. Contents:

- the signal conductor (drawn width, trapezoid from the layer's ``Etch``), plus its differential
  partner when the net is half of a ``DiffPair`` (conductors ordered P, N);
- aggressors: same-layer copper of other signal/unknown nets inside the window, appended as
  further ``signal`` conductors (nearest first) when ``include_aggressors``;
- coplanar grounds: same-layer copper of ground/power nets inside the window (rectangles, clipped
  at the window); copper without a net becomes a ``floating`` conductor;
- reference planes from the ``ReferenceMap`` span at ``s`` (above/below), truncated to the actual
  copper extent along the cut (an open end means the copper continues past the window);
- dielectric slabs from the stackup between the lowest and highest plane (or the stack faces),
  filling inner copper layers with the adjacent dielectric, plus solder mask on outer layers.

The result is passed through ``CrossSection.canonicalize`` (1 nm grid, u=0 at the signal bbox
midpoint, z=0 at the lowest material boundary).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import shapely

from oa_geometry._native import RingSet
from oa_geometry._native import kernels as _kernels
from oa_geometry._shapes import path_points, polygons_of
from oa_geometry.errors import UnknownItem
from oa_geometry.models import ReferenceSpan, RefInfo, RoutedSegment, XsSample
from oa_geometry.refmap import (
    _compute_spans,
    _piece_index,
    flip_spans,
    piece_spans,
)
from oa_geometry.segs import Piece
from oa_geometry.topology import oriented_pieces
from oa_model.arrays import Points
from oa_model.geometry import LineSeg, PathSeg
from oa_model.geometry import Polygon as ModelPolygon
from oa_model.materials import ConductorMaterial, DielectricMaterial
from oa_model.stackup import ConductorLayer, DielectricLayer, MaskLayer
from oa_model.xsection import (
    CrossSection,
    XsConductor,
    XsDielectric,
    XsPlane,
    XsProvenance,
    XsRegion,
    XsSlab,
    XsTrapezoid,
)

if TYPE_CHECKING:
    from oa_geometry.geometry import BoardGeometry

F64 = npt.NDArray[np.float64]
NM = 1e-9
MIN_INTERVAL = 1e-9

__all__ = ["cross_section_at", "sample_net"]


def _piece_of(geo: BoardGeometry, seg: RoutedSegment) -> Piece:
    pts = path_points((seg.seg,), geo.arc_tol)
    return Piece(seg=seg, pts=pts, line=shapely.linestrings(pts), track=seg.id)


def _spans_of(geo: BoardGeometry, seg: RoutedSegment) -> tuple[ReferenceSpan, ...]:
    """Reference spans of `seg` in the orientation of `seg` (flipped if the stored piece is
    reversed; computed on the fly for segments that are not board pieces)."""
    stored = _piece_index(geo).get(seg.id)
    if stored is None:
        return _compute_spans(geo, _piece_of(geo, seg), 3.0, {})
    spans = piece_spans(geo, seg.id)
    if _same_pt(stored.start, _start(seg.seg)):
        return spans
    return flip_spans(spans, seg.length)


def _start(seg: PathSeg) -> tuple[float, float]:
    return seg.start


def _same_pt(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= 1e-8


def _span_at(spans: tuple[ReferenceSpan, ...], s: float) -> ReferenceSpan | None:
    for sp in spans:
        if sp.s_start - 1e-12 <= s <= sp.s_end + 1e-12:
            return sp
    return spans[-1] if spans else None


def _etch_widths(layer: ConductorLayer, w: float) -> tuple[float, float]:
    """(bottom, top) width of a trace of drawn width `w` on `layer`."""
    e = layer.etch
    if e is None:
        return w, w
    if e.kind == "angle":
        delta = 2.0 * layer.thickness / math.tan(e.value) if 0 < e.value < math.pi else 0.0
    else:
        delta = e.value
    if e.reference == "bottom":
        wb, wt = w, w - delta
    else:
        wb, wt = w + delta, w
    return max(wb, NM), max(wt, NM)


def _sigma(mat: ConductorMaterial | None, temp: float | None) -> float:
    if mat is None:
        return 5.8e7
    if temp is None:
        return mat.conductivity
    return mat.conductivity / max(1e-3, 1.0 + mat.temp_coeff * (temp - mat.t_ref))


def _clip(lines: object, geoms: object) -> object:
    """``shapely.intersection(lines, geoms)``, skipping the overlay for a single `geoms` that
    covers a line entirely (its intersection is the line itself): planes cover most cuts."""
    if not isinstance(geoms, shapely.Geometry):
        return shapely.intersection(lines, geoms)
    shapely.prepare(geoms)
    covered = shapely.covers(geoms, lines)
    if not covered.any():
        return shapely.intersection(lines, geoms)
    res = np.empty(len(lines), dtype=object)  # type: ignore[arg-type,type-var]
    res[covered] = lines[covered]  # type: ignore[index]
    rest = ~covered
    if rest.any():
        res[rest] = shapely.intersection(lines[rest], geoms)  # type: ignore[index]
    return res


_FM_DIGITS = 15
"""Cut intervals are rounded to 1 fm: canonical output, equal for the GEOS and native paths."""


def _native_u_intervals(
    lines: object,
    geoms: object,
    centers: F64,
    normals: F64,
    out: list[list[tuple[float, float]]],
    rings: RingSet | None = None,
    gi: npt.NDArray[np.int64] | None = None,
) -> bool:
    """``_u_intervals`` through the compiled cut kernel (closed-set segment/polygon clipping, the
    same point set as ``shapely.intersection``); fills `out`, False when it is not available."""
    kern = _kernels()
    if kern is None:
        return False
    if rings is None or gi is None:
        if isinstance(geoms, shapely.Geometry):
            uniq = np.empty(1, dtype=object)
            uniq[0] = geoms
            gi = np.zeros(len(lines), dtype=np.int64)  # type: ignore[arg-type]
        else:
            ids = np.fromiter((id(g) for g in geoms), dtype=np.int64, count=len(geoms))  # type: ignore[arg-type]
            _, first, inv = np.unique(ids, return_index=True, return_inverse=True)
            uniq = np.asarray(geoms, dtype=object)[first]
            gi = inv.astype(np.int64)
        rings = RingSet(uniq)
    segs = np.ascontiguousarray(shapely.get_coordinates(lines).reshape(-1, 2, 2))
    own, xy = kern.cut_intervals(segs, gi, rings.xy, rings.ring_start, rings.geom_start)
    own = np.asarray(own, dtype=np.intp)
    if len(own) == 0:
        return True
    xy = np.asarray(xy).reshape(-1, 2, 2)
    d = xy - centers[own][:, None, :]
    u = d[:, :, 0] * normals[own][:, None, 0] + d[:, :, 1] * normals[own][:, None, 1]
    lo, hi = np.round(u.min(axis=1), _FM_DIGITS), np.round(u.max(axis=1), _FM_DIGITS)
    for o, a_, b_ in zip(own.tolist(), lo.tolist(), hi.tolist(), strict=True):
        if b_ - a_ > MIN_INTERVAL:
            out[o].append((a_, b_))
    return True


def _u_intervals(
    lines: object,
    geoms: object,
    centers: F64,
    normals: F64,
    rings: RingSet | None = None,
    gi: npt.NDArray[np.int64] | None = None,
) -> list[list[tuple[float, float]]]:
    """u-intervals (unmerged) where ``lines[i]`` lies inside ``geoms[i]``; ``geoms`` may be one
    geometry for all lines. ``centers`` / ``normals`` are (n, 2) arrays aligned with ``lines``."""
    k = len(lines)  # type: ignore[arg-type]
    out: list[list[tuple[float, float]]] = [[] for _ in range(k)]
    if k == 0:
        return out
    nat = _native_u_intervals(lines, geoms, centers, normals, out, rings, gi)
    if nat:
        return out
    inter = _clip(lines, geoms)
    parts, src = shapely.get_parts(inter, return_index=True)
    if len(parts) == 0:
        return out
    ls = shapely.get_type_id(parts) == 1  # LineString
    parts, src = parts[ls], src[ls]
    if len(parts) == 0:
        return out
    xy, pi = shapely.get_coordinates(parts, return_index=True)
    if len(xy) == 0:
        return out
    own = src[pi]
    # same products and sum as the scalar ``(xy - centre) @ normal``, up to the BLAS fused
    # multiply-add (1 ulp, far below the 1 nm grid of every consumer)
    d = xy - centers[own]
    u = d[:, 0] * normals[own, 0] + d[:, 1] * normals[own, 1]
    starts = np.flatnonzero(np.concatenate(([True], pi[1:] != pi[:-1])))
    lo = np.round(np.minimum.reduceat(u, starts), _FM_DIGITS)
    hi = np.round(np.maximum.reduceat(u, starts), _FM_DIGITS)
    for o, a_, b_ in zip(src[pi[starts]].tolist(), lo.tolist(), hi.tolist(), strict=True):
        if b_ - a_ > MIN_INTERVAL:
            out[o].append((a_, b_))
    return out


def _merge(iv: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for lo, hi in sorted(iv):
        if out and lo <= out[-1][1] + MIN_INTERVAL:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _layer_intervals(
    geo: BoardGeometry,
    layer: str,
    lines: object,
    centers: F64,
    normals: F64,
    skip: frozenset[str],
) -> list[dict[str | None, list[tuple[float, float]]]]:
    """u-intervals of copper on `layer` along each cut line, per net (merged). Nets in `skip`
    (the signal itself and its partner, which the section ignores) are not clipped."""
    tree = geo.strtree(layer)
    k = len(lines)  # type: ignore[arg-type]
    qi, ti = tree._tree.query(lines, predicate="intersects")
    if skip and len(qi):
        keep = np.fromiter(
            (tree.net(j) not in skip for j in ti.tolist()), dtype=bool, count=len(ti)
        )
        qi, ti = qi[keep], ti[keep]
    per: list[dict[str | None, list[tuple[float, float]]]] = [{} for _ in range(k)]
    if len(qi):
        rings = (
            geo._memoized(("rings-tree", layer), lambda: RingSet(tree._geoms))
            if _kernels() is not None
            else None
        )
        ivs = _u_intervals(
            lines[qi], tree._geoms[ti], centers[qi], normals[qi], rings, ti.astype(np.int64)
        )  # type: ignore[index]
        for a, j, iv in zip(qi.tolist(), ti.tolist(), ivs, strict=True):
            if iv:
                per[a].setdefault(tree.net(j), []).extend(iv)
    return [{n: _merge(v) for n, v in d.items()} for d in per]


def _dielectric(geo: BoardGeometry, name: str) -> DielectricMaterial:
    for m in geo.board.stackup.materials:
        if m.name == name and isinstance(m, DielectricMaterial):
            return m
    raise UnknownItem(f"dielectric material {name!r} not in the stackup", path=name)


def _conductor_material(geo: BoardGeometry, layer: ConductorLayer) -> ConductorMaterial | None:
    for m in geo.board.stackup.materials:
        if m.name == layer.material and isinstance(m, ConductorMaterial):
            return m
    return None


def _partner(geo: BoardGeometry, net: str) -> str | None:
    for dp in geo.board.diff_pairs:
        if dp.p_net == net:
            return dp.n_net
        if dp.n_net == net:
            return dp.p_net
    return None


def _default_window(
    geo: BoardGeometry, w: float, refs: list[RefInfo], layer: ConductorLayer
) -> float:
    if refs:
        h_max = max(r.distance for r in refs)
    else:
        diel = [x for x in geo.board.stackup.layers if isinstance(x, DielectricLayer)]
        below = [x.thickness for x in diel if x.z_top <= layer.z_bottom + 1e-12]
        above = [x.thickness for x in diel if x.z_bottom >= layer.z_top - 1e-12]
        h_max = (below[-1:] or above[:1] or [0.2e-3])[0]
    return w / 2.0 + max(5.0 * h_max, 3.0 * w)


def _nearest_partner_piece(
    geo: BoardGeometry, net: str, p: F64, layer: str
) -> tuple[F64, float] | None:
    """Nearest point of the partner net's routed pieces on `layer` (the trace layer), and the
    width of the piece it lies on (None when the partner has no routing on that layer)."""
    from oa_geometry.topology import pieces_by_net

    pt = shapely.points(p)
    best: tuple[float, F64, float] | None = None
    for pc in pieces_by_net(geo).get(net, []):
        if pc.seg.layer != layer:
            continue
        d = float(shapely.distance(pt, pc.line))
        if best is None or d < best[0]:
            q = shapely.line_interpolate_point(pc.line, shapely.line_locate_point(pc.line, pt))
            best = (d, np.asarray(q.coords[0]), pc.seg.width)
    return None if best is None else (best[1], best[2])


def _partner_intervals(
    geo: BoardGeometry, layer: str, pair: str | None, lines: object, centers: F64, normals: F64
) -> list[list[tuple[float, float]]]:
    """Merged u-intervals of the partner's copper on `layer` along each cut line."""
    k = len(lines)  # type: ignore[arg-type]
    if pair is None:
        return [[] for _ in range(k)]
    cu = geo.copper(layer, pair)
    if cu.is_empty:
        return [[] for _ in range(k)]
    rings = (
        geo._memoized(("rings-cu", layer, pair), lambda: RingSet(np.array([cu], dtype=object)))
        if _kernels() is not None
        else None
    )
    res = _u_intervals(lines, cu, centers, normals, rings, np.zeros(k, dtype=np.int64))
    return [_merge(iv) for iv in res]


def _placed_partner(
    u_c: float,
    pw: float,
    half: float,
    w_sig: float,
    ivs: list[tuple[float, float]],
    others: dict[str | None, list[tuple[float, float]]],
) -> tuple[float, float] | None:
    """``(u_c, pw)`` when the partner trapezoid centred at `u_c` may stand in the section, None
    otherwise. It must lie inside the window, and either stand on partner copper that the cut
    crosses, or (the converging ends of a pair, where the cut misses the partner's angled run)
    overlap neither the signal nor any other copper on the cut.

    The nearest partner point can lie along the trace rather than across it (a meander, or a
    fan-out where the pair separates): projected on the cut it lands at u ~ 0, on top of the
    signal or of a neighbouring ground, and the BEM would see two coincident conductors."""
    if abs(u_c) + pw / 2 >= half - 1e-9:
        return None
    lo, hi = u_c - pw / 2, u_c + pw / 2

    def hits(a: float, b: float) -> bool:
        return min(hi, b) - max(lo, a) > MIN_INTERVAL

    if any(hits(a, b) for a, b in ivs):
        return (u_c, pw)
    if hits(-w_sig / 2, w_sig / 2) or any(hits(a, b) for iv in others.values() for a, b in iv):
        return None
    return (u_c, pw)


def _poses(piece: Piece, seg: RoutedSegment, ss: list[float]) -> tuple[F64, F64]:
    """Points and unit tangents ((k, 2) arrays) at `ss` metres along the segment; the same
    arithmetic as ``_pose``, vectorised."""
    total = float(piece.line.length)
    s_arr = np.asarray(ss, dtype=float)
    s_poly = np.zeros_like(s_arr) if seg.length <= 0 else s_arr * total / seg.length
    s_poly = np.minimum(np.maximum(s_poly, 0.0), total)
    d = min(1e-6, total / 4.0) if total > 0 else 0.0
    p = shapely.get_coordinates(shapely.line_interpolate_point(piece.line, s_poly))
    if d == 0.0:
        return p, np.tile(np.array([1.0, 0.0]), (len(ss), 1))
    p0 = shapely.get_coordinates(
        shapely.line_interpolate_point(piece.line, np.maximum(0.0, s_poly - d))
    )
    p1 = shapely.get_coordinates(
        shapely.line_interpolate_point(piece.line, np.minimum(total, s_poly + d))
    )
    t = p1 - p0
    return p, t / np.hypot(t[:, 0], t[:, 1])[:, None]


def cross_section_at(
    geo: BoardGeometry,
    seg: RoutedSegment,
    s: float,
    *,
    window: float | None = None,
    include_aggressors: bool = True,
    physics_temp: float | None = None,
    partner: str | None = None,
) -> CrossSection:
    """See ``BoardGeometry.cross_section_at``. `partner` overrides the diff-pair lookup."""
    pair = partner if partner is not None else _partner(geo, seg.net)
    return _sections(
        geo,
        seg,
        _piece_of(geo, seg),
        _spans_of(geo, seg),
        [s],
        pair,
        window,
        include_aggressors,
        physics_temp,
    )[0]


def _sections(
    geo: BoardGeometry,
    seg: RoutedSegment,
    piece: Piece,
    spans: tuple[ReferenceSpan, ...],
    svals: list[float],
    pair: str | None,
    window: float | None,
    include_aggressors: bool,
    physics_temp: float | None,
) -> list[CrossSection]:
    """Cross-sections of `seg` at every `svals`: poses, cut lines and copper intervals are
    computed in bulk (shapely array calls); sections with an identical geometric signature
    share one canonical build and differ only in provenance."""
    layer = geo.conductor(seg.layer)
    k = len(svals)
    ss = [min(max(s, 0.0), seg.length) for s in svals]
    spn = [_span_at(spans, s) for s in ss]
    refs_a = [sp.above if sp else None for sp in spn]
    refs_b = [sp.below if sp else None for sp in spn]
    halves = np.empty(k)
    for i in range(k):
        refs = [r for r in (refs_a[i], refs_b[i]) if r is not None]
        halves[i] = window if window is not None else _default_window(geo, seg.width, refs, layer)
    p, t = _poses(piece, seg, ss)
    n = np.stack((-t[:, 1], t[:, 0]), axis=1)
    lines = shapely.linestrings(
        np.stack((p - n * halves[:, None], p + n * halves[:, None]), axis=1)
    )
    per_nets = _layer_intervals(
        geo, seg.layer, lines, p, n, frozenset(x for x in (seg.net, pair) if x is not None)
    )
    groups: dict[tuple[str, str | None], list[tuple[int, str]]] = {}
    for i in range(k):
        for side, ref in (("above", refs_a[i]), ("below", refs_b[i])):
            if ref is not None:
                groups.setdefault((ref.layer, ref.net), []).append((i, side))
    plane_ivs: dict[tuple[int, str], list[tuple[float, float]]] = {}
    for (lyr, net), mem in groups.items():
        idx = np.array([i for i, _ in mem], dtype=np.intp)
        cu = geo.copper(lyr, net)
        rings = (
            geo._memoized(
                ("rings-cu", lyr, net), lambda cu=cu: RingSet(np.array([cu], dtype=object))
            )
            if _kernels() is not None
            else None
        )
        res = _u_intervals(
            lines[idx], cu, p[idx], n[idx], rings, np.zeros(len(idx), dtype=np.int64)
        )
        for (i, side), iv in zip(mem, res, strict=True):
            plane_ivs[(i, side)] = _merge(iv)
    pair_ivs = _partner_intervals(geo, seg.layer, pair, lines, p, n)
    w_sig = max(_etch_widths(layer, seg.width))
    cache: dict[object, CrossSection] = geo._memoized("xs_templates", dict)
    out: list[CrossSection] = []
    for i in range(k):
        half = float(halves[i])
        per_net = per_nets[i]
        near = None
        if pair is not None:
            nr = _nearest_partner_piece(geo, pair, p[i], seg.layer)
            if nr is not None:
                u_c = float((nr[0] - p[i]) @ n[i])
                near = _placed_partner(u_c, nr[1], half, w_sig, pair_ivs[i], per_net)
            if near is None and pair_ivs[i]:
                # the partner is not alongside here (meander, fan-out, other layer): its copper
                # on the cut is an ordinary aggressor, never a copy laid over the signal
                per_net = {**per_net, pair: pair_ivs[i]}
        side_sig = tuple(
            None if ref is None else (ref.layer, ref.net, ref.distance, tuple(plane_ivs[(i, side)]))
            for side, ref in (("above", refs_a[i]), ("below", refs_b[i]))
        )
        sig = (
            seg.layer,
            seg.net,
            seg.width,
            pair,
            half,
            include_aggressors,
            physics_temp,
            tuple(
                (net, tuple(iv))
                for net, iv in sorted(per_net.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
            ),
            side_sig,
            near,
        )
        tmpl = cache.get(sig)
        pos = (float(p[i, 0]), float(p[i, 1]))
        if tmpl is None:
            tmpl = _build_section(
                geo,
                seg,
                layer,
                pair,
                half,
                per_net,
                [
                    (side, ref, plane_ivs[(i, side)])
                    for side, ref in (("above", refs_a[i]), ("below", refs_b[i]))
                    if ref is not None
                ],
                refs_a[i],
                refs_b[i],
                near,
                pos,
                piece.track,
                include_aggressors,
                physics_temp,
            )
            cache[sig] = tmpl
            out.append(tmpl)
        else:
            prov = tmpl.provenance.model_copy(  # type: ignore[union-attr]
                update={"position": pos, "track_ids": (piece.track,)}
            )
            out.append(tmpl.model_copy(update={"provenance": prov}))
    return out


def _build_section(
    geo: BoardGeometry,
    seg: RoutedSegment,
    layer: ConductorLayer,
    pair: str | None,
    half: float,
    per_net: dict[str | None, list[tuple[float, float]]],
    planes_in: list[tuple[str, RefInfo, list[tuple[float, float]]]],
    refs_above: RefInfo | None,
    refs_below: RefInfo | None,
    near: tuple[float, float] | None,
    pos: tuple[float, float],
    track: str,
    include_aggressors: bool,
    physics_temp: float | None,
) -> CrossSection:
    wb, wt = _etch_widths(layer, seg.width)
    cmat = _conductor_material(geo, layer)
    sigma = _sigma(cmat, physics_temp)

    def trap(u0: float, w_bottom: float, w_top: float) -> XsTrapezoid:
        return XsTrapezoid(
            u_center=u0,
            z_bottom=layer.z_bottom,
            w_bottom=max(w_bottom, NM),
            w_top=max(w_top, NM),
            thickness=layer.thickness,
        )

    def cond(
        id_: str, role: str, shape: XsTrapezoid | XsPlane, net: str | None, lyr: ConductorLayer
    ) -> XsConductor:
        return XsConductor(
            id=id_,
            role=role,  # type: ignore[arg-type]
            shape=shape,
            net=net,
            conductivity=sigma
            if lyr is layer
            else _sigma(_conductor_material(geo, lyr), physics_temp),
            roughness_top=lyr.roughness_top,
            roughness_bottom=lyr.roughness_bottom,
            roughness_side=lyr.roughness_side,
        )

    signals: list[XsConductor] = [cond("s1", "signal", trap(0.0, wb, wt), seg.net, layer)]
    net_ids: list[str] = [seg.net]
    if pair is not None and near is not None:
        u_c, pw = near
        shape = trap(u_c, pw, pw - (wb - wt))
        sig = cond("s2", "signal", shape, pair, layer)
        # P first, N second
        p_net = next((d.p_net for d in geo.board.diff_pairs if pair in (d.p_net, d.n_net)), None)
        if p_net == pair:
            signals = [
                sig.model_copy(update={"id": "s1"}),
                signals[0].model_copy(update={"id": "s2"}),
            ]
            net_ids = [pair, seg.net]
        else:
            signals.append(sig)
            net_ids.append(pair)
    grounds: list[XsConductor] = []
    floating: list[XsConductor] = []
    aggressors: list[tuple[float, XsConductor]] = []
    for net, ivs in sorted(per_net.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        if net == seg.net:
            continue
        for lo, hi in ivs:
            mid, wid = (lo + hi) / 2, hi - lo
            kind = geo.net_kind(net)
            if net is None:
                floating.append(cond("f?", "floating", trap(mid, wid, wid), None, layer))
            elif kind in ("ground", "power"):
                grounds.append(cond("g?", "ground", trap(mid, wid, wid), net, layer))
            elif include_aggressors:
                aggressors.append((abs(mid), cond("a?", "signal", trap(mid, wid, wid), net, layer)))
            else:
                continue
    aggressors.sort(key=lambda a: a[0])
    n_sig = len(signals)
    for k, (_, a) in enumerate(aggressors):
        signals.append(a.model_copy(update={"id": f"s{n_sig + k + 1}"}))
        if a.net is not None and a.net not in net_ids:
            net_ids.append(a.net)
    grounds.sort(key=lambda c: c.shape.u_center)  # type: ignore[union-attr]
    n_cop = len(grounds)
    for k, g in enumerate(grounds):
        grounds[k] = g.model_copy(update={"id": f"g{k + 1}"})
    for k, f in enumerate(floating):
        floating[k] = f.model_copy(update={"id": f"f{k + 1}"})

    # reference planes truncated to the actual copper along the cut
    planes: list[XsConductor] = []
    plane_layers: list[tuple[str, ConductorLayer, RefInfo]] = []
    for side, ref, ivs in planes_in:
        rl = geo.conductor(ref.layer)
        if ivs:
            plane_layers.append((side, rl, ref))
        for lo, hi in ivs:
            u_min = None if lo <= -half + 1e-9 else lo
            u_max = None if hi >= half - 1e-9 else hi
            planes.append(
                cond(
                    f"g{n_cop + len(planes) + 1}",
                    "ground",
                    XsPlane(z_bottom=rl.z_bottom, thickness=rl.thickness, u_min=u_min, u_max=u_max),
                    ref.net,
                    rl,
                )
            )
    above_z = [rl.z_top for side, rl, _ in plane_layers if side == "above"]
    below_z = [rl.z_bottom for side, rl, _ in plane_layers if side == "below"]
    stack = geo.board.stackup.layers
    z_lo = min(below_z) if below_z else stack[-1].z_bottom
    z_hi = max(above_z) if above_z else stack[0].z_top
    z_lo = min(z_lo, layer.z_bottom)
    z_hi = max(z_hi, layer.z_top)

    dielectrics = _dielectrics(geo, z_lo, z_hi)
    all_conductors = (*signals, *grounds, *floating, *planes)
    dielectrics += _mask(geo, layer, all_conductors, z_lo, z_hi)

    has_above = any(side == "above" for side, _, _ in plane_layers)
    has_below = any(side == "below" for side, _, _ in plane_layers)
    ra = next((r for sd, _, r in plane_layers if sd == "above"), None)
    rb = next((r for sd, _, r in plane_layers if sd == "below"), None)
    hint = _classify(len(signals), n_cop, has_above, has_below, ra, rb, _is_outer(geo, layer))
    raw = CrossSection(
        conductors=all_conductors,
        dielectrics=tuple(dielectrics),
        background_dk=1.0,
        topology_hint=hint,  # type: ignore[arg-type]
        symmetric_about_u0=False,
        provenance=XsProvenance(
            board_hash=geo.content_hash,
            net_ids=tuple(net_ids),
            layer=seg.layer,
            position=pos,
            track_ids=(track,),
        ),
    )
    try:  # canonicalize raises when the conductors are not mirror symmetric
        return CrossSection.canonicalize(raw.model_copy(update={"symmetric_about_u0": True}))
    except ValueError:
        return CrossSection.canonicalize(raw)


def _is_outer(geo: BoardGeometry, layer: ConductorLayer) -> bool:
    cl = geo.idx.conductor_layers
    return layer.index == cl[0].index or layer.index == cl[-1].index


def _classify(
    n_sig: int,
    n_cop: int,
    above: bool,
    below: bool,
    ra: RefInfo | None,
    rb: RefInfo | None,
    outer: bool,
) -> str:
    if n_sig == 1:
        if n_cop and (above ^ below):
            return "cpwg"
        if n_cop and not (above or below):
            return "cpw"
        if n_cop:
            return "general"
        if above and below:
            assert ra is not None and rb is not None
            same = round(ra.distance / NM) == round(rb.distance / NM)
            return "stripline" if same else "offset_stripline"
        if above or below:
            return "microstrip" if outer else "embedded_microstrip"
        return "general"
    if n_sig == 2 and not n_cop:
        if above and below:
            return "edge_coupled_stripline"
        if (above or below) and outer:
            return "edge_coupled_microstrip"
    return "general"


def _dielectrics(geo: BoardGeometry, z_lo: float, z_hi: float) -> list[XsDielectric]:
    out: list[XsDielectric] = []
    layers = geo.board.stackup.layers
    diel = [x for x in layers if isinstance(x, DielectricLayer)]
    for d in diel:
        lo, hi = max(d.z_bottom, z_lo), min(d.z_top, z_hi)
        if hi - lo <= NM / 10:
            continue
        m = _dielectric(geo, d.material)
        out.append(
            XsDielectric(
                region=XsSlab(z_bottom=lo, z_top=hi),
                model=m.model,
                dk_z_over_dk_xy=m.dk_z_over_dk_xy,
                priority=0,
            )
        )
    # inner copper layers (dielectric on both sides): the adjacent laminate fills between traces
    for c in geo.idx.conductor_layers:
        below = [d for d in diel if abs(d.z_top - c.z_bottom) < 1e-9]
        above = [d for d in diel if abs(d.z_bottom - c.z_top) < 1e-9]
        if not below or not above:
            continue
        lo, hi = max(c.z_bottom, z_lo), min(c.z_top, z_hi)
        if hi - lo <= NM / 10:
            continue
        src = (
            below[0]
            if below[0].fills_between_traces or not above[0].fills_between_traces
            else above[0]
        )
        m = _dielectric(geo, src.material)
        out.append(
            XsDielectric(
                region=XsSlab(z_bottom=lo, z_top=hi),
                model=m.model,
                dk_z_over_dk_xy=m.dk_z_over_dk_xy,
                priority=0,
            )
        )
    return out


def _mask(
    geo: BoardGeometry,
    layer: ConductorLayer,
    conductors: tuple[XsConductor, ...],
    z_lo: float,
    z_hi: float,
) -> list[XsDielectric]:
    """Solder mask over an outer trace layer: a slab over bare laminate plus a conformal region
    (conductor outlines offset by the mask thickness over copper)."""
    cl = geo.idx.conductor_layers
    masks = [m for m in geo.board.stackup.layers if isinstance(m, MaskLayer) and m.present]
    want = "MT" if layer.index == cl[0].index else "MB" if layer.index == cl[-1].index else None
    mask = next((m for m in masks if m.id == want), None)
    if mask is None:
        return []
    mat = _dielectric(geo, mask.material)
    top = want == "MT"
    face = layer.z_bottom if top else layer.z_top
    t_bare = mask.thickness
    t_cu = mask.thickness_over_copper if mask.thickness_over_copper is not None else mask.thickness
    out = [
        XsDielectric(
            region=XsSlab(z_bottom=face, z_top=face + t_bare)
            if top
            else XsSlab(z_bottom=face - t_bare, z_top=face),
            model=mat.model,
            dk_z_over_dk_xy=mat.dk_z_over_dk_xy,
            priority=0,
        )
    ]
    boxes = []
    for c in conductors:
        sh = c.shape
        if isinstance(sh, XsTrapezoid) and abs(sh.z_bottom - layer.z_bottom) < 1e-9:
            hb, ht = sh.w_bottom / 2, sh.w_top / 2
            boxes.append(
                shapely.Polygon(
                    [
                        (sh.u_center - hb, sh.z_bottom),
                        (sh.u_center + hb, sh.z_bottom),
                        (sh.u_center + ht, sh.z_bottom + sh.thickness),
                        (sh.u_center - ht, sh.z_bottom + sh.thickness),
                    ]
                )
            )
    if boxes:
        region = shapely.union_all(boxes).buffer(t_cu, join_style="round", quad_segs=8)
        clip = (
            shapely.box(-1.0, face, 1.0, z_hi + t_cu)
            if top
            else shapely.box(-1.0, z_lo - t_cu, 1.0, face)
        )
        region = shapely.intersection(region, clip)
        for poly in polygons_of(region):
            if poly.area <= 0:
                continue
            q = shapely.geometry.polygon.orient(poly, 1.0)
            out.append(
                XsDielectric(
                    region=XsRegion(
                        polygon=ModelPolygon(
                            exterior=Points(np.asarray(q.exterior.coords)[:-1]),
                            holes=tuple(Points(np.asarray(r.coords)[:-1]) for r in q.interiors),
                        )
                    ),
                    model=mat.model,
                    dk_z_over_dk_xy=mat.dk_z_over_dk_xy,
                    priority=10,
                )
            )
    return out


# ---------------------------------------------------------------------------------------------
# sample_net
# ---------------------------------------------------------------------------------------------


def _ref_key(r: RefInfo | None) -> tuple[str, str] | None:
    return None if r is None else (r.layer, r.net)


def sample_net(
    geo: BoardGeometry, net: str | tuple[str, str], *, ds: float | None = None
) -> tuple[XsSample, ...]:
    """Cross-sections along `net` (or the pair ``(p, n)``, sampled along `p`).

    Samples every `ds` metres (default ``min(0.5 mm, 2h)`` per segment, h = distance to the
    nearest reference) and at every event: width change, neckdown (width < 0.8 x previous),
    layer change, reference change, plane edge (partial cover) and no reference.
    """
    if isinstance(net, tuple):
        primary, partner = net
    else:
        primary, partner = net, None
    pieces = oriented_pieces(geo, primary)
    ends: dict[tuple[int, int], Piece] = {}
    for pc in pieces:
        ends[(round(pc.end[0] / 1e-8), round(pc.end[1] / 1e-8))] = pc
    out: list[XsSample] = []
    for pc in pieces:
        seg = pc.seg
        spans = _spans_of(geo, seg)
        prev = ends.get((round(pc.start[0] / 1e-8), round(pc.start[1] / 1e-8)))
        h_seg = min(
            (r.distance for sp in spans for r in (sp.above, sp.below) if r is not None),
            default=0.25e-3,
        )
        step = ds if ds is not None else min(0.5e-3, 2.0 * max(h_seg, 1e-6))
        eps = min(1e-6, seg.length * 1e-3)
        pos: dict[float, str | None] = {}
        n_steps = max(1, math.ceil(seg.length / step))
        for i in range(n_steps + 1):
            pos[min(seg.length, i * step)] = None
        pos[max(0.0, seg.length - eps)] = pos.get(max(0.0, seg.length - eps))
        pos.pop(seg.length, None)
        for k, sp0 in enumerate(spans):
            if k == 0:
                continue
            at = min(seg.length, sp0.s_start + eps)
            pos[at] = "boundary"
        samples = sorted(pos.items())
        pair = partner if partner is not None else _partner(geo, seg.net)
        xss = _sections(
            geo,
            seg,
            _piece_of(geo, seg),
            spans,
            [s for s, _ in samples],
            pair,
            None,
            True,
            None,
        )
        first = True
        for (s, tag), xs in zip(samples, xss, strict=True):
            sp = _span_at(spans, s)
            event = "regular"
            if first:
                event = _start_event(prev.seg if prev else None, seg)
            if sp is not None and (first or tag == "boundary"):
                before = _span_at(spans, max(0.0, s - 2 * eps)) if not first else None
                event = _ref_event(event, before, sp, first)
            if event == "regular" and sp is not None and tag == "boundary":
                event = "plane_edge" if _partial(sp) else "regular"
            first = False
            xy = pc.line.interpolate(
                s * float(pc.line.length) / seg.length if seg.length > 0 else 0.0
            )
            out.append(
                XsSample(
                    s=seg.s_start + s,
                    position=(float(xy.x), float(xy.y)),
                    layer=seg.layer,
                    seg_id=seg.id,
                    xs=xs,
                    event=event,  # type: ignore[arg-type]
                )
            )
    return tuple(out)


def _partial(sp: ReferenceSpan) -> bool:
    return any(r is not None and r.coverage < 0.999 for r in (sp.above, sp.below))


def _start_event(prev: RoutedSegment | None, seg: RoutedSegment) -> str:
    if prev is None:
        return "regular"
    if prev.layer != seg.layer:
        return "layer_change"
    if abs(prev.width - seg.width) > NM:
        return "neckdown" if seg.width < 0.8 * prev.width else "width_change"
    return "regular"


def _ref_event(event: str, before: ReferenceSpan | None, now: ReferenceSpan, first: bool) -> str:
    if now.above is None and now.below is None:
        return "no_reference"
    if event != "regular":
        return event
    if before is not None and (
        _ref_key(before.above) != _ref_key(now.above)
        or _ref_key(before.below) != _ref_key(now.below)
    ):
        return "ref_change"
    if _partial(now) and not first:
        return "plane_edge"
    return event


__all__ += ["LineSeg"]
