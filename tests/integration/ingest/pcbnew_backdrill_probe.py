# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: ignore-errors
"""Which copper layers does pcbnew treat as backdrilled? Runs ONLY under KiCad's Python.

    kicad-python pcbnew_backdrill_probe.py BOARD.kicad_pcb

Prints one JSON line: for every via with a secondary or tertiary drill, its position (mm, file
frame), the net and, per copper layer name, ``IsBackdrilledOrPostMachined``. This is the oracle
for the "the listed end layer is itself drilled" convention (WP-208 backdrill check).
"""

from __future__ import annotations

import json
import sys

import pcbnew


def main(path: str) -> None:
    board = pcbnew.LoadBoard(path)
    out = []
    for track in board.GetTracks():
        if track.Type() != pcbnew.PCB_VIA_T:
            continue
        cut = {
            board.GetLayerName(layer): bool(track.IsBackdrilledOrPostMachined(layer))
            for layer in board.GetEnabledLayers().CuStack()
        }
        if not any(cut.values()):
            continue
        pos = track.GetPosition()
        out.append(
            {
                "net": track.GetNetname(),
                "x_mm": pcbnew.ToMM(pos.x),
                "y_mm": pcbnew.ToMM(pos.y),
                "cut": cut,
            }
        )
    sys.stdout.write("\n" + json.dumps(out) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
