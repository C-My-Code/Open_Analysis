# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Robustness / smoke tests on the KiCad demo boards (read-only, never copied into the repo)."""

from __future__ import annotations

import os
import sys
import time
import tomllib
from pathlib import Path

import numpy as np
import pytest

from oa_ingest_kicad import KiCadReader
from oa_model import validate_board
from oa_model.geometry import signed_area

pytestmark = pytest.mark.kicad

DEMOS = Path(os.environ.get("KICAD_DEMOS", r"D:\Program Files\KiCad\10.0\share\kicad\demos"))
GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "golden" / "ingest_kicad"
BOARDS = sorted(DEMOS.rglob("*.kicad_pcb")) if DEMOS.is_dir() else []

if not BOARDS:
    pytest.skip("KiCad demo boards not found", allow_module_level=True)


def _peak_rss_bytes() -> int:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return int(pmc.PeakWorkingSetSize)
    import resource

    kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return kb * (1 if sys.platform == "darwin" else 1024)


@pytest.mark.parametrize("path", BOARDS, ids=[p.stem for p in BOARDS])
def test_demo_parses_and_validates(path: Path, record_property: pytest.RecordProperty) -> None:
    if path.stem == "jetson-agx-thor-baseboard":
        pytest.skip("covered by test_jetson_performance")
    t0 = time.perf_counter()
    board = KiCadReader().read(path)
    dt = time.perf_counter() - t0
    record_property("read_seconds", round(dt, 2))
    print(f"\n{path.name}: {dt:.2f} s, {len(board.tracks)} tracks, {len(board.pads)} pads")
    errors = [d for d in board.diagnostics if d.severity == "error"]
    assert not errors, errors[:3]
    validate_board(board, strict=True)
    assert len(board.stackup.layers) > 0


@pytest.mark.slow
def test_jetson_performance(record_property: pytest.RecordProperty) -> None:
    path = DEMOS / "jetson-agx-thor-baseboard" / "jetson-agx-thor-baseboard.kicad_pcb"
    if not path.is_file():
        pytest.skip("jetson demo not found")
    t0 = time.perf_counter()
    board = KiCadReader().read(path)
    dt = time.perf_counter() - t0
    peak = _peak_rss_bytes()
    print(f"\njetson: {dt:.1f} s, peak RSS {peak / 2**30:.2f} GiB")
    record_property("read_seconds", round(dt, 1))
    assert dt < 30.0
    assert peak < 4 * 2**30
    assert not [d for d in board.diagnostics if d.severity == "error"]
    validate_board(board, strict=True)


# ---------------------------------------------------------------- golden: pcbnew reference
CASES = sorted(p for p in GOLDEN.glob("*/case.toml")) if GOLDEN.is_dir() else []


def _poly_area(p) -> float:
    return abs(signed_area(p.exterior)) - sum(abs(signed_area(h)) for h in p.holes)


@pytest.mark.golden
@pytest.mark.parametrize("case_path", CASES, ids=[p.parent.name for p in CASES])
def test_golden_vs_pcbnew(case_path: Path) -> None:
    case = tomllib.loads(case_path.read_text(encoding="utf-8"))
    tol, exp = case["tolerance"], case["expected"]
    path = DEMOS / case["inputs"]["demo_path"]
    if not path.is_file():
        pytest.skip("demo board not found")
    b = KiCadReader().read(path)
    ids = {lyr.source_name: lyr.id for lyr in b.stackup.layers if lyr.kind == "conductor"}

    assert len(b.nets) == exp["nets"]
    assert len(b.pads) == exp["pads"]
    assert len(b.tracks) == exp["tracks"]
    assert sum(1 for t in b.tracks if t.path[0].kind == "arc") == exp["track_arcs"]
    assert len(b.vias) == exp["vias_total"]
    assert sum(v.kind == "through" for v in b.vias) == exp["vias_through"]
    assert sum(v.kind == "micro" for v in b.vias) == exp["vias_micro"]
    assert sum(v.kind in ("blind", "buried") for v in b.vias) == exp["vias_blind_or_buried"]

    from oa_ingest_kicad._geom import path_length

    by_layer: dict[str, float] = {}
    arcs_on: dict[str, int] = {}
    for t in b.tracks:
        by_layer[t.layer] = by_layer.get(t.layer, 0.0) + path_length(t.path)
        arcs_on[t.layer] = arcs_on.get(t.layer, 0) + (t.path[0].kind == "arc")
    for name, ref in exp["track_length_by_layer"].items():
        got = by_layer.get(ids[name], 0.0)
        # pcbnew snaps every arc centre to the 1 nm grid, so each arc may differ by a few nm (measured: <= 3 nm);
        # straight segments are exact (relative 1e-9).
        allowed = tol["length_rel"] * ref + arcs_on.get(ids[name], 0) * tol["arc_abs_m"]
        assert abs(got - ref) <= allowed, (name, got, ref)

    lay = {lyr.source_name: lyr for lyr in b.stackup.layers}
    for key, ref in exp["stackup_z"].items():
        name, field = key.split(":")
        assert getattr(lay[name], field) == pytest.approx(ref, abs=tol["z_abs_m"]), key

    area: dict[str, float] = {}
    for z in b.zones:
        if z.keepout is not None:
            continue
        for f in z.fills:
            area[f.layer] = area.get(f.layer, 0.0) + sum(_poly_area(p) for p in f.polygons)
    for name, ref in exp["zone_fill_area_by_layer"].items():
        assert area.get(ids[name], 0.0) == pytest.approx(ref, rel=tol["zone_area_rel"]), name

    # absolute pad pose, including back-side and rotated footprints
    slot_ps = {ps.id for ps in b.padstacks if ps.drill is not None and ps.drill.shape == "slot"}
    poses: dict[tuple[str, str], list[tuple[float, float, float, bool]]] = {}
    for p in b.pads:
        poses.setdefault((p.component or "", p.number), []).append(
            (*p.position, p.rotation, p.padstack in slot_ps)
        )
    checked = 0
    for ref, num, x, y, a in exp["pad_poses"]:
        cands = poses.get((ref, num))
        if not cands or len(cands) > 1:
            continue  # duplicate pad numbers: ambiguous by name
        cx, cy, ca, is_slot = cands[0]
        assert cx == pytest.approx(x, abs=tol["pad_position_abs_m"]), (ref, num)
        assert cy == pytest.approx(y, abs=tol["pad_position_abs_m"]), (ref, num)
        # slot pads whose slot ran along local y are re-expressed in a frame rotated by 90 degrees
        period = np.pi / 2 if is_slot else 2 * np.pi
        da = (ca - a + period / 2) % period - period / 2
        assert abs(da) < tol["pad_angle_abs_rad"], (ref, num)
        checked += 1
    assert checked > 50
