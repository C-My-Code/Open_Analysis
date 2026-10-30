# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Case runners (WP-304). Each runner executes one benchmark sub-case once and returns:

* ``accuracy``: the value compared with the target ``accuracy_max`` (lower is better), or absent;
* ``units``: how many sections/boards/... one call covers (for per-unit time targets);
* ``time_s`` (optional): the runner's own timing of the measured core, replacing the wall time of
  the call (used when setup that a user does not pay per run is inside the call);
* ``detail``: anything worth recording (unknowns, per-section errors, ...).

Imports happen inside the runners, so listing cases never loads a solver. Reference data and
helpers are the ones the validation runner uses (WP-302): nothing is duplicated.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
Runner = Callable[[str], dict[str, Any]]


def _paths(*parts: str) -> None:
    for p in parts:
        s = str(REPO / p)
        if s not in sys.path:
            sys.path.insert(0, s)


# ------------------------------------------------------------------------------ B-IMP-01
def imp01_sections(device: str) -> dict[str, Any]:
    """One BEM solve per exact cross-section; accuracy = worst error in percent."""
    _paths(
        "tools/validation",
        "packages/oa-impedance/tests",
        "packages/oa-impedance/tests/fem",
    )
    import _xs
    from fem_crosscheck import impedance_of
    from val_model import load_case

    from oa_impedance.service import ImpedanceServiceImpl
    from oa_testkit import xsections as tk

    sections: list[tuple[str, Any, float]] = []
    g = load_case("impedance_bem", "cohn_thin_stripline")
    for w, exp in zip(g["inputs"]["w_over_b"], g["expected"]["z0_er1"], strict=True):
        sections.append((f"cohn.w{w:g}", _xs.stripline(w, 1.0, g["inputs"]["t_over_b"]), exp))
    c = load_case("impedance_bem", "cpw_halfspace")
    for er, exp in zip(c["inputs"]["er"], c["expected"]["z0"], strict=True):
        sections.append((f"cpw.er{er:g}", _xs.cpw_halfspace(er, depth=c["inputs"]["depth"]), exp))
    for tc in tk.all_cases():
        if tc.name.startswith(("coax", "wire_over", "two_wire")):
            sections.append((tc.name, tc.xs, tc.expected.get("z0", tc.expected.get("zdiff"))))

    errs: dict[str, float] = {}
    for name, xs, exp in sections:
        r = ImpedanceServiceImpl().solve(xs, [0.0], method="bem")
        _q, got, _e = impedance_of(r)
        errs[name] = abs(got / exp - 1.0) * 100.0
    worst = max(errs, key=lambda k: errs[k])
    return {
        "accuracy": errs[worst],
        "units": len(sections),
        "detail": {"worst_section": worst, "error_pct": {k: round(v, 5) for k, v in errs.items()}},
    }


# ------------------------------------------------------------------------------ B-DC-01/02
def dc01_power_strip(device: str) -> dict[str, Any]:
    """WP-102 power_strip board vs expected.yaml (R = Rs L / W); accuracy in percent."""
    _paths("packages/oa-pi/tests")
    import yaml
    from pi_helpers import T20, kicad_solve, two_point_resistance

    exp = yaml.safe_load(
        (REPO / "tests/boards/kicad10/power_strip/expected.yaml").read_text(encoding="utf-8")
    )
    (case,) = exp["dc"]["cases"]
    sol = kicad_solve(
        "power_strip",
        {
            "rail": {"net": "STRIP"},
            "vrm": {"component": "J1"},
            "loads": [{"component": "J2", "current": 1.0}],
            "temperature": T20,
        },
    )
    got = two_point_resistance(sol, "J2", 1.0, 1.0)
    err = abs(got / case["resistance_ohm"] - 1.0) * 100.0
    return {
        "accuracy": err,
        "units": 1,
        "detail": {"R_ohm": got, "expected_ohm": case["resistance_ohm"], "case": case["name"]},
    }


_H_CACHE: dict[float, float] = {}


def _dc_scale(n_target: int, h_far: float | None = None) -> dict[str, Any]:
    """power_plane testkit board refined to about ``n_target`` unknowns, build + FEM solve."""
    from oa_geometry import BoardGeometry
    from oa_pi.dc.config import DcConfig
    from oa_pi.dc.fem import solve_fem
    from oa_pi.dc.network import build_network
    from oa_testkit.builders import power_plane_board

    board = power_plane_board(neck_width=None)
    geo = BoardGeometry.build(board)

    def cfg(h: float, far: float | None) -> Any:
        return DcConfig.model_validate(
            {
                "rail": {"net": "VDD", "return": "GND"},
                "vrm": {"component": "U1"},
                "loads": [{"component": "U2", "current": 5.0}],
                "temperature": "20degC",
                "mesh": {"h_plane": h, "h_far": far},
            }
        )

    if n_target not in _H_CACHE:  # calibrate once (cold run): fit n = a h^-p on two cheap meshes
        import math

        h1, h2 = 0.2e-3, 0.1e-3
        n1 = solve_fem(build_network(board, geo, cfg(h1, None))).n_unknowns
        n2 = solve_fem(build_network(board, geo, cfg(h2, None))).n_unknowns
        p = math.log(n2 / n1) / math.log(h1 / h2)
        _H_CACHE[n_target] = h2 * (n2 / n_target) ** (1.0 / p)
    h = _H_CACHE[n_target]
    t0 = time.perf_counter()
    sol = solve_fem(build_network(board, geo, cfg(h, h_far)))
    dt = time.perf_counter() - t0
    return {
        "units": 1,
        "time_s": dt,
        "detail": {
            "n_unknowns": sol.n_unknowns,
            "h_plane_m": h,
            "n_target": n_target,
            "h_far_m": h_far,
        },
    }


