# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers shared by the oa-em tests: a project on disk and a real ``LocalJobService`` run."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from oa_jobs import Budget, JobRequest, LocalJobService, ToolsConfig
from oa_model.board import Board
from oa_model.io import dump_board
from oa_model.setup import AnalysisSetup
from oa_results.bundle import ResultBundle


def make_project(root: Path, board: Board) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    dump_board(board, root / "board.oab.json")
    (root / "oa-project.yaml").write_text(
        "schema: oa-project/1\nname: em\nboard: board.oab.json\n", encoding="utf-8"
    )
    return root / "oa-project.yaml"


def run_job(
    root: Path,
    board: Board,
    config: dict[str, Any],
    *,
    tools: dict[str, dict[str, str]] | None = None,
    budget: Budget | None = None,
    executor: str = "inline",
    timeout: float = 1800.0,
) -> tuple[ResultBundle, Any, float]:
    """Submit ``em.sparams`` to a ``LocalJobService``; returns (bundle, status, wall seconds)."""
    project = make_project(root, board)
    svc = LocalJobService(
        root,
        tools=ToolsConfig(tools or {}),
        geometry_factory="oa_geometry:BoardGeometry.build",
        workers=2,
    )
    try:
        setup = AnalysisSetup(schema="oa-setup/1", name="em", analysis="em.sparams", config=config)
        req = JobRequest(
            project=project,
            setup="inline",
            inline_setup=setup,
            executor=executor,  # type: ignore[arg-type]
            budget=budget,
        )
        t0 = time.perf_counter()
        jid = svc.submit(req)
        status = svc.wait(jid, timeout=timeout)
        wall = time.perf_counter() - t0
        if status.state != "succeeded":
            raise AssertionError(f"job {status.state}: {status.error}")
        return svc.result(jid), status, wall
    finally:
        svc.close()
