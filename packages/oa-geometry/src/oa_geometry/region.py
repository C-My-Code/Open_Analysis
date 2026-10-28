# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="arg-type,assignment,attr-defined,call-overload,index,return-value"
# mypy: disable-error-code="no-any-return,unused-ignore,import-untyped,union-attr,var-annotated"
"""Region extraction for 3D (``cut_region``) and terminal resolution (``resolve_terminal``).

``cut_region`` clips all copper to a box and returns a solver-neutral ``Structure3D``:

- one ``ConductorBody`` per (layer, net) holding the clipped polygons with their holes (antipads,
  slots) intact; role ``signal`` for ``spec.nets``, ``reference`` for the reference nets (given or
  taken from the ReferenceMap spans of the signal segments in the box), else ``floating``;
- dielectric slabs for every ``DielectricLayer`` in the z range, filler slabs over inner copper
  layers, and solder mask slabs;
- one ``ViaBody`` per plated via/through-hole pad inside the box (after backdrilling);
- ``boundary_crossings``: every routed piece of ``spec.nets`` crossing a face of the box.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import shapely

from oa_geometry._shapes import place, shape_geometry, to_model_polygons
from oa_geometry.errors import RegionError, TerminalError, UnknownItem
from oa_geometry.models import (
    BoundaryCrossing,
    RegionCut,
    RegionSpec,
    ResolvedTerminal,
)
from oa_geometry.topology import pieces_by_net
from oa_geometry.vias import barrel_z_range, pad_copper_layers, via_copper_layers
from oa_model.em import (
    Box3,
    ConductorBody,
    MaterialSlab,
    RegionProvenance,
    Structure3D,
    ViaBody,
    ViaPad,
)
from oa_model.materials import ConductorMaterial, DielectricMaterial
from oa_model.setup.ports import (
    NearestReferenceTerminal,
    PadTerminal,
    PinGroupTerminal,
    PointTerminal,
    Terminal,
)
from oa_model.stackup import ConductorLayer, DielectricLayer, MaskLayer

if TYPE_CHECKING:
    from oa_geometry.geometry import BoardGeometry

C0 = 299_792_458.0
NM = 1e-9
_RULES = ("clip_copper_to_bbox", "keep_holes", "dielectric_slabs", "plated_vias")

__all__ = ["cut_region", "resolve_terminal"]


def _dk(m: DielectricMaterial) -> float:
    return float(getattr(m.model, "dk", 4.5))


def _auto_bbox(
    geo: BoardGeometry, spec: RegionSpec, h_max: float
) -> tuple[float, float, float, float]:
    boxes: list[tuple[float, float, float, float]] = []
    w_max = 0.0
    for net in spec.nets:
        for pc in pieces_by_net(geo).get(net, []):
            boxes.append(tuple(pc.line.bounds))
            w_max = max(w_max, pc.seg.width)
    for lid in geo.layer_ids():
        for net in spec.nets:
            mp = geo.copper(lid, net)
            if not mp.is_empty:
                boxes.append(tuple(mp.bounds))
    if not boxes:
        raise RegionError(
            f"nets {list(spec.nets)} have no copper", hint="Check the net names in the RegionSpec."
        )
    b = np.array(boxes)
    margin = spec.margin
    if margin is None:
        margin = max(5.0 * h_max, 3.0 * w_max)
        if spec.f_max is not None:
            dk = max(
                (_dk(m) for m in geo.board.stackup.materials if isinstance(m, DielectricMaterial)),
                default=4.5,
            )
            margin = max(margin, C0 / (spec.f_max * math.sqrt(dk)) / 10.0)
    return (
        float(b[:, 0].min() - margin),
        float(b[:, 1].min() - margin),
        float(b[:, 2].max() + margin),
        float(b[:, 3].max() + margin),
    )


def cut_region(geo: BoardGeometry, spec: RegionSpec) -> RegionCut:
    """See ``BoardGeometry.cut_region``."""
    if not spec.nets:
        raise RegionError("RegionSpec.nets is empty", hint="Name at least one signal net.")
    known = {n.id for n in geo.board.nets}
    for n in (*spec.nets, *spec.reference_nets):
        if n not in known:
            raise UnknownItem(f"unknown net {n!r}", path=n)
    stack = geo.board.stackup
    layers_all = geo.idx.conductor_layers
    allowed = set(spec.layers) if spec.layers is not None else {c.id for c in layers_all}
    for lid in allowed:
        geo.conductor(lid)
    sel = [c for c in layers_all if c.id in allowed]
    z0, z1 = min(c.z_bottom for c in sel), max(c.z_top for c in sel)
    if spec.layers is None:
        z0, z1 = stack.layers[-1].z_bottom, stack.layers[0].z_top

    # reference nets
    ref_nets = list(spec.reference_nets)
    spans_h = 0.0
    if not ref_nets:
        from oa_geometry.refmap import spans_for_net

        found: dict[str, None] = {}
        for net in spec.nets:
            spans_for_net(geo, net)
            for pc in pieces_by_net(geo).get(net, []):
                from oa_geometry.refmap import piece_spans

                for sp in piece_spans(geo, pc.seg.id):
                    for r in (sp.above, sp.below):
                        if r is not None:
                            found.setdefault(r.net, None)
                            spans_h = max(spans_h, r.distance)
        ref_nets = list(found)
    if spans_h == 0.0:
        diel = [x.thickness for x in stack.layers if isinstance(x, DielectricLayer)]
        spans_h = max(diel, default=0.0)

    bbox = spec.bbox if spec.bbox is not None else _auto_bbox(geo, spec, spans_h)
    xmin, ymin, xmax, ymax = bbox
    if xmax <= xmin or ymax <= ymin:
        raise RegionError(f"degenerate bbox {bbox}", hint="Use xmin < xmax and ymin < ymax.")
    box = shapely.box(xmin, ymin, xmax, ymax)

    def role(net: str | None) -> str:
        if net in spec.nets:
            return "signal"
        return "reference" if net in ref_nets else "floating"

    bodies: list[ConductorBody] = []
    used_mats: dict[str, None] = {}
    for c in sel:
        for cnet, mp in geo._copper[c.id].by_net().items():
            brole = role(cnet)
            if brole == "floating" and not spec.keep_all_copper:
                continue
            clipped = shapely.intersection(mp, box)
            polys = to_model_polygons(clipped, min_area=1e-15)
            if not polys:
                continue
            used_mats.setdefault(c.material, None)
            bodies.append(
                ConductorBody(
                    id=f"{c.id}:{cnet if cnet is not None else '-'}",
                    net=cnet,
                    role=brole,  # type: ignore[arg-type]
                    layer=c.id,
                    z_bottom=c.z_bottom,
                    z_top=c.z_top,
                    polygons=polys,
                    material=c.material,
                    roughness=c.roughness_top,
                )
            )

    slabs = _slabs(geo, z0, z1, used_mats)
    vias = _vias(geo, box, allowed, spec, ref_nets)
    mats = tuple(m for m in stack.materials if m.name in used_mats)
    structure = Structure3D(
        bbox=Box3(xmin=xmin, ymin=ymin, zmin=z0, xmax=xmax, ymax=ymax, zmax=z1),
        materials=mats,
        slabs=tuple(slabs),
        conductors=tuple(bodies),
        vias=tuple(vias),
        provenance=RegionProvenance(
            board_hash=geo.content_hash,
            nets=spec.nets,
            bbox=bbox,
            region=spec.model_dump(mode="json"),
            cut_rules=_RULES,
        ),
    )
    return RegionCut(
        bbox=bbox, structure=structure, boundary_crossings=_crossings(geo, spec, bbox, allowed)
    )


def _slabs(geo: BoardGeometry, z0: float, z1: float, used: dict[str, None]) -> list[MaterialSlab]:
    out: list[MaterialSlab] = []
    layers = geo.board.stackup.layers
    diel = [x for x in layers if isinstance(x, DielectricLayer)]
    for d in diel:
        lo, hi = max(d.z_bottom, z0), min(d.z_top, z1)
        if hi - lo > NM / 10:
            out.append(MaterialSlab(material=d.material, z_bottom=lo, z_top=hi))
            used.setdefault(d.material, None)
    for c in geo.idx.conductor_layers:
        below = [d for d in diel if abs(d.z_top - c.z_bottom) < 1e-9]
        above = [d for d in diel if abs(d.z_bottom - c.z_top) < 1e-9]
        if not below or not above:
            continue
        lo, hi = max(c.z_bottom, z0), min(c.z_top, z1)
        if hi - lo > NM / 10:
            src = below[0] if below[0].fills_between_traces else above[0]
            out.append(MaterialSlab(material=src.material, z_bottom=lo, z_top=hi))
    for m in layers:
        if isinstance(m, MaskLayer) and m.present:
            lo, hi = max(m.z_bottom, z0), min(m.z_top, z1)
            if hi - lo > NM / 10:
                out.append(MaterialSlab(material=m.material, z_bottom=lo, z_top=hi))
                used.setdefault(m.material, None)
    return out


def _vias(
    geo: BoardGeometry,
    box: shapely.Polygon,
    allowed: set[str],
    spec: RegionSpec,
    ref_nets: list[str],
) -> list[ViaBody]:
    out: list[ViaBody] = []
    ps_map = {p.id: p for p in geo.board.padstacks}

    def r_of(net: str | None) -> str:
        return "signal" if net in spec.nets else "reference" if net in ref_nets else "floating"

    def discs(ps_id: str, layers: tuple[str, ...]) -> tuple[ViaPad, ...]:
        res = []
        ps = ps_map[ps_id]
        for pl in ps.layers:
            if pl.pad is None or pl.layer not in layers or pl.layer not in allowed:
                continue
            area = shape_geometry(pl.pad, geo.arc_tol).area
            lay = geo.conductor(pl.layer)
            res.append(
                ViaPad(
                    layer=pl.layer,
                    radius=math.sqrt(area / math.pi),
                    z_bottom=lay.z_bottom,
                    z_top=lay.z_top,
                )
            )
        return tuple(res)

    for v in geo.board.vias:
        ps = ps_map.get(v.padstack)
        if ps is None or ps.drill is None or not box.covers(shapely.Point(v.position)):
            continue
        lays = via_copper_layers(geo.idx, v, ps)
        if not set(lays) & allowed:
            continue
        zb, zt = barrel_z_range(geo.board, v)
        r = ps.drill.diameter / 2.0
        filled = v.protection.filled == "conductive"
        out.append(
            ViaBody(
                id=f"via:{v.id}",
                net=v.net,
                role=r_of(v.net),  # type: ignore[arg-type]
                center=v.position,
                z_bottom=zb,
                z_top=zt,
                r_outer=r,
                plating=r if filled else min(ps.drill.plating_thickness, r),
                fill_material=v.protection.fill_material if filled else None,
                pads=discs(v.padstack, lays),
            )
        )
    for p in geo.board.pads:
        ps = ps_map.get(p.padstack)
        if ps is None or ps.drill is None or ps.drill.kind != "plated":
            continue
        if not box.covers(shapely.Point(p.position)):
            continue
        lays = pad_copper_layers(ps)
        if len(lays) < 2 or not set(lays) & allowed:
            continue
        cl = [geo.conductor(x) for x in lays]
        r = ps.drill.diameter / 2.0
        out.append(
            ViaBody(
                id=f"pad:{p.id}",
                net=p.net,
                role=r_of(p.net),  # type: ignore[arg-type]
                center=p.position,
                z_bottom=min(c.z_bottom for c in cl),
                z_top=max(c.z_top for c in cl),
                r_outer=r,
                plating=min(ps.drill.plating_thickness, r),
                pads=discs(p.padstack, lays),
            )
        )
    return out


def _crossings(
    geo: BoardGeometry, spec: RegionSpec, bbox: tuple[float, float, float, float], allowed: set[str]
) -> tuple[BoundaryCrossing, ...]:
    xmin, ymin, xmax, ymax = bbox
    edge = shapely.box(*bbox).exterior
    faces = (("xmin", xmin, 0), ("xmax", xmax, 0), ("ymin", ymin, 1), ("ymax", ymax, 1))
    out: list[BoundaryCrossing] = []
    for net in spec.nets:
        for pc in pieces_by_net(geo).get(net, []):
            if pc.seg.layer not in allowed or not shapely.intersects(pc.line, edge):
                continue
            lay = geo.conductor(pc.seg.layer)
            pts = [
                g
                for g in shapely.get_parts(shapely.intersection(pc.line, edge))
                if g.geom_type == "Point"
            ]
            for pt in pts:
                x, y = pt.x, pt.y
                face = next((name for name, v, ax in faces if abs((x, y)[ax] - v) <= 1e-9), None)
                if face is None:
                    continue
                at = float(shapely.line_locate_point(pc.line, pt))
                total = float(pc.line.length)
                d = min(1e-6, total / 4)
                a = shapely.line_interpolate_point(pc.line, max(0.0, at - d))
                b = shapely.line_interpolate_point(pc.line, min(total, at + d))
                out.append(
                    BoundaryCrossing(
                        net=net,
                        layer=pc.seg.layer,
                        seg_id=pc.seg.id,
                        face=face,  # type: ignore[arg-type]
                        position=(x, y),
                        direction=math.atan2(b.y - a.y, b.x - a.x),
                        width=pc.seg.width,
                        z_bottom=lay.z_bottom,
                        z_top=lay.z_top,
                    )
                )
    out.sort(key=lambda c: (c.net, c.seg_id, c.face))
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# Terminals
# ---------------------------------------------------------------------------------------------


def _pad_patch(
    geo: BoardGeometry, pad_ids: list[str]
) -> tuple[tuple[float, float], tuple[float, float], str | None, float]:
    """Centre, extent, first copper layer and its mid height for a set of pads."""
    boxes = []
    layer: str | None = None
    for pid in pad_ids:
        p = geo.idx.pad(pid)
        ps = geo.idx.padstack(p.padstack)
        lays = pad_copper_layers(ps)
        if lays and layer is None:
            layer = lays[0]
        if lays:
            local = shape_geometry(
                next(x.pad for x in ps.layers if x.layer == lays[0] and x.pad), geo.arc_tol
            )
            g = place(local, np.array([p.position]), np.array([p.rotation]))[0]
            boxes.append(g.bounds)
        else:
            boxes.append((*p.position, *p.position))
    b = np.array(boxes)
    x0, y0, x1, y1 = b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()
    z = 0.0
    if layer is not None:
        lay = geo.conductor(layer)
        z = (lay.z_bottom + lay.z_top) / 2.0
    return ((float(x0 + x1) / 2, float(y0 + y1) / 2), (float(x1 - x0), float(y1 - y0)), layer, z)


def resolve_terminal(
    geo: BoardGeometry,
    t: Terminal,
    *,
    near: tuple[float, float] | None = None,
    layer: str | None = None,
) -> ResolvedTerminal:
    """See ``BoardGeometry.resolve_terminal``."""
    if isinstance(t, PadTerminal):
        pad = None
        try:
            if t.pad is not None:
                pad = geo.idx.pad(t.pad)
            else:
                pad = next(
                    (p for p in geo.board.pads if p.component == t.component and p.number == t.pin),
                    None,
                )
        except KeyError:
            pad = None
        if pad is None:
            raise TerminalError(
                f"no pad for terminal {t.model_dump(exclude_none=True)}",
                path=t.pad or f"{t.component}.{t.pin}",
                hint="Give an existing pad id, or component and pin.",
            )
        c, ext, lyr, z = _pad_patch(geo, [pad.id])
        return ResolvedTerminal(
            kind="pad",
            position=pad.position,
            z=z,
            layer=lyr,
            net=pad.net,
            pads=(pad.id,),
            extent=ext,
        )
    if isinstance(t, PinGroupTerminal):
        pads = [
            p
            for p in geo.board.pads
            if p.component == t.component and p.net in t.nets and (not t.pins or p.number in t.pins)
        ]
        if not pads:
            raise TerminalError(
                f"component {t.component!r} has no pads on {list(t.nets)}",
                path=t.component,
                hint="Check the component refdes, nets and pin numbers.",
            )
        c, ext, lyr, z = _pad_patch(geo, [p.id for p in pads])
        return ResolvedTerminal(
            kind="pin_group",
            position=c,
            z=z,
            layer=lyr,
            net=pads[0].net,
            pads=tuple(p.id for p in pads),
            extent=ext,
        )
    if isinstance(t, PointTerminal):
        lay = geo.conductor(t.layer)
        pt = shapely.Point(t.position)
        hit = [(n, mp) for n, mp in geo._copper[t.layer].by_net().items() if mp.distance(pt) <= NM]
        if not hit:
            raise TerminalError(
                f"no copper at {t.position} on {t.layer}",
                path=t.layer,
                hint="Move the point onto copper.",
            )
        net = hit[0][0]
        if t.net is not None and all(n != t.net for n, _ in hit):
            raise TerminalError(
                f"copper at {t.position} on {t.layer} belongs to {net!r}, not {t.net!r}",
                path=t.layer,
                hint="Fix the net assertion or the position.",
            )
        return ResolvedTerminal(
            kind="point",
            position=t.position,
            z=(lay.z_bottom + lay.z_top) / 2.0,
            layer=t.layer,
            net=t.net if t.net is not None else net,
            pads=(),
            extent=(0.0, 0.0),
        )
    assert isinstance(t, NearestReferenceTerminal)
    if near is None:
        raise TerminalError(
            "nearest_reference terminal needs the positive terminal position",
            hint="Pass near=(x, y) (and layer=) to resolve_terminal.",
        )
    pt = shapely.Point(near)
    wanted = set(t.nets) if t.nets else None
    best: tuple[float, float, str, str, tuple[float, float]] | None = None
    ref_idx = geo.conductor(layer).index if layer is not None else None
    for lid in geo.layer_ids():
        for net, mp in geo._copper[lid].by_net().items():
            if net is None:
                continue
            if wanted is not None and net not in wanted:
                continue
            if wanted is None and geo.net_kind(net) != "ground":
                continue
            d = float(mp.distance(pt))
            if d > t.search_radius:
                continue
            dz = abs(geo.conductor(lid).index - ref_idx) if ref_idx is not None else 0
            q = shapely.ops.nearest_points(mp, pt)[0]
            cand = (d, float(dz), lid, net, (float(q.x), float(q.y)))
            if best is None or cand[:2] < best[:2]:
                best = cand
    if best is None:
        raise TerminalError(
            f"no reference copper within {t.search_radius} m of {near}",
            hint="Increase search_radius or name the reference nets.",
        )
    _, _, lid, net, xy = best
    lay = geo.conductor(lid)
    return ResolvedTerminal(
        kind="nearest_reference",
        position=xy,
        z=(lay.z_bottom + lay.z_top) / 2.0,
        layer=lid,
        net=net,
        pads=(),
        extent=(0.0, 0.0),
    )


__all__ += ["ConductorLayer", "ConductorMaterial"]
