# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: ignore-errors
"""pcbnew helper. Runs ONLY under KiCad's bundled Python, as a subprocess; never imported.

Usage (all modes print one JSON document on the last line of stdout)::

    python pcbnew_helper.py inspect IN.kicad_pcb
    python pcbnew_helper.py refill  IN.kicad_pcb OUT.kicad_pcb
    python pcbnew_helper.py unfill  IN.kicad_pcb OUT.kicad_pcb     (test support)

``refill`` fills every zone with ``pcbnew.ZONE_FILLER`` and saves to OUT. The report lists, per
zone and copper layer, the filled copper area in m^2 (``areas``) and the zone counts.
"""

from __future__ import annotations

import json
import sys

import pcbnew

NM = 1e-9


def _areas(board):
    out = []
    for z in board.Zones():
        if z.GetIsRuleArea():
            continue
        for lid in z.GetLayerSet().CuStack():
            area = (
                z.GetFilledPolysList(lid).Area() * NM * NM if z.HasFilledPolysForLayer(lid) else 0.0
            )
            filled = area > 0.0
            out.append(
                {
                    "uuid": z.m_Uuid.AsString(),
                    "net": z.GetNetname(),
                    "layer": board.GetLayerName(lid),
                    "filled": bool(filled),
                    "area_m2": area,
                }
            )
    return out


def _report(board, **extra):
    zones = _areas(board)
    return {
        "kicad_version": pcbnew.Version(),
        "zones": len(zones),
        "unfilled": sum(1 for z in zones if not z["filled"]),
        "areas": zones,
        **extra,
    }


def main(argv):
    mode = argv[1]
    board = pcbnew.LoadBoard(argv[2])
    if mode == "inspect":
        rep = _report(board)
    elif mode == "unfill":
        for z in board.Zones():
            z.UnFill()
        pcbnew.SaveBoard(argv[3], board)
        rep = _report(board)
    elif mode == "refill":
        filler = pcbnew.ZONE_FILLER(board)
        filler.Fill(board.Zones())
        pcbnew.SaveBoard(argv[3], board)
        rep = _report(board, saved=argv[3])
    else:
        raise SystemExit(f"unknown mode {mode}")
    sys.stdout.write("\n" + json.dumps(rep) + "\n")


if __name__ == "__main__":
    main(sys.argv)
