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


def groups(name: str) -> dict[str, Any]:
    """The committed ``<mesh>.groups.json`` of a fixture (WP-206 sidecar, coarse preset)."""
    return json.loads((GOLDEN / f"groups_{name}.json").read_text(encoding="utf-8"))
