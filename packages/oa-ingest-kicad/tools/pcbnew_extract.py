# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Extract reference numbers from a board with KiCad's own geometry engine.

Run ONCE per golden board with KiCad's bundled Python (it imports ``pcbnew``, which is GPL and
therefore never imported by the product code):

    "D:\\Program Files\\KiCad\\10.0\\bin\\python.exe" pcbnew_extract.py BOARD.kicad_pcb OUT.json

Output keys are in pcbnew's units converted to metres / square metres. Counts are exact; the
test compares lengths at 1e-9 relative and zone areas at 0.1 %.

The dielectric stackup is not reachable through SWIG (``BOARD_STACKUP.GetList()`` is an opaque
``SwigPyObject``). The z-table is therefore cross-computed here from the raw file text with an
independent minimal scanner (``stackup_from_text``), not from ``oa_ingest_kicad``.
"""

from __future__ import annotations

import json
import math
import re
import sys

import pcbnew

NM = 1e-9


def via_kind(v) -> str:
    t = v.GetViaType()
    if t == pcbnew.VIATYPE_THROUGH:
        return "through"
    if t == pcbnew.VIATYPE_MICROVIA:
        return "micro"
    # blind / buried (v9 and earlier share one type): classify from the span
    return "blind_or_buried"


def stackup_from_text(path: str) -> dict[str, float]:
    """Copper z_bottom/z_top table (metres) from the (stackup) block, bottom-up accumulation."""
    text = open(path, encoding="utf-8").read()
    m = re.search(r"\(stackup", text)
    if not m:
        return {}
    depth = 0
    end = m.start()
    for i in range(m.start(), len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    block = text[m.start() : end + 1]
    # split into layer blocks at depth 1
    layers: list[tuple[str, list[float]]] = []
    for lm in re.finditer(r'\(layer\s+"([^"]+)"', block):
        start = lm.start()
        d = 0
        for j in range(start, len(block)):
            if block[j] == "(":
                d += 1
            elif block[j] == ")":
                d -= 1
                if d == 0:
                    body = block[start : j + 1]
                    break
        th = [float(x) for x in re.findall(r"\(thickness\s+([-0-9.eE]+)\)", body)]
        layers.append((lm.group(1), th))
    z = 0.0
    out: dict[str, float] = {}
    for name, th in reversed(layers):
        t = sum(th) * 1e-3
        if name.endswith(".Cu"):
            out[name + ":z_bottom"] = z
            out[name + ":z_top"] = z + t
        z += t
    return out


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    b = pcbnew.LoadBoard(src)
    kinds: dict[str, int] = {}
    seg_n = arc_n = 0
    length: dict[str, float] = {}
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or t.GetClass() == "PCB_VIA":
            v = pcbnew.Cast_to_PCB_VIA(t)
            k = via_kind(v)
            kinds[k] = kinds.get(k, 0) + 1
            continue
        name = b.GetStandardLayerName(t.GetLayer())
        if t.GetClass() == "PCB_ARC":
            arc_n += 1
            a = pcbnew.Cast_to_PCB_ARC(t)
            ln = a.GetRadius() * abs(a.GetAngle().AsRadians())
        else:
            seg_n += 1
            ln = t.GetLength()
        length[name] = length.get(name, 0.0) + ln * NM
    pads = []
    for p in b.GetPads():
        pos = p.GetPosition()
        pads.append(
            [
                p.GetParentAsString() if hasattr(p, "GetParentAsString") else "",
                p.GetNumber(),
                pos.x * NM,
                -pos.y * NM,
                math.radians(p.GetOrientationDegrees()),
            ]
        )
    area: dict[str, float] = {}
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for layer in z.GetLayerSet().Seq():
            if not z.HasFilledPolysForLayer(layer):
                continue
            ps = z.GetFilledPolysList(layer)
            a = 0.0
            for i in range(ps.OutlineCount()):
                a += abs(ps.Outline(i).Area())
                for h in range(ps.HoleCount(i)):
                    a -= abs(ps.Hole(i, h).Area())
            area[b.GetStandardLayerName(layer)] = (
                area.get(b.GetStandardLayerName(layer), 0.0) + a * NM * NM
            )
    nets = b.GetNetCount() - 1  # net 0 is "no net"
    out = {
        "pcbnew_version": pcbnew.Version(),
        "nets": nets,
        "vias": kinds,
        "pads": len(pads),
        "tracks": seg_n + arc_n,
        "track_segments": seg_n,
        "track_arcs": arc_n,
        "track_length_by_layer": dict(sorted(length.items())),
        "zone_fill_area_by_layer": dict(sorted(area.items())),
        "board_thickness": b.GetDesignSettings().GetBoardThickness() * NM,
        "stackup_z": stackup_from_text(src),
        "pads_sample": pads[:: max(1, len(pads) // 300)],
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)


if __name__ == "__main__":
    main()
