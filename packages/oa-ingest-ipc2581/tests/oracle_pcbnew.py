# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""pcbnew oracle: record counts, stackup and per-layer copper areas of a ``.kicad_pcb``.

Runs ONLY under KiCad's bundled Python (``kicad python oracle_pcbnew.py BOARD OUT.json``); it is
never imported by the test process. It uses shapely, which KiCad's Python ships.

Definitions (shared with the tests, which compute the same figures from a ``Board``):
- ``nets``: nets that own at least one copper item (track, via, pad or zone fill).
- ``tracks``: straight segments plus arcs. ``vias.<kind>``: classified by the copper span only
  (through = outer to outer, blind = one outer end, buried = inner only), because IPC-2581 has no
  micro-via flag.
- ``pads``: every footprint pad that has copper or a drill (paste/mask-only apertures excluded). ``zone_fills``: distinct (net, copper layer) pairs with filled
  copper.
- ``copper_area``: per copper layer, the area (m^2) of the union of all copper of one net, and the
  union of everything (``total``). Pads and vias count on a layer only where they flash copper.
"""

from __future__ import annotations

import json
import sys

import pcbnew
from shapely.geometry import Polygon
from shapely.ops import unary_union

NM = 1e-9
MAX_ERR = 500  # nm, tessellation error for arcs/circles


def to_shapely(poly_set):
    geoms = []
    for i in range(poly_set.OutlineCount()):
        outline = poly_set.Outline(i)
        ext = [(outline.CPoint(k).x, outline.CPoint(k).y) for k in range(outline.PointCount())]
        holes = []
        for h in range(poly_set.HoleCount(i)):
            hole = poly_set.Hole(i, h)
            holes.append([(hole.CPoint(k).x, hole.CPoint(k).y) for k in range(hole.PointCount())])
        if len(ext) >= 3:
            p = Polygon(ext, holes)
            if not p.is_valid:
                p = p.buffer(0)
            geoms.append(p)
    return unary_union(geoms) if geoms else None


def item_poly(item, layer):
    ps = pcbnew.SHAPE_POLY_SET()
    item.TransformShapeToPolySet(ps, layer, 0, MAX_ERR, pcbnew.ERROR_INSIDE)
    return to_shapely(ps)


def _sexpr(text: str):
    """Minimal S-expression reader (strings, atoms, lists)."""
    pos = 0
    n = len(text)

    def skip_ws():
        nonlocal pos
        while pos < n and text[pos].isspace():
            pos += 1

    def parse():
        nonlocal pos
        skip_ws()
        ch = text[pos]
        if ch == "(":
            pos += 1
            items = []
            while True:
                skip_ws()
                if text[pos] == ")":
                    pos += 1
                    return items
                items.append(parse())
        if ch == '"':
            end = pos + 1
            while text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            tok = text[pos + 1 : end]
            pos = end + 1
            return tok
        start = pos
        while pos < n and not text[pos].isspace() and text[pos] not in "()":
            pos += 1
        return text[start:pos]

    return parse()


def read_stackup(board_path: str) -> list[dict]:
    """Stackup as stored in the file. pcbnew's SWIG API cannot reach the descriptor items, so the
    ``(stackup ...)`` section is read directly (the only non-pcbnew figure in this oracle)."""
    with open(board_path, encoding="utf-8") as fh:
        head = fh.read(400_000)
    i = head.find("(stackup")
    if i < 0:
        return []
    start, depth, j = i, 0, i
    while True:
        c = head[j]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    tree = _sexpr(head[start : j + 1])
    out = []
    for layer in tree[1:]:
        if not isinstance(layer, list) or layer[0] != "layer":
            continue
        rec = {
            "name": layer[1],
            "type": None,
            "thickness_m": 0.0,
            "epsilon_r": None,
            "loss_tangent": None,
            "material": None,
            "sublayers": [],
        }
        cur = rec
        for f in layer[2:]:
            if not isinstance(f, list):
                continue
            key = f[0]
            if key == "type":
                rec["type"] = f[1]
            elif key == "addsublayer":
                cur = {
                    "thickness_m": 0.0,
                    "epsilon_r": None,
                    "loss_tangent": None,
                    "material": None,
                }
                rec["sublayers"].append(cur)
            elif key == "thickness":
                cur["thickness_m"] = float(f[1]) * 1e-3
            elif key == "epsilon_r":
                cur["epsilon_r"] = float(f[1])
            elif key == "loss_tangent":
                cur["loss_tangent"] = float(f[1])
            elif key == "material":
                cur["material"] = f[1]
        out.append(rec)
    return out


def main(board_path: str, out_path: str) -> None:
    b = pcbnew.LoadBoard(board_path)
    cu = list(b.GetEnabledLayers().CuStack())
    names = {lid: b.GetLayerName(lid) for lid in cu}
    top, bottom = cu[0], cu[-1]

    per_layer: dict[int, dict[str, list]] = {lid: {} for lid in cu}
    nets_with_copper: set[str] = set()

    def add(layer, net, geom):
        if geom is None or geom.is_empty:
            return
        per_layer[layer].setdefault(net, []).append(geom)
        if net:
            nets_with_copper.add(net)

    n_tracks = 0
    vias = {"through": 0, "blind": 0, "buried": 0}
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            lo, hi = t.TopLayer(), t.BottomLayer()
            if lo == top and hi == bottom:
                vias["through"] += 1
            elif lo == top or hi == bottom:
                vias["blind"] += 1
            else:
                vias["buried"] += 1
            for lid in cu:
                if t.FlashLayer(lid):
                    add(lid, t.GetNetname(), item_poly(t, lid))
        else:
            n_tracks += 1
            lid = t.GetLayer()
            add(lid, t.GetNetname(), item_poly(t, lid))

    pad_bins = {}
    for fp in b.GetFootprints():
        for pad in fp.Pads():
            flashed = frozenset(lid for lid in cu if pad.FlashLayer(lid))
            if pad.GetDrillSizeX() > 0 or flashed:
                # copper-less paste/mask-only apertures are not counted; pads with one number at
                # one position on disjoint layers are one pad (IPC-2581 cannot tell them apart)
                p = pad.GetPosition()
                bins = pad_bins.setdefault((fp.GetReference(), pad.GetNumber(), p.x, p.y), [])
                for i, used in enumerate(bins):
                    if not (used & flashed) or (not flashed):
                        bins[i] = used | flashed
                        break
                else:
                    bins.append(flashed)
            for lid in cu:
                if pad.FlashLayer(lid):
                    add(lid, pad.GetNetname(), item_poly(pad, lid))

    def graphic(item, lid):
        ps = pcbnew.SHAPE_POLY_SET()
        if isinstance(item, pcbnew.PCB_SHAPE):
            item.TransformShapeToPolySet(ps, lid, 0, MAX_ERR, pcbnew.ERROR_INSIDE)
        elif hasattr(item, "TransformTextToPolySet"):
            if hasattr(item, "IsVisible") and not item.IsVisible():
                return None
            item.TransformTextToPolySet(ps, 0, MAX_ERR, pcbnew.ERROR_INSIDE)
        else:
            return None
        return to_shapely(ps)

    items = list(b.GetDrawings())
    for fp in b.GetFootprints():
        items.extend(fp.GraphicalItems())
        items.extend(fp.GetFields())
    for item in items:
        lid = item.GetLayer()
        if lid in per_layer:
            net = item.GetNetname() if hasattr(item, "GetNetname") else ""
            add(lid, net, graphic(item, lid))

    fills = set()
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().CuStack():
            if lid not in per_layer or not z.HasFilledPolysForLayer(lid):
                continue
            g = to_shapely(z.GetFilledPolysList(lid))
            if g is not None and not g.is_empty:
                add(lid, z.GetNetname(), g)
                fills.add((z.GetNetname(), lid))

    area = {}
    for lid, by_net in per_layer.items():
        nets = {}
        for net, geoms in by_net.items():
            nets[net] = unary_union(geoms).area * NM * NM
        total = unary_union([g for gs in by_net.values() for g in gs]).area * NM * NM
        area[f"L{cu.index(lid) + 1}"] = {"total": total, "nets": dict(sorted(nets.items()))}

    stack = read_stackup(board_path)

    out = {
        "kicad_version": pcbnew.Version(),
        "copper_layers": [names[lid] for lid in cu],
        "counts": {
            "nets": len(nets_with_copper),
            "tracks": n_tracks,
            "vias": vias,
            "pads": sum(len(v) for v in pad_bins.values()),
            "components": len(list(b.GetFootprints())),
            "zone_fills": len(fills),
        },
        "stackup": stack,
        "copper_area": area,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
