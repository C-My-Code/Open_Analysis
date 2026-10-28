# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Entry point of the spawned worker processes (``process`` executor)."""

from __future__ import annotations

from typing import Any

from oa_jobs.execution import ExecSpec, execute_task

__all__ = ["run_task_in_worker", "warm_up"]


def warm_up() -> int:
    """No-op task used to make the pool import everything before real work arrives."""
    import os

    return os.getpid()


def run_task_in_worker(spec: ExecSpec, task_json: str) -> dict[str, Any]:
    """Run one task in this worker process; returns the outcome dict of ``execute_task``."""
    return execute_task(spec, task_json)
