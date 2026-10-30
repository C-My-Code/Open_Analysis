# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deterministic synthetic KiCad 10 board with N copper tracks (WP-508b).

Routed nets are chains of short segments (3 mm, small jogs) on F.Cu / In2.Cu / In3.Cu over GND
planes on In1.Cu (split by a 1 mm gap) and In4.Cu, with a few GND vias. Used for the
ReferenceMap / ERC / reader scaling gates (10k, 50k tracks).

    uv run python tools/bench/gen_synth_board.py --tracks 50000 --out build/synth50k.kicad_pcb

``--mix`` injects a known violation mix for ERC comparisons (WP-509b): every net crosses the
1 mm split of In1.Cu (reference-plane crossings, RP-003), the 0.4 mm lane pitch gives parallel
runs (crosstalk), every 7th net is 0.4 mm wide (impedance off target against a 50 ohm intent)
and every 40th net (the first 33) runs 0.3 mm or more from the board edge (edge proximity).
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "tests/boards/kicad10/stripline/board.kicad_pcb"
HEADER_LINES = 152  # file header through the (setup ...) block of the template
SEG_LEN = 3.0
SEGS_PER_NET = 25
PITCH = 0.4
LAYERS = ("F.Cu", "In2.Cu", "In3.Cu")
WIDTH = 0.15


def _uid(n: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"oa-synth/{n}"))


def _plane(layer: str, x0: float, x1: float, h: float, n: int) -> str:
    pts = f"(xy {x0} 0) (xy {x1} 0) (xy {x1} {h}) (xy {x0} {h})"
    return (
        f'\t(zone\n\t\t(net "GND")\n\t\t(layer "{layer}")\n\t\t(uuid "{_uid(n)}")\n'
        "\t\t(hatch edge 0.5)\n\t\t(connect_pads yes\n\t\t\t(clearance 0.3)\n\t\t)\n"
        "\t\t(min_thickness 0.25)\n\t\t(fill yes\n\t\t\t(thermal_gap 0.5)\n"
        "\t\t\t(thermal_bridge_width 0.5)\n\t\t\t(island_removal_mode 0)\n\t\t)\n"
        f"\t\t(polygon\n\t\t\t(pts\n\t\t\t\t{pts}\n\t\t\t)\n\t\t)\n"
        f'\t\t(filled_polygon\n\t\t\t(layer "{layer}")\n\t\t\t(pts\n\t\t\t\t{pts}\n'
        "\t\t\t)\n\t\t)\n\t)\n"
    )


def generate(tracks: int, mix: bool = False) -> str:
    """Return the board text with `tracks` segments."""
    lines = TEMPLATE.read_text(encoding="utf-8").splitlines(keepends=True)
    out = ["".join(lines[:HEADER_LINES])]
    nets = max(1, -(-tracks // SEGS_PER_NET))
    lanes = -(-nets // len(LAYERS))
    w = SEGS_PER_NET * SEG_LEN + 10.0
    h = round(lanes * PITCH + 10.0, 3)
    out.append(
        f"\t(gr_rect\n\t\t(start 0 0)\n\t\t(end {w} {h})\n\t\t(stroke\n\t\t\t(width 0.1)\n"
        f'\t\t\t(type default)\n\t\t)\n\t\t(fill no)\n\t\t(layer "Edge.Cuts")\n'
        f'\t\t(uuid "{_uid(-1)}")\n\t)\n'
    )
    k = 0
    for i in range(nets):
        layer = LAYERS[i % len(LAYERS)]
        y = 5.0 + (i // len(LAYERS)) * PITCH
        width = 0.4 if (mix and i % 7 == 3) else WIDTH
        if mix and i % 40 == 5 and i // 40 < 33:
            y = 0.3 + 0.4 * (i // 120)  # unique (layer, y), clear of the regular lanes
        x = 5.0
        for s in range(SEGS_PER_NET):
            if k >= tracks:
                break
            dy = 0.2 if (s % 7 == 3) else 0.0
            out.append(
                f"\t(segment\n\t\t(start {x:.3f} {y:.3f})\n"
                f"\t\t(end {x + SEG_LEN:.3f} {y + dy:.3f})\n"
                f'\t\t(width {width})\n\t\t(layer "{layer}")\n\t\t(net "N{i}")\n'
                f'\t\t(uuid "{_uid(k)}")\n\t)\n'
            )
            x += SEG_LEN
            y += dy
            k += 1
    for j in range(0, int(h), 20):
        out.append(
            f"\t(via\n\t\t(at 2 {j + 1})\n\t\t(size 0.5)\n\t\t(drill 0.25)\n"
            f'\t\t(layers "F.Cu" "B.Cu")\n\t\t(net "GND")\n\t\t(uuid "{_uid(10**7 + j)}")\n\t)\n'
        )
    mid = round(w / 2, 3)
    out.append(_plane("In1.Cu", 0, mid - 0.5, h, 10**8))
    out.append(_plane("In1.Cu", mid + 0.5, w, h, 10**8 + 1))
    out.append(_plane("In4.Cu", 0, w, h, 10**8 + 2))
    out.append("\t(embedded_fonts no)\n)\n")
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tracks", type=int, default=10_000)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mix", action="store_true", help="inject a known ERC violation mix")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(generate(a.tracks, a.mix), encoding="utf-8", newline="\n")
    print(f"wrote {a.out} ({a.tracks} tracks)")


if __name__ == "__main__":
    main()
