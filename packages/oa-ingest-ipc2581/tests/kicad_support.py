# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Helpers for tests that need KiCad: demo boards, IPC-2581 export, the pcbnew oracle."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from oa_kicad_bridge import KiCadTools, export_ipc2581

ORACLE = Path(__file__).with_name("oracle_pcbnew.py")

# (golden case name, demo directory, board file stem)
DEMOS = (
    ("ecc83_pp", "ecc83", "ecc83-pp"),
    ("stickhub", "stickhub", "StickHub"),
    ("cm5_minima", "cm5_minima", "CM5_MINIMA_3"),
    ("pic_programmer", "pic_programmer", "pic_programmer"),
)
# nets smaller than this are not recorded individually (their relative error is not meaningful)
MIN_NET_AREA_M2 = 2e-7


def tools() -> KiCadTools:
    return KiCadTools.discover()


def demo_board(directory: str, stem: str) -> Path | None:
    t = tools()
    if t.cli is None:
        return None
    for share in (t.cli.parent.parent / "share" / "kicad", Path("/usr/share/kicad")):
        p = share / "demos" / directory / f"{stem}.kicad_pcb"
        if p.is_file():
            return p
    return None


def export(board: Path, out: Path) -> Path:
    return export_ipc2581(board, out, tools=tools())


def run_oracle(board: Path, out_json: Path, timeout: float = 600.0) -> dict[str, Any]:
    """Run ``oracle_pcbnew.py`` under KiCad's Python (never in this process)."""
    py = tools().require_python()
    subprocess.run(
        [str(py), str(ORACLE), str(board), str(out_json)],
        check=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        capture_output=True,
    )
    return json.loads(out_json.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def stackup_expected(stack: list[dict[str, Any]]) -> dict[str, list[float]]:
    """Copper/dielectric lists (top to bottom) from the oracle's ``(stackup ...)`` records."""
    cu: list[float] = []
    th: list[float] = []
    dk: list[float] = []
    df: list[float] = []
    for rec in stack:
        t = (rec["type"] or "").lower()
        if t == "copper":
            cu.append(rec["thickness_m"])
        elif t in ("core", "prepreg", "dielectric"):
            for sub in [rec, *rec["sublayers"]]:
                th.append(sub["thickness_m"])
                dk.append(sub["epsilon_r"] or 0.0)
                df.append(sub["loss_tangent"] or 0.0)
    return {
        "copper_thickness": cu,
        "dielectric_thickness": th,
        "dielectric_dk": dk,
        "dielectric_df": df,
    }


def reader_stackup(board: Any) -> dict[str, list[float]]:
    mats = {m.name: m for m in board.stackup.materials}
    cu: list[float] = []
    th: list[float] = []
    dk: list[float] = []
    df: list[float] = []
    for layer in board.stackup.layers:
        if layer.kind == "conductor":
            cu.append(layer.thickness)
        elif layer.kind == "dielectric":
            m = mats[layer.material].model
            th.append(layer.thickness)
            dk.append(m.dk)
            df.append(m.df)
    return {
        "copper_thickness": cu,
        "dielectric_thickness": th,
        "dielectric_dk": dk,
        "dielectric_df": df,
    }
