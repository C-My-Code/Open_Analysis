# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Data behind ``oa doctor``: platform, process-control capabilities, tools, WSL and Docker."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any

from oa_jobs.procs import IS_WINDOWS, ProcessGuard
from oa_jobs.runners import detect_wsl
from oa_jobs.tools import ToolsConfig, default_tools_path
from oa_model.errors import ToolMissing

__all__ = ["doctor"]


def doctor(tools: ToolsConfig | None = None) -> dict[str, Any]:
    """JSON-able report. Never raises: problems are reported per entry."""
    cfg = tools or ToolsConfig()
    guard = ProcessGuard(None)
    job_objects = bool(IS_WINDOWS and guard._job)
    guard.close()
    entries: dict[str, Any] = {}
    for name in sorted(cfg.tools):
        try:
            info = cfg.resolve(name)
            entries[name] = {"ok": True, **info.model_dump(mode="json")}
        except ToolMissing as exc:
            entries[name] = {
                "ok": False,
                "runner": cfg.runner_name(name),
                "error": str(exc),
                "hint": getattr(exc, "hint", ""),
            }
    docker = shutil.which("docker")
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpus": os.cpu_count(),
        "tools_file": str(default_tools_path()),
        "tools_file_exists": default_tools_path().is_file(),
        "tools": entries,
        "wsl": detect_wsl().as_dict(),
        "docker": {"available": docker is not None, "path": docker},
        "process_control": {
            "tree_kill": True,
            "windows_job_objects": job_objects,
            "memory_limit": "job-object commit limit (backstop) + RSS watchdog"
            if IS_WINDOWS
            else "RSS watchdog (RLIMIT_AS not used)",
        },
    }