def dc02_scale_3e5(device: str) -> dict[str, Any]:
    return _dc_scale(300_000, h_far=1e-3)


def dc02_scale_1e6(device: str) -> dict[str, Any]:
    """h_plane of the 1e6-unknown uniform mesh with WP-514 interior coarsening (h_far 1 mm)."""
    return _dc_scale(1_000_000, h_far=1e-3)


# ------------------------------------------------------------------------------ B-ERC-01
def erc_combined(device: str) -> dict[str, Any]:
    """Parse + all M1 rules on the combined board; accuracy = mismatches vs expected.yaml."""
    _paths("tools/validation", "packages/oa-erc/tests")
    from erc_helpers import INTENTS, expected, services
    from val_erc import EXTRA_IM002, _match

    from oa_erc import run_rules
    from oa_ingest_kicad import KiCadReader
    from oa_model.setup import ReadOptions

    t0 = time.perf_counter()
    board = KiCadReader().read(
        REPO / "tests/boards/kicad10/combined/board.kicad_pcb", ReadOptions()
    )
    all_found = list(run_rules(board, intent=INTENTS.get("combined"), services=services()))
    dt = time.perf_counter() - t0
    # same filter as the validation runner: IM-002 on nets whose geometry misses its own target
    # is a documented known gap (docs/change-requests/WP-201-expected-impedance.md)
    found = [v for v in all_found if not (v.rule_id == "IM-002" and v.nets[0] in EXTRA_IM002)]
    exp = expected("combined")
    items = exp["erc"]
    missed = [e for e in items if not any(_match(e, v) for v in found)]
    unspecified = {(u["rule_id"], tuple(sorted(u["nets"]))) for u in exp["unspecified"]}
    extras = [
        v
        for v in found
        if not any(_match(e, v) for e in items)
        and (v.rule_id, tuple(sorted(v.nets))) not in unspecified
    ]
    forbidden = [v for v in found if v.rule_id in set(exp["must_not_report"])]
    return {
        "accuracy": float(len(missed) + len(extras) + len(forbidden)),
        "units": 1,
        "time_s": dt,
        "detail": {
            "violations": len(all_found),
            "missed": len(missed),
            "extra": len(extras),
            "forbidden": len(forbidden),
            "known_gap_im002_filtered": len(all_found) - len(found),
        },
    }


# ------------------------------------------------------------------------------ B-ING-01
def _fails(name: str, board: Any, exp: Any) -> int:
    import ingest_xval as xv

    j = xv.compare_expected(name, "kicad", board, exp, areas=xv.AreaCache(board))
    return int(xv.status_counts(j.findings)["fail"])


def ing_wp102(device: str) -> dict[str, Any]:
    """Native KiCad parse of every WP-102 board (timed), counts/areas vs expected.yaml."""
    _paths("tests/integration/ingest")
    import ingest_xval as xv

    from oa_ingest_kicad import KiCadReader

    total = 0.0
    fails = 0
    per_board: dict[str, float] = {}
    dirs = xv.synthetic_boards()
    for d in dirs:
        t0 = time.perf_counter()
        board = KiCadReader().read(d / "board.kicad_pcb")
        dt = time.perf_counter() - t0
        total += dt
        per_board[d.name] = round(dt, 4)
        fails += _fails(d.name, board, xv.load_expected(d))
    return {
        "accuracy": float(fails),
        "units": len(dirs),
        "time_s": total,
        "detail": {"parse_s": per_board},
    }


def _parse_file(path: Path) -> dict[str, Any]:
    from oa_ingest_kicad import KiCadReader

    t0 = time.perf_counter()
    board = KiCadReader().read(path)
    dt = time.perf_counter() - t0
    errors = [d for d in board.diagnostics if d.severity == "error"]
    return {
        "accuracy": float(len(errors)),
        "units": 1,
        "time_s": dt,
        "detail": {
            "tracks": len(board.tracks),
            "pads": len(board.pads),
            "file_mb": round(path.stat().st_size / 2**20, 1),
        },
    }


def _demo(rel: str) -> Path:
    demos = Path(os.environ.get("KICAD_DEMOS", r"D:\Program Files\KiCad\10.0\share\kicad\demos"))
    path = demos / rel
    if not path.is_file():
        raise FileNotFoundError(f"KiCad demo board not found: {path} (set KICAD_DEMOS)")
    return path


def ing_cm5(device: str) -> dict[str, Any]:
    return _parse_file(_demo("cm5_minima/CM5_MINIMA_3.kicad_pcb"))


def ing_jetson(device: str) -> dict[str, Any]:
    return _parse_file(_demo("jetson-agx-thor-baseboard/jetson-agx-thor-baseboard.kicad_pcb"))


RUNNERS: dict[str, Runner] = {
    "imp01_sections": imp01_sections,
    "dc01_power_strip": dc01_power_strip,
    "dc02_scale_3e5": dc02_scale_3e5,
    "dc02_scale_1e6": dc02_scale_1e6,
    "erc_combined": erc_combined,
    "ing_wp102": ing_wp102,
    "ing_cm5": ing_cm5,
    "ing_jetson": ing_jetson,
}
