# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for the CLI end-to-end suite: run the real ``oa`` command line in a subprocess.

Every call uses ``--json`` and returns the parsed envelope, so tests assert on structure and
accuracy classes and never parse human text.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
BOARDS = REPO / "tests" / "boards" / "kicad10"
KICAD_DEMOS = Path(os.environ.get("KICAD_DEMOS", r"D:\Program Files\KiCad\10.0\share\kicad\demos"))
CM5 = KICAD_DEMOS / "cm5_minima" / "CM5_MINIMA_3.kicad_pcb"

ACCURACY_CLASSES = frozenset({"reference", "validated", "screening", "estimate", "unverified"})

SIGNAL_NETS = {"kinds": ["signal"]}
# cm5_minima has 1369 signal sections (minutes on CPU): profile one HDMI lane set instead
NET_SELECTORS = {"cm5_minima": {"names": ["/CM5/HDMI1_D*"]}}

# Boards with a DC power rail: board -> pi.dc config (the rail, source and load components).
POWER_BOARDS: dict[str, dict[str, Any]] = {
    "power_strip": {
        "rail": {"net": "STRIP"},
        "vrm": {"component": "J1"},
        "loads": [{"component": "J2", "current": 1.0}],
        "temperature": "20degC",
    },
    "power_two_layer_vias": {
        "rail": {"net": "VX"},
        "vrm": {"component": "J1"},
        "loads": [{"component": "J2", "current": 1.0}],
        "temperature": "20degC",
    },
    "power_plane": {
        "rail": {"net": "+1V0"},
        "vrm": {"component": "U5"},
        "loads": [{"component": "U1", "current": 1.0}],
        "temperature": "20degC",
    },
}


def wp102_boards() -> list[str]:
    """Directory names of every WP-102 synthetic board (sorted: deterministic order)."""
    return sorted(p.name for p in BOARDS.iterdir() if (p / "board.kicad_pcb").is_file())


def stage_board(source: Path, dest: Path) -> Path:
    """Copy a board (and its ``.kicad_pro``) into ``dest``; the repository is never written."""
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / source.name if source.name.endswith(".kicad_pcb") else dest / "board.kicad_pcb"
    shutil.copy2(source, out)
    pro = source.with_suffix(".kicad_pro")
    if pro.is_file():
        shutil.copy2(pro, out.with_suffix(".kicad_pro"))
    return out


class Cli:
    """One working directory (the project) plus the ``oa`` entry point of this environment."""

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd
        self.env = {
            **os.environ,
            "OA_DEVICE": "cpu",
            "OA_OUTPUT": "json",
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS", "2"),
            "OPENBLAS_NUM_THREADS": os.environ.get("OPENBLAS_NUM_THREADS", "2"),
            "OA_NUM_THREADS": os.environ.get("OA_NUM_THREADS", "2"),
            "PYTHONIOENCODING": "utf-8",
        }

    def run(self, *args: str, expect_rc: tuple[int, ...] = (0,)) -> dict[str, Any]:
        """Run ``oa <args> --json``; return the envelope after checking the exit code."""
        cmd = [
            sys.executable,
            "-c",
            "from oa_cli.main import main; main()",
            *args,
            "--json",
        ]
        proc = subprocess.run(
            cmd,
            cwd=self.cwd,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
        assert proc.returncode in expect_rc, (
            f"oa {' '.join(args)} exited {proc.returncode}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
        env: dict[str, Any] = json.loads(proc.stdout)
        env["_rc"] = proc.returncode
        return env


def job_of(env: dict[str, Any]) -> dict[str, Any]:
    """The finished job of a ``run``/``erc``/``impedance`` envelope."""
    assert env["ok"] is True, env["errors"]
    job: dict[str, Any] = env["data"]["job"]
    assert job["state"] == "succeeded", job
    return job


def accuracy_classes(summary: dict[str, Any]) -> set[str]:
    """Accuracy class of every item of a bundle summary (each must be a known class)."""
    classes = {str(i["accuracy_class"]) for i in summary["items"]}
    assert classes, summary
    assert classes <= ACCURACY_CLASSES, classes
    return classes


@dataclass
class Project:
    """A staged board plus its ``oa init`` project; step results are computed once and cached."""

    name: str
    cli: Cli
    board: Path
    init: dict[str, Any]
    cache: dict[str, dict[str, Any]] = field(default_factory=dict)

    def step(self, key: str, *args: str, expect_rc: tuple[int, ...] = (0,)) -> dict[str, Any]:
        if key not in self.cache:
            self.cache[key] = self.cli.run(*args, expect_rc=expect_rc)
        return self.cache[key]

    def erc(self) -> dict[str, Any]:
        sarif = self.cli.cwd / "erc.sarif"
        return self.step("erc", "erc", "--sarif", str(sarif), "--fail-on", "never")

    def impedance(self) -> dict[str, Any]:
        return self.step(
            "impedance",
            "impedance",
            "profile",
            "--nets",
            json.dumps(NET_SELECTORS.get(self.name, SIGNAL_NETS)),
            "--method",
            "auto",
            expect_rc=(0, 4),
        )

    def dc(self) -> dict[str, Any]:
        cfg = self.cli.cwd / "pi_dc.json"
        cfg.write_text(json.dumps(POWER_BOARDS[self.name]), encoding="utf-8")
        return self.step("dc", "run", "--analysis", "pi.dc", "--config", f"@{cfg}")

    def summary(self, env: dict[str, Any]) -> dict[str, Any]:
        rid = job_of(env)["result_id"]
        out = self.cli.run("results", "summary", rid)
        assert out["ok"] is True
        data: dict[str, Any] = out["data"]
        return data


EM_CONFIG = {  # 10 mm cut of the 50 mm MS_500 microstrip (model frame: y = -y_file)
    "region": {"nets": ["MS_500"], "bbox": [0.0125, -0.0335, 0.0225, -0.0265]},
    "frequency": {"f_min": 0, "f_max": 5e9, "n_points": 11},
}


def stage_em_project(work: Path) -> tuple[Cli, str]:
    """An ``oa init``-ed microstrip project and the ``--config`` argument of its EM run."""
    stage_board(BOARDS / "microstrip" / "board.kicad_pcb", work)
    cli = Cli(work)
    cli.run("init", "--yes", ".")
    cfg = work / "em.json"
    cfg.write_text(json.dumps(EM_CONFIG), encoding="utf-8")
    return cli, f"@{cfg}"
