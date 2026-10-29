# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers of the oa-em-palace tests (unique module name, see TASKS.md rule 8)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oa_model.em import EMProblem
from oa_testkit.emproblems import (
    microstrip_thru_problem,
    stripline_thru_problem,
    via_transition_problem,
)

REPO = Path(__file__).resolve().parents[3]
GOLDEN = REPO / "tests" / "golden" / "em_palace"

FIXTURES: dict[str, Callable[[], EMProblem]] = {
    "microstrip_thru": microstrip_thru_problem,
    "stripline_thru": stripline_thru_problem,
    "via_transition": via_transition_problem,
}


def coarse(problem: EMProblem) -> EMProblem:
    """The fixture at the preset used to produce the committed group sidecars."""
    return problem.model_copy(update={"mesh": problem.mesh.model_copy(update={"preset": "coarse"})})


def microstrip_wave_problem(n_points: int = 3) -> EMProblem:
    """The microstrip thru fixture with numeric wave ports on the x faces instead of lumped ports.

    Lumped ports at the end of a line whose x faces absorb are lossy in a FEM (the absorbing face
    shunts the line end, 4 dB at 1 GHz); modal ports are the right choice for Palace.
    """
    from oa_model.em import EMWavePort

    p = microstrip_thru_problem(n_points=n_points)
    box = p.structure.bbox
    trace = next(c for c in p.structure.conductors if c.role == "signal")
    ports = tuple(
        EMWavePort(
            name=name,
            face=face,  # type: ignore[arg-type]
            rect=(box.ymin, box.ymax, box.zmin, box.zmax),
            line_type="microstrip",
            signal_conductors=(trace.id,),
            z0=None,
        )
        for name, face in (("P1", "xmin"), ("P2", "xmax"))
    )
    p = p.model_copy(update={"ports": ports, "name": "microstrip_thru_25mm_wave"})
    return coarse(p).model_copy(
        update={
            "mesh": p.mesh.model_copy(update={"preset": "coarse", "fem_order": 1}),
        }
    )


def groups(name: str) -> dict[str, Any]:
    """The committed ``<mesh>.groups.json`` of a fixture (WP-206 sidecar, coarse preset)."""
    doc: dict[str, Any] = json.loads((GOLDEN / f"groups_{name}.json").read_text(encoding="utf-8"))
    return doc
