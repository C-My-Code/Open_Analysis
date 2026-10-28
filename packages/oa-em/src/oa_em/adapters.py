# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Solver adapter lookup (entry point group ``open_analysis.em_adapters``) and tool resolution."""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Any

from oa_framework.em import SolverAdapter
from oa_framework.errors import UnknownPlugin
from oa_framework.external import ToolInfo

__all__ = ["load_adapter", "resolve_tool"]

# Used when the adapter package is installed without its entry point (the openEMS adapter does
# not declare one yet, see docs/change-requests/WP-205-adapter-entry-point.md). Loaded by string,
# so there is no static dependency of oa_em on the adapter packages.
_BUILTIN = {"openems": ("oa_em_openems", "OpenEMSAdapter")}


def load_adapter(name: str) -> SolverAdapter:
    """Instantiate the adapter registered under ``name``."""
    for ep in entry_points(group="open_analysis.em_adapters"):
        if ep.name == name:
            obj = ep.load()
            return obj()  # type: ignore[no-any-return]
    if name in _BUILTIN:
        mod, attr = _BUILTIN[name]
        try:
            return getattr(importlib.import_module(mod), attr)()  # type: ignore[no-any-return]
        except ImportError:
            pass
    raise UnknownPlugin(
        f"No EM solver adapter named {name!r}",
        path=f"open_analysis.em_adapters/{name}",
        hint="Install the adapter package (e.g. oa-em-openems) or choose another solver.",
    )


def resolve_tool(ctx: Any, adapter: SolverAdapter) -> ToolInfo:
    """The ``ToolInfo`` for ``adapter.tool``: from the job's tool configuration when the context
    has one, else a bare local description (adapters then discover the install themselves)."""
    spec = getattr(ctx, "spec", None)
    tools = getattr(spec, "tools", None)
    if tools is not None:
        try:
            from oa_jobs.tools import ToolsConfig

            return ToolsConfig(tools).resolve(adapter.tool, probe_version=False)
        except Exception:  # missing tool is reported by the runner with a proper hint
            pass
    return ToolInfo(program=adapter.tool, runner="local")
