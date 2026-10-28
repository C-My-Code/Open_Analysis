# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hand-written minimal ``.kicad_pcb`` text for unit tests."""

from __future__ import annotations

from pathlib import Path

from oa_ingest_kicad import KiCadReader
from oa_model import Board

LAYERS_4 = """(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (2 "B.Cu" signal)
  (25 "Edge.Cuts" user))"""

STACKUP_4 = """(stackup
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))
  (layer "In1.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 2" (type "core") (thickness 0.5) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02)
    addsublayer (thickness 0.3) (material "FR4b") (epsilon_r 4.1) (loss_tangent 0.015))
  (layer "In2.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 3" (type "prepreg") (thickness 0.2) (material "FR4") (epsilon_r 4.3) (loss_tangent 0.02))
  (layer "B.Cu" (type "copper") (thickness 0.035))
  (copper_finish "ENIG"))"""

OUTLINE = """(gr_line (start 0 0) (end 20 0) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "e1"))
(gr_line (start 20 0) (end 20 10) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "e2"))
(gr_line (start 20 10) (end 0 10) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "e3"))
(gr_line (start 0 10) (end 0 0) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "e4"))"""


def pcb(
    body: str,
    *,
    version: int = 20241229,
    net_table: str = "",
    layers: str = LAYERS_4,
    stackup: str | None = STACKUP_4,
    edge: str = OUTLINE,
) -> str:
    setup = f"(setup {stackup})" if stackup else "(setup)"
    return (
        f'(kicad_pcb (version {version}) (generator "pcbnew") (generator_version "9.0")\n'
        f"(general (thickness 1.6))\n{layers}\n{setup}\n{net_table}\n{edge}\n{body}\n)\n"
    )


def read_text(tmp_path: Path, text: str, name: str = "t.kicad_pcb") -> Board:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return KiCadReader().read(p)
