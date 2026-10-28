# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run with KiCad's bundled Python (``python.exe kicad_post.py IN.kicad_pcb OUT.kicad_pcb STATS.json``).

Loads a generated board with pcbnew, refills all zones (``ZONE_FILLER``), saves it, and writes a JSON
report: object counts and per-layer, per-net copper area (m^2) of the refilled board.  GPL: this file imports
pcbnew and therefore runs out of process only (see ADR-0006).
"""

from __future__ import annotations

import json
import sys

import pcbnew  # type: ignore[import-not-found]

NM = 1e-9


def to_shapely(ps):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    polys = []
    for i in range(ps.OutlineCount()):
        o = ps.Outline(i)
        shell = [(o.CPoint(k).x * NM, o.CPoint(k).y * NM) for k in range(o.PointCount())]
        holes = []
        for h in range(ps.HoleCount(i)):
            ho = ps.Hole(i, h)
            holes.append([(ho.CPoint(k).x * NM, ho.CPoint(k).y * NM) for k in range(ho.PointCount())])
        p = Polygon(shell, holes)
        if not p.is_valid:
            p = p.buffer(0)
        polys.append(p)
    return unary_union(polys) if polys else None


def item_poly(item, layer, maxerr):
    ps = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolygon(ps, layer, 0, maxerr, pcbnew.ERROR_INSIDE)
    return to_shapely(ps)


def main(src: str, dst: str, out_json: str) -> None:
    from shapely.ops import unary_union

    board = pcbnew.LoadBoard(src)
    if board is None:
        raise SystemExit(f"pcbnew could not load {src}")
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(dst)

    cu = list(board.GetEnabledLayers().CuStack())
    names = {l: board.GetLayerName(l) for l in cu}
    maxerr = 1000  # 1 um

    vt = {pcbnew.VIATYPE_THROUGH: "through", pcbnew.VIATYPE_BLIND: "blind", pcbnew.VIATYPE_BURIED: "buried",
          pcbnew.VIATYPE_MICROVIA: "micro"}
    vias = {}
    tracks = arcs = 0
    nets = set()
    for t in board.GetTracks():
        cls = t.GetClass()
        if cls == "PCB_VIA":
            vias[vt[t.GetViaType()]] = vias.get(vt[t.GetViaType()], 0) + 1
        elif cls == "PCB_ARC":
            arcs += 1
        else:
            tracks += 1
        if t.GetNetname():
            nets.add(t.GetNetname())
    pads = smd = tht = 0
    for fp in board.GetFootprints():
        for p in fp.Pads():
            pads += 1
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD:
                smd += 1
            else:
                tht += 1
            if p.GetNetname():
                nets.add(p.GetNetname())
    zones = rule_areas = 0
    zone_report = []
    for z in board.Zones():
        if z.GetIsRuleArea():
            rule_areas += 1
            continue
        zones += 1
        nets.add(z.GetNetname())
        for l in z.GetLayerSet().CuStack():
            fp = z.GetFilledPolysList(l)
            zone_report.append({"net": z.GetNetname(), "layer": names[l], "priority": z.GetAssignedPriority(),
                                "filled_area_m2": fp.Area() * NM * NM, "outline_count": fp.OutlineCount()})

    # copper area per layer per net
    area: dict[str, dict[str, float]] = {}
    geoms: dict[tuple[str, str], list] = {}
    for l in cu:
        per: dict[str, list] = {}
        for t in board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                if not t.FlashLayer(l):
                    continue
            elif t.GetLayer() != l:
                continue
            g = item_poly(t, l, maxerr)
            if g is not None:
                per.setdefault(t.GetNetname(), []).append(g)
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.IsOnLayer(l) and p.FlashLayer(l):
                    g = item_poly(p, l, maxerr)
                    if g is not None:
                        per.setdefault(p.GetNetname(), []).append(g)
        for z in board.Zones():
            if z.GetIsRuleArea() or not z.IsOnLayer(l):
                continue
            g = to_shapely(z.GetFilledPolysList(l))
            if g is not None:
                per.setdefault(z.GetNetname(), []).append(g)
        a = {}
        for net, gl in per.items():
            u = unary_union(gl)
            a[net or ""] = u.area
            geoms[(names[l], net)] = u
        area[names[l]] = {k: a[k] for k in sorted(a)}

    checks = {}
    ped = geoms.get(("F.Cu", "SIG_PLEDGE"))
    gnd2 = geoms.get(("In1.Cu", "GND"))
    if ped is not None and gnd2 is not None:
        checks["SIG_PLEDGE_to_L2_plane_edge_m"] = ped.distance(gnd2.boundary)
    report = {
        "kicad_version": pcbnew.Version(),
        "counts": {"nets": len(nets - {""}), "vias": dict(sorted(vias.items())), "pads": pads, "pads_smd": smd,
                   "pads_tht": tht, "tracks": tracks, "arcs": arcs, "zones": zones, "rule_areas": rule_areas,
                   "footprints": len(board.GetFootprints()), "copper_layers": len(cu)},
        "copper_area_m2": area, "zones": zone_report, "checks": checks,
    }
    with open(out_json, "w", encoding="utf8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main(*sys.argv[1:4])
