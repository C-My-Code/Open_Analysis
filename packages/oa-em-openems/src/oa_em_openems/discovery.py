# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find an openEMS installation: explicit arguments, environment, ``tools.toml``, project-local
tools directory, then ``PATH``.

An installation is a Python interpreter that can ``import openEMS`` plus the folder holding
``CSXCAD.dll``/``openEMS.exe`` (``CSXCAD_INSTALL_PATH``). Nothing here imports openEMS in this
process: candidates are probed in a child interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from oa_framework.external import ToolInfo

PROGRAM = "openems-python"
MIN_VERSION = (0, 37)
ENV_PYTHON = "OA_OPENEMS_PYTHON"
ENV_HOME = "OA_OPENEMS_HOME"
ENV_DLL = "CSXCAD_INSTALL_PATH"

_DLL_NAMES = ("CSXCAD.dll", "libCSXCAD.so", "libCSXCAD.dylib")


@dataclass(frozen=True)
class OpenEMSInstall:
    """A usable installation."""

    python: Path
    home: Path | None
    version: str | None
    source: str  # where it was found: "argument", "env", "tools.toml", "project", "path"

    def tool_info(self) -> ToolInfo:
        return ToolInfo(
            program=PROGRAM, runner="local", path=str(self.python), version=self.version
        )

    def env(self) -> dict[str, str]:
        return {ENV_DLL: str(self.home)} if self.home else {}


def _is_home(path: Path) -> bool:
    return path.is_dir() and any((path / n).exists() for n in _DLL_NAMES)


def _version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def version_ok(version: str | None) -> bool:
    """True when ``version`` is at least the supported minimum (0.37)."""
    return _version_tuple(version)[: len(MIN_VERSION)] >= MIN_VERSION


def probe(python: Path, home: Path | None, timeout: float = 60.0) -> str | None:
    """openEMS version reported by ``python`` (``None`` when it cannot import openEMS)."""
    env = dict(os.environ)
    if home is not None:
        env[ENV_DLL] = str(home)
    try:
        proc = subprocess.run(
            [str(python), "-I", "-c", "import openEMS; print(openEMS.__version__)"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip().splitlines()
    return out[-1].strip() if out else None


def find_home(python: Path, env: Mapping[str, str] | None = None) -> Path | None:
    """The CSXCAD folder belonging to ``python`` (environment, then well-known layouts)."""
    env = os.environ if env is None else env
    for key in (ENV_HOME, ENV_DLL):
        if env.get(key) and _is_home(Path(env[key])):
            return Path(env[key])
    for base in (python.parent, python.parent.parent, python.parent.parent.parent):
        if _is_home(base):
            return base
    for anc in (python.parent, *python.parents):
        for hit in sorted((anc / ".oa" / "tools" / "openems").glob("v*/openEMS")):
            if _is_home(hit):
                return hit
        for hit in sorted(anc.glob("v*/openEMS")):
            if _is_home(hit):
                return hit
    return None


def _tools_toml_paths(env: Mapping[str, str]) -> list[Path]:
    paths = []
    if env.get("APPDATA"):
        paths.append(Path(env["APPDATA"]) / "open_analysis" / "tools.toml")
    paths.append(Path.home() / ".config" / "open_analysis" / "tools.toml")
    return paths


def _from_tools_toml(env: Mapping[str, str]) -> tuple[Path, Path | None] | None:
    for toml in _tools_toml_paths(env):
        if not toml.is_file():
            continue
        try:
            data = tomllib.loads(toml.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        entry = data.get("tools", {}).get(PROGRAM)
        if entry and entry.get("path"):
            home = entry.get("home")
            return Path(entry["path"]), Path(home) if home else None
    return None


def _project_local(start: Path) -> tuple[Path, Path | None] | None:
    for anc in (start, *start.parents):
        root = anc / ".oa" / "tools" / "openems"
        for rel in ("venv/Scripts/python.exe", "venv/bin/python"):
            python = root / rel
            if python.is_file():
                homes = sorted(root.glob("v*/openEMS"))
                return python, (homes[-1] if homes else None)
    return None


def discover(
    python: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    verify: bool = True,
) -> OpenEMSInstall | None:
    """Find openEMS. Returns ``None`` when nothing usable (or recent enough) is found.

    Order: explicit arguments; ``OA_OPENEMS_PYTHON``/``OA_OPENEMS_HOME`` (and
    ``CSXCAD_INSTALL_PATH``); ``tools.toml`` entry ``[tools.openems-python]``; a project-local
    ``.oa/tools/openems`` found from ``cwd`` upwards; finally ``openEMS`` on ``PATH`` combined
    with the current interpreter.
    """
    env = os.environ if env is None else env
    candidates: list[tuple[Path, Path | None, str]] = []
    if python:
        candidates.append((Path(python), Path(home) if home else None, "argument"))
    if env.get(ENV_PYTHON):
        env_home = env.get(ENV_HOME) or env.get(ENV_DLL)
        candidates.append((Path(env[ENV_PYTHON]), Path(env_home) if env_home else None, "env"))
    toml = _from_tools_toml(env)
    if toml:
        candidates.append((toml[0], toml[1], "tools.toml"))
    local = _project_local((cwd or Path.cwd()).resolve())
    if local:
        candidates.append((local[0], local[1], "project"))
    exe = shutil.which("openEMS", path=env.get("PATH"))
    if exe:
        candidates.append((Path(sys.executable), Path(exe).parent, "path"))
    for py, h, source in candidates:
        if not py.is_file():
            continue
        resolved_home = h if (h and _is_home(h)) else find_home(py, env)
        version = probe(py, resolved_home) if verify else None
        if verify and not (version and version_ok(version)):
            continue
        return OpenEMSInstall(python=py, home=resolved_home, version=version, source=source)
    return None


def candidate_dirs(extra: Sequence[str] = ()) -> list[str]:
    """Human-readable list of the places searched (for ``oa doctor`` hints)."""
    return [
        f"argument/{ENV_PYTHON}/{ENV_HOME}",
        *(str(p) for p in _tools_toml_paths(os.environ)),
        ".oa/tools/openems (project-local, searched upwards)",
        "openEMS on PATH",
        *extra,
    ]
