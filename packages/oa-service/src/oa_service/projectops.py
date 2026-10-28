# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Project scaffolding (``project.init``) and the environment report (``meta.doctor``)."""

from __future__ import annotations

import importlib.metadata as md
import importlib.util
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from oa_jobs import ToolsConfig, doctor
from oa_model.physics import PhysicsOverlay
from oa_model.setup import DesignIntent
from oa_service.errors import ServiceError

__all__ = ["doctor_report", "init_project"]

BOARD_SUFFIXES = (".kicad_pcb", ".oab.json", ".oab")


def init_project(
    directory: Path, *, board: str | None = None, name: str | None = None
) -> list[Path]:
    """Create ``oa-project.yaml`` plus physics and intent templates. Never overwrites."""
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "oa-project.yaml"
    if manifest.exists():
        raise ServiceError(
            "PERMISSION_DENIED",
            f"{manifest} already exists.",
            path="/dir",
            hint="Edit the existing file, or choose another directory.",
        )
    if board is None:
        found = sorted(p.name for p in directory.iterdir() if p.name.endswith(BOARD_SUFFIXES))
        if len(found) != 1:
            raise ServiceError(
                "USAGE",
                f"Found {len(found)} board files in {directory}; cannot pick one.",
                path="/board",
                hint="Pass the board file name explicitly (--board).",
                details={"candidates": found},
            )
        board = found[0]
    elif not (directory / board).is_file():
        raise ServiceError(
            "NOT_FOUND",
            f"Board {board!r} is not in {directory}.",
            path="/board",
            hint="Check the name.",
        )
    files: list[tuple[Path, str]] = [
        (
            manifest,
            yaml.safe_dump(
                {
                    "schema": "oa-project/1",
                    "name": name or directory.resolve().name,
                    "board": board,
                    "physics": "oa/physics.yaml",
                    "intent": "oa/intent.yaml",
                },
                sort_keys=False,
            ),
        ),
        (
            directory / "oa" / "physics.yaml",
            yaml.safe_dump(
                PhysicsOverlay().model_dump(mode="json", by_alias=True), sort_keys=False
            ),
        ),
        (
            directory / "oa" / "intent.yaml",
            yaml.safe_dump(DesignIntent().model_dump(mode="json", by_alias=True), sort_keys=False),
        ),
    ]
    written = []
    for path, text in files:
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    (directory / "oa" / "setups").mkdir(parents=True, exist_ok=True)
    return written


def _kicad_version(cli: Path | None) -> str | None:
    if cli is None:
        return None
    try:
        cp = subprocess.run(
            [str(cli), "version"],
            capture_output=True,
            text=True,
            timeout=2.5,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = cp.stdout.strip()
    return out or None


def doctor_report(tools: ToolsConfig | None = None) -> dict[str, Any]:
    """Offline environment report: platform, tools, KiCad and gmsh versions, optional packages."""
    from oa_kicad_bridge.tools import KiCadTools

    rep = doctor(tools)
    kt = KiCadTools.discover()
    rep["kicad"] = {
        "cli": str(kt.cli) if kt.cli else None,
        "python": str(kt.python) if kt.python else None,
        "version": _kicad_version(kt.cli),
    }
    try:
        gmsh_version: str | None = md.version("gmsh")
    except md.PackageNotFoundError:
        gmsh_version = None
    rep["gmsh"] = {"available": gmsh_version is not None, "version": gmsh_version}
    for pkg in ("oa_mcp", "oa_assistant"):
        found = importlib.util.find_spec(pkg) is not None
        rep[pkg.replace("_", "-")] = {"installed": found, "enabled": False if found else None}
    rep["offline"] = True
    rep["python_executable"] = sys.executable
    rep["system"] = platform.system()
    rep["env"] = {k: os.environ[k] for k in ("OA_CACHE_DIR", "OA_TOOLS_TOML") if k in os.environ}
    return rep
