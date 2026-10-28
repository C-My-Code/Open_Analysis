# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""External tool resolution from ``tools.toml`` (interfaces.md section 4.3).

User file: ``%APPDATA%\\open_analysis\\tools.toml`` (Windows) or
``~/.config/open_analysis/tools.toml``; ``$OA_TOOLS_TOML`` overrides the location. A project can
override single keys with ``oa-project.yaml: tools:``. Example::

    [tools.palace]
    runner = "wsl"
    path = "/opt/palace/bin/palace"
    distro = "Ubuntu"

    [tools.openems-python]
    runner = "local"
    path = 'C:\\openEMS\\python\\python.exe'
    min_version = "0.37"
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from oa_framework.external import ToolInfo
from oa_jobs.procs import popen_kwargs
from oa_model.errors import ToolMissing

__all__ = ["ToolsConfig", "default_tools_path", "parse_version"]


def default_tools_path() -> Path:
    env = os.environ.get("OA_TOOLS_TOML")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "open_analysis" / "tools.toml"


def parse_version(text: str) -> tuple[int, ...]:
    m = re.search(r"\d+(?:\.\d+)*", text)
    return tuple(int(p) for p in m.group(0).split(".")) if m else ()


class ToolsConfig:
    """Resolves logical tool names to ``ToolInfo`` using ``tools.toml`` plus project overrides."""

    def __init__(
        self,
        tools: Mapping[str, Mapping[str, str]] | None = None,
        *,
        project_overrides: Mapping[str, Mapping[str, str]] | None = None,
        path: Path | None = None,
    ) -> None:
        if tools is None:
            tools = self._load(path or default_tools_path())
        merged: dict[str, dict[str, str]] = {k: dict(v) for k, v in tools.items()}
        for k, v in (project_overrides or {}).items():
            merged.setdefault(k, {}).update(v)
        # Built-in: the interpreter running this process (handy for tests and Python tools).
        merged.setdefault("python", {"runner": "local", "path": sys.executable})
        self.tools = merged
        self._versions: dict[str, str | None] = {}

    @staticmethod
    def _load(path: Path) -> dict[str, dict[str, str]]:
        if not path.is_file():
            return {}
        with open(path, "rb") as fh:
            doc = tomllib.load(fh)
        raw = doc.get("tools", {})
        return {str(k): {kk: str(vv) for kk, vv in v.items()} for k, v in raw.items()}

    def entry(self, program: str) -> dict[str, str]:
        return dict(self.tools.get(program, {}))

    def runner_name(self, program: str) -> str:
        return self.tools.get(program, {}).get("runner", "local")

    def resolve(self, program: str, *, probe_version: bool = True) -> ToolInfo:
        """Path/image plus version. Raises ``ToolMissing`` when nothing is found."""
        cfg = self.tools.get(program, {})
        runner = cfg.get("runner", "local")
        path = cfg.get("path")
        image = cfg.get("image")
        if runner == "local":
            if path is None:
                path = shutil.which(program)
            if path is None or not Path(path).exists():
                raise ToolMissing(
                    f"External tool {program!r} was not found",
                    program=program,
                    hint=f"Add [tools.{program}] with runner and path to {default_tools_path()}.",
                )
        elif runner == "docker" and image is None:
            raise ToolMissing(
                f"Tool {program!r} uses the docker runner but has no image",
                program=program,
                hint=f"Set image = '...' in [tools.{program}].",
            )
        version = cfg.get("version")
        if version is None and probe_version and runner == "local" and "version_arg" in cfg:
            version = self._probe(program, str(path), cfg["version_arg"])
        min_v = cfg.get("min_version")
        if min_v and version and parse_version(version) < parse_version(min_v):
            raise ToolMissing(
                f"{program} {version} is older than the required {min_v}",
                program=program,
                hint="Upgrade the tool or lower min_version.",
            )
        return ToolInfo(program=program, runner=runner, path=path, image=image, version=version)

    def _probe(self, program: str, path: str, arg: str) -> str | None:
        if program not in self._versions:
            try:
                out = subprocess.run(
                    [path, arg],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    **popen_kwargs(),
                )
                text = (out.stdout or out.stderr).strip()
                self._versions[program] = text.splitlines()[0] if text else None
            except (OSError, subprocess.SubprocessError):
                self._versions[program] = None
        return self._versions[program]

    def version(self, program: str) -> str | None:
        """Version string if the tool resolves and has one, else None (never raises)."""
        try:
            return self.resolve(program).version
        except ToolMissing:
            return None
