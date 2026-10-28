# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Locate ``kicad-cli`` and KiCad's bundled Python, and run them as subprocesses.

Resolution order for each tool (first hit wins, nothing is hard-coded to one machine):

1. explicit constructor argument;
2. environment: ``KICAD_CLI`` / ``KICAD_PYTHON`` (full paths);
3. ``tools.toml`` (``[tools.kicad-cli] path = ...``, ``[tools.kicad-python] path = ...``) from
   ``$OA_TOOLS_TOML``, ``%APPDATA%\\open_analysis\\tools.toml`` or
   ``~/.config/open_analysis/tools.toml``;
4. ``$KICAD_ROOT`` / ``$OA_KICAD_ROOT`` (an install directory containing ``bin``);
5. ``PATH``;
6. platform install locations (every drive's ``Program Files\\KiCad\\<ver>\\bin`` on Windows,
   ``/usr/bin`` and ``/usr/local/bin`` on Linux, the application bundle on macOS).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from oa_model.errors import ExternalToolFailed, ToolMissing

_WIN = sys.platform == "win32"
_MAC = sys.platform == "darwin"


def _version_key(p: Path) -> tuple[int, ...]:
    nums = re.findall(r"\d+", p.parent.parent.name)
    return tuple(int(n) for n in nums) or (0,)


def _tools_toml_paths(env: Mapping[str, str]) -> list[Path]:
    paths: list[Path] = []
    if env.get("OA_TOOLS_TOML"):
        paths.append(Path(env["OA_TOOLS_TOML"]))
    if env.get("APPDATA"):
        paths.append(Path(env["APPDATA"]) / "open_analysis" / "tools.toml")
    home = env.get("HOME") or env.get("USERPROFILE")
    if home:
        paths.append(Path(home) / ".config" / "open_analysis" / "tools.toml")
    return paths


def _from_toml(env: Mapping[str, str], tool: str) -> Path | None:
    for p in _tools_toml_paths(env):
        if not p.is_file():
            continue
        try:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            continue
        entry = data.get("tools", {}).get(tool, {})
        if isinstance(entry, dict) and entry.get("path"):
            cand = Path(str(entry["path"]))
            if cand.is_file():
                return cand
    return None


def _default_candidates(exe: str, python: bool) -> list[Path]:
    """Install locations, highest KiCad version first."""
    found: list[Path] = []
    if _WIN:
        roots: list[Path] = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            for pf in ("Program Files", "Program Files (x86)"):
                base = Path(f"{letter}:/{pf}/KiCad")
                try:
                    if base.is_dir():
                        roots.append(base)
                except OSError:
                    continue
        for root in roots:
            for ver in root.iterdir():
                cand = ver / "bin" / ("python.exe" if python else "kicad-cli.exe")
                if cand.is_file():
                    found.append(cand)
        found.sort(key=_version_key, reverse=True)
    elif _MAC:
        app = Path("/Applications/KiCad/KiCad.app/Contents")
        cand = (
            app / "Frameworks/Python.framework/Versions/Current/bin/python3"
            if python
            else app / "MacOS/kicad-cli"
        )
        if cand.is_file():
            found.append(cand)
    else:
        for d in ("/usr/bin", "/usr/local/bin", "/opt/kicad/bin"):
            cand = Path(d) / ("python3" if python else "kicad-cli")
            if cand.is_file():
                found.append(cand)
    del exe
    return found


def _resolve(
    explicit: Path | str | None,
    env: Mapping[str, str],
    env_var: str,
    toml_name: str,
    exe_name: str,
    python: bool,
) -> Path | None:
    if explicit:
        return Path(explicit)
    if env.get(env_var):
        return Path(env[env_var])
    t = _from_toml(env, toml_name)
    if t is not None:
        return t
    for var in ("KICAD_ROOT", "OA_KICAD_ROOT"):
        if env.get(var):
            base = Path(env[var]) / "bin"
            cand = base / (exe_name + (".exe" if _WIN else ""))
            if cand.is_file():
                return cand
    if not python:
        w = shutil.which("kicad-cli")
        if w:
            return Path(w)
    cands = _default_candidates(exe_name, python)
    return cands[0] if cands else None


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str


class KiCadTools:
    """Resolved ``kicad-cli`` and KiCad-Python executables (either may be ``None``)."""

    def __init__(self, cli: Path | None, python: Path | None) -> None:
        self.cli = cli
        self.python = python

    @classmethod
    def discover(
        cls,
        cli: Path | str | None = None,
        python: Path | str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> KiCadTools:
        e = os.environ if env is None else env
        return cls(
            _resolve(cli, e, "KICAD_CLI", "kicad-cli", "kicad-cli", False),
            _resolve(python, e, "KICAD_PYTHON", "kicad-python", "python", True),
        )

    # ------------------------------------------------------------------ requirements
    def require_cli(self) -> Path:
        if self.cli is None or not Path(self.cli).is_file():
            raise ToolMissing(
                "kicad-cli was not found",
                program="kicad-cli",
                hint="Install KiCad 10, set KICAD_CLI, or add a tools.toml entry.",
            )
        return Path(self.cli)

    def require_python(self) -> Path:
        if self.python is None or not Path(self.python).is_file():
            raise ToolMissing(
                "KiCad's bundled Python was not found",
                program="kicad-python",
                hint="Set KICAD_PYTHON or add [tools.kicad-python] path to tools.toml.",
            )
        return Path(self.python)

    # ------------------------------------------------------------------ execution
    def run(
        self,
        program: Path,
        args: Sequence[str | Path],
        *,
        timeout: float = 600.0,
        check: bool = True,
    ) -> RunResult:
        cmd = [str(program), *map(str, args)]
        try:
            cp = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExternalToolFailed(
                f"{Path(program).name} timed out after {timeout:.0f} s", returncode=-1
            ) from exc
        except OSError as exc:
            raise ToolMissing(
                f"cannot start {program}: {exc}", program=str(program), hint="Check the path."
            ) from exc
        res = RunResult(cp.returncode, cp.stdout, cp.stderr)
        if check and cp.returncode != 0:
            tail = "\n".join((cp.stderr or cp.stdout).strip().splitlines()[-20:])
            raise ExternalToolFailed(
                f"{Path(program).name} {' '.join(map(str, args[:3]))} failed ({cp.returncode})",
                returncode=cp.returncode,
                log_tail=tail,
            )
        return res

    def run_cli(
        self, args: Sequence[str | Path], *, timeout: float = 600.0, check: bool = True
    ) -> RunResult:
        return self.run(self.require_cli(), args, timeout=timeout, check=check)

    def version(self) -> str | None:
        try:
            return self.run_cli(["version"], timeout=30).stdout.strip() or None
        except (ToolMissing, ExternalToolFailed):
            return None
