# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""External tool execution types (interfaces.md section 4.3)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable

from oa_model._base import OAModel


class ToolInfo(OAModel):
    """A resolved external tool: host path or container image, plus version."""

    program: str
    runner: str
    path: str | None = None
    image: str | None = None
    version: str | None = None


class ExternalCommand(OAModel):
    program: str  # logical tool: "openems-python", "palace", "fastercap", "kicad-cli", ...
    args: tuple[str, ...]
    workdir: Path  # host path of the job directory (inputs already written)
    env: dict[str, str] = {}
    stdin: str | None = None
    timeout_s: float | None = None
    progress_parser: str | None = None  # "openems", "palace", "generic"
    outputs: tuple[str, ...] = ()  # relative paths that must exist after a successful run


class ExternalResult(OAModel):
    returncode: int
    stdout_tail: str
    stderr_tail: str  # last 200 lines each (full logs in workdir/logs)
    wall_s: float
    runner: str
    tool_version: str | None = None


@runtime_checkable
class CancelToken(Protocol):
    def is_cancelled(self) -> bool: ...


@runtime_checkable
class ExternalRunner(Protocol):
    name: ClassVar[str]  # "local", "wsl", "docker", "ssh-slurm"

    def available(self) -> bool: ...

    def resolve(self, program: str) -> ToolInfo:
        """Path/image plus version; raises ``ToolMissing``."""
        ...

    def run(
        self,
        cmd: ExternalCommand,
        on_progress: Callable[[float, str], None],
        cancel: CancelToken,
    ) -> ExternalResult: ...

    def translate_path(self, host_path: Path) -> str: ...
