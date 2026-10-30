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
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import shapely
from shapely import LineString

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


@dataclass(frozen=True)
class _Cut:
    center: F64
    normal: F64
    half: float
    line: LineString


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


def _pose(piece: Piece, s: float) -> tuple[F64, F64]:
    """Point and unit tangent at `s` metres along the segment."""
    total = float(piece.line.length)
    s_poly = 0.0 if piece.seg.length <= 0 else s * total / piece.seg.length
    s_poly = min(max(s_poly, 0.0), total)
    d = min(1e-6, total / 4.0) if total > 0 else 0.0
    p = np.asarray(shapely.line_interpolate_point(piece.line, s_poly).coords[0])
    if d == 0.0:
        return p, np.array([1.0, 0.0])
    p0 = np.asarray(shapely.line_interpolate_point(piece.line, max(0.0, s_poly - d)).coords[0])
    p1 = np.asarray(shapely.line_interpolate_point(piece.line, min(total, s_poly + d)).coords[0])
    t = p1 - p0
    return p, t / float(np.hypot(t[0], t[1]))


def _intervals(cut: _Cut, geom: object) -> list[tuple[float, float]]:
    """u-intervals where the cut line lies inside `geom`."""
    inter = shapely.intersection(cut.line, geom)
    out: list[tuple[float, float]] = []
    for part in shapely.get_parts(inter):
        if part.geom_type != "LineString":
            continue
        xy = np.asarray(part.coords)
        u = (xy - cut.center[None, :]) @ cut.normal
        lo, hi = float(np.min(u)), float(np.max(u))
        if hi - lo > MIN_INTERVAL:
            out.append((lo, hi))
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
    geo: BoardGeometry, layer: str, cut: _Cut
) -> dict[str | None, list[tuple[float, float]]]:
    """u-intervals of copper on `layer` along the cut, per net."""
    tree = geo.strtree(layer)
    per: dict[str | None, list[tuple[float, float]]] = {}
    for i in tree.query_indices(cut.line):
        iv = _intervals(cut, tree.geometry(int(i)))
        if iv:
            per.setdefault(tree.net(int(i)), []).extend(iv)
    return {k: _merge(v) for k, v in per.items()}


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


def _nearest_partner_piece(geo: BoardGeometry, net: str, p: F64) -> tuple[F64, float] | None:
    """Nearest point of the partner net's routed pieces on the trace layer's plane, and the
    width of the piece it lies on (None when the partner has no routing)."""
    from oa_geometry.topology import pieces_by_net

    pt = shapely.points(p)
    best: tuple[float, F64, float] | None = None
    for pc in pieces_by_net(geo).get(net, []):
        d = float(shapely.distance(pt, pc.line))
        if best is None or d < best[0]:
            q = shapely.line_interpolate_point(pc.line, shapely.line_locate_point(pc.line, pt))
            best = (d, np.asarray(q.coords[0]), pc.seg.width)
    return None if best is None else (best[1], best[2])


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
    layer = geo.conductor(seg.layer)
    piece = _piece_of(geo, seg)
    spans = _spans_of(geo, seg)
    s = min(max(s, 0.0), seg.length)
    span = _span_at(spans, s)
    refs_above = span.above if span else None
    refs_below = span.below if span else None
    refs = [r for r in (refs_above, refs_below) if r is not None]
    half = window if window is not None else _default_window(geo, seg.width, refs, layer)
    p, t = _pose(piece, s)
    n = np.array([-t[1], t[0]])
    cut = _Cut(p, n, half, shapely.linestrings([p - n * half, p + n * half]))

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

    per_net = _layer_intervals(geo, seg.layer, cut)
    signals: list[XsConductor] = [cond("s1", "signal", trap(0.0, wb, wt), seg.net, layer)]
    pair = partner if partner is not None else _partner(geo, seg.net)
    net_ids: list[str] = [seg.net]
    near = _nearest_partner_piece(geo, pair, p) if pair is not None else None
    if pair is not None and near is not None:
        q, pw = near
        u_c = float((q - p) @ n)
        if abs(u_c) + pw / 2 < half - 1e-9:
            shape = trap(u_c, pw, pw - (wb - wt))
            sig = cond("s2", "signal", shape, pair, layer)
            # P first, N second
            p_net = next(
                (d.p_net for d in geo.board.diff_pairs if pair in (d.p_net, d.n_net)), None
            )
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
        if net == seg.net or (net == pair and net is not None):
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
    for side, ref in (("above", refs_above), ("below", refs_below)):
        if ref is None:
            continue
        rl = geo.conductor(ref.layer)
        ivs = _merge(_intervals(cut, geo.copper(ref.layer, ref.net)))
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
            position=(float(p[0]), float(p[1])),
            track_ids=(piece.track,),
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
        first = True
        for s, tag in samples:
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
            xs = cross_section_at(geo, seg, s, partner=partner)
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
