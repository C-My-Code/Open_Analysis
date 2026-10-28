# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Server settings: flags, the ``[mcp]`` table of the user config, and budget defaults (10.4)."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oa_mcp.protocol import McpCallContext

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(s|m|h)?\s*$")
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(gb|g|mb|m)?\s*$", re.IGNORECASE)


def parse_duration(text: str | float) -> float:
    """Parse ``"15m"``, ``"4h"``, ``"90s"`` or a bare number of seconds into seconds [s]."""
    if isinstance(text, int | float):
        return float(text)
    m = _DURATION.match(text)
    if not m:
        raise ValueError(f"bad duration {text!r}; use e.g. 90s, 15m or 4h")
    scale = {"s": 1.0, "m": 60.0, "h": 3600.0, None: 1.0}[m.group(2)]
    return float(m.group(1)) * scale


def parse_memory_gb(text: str | float) -> float:
    """Parse ``"16GB"`` or a bare number of gigabytes into gigabytes [GB]."""
    if isinstance(text, int | float):
        return float(text)
    m = _SIZE.match(text)
    if not m:
        raise ValueError(f"bad memory size {text!r}; use e.g. 16GB")
    unit = (m.group(2) or "gb").lower()
    return float(m.group(1)) / (1024.0 if unit.startswith("m") else 1.0)


def user_config_path() -> Path:
    """Location of ``open_analysis/config.toml`` (``OA_CONFIG`` overrides)."""
    override = os.environ.get("OA_CONFIG")
    if override:
        return Path(override)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "open_analysis" / "config.toml"


def read_mcp_table(path: Path | None = None) -> dict[str, Any]:
    """Return the ``[mcp]`` table of the user config, or ``{}`` when absent or unreadable."""
    p = path or user_config_path()
    try:
        with p.open("rb") as fh:
            table = tomllib.load(fh).get("mcp", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return dict(table) if isinstance(table, dict) else {}


@dataclass(frozen=True)
class Settings:
    """Resolved server settings. Flags win over config, config wins over defaults."""

    roots: tuple[str, ...] = ()
    allow_writes: bool = False
    max_runtime_s: float = 4 * 3600.0
    max_memory_gb: float = 32.0
    max_cpus: int = field(default_factory=lambda: os.cpu_count() or 1)
    allow_external: bool = True
    executor: str = "process"
    max_response_bytes: int = 24 * 1024

    def call_context(self) -> McpCallContext:
        """Build the context for one call; the default budget is clamped to the ceiling."""
        cpus = os.cpu_count() or 1
        default = {
            "max_runtime_s": min(600.0, self.max_runtime_s),
            "max_memory_gb": min(8.0, self.max_memory_gb),
            "max_cpus": max(1, min(cpus - 1, 8, self.max_cpus)),
            "allow_external": self.allow_external,
        }
        ceiling = {
            "max_runtime_s": self.max_runtime_s,
            "max_memory_gb": self.max_memory_gb,
            "max_cpus": self.max_cpus,
            "allow_external": self.allow_external,
        }
        return McpCallContext(
            roots=self.roots,
            allow_writes=self.allow_writes,
            budget_ceiling=ceiling,
            default_budget=default,
            max_response_bytes=self.max_response_bytes,
        )


def resolve_settings(flags: Mapping[str, Any], config: Mapping[str, Any]) -> Settings:
    """Merge CLI ``flags`` (``None`` = not given) over the ``[mcp]`` ``config`` table."""

    def pick(key: str, default: Any = None) -> Any:
        value = flags.get(key)
        return config.get(key, default) if value is None else value

    roots = flags.get("root") or config.get("root") or [os.getcwd()]
    if isinstance(roots, str):
        roots = [roots]
    base = Settings()
    runtime = pick("max_runtime")
    memory = pick("max_memory")
    cpus = pick("max_cpus")
    response_kb = pick("max_response_kb", 24)
    return Settings(
        roots=tuple(str(Path(r).resolve()) for r in roots),
        allow_writes=bool(flags.get("allow_writes") or config.get("allow_writes", False)),
        max_runtime_s=parse_duration(runtime) if runtime is not None else base.max_runtime_s,
        max_memory_gb=parse_memory_gb(memory) if memory is not None else base.max_memory_gb,
        max_cpus=int(cpus) if cpus is not None else base.max_cpus,
        allow_external=not (flags.get("no_external") or config.get("no_external", False)),
        executor=str(pick("executor", "process")),
        max_response_bytes=int(float(response_kb) * 1024),
    )
