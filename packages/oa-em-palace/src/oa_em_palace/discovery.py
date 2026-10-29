# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find a Palace install: ``tools.toml`` first, then a WSL distro with ``~/opt/palace``.

The logical tool ``palace`` is the ``oa-palace-run`` wrapper (``scripts/oa-palace-run``), not the
Palace launcher itself: the wrapper puts every run in a memory/CPU box and records runtime and
peak memory. ``tools.toml``::

    [tools.palace]
    runner = "wsl"
    distro = "Ubuntu"
    path = "/home/<user>/opt/palace/bin/oa-palace-run"
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from importlib import resources

from oa_framework.external import ToolInfo

__all__ = ["PROGRAM", "WRAPPER_NAME", "discover", "wrapper_text"]

PROGRAM = "palace"
WRAPPER_NAME = "oa-palace-run"
_HOME_REL = "opt/palace/bin"


def wrapper_text() -> str:
    """Source of the ``oa-palace-run`` wrapper shipped with this package."""
    return resources.files("oa_em_palace").joinpath("scripts", WRAPPER_NAME).read_text("utf-8")


def _wsl(distro: str | None, *args: str) -> subprocess.CompletedProcess[str] | None:
    exe = shutil.which("wsl.exe")
    if exe is None:
        return None
    cmd = [exe] + (["-d", distro] if distro else []) + ["--", *args]
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            check=False,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _wsl_probe(distro: str | None) -> tuple[str, str] | None:
    """(distro, wrapper path) when ``<home>/opt/palace/bin/{palace,oa-palace-run}`` exist."""
    out = _wsl(distro, "sh", "-c", 'echo "$HOME"')
    if out is None or out.returncode != 0 or not out.stdout.strip():
        return None
    base = f"{out.stdout.strip().splitlines()[-1]}/{_HOME_REL}"
    for name in ("palace", WRAPPER_NAME):
        res = _wsl(distro, "test", "-x", f"{base}/{name}")
        if res is None or res.returncode != 0:
            return None
    return (distro or "", f"{base}/{WRAPPER_NAME}")


def discover(*, distro: str | None = None, probe_wsl: bool = True) -> ToolInfo | None:
    """The ``ToolInfo`` for Palace, or ``None`` when nothing usable is found.

    Order: ``[tools.palace]`` in ``tools.toml``; ``$OA_PALACE_WRAPPER`` (a local Linux path);
    on Windows a WSL distro (``distro``, ``$OA_PALACE_DISTRO``, else the default one) holding
    ``~/opt/palace``; on Linux ``oa-palace-run`` on ``PATH``.
    """
    try:
        from oa_jobs.tools import ToolsConfig

        cfg = ToolsConfig()
        if PROGRAM in cfg.tools:
            return cfg.resolve(PROGRAM, probe_version=False)
    except Exception:  # missing tools.toml entry or file: fall through to probing
        pass
    local = os.environ.get("OA_PALACE_WRAPPER")
    if local and os.path.exists(local):
        return ToolInfo(program=PROGRAM, runner="local", path=local)
    if sys.platform == "win32":
        if not probe_wsl:
            return None
        want = distro or os.environ.get("OA_PALACE_DISTRO") or None
        found = _wsl_probe(want)
        if found is None:
            return None
        return ToolInfo(program=PROGRAM, runner="wsl", path=found[1])
    on_path = shutil.which(WRAPPER_NAME)
    return ToolInfo(program=PROGRAM, runner="local", path=on_path) if on_path else None
