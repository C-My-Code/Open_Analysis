# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn the JSON of ``pcbnew_extract.py`` into ``tests/golden/ingest_kicad/<name>/case.toml``.

Usage: ``python make_case.py EXTRACT.json OUT_DIR DEMO_RELATIVE_PATH``.
Run once; the generated ``case.toml`` is committed (golden rule 5).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def fmt(v: float) -> str:
    return repr(float(v))


def main() -> None:
    src, out_dir, rel = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
    d = json.loads(src.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    L: list[str] = []
    L.append(
        f'source = "pcbnew {d["pcbnew_version"]} via tools/pcbnew_extract.py; '
        'stackup z cross-computed from the raw file text"'
    )
    L.append("")
    L.append("[tolerance]")
    L.append("counts = 0")
    L.append("length_rel = 1e-9")
    L.append("arc_abs_m = 5e-9")
    L.append("z_abs_m = 1e-9")
    L.append("zone_area_rel = 1e-3")
    L.append("pad_position_abs_m = 1e-9")
    L.append("pad_angle_abs_rad = 1e-9")
    L.append("")
    L.append("[inputs]")
    L.append(f'demo_path = "{rel}"')
    L.append("")
    L.append("[expected]")
    L.append(f"nets = {d['nets']}")
    L.append(f"pads = {d['pads']}")
    L.append(f"tracks = {d['tracks']}")
    L.append(f"track_arcs = {d['track_arcs']}")
    L.append(f"vias_total = {sum(d['vias'].values())}")
    L.append(f"vias_through = {d['vias'].get('through', 0)}")
    L.append(f"vias_micro = {d['vias'].get('micro', 0)}")
    L.append(f"vias_blind_or_buried = {d['vias'].get('blind_or_buried', 0)}")
    # a sample of absolute pad poses: [refdes, number, x, y, angle]
    seen: set[tuple[str, str]] = set()
    L.append("pad_poses = [")
    for ref, num, x, y, a in d["pads_sample"]:
        if (ref, num) in seen:
            continue
        seen.add((ref, num))
        L.append(f'  ["{ref}", "{num}", {fmt(x)}, {fmt(y)}, {fmt(a)}],')
    L.append("]")
    L.append("")
    L.append("[expected.track_length_by_layer]")
    for k, v in d["track_length_by_layer"].items():
        L.append(f'"{k}" = {fmt(v)}')
    L.append("")
    L.append("[expected.zone_fill_area_by_layer]")
    for k, v in d["zone_fill_area_by_layer"].items():
        L.append(f'"{k}" = {fmt(v)}')
    L.append("")
    L.append("[expected.stackup_z]")
    for k, v in d["stackup_z"].items():
        L.append(f'"{k}" = {fmt(v)}')
    L.append("")
    (out_dir / "case.toml").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
