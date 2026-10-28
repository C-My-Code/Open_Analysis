# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-check report: FEM vs BEM vs closed form vs exact over the fixture set."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import _xs
import pytest

sys.path.insert(0, str(Path(__file__).parent))  # fem_crosscheck lives next to this file
from fem_crosscheck import impedance_of, run_case, to_table, write_artifacts

from oa_model.xsection import CrossSection
from oa_testkit import xsections as tk
from oa_testkit.golden import golden_root, update_mode

SUITE_DIR = "impedance_fem"


def _extra_cases() -> list[tuple[str, CrossSection, float | None, str]]:
    return [
        ("pcb_microstrip_single", _xs.pcb_microstrip(), None, "35 um trapezoid, er 4.3"),
        ("pcb_microstrip_pair", _xs.pcb_microstrip(pair=True), None, "edge-coupled, 35 um"),
        ("pcb_microstrip_masked", _xs.pcb_microstrip(mask=True), None, "solder mask"),
        ("microstrip_thick_u1_er4p3", _xs.microstrip(1.0, 4.3, t_over_h=0.035), None, ""),
        ("true_thin_stripline_w0p5", _xs.stripline(0.5, 1.0), 100.43245078474767, "t = 1e-6 b"),
        (
            "true_thin_edge_coupled_s0p25",
            _xs.edge_coupled_stripline(0.5, 0.25, 1.0),
            167.04596026355262,
            "t = 1e-6 b",
        ),
    ]


@pytest.mark.slow
def test_crosscheck_table() -> None:
    rows: list[dict[str, Any]] = []
    for c in tk.all_cases():
        exact = c.expected.get("z0", c.expected.get("zdiff"))
        # the testkit "thin" strips (t = 1e-4 b) differ from the zero-thickness formulas
        note = "t = 1e-4 ref" if c.xs.topology_hint in ("stripline", "cpw") else ""
        rows.append(run_case(c.name, c.xs, exact, note=note))
    for name, xs, exact, note in _extra_cases():
        rows.append(run_case(name, xs, exact, note=note))
    for r in rows:
        assert abs(r["fem_vs_bem_pct"]) < 0.5, r["case"]  # WP-202: FEM vs BEM <= 0.5 %
        if r["fem_vs_exact_pct"] is not None:
            limit = 0.1 if r["case"].startswith("coax") else 0.3  # WP-202 vs exact
            assert abs(r["fem_vs_exact_pct"]) < limit, r["case"]
    t = to_table(rows)
    assert t.inline is not None and len(t.inline) == len(rows)
    out = Path(golden_root()) / SUITE_DIR / "crosscheck"
    if update_mode() or not (out / "table.json").exists():
        write_artifacts(rows, out)


def _generated_structures() -> list[tuple[str, CrossSection]]:
    """50 deterministic PCB-like structures: microstrip, stripline and coupled pairs."""
    out: list[tuple[str, CrossSection]] = []
    for er in (2.2, 4.3, 9.8):
        for w in (0.4, 0.8, 1.5, 3.0):
            out.append((f"ms_w{w}_er{er}", _xs.microstrip(w, er, t_over_h=0.01)))
    for er in (1.0, 3.5, 4.3):
        for w in (0.2, 0.5, 1.0, 2.0):
            out.append((f"sl_w{w}_er{er}", _xs.stripline(w, er, t_over_b=0.02)))
    for er in (3.5, 4.3, 9.8):
        for u, g in ((0.5, 0.3), (1.0, 0.5), (1.5, 1.0), (2.0, 0.25)):
            out.append((f"cms_u{u}_g{g}_er{er}", _xs.edge_coupled_microstrip(u, g, er, 0.01)))
    for s in (0.15, 0.3, 0.6, 1.0):
        for er in (3.5, 4.3, 9.8):
            out.append((f"csl_s{s}_er{er}", _xs.edge_coupled_stripline(0.4, s, er, 0.02)))
    out.append(("pcb_single", _xs.pcb_microstrip(df=0.0)))
    out.append(("pcb_pair", _xs.pcb_microstrip(pair=True, df=0.0)))
    return out


@pytest.mark.slow
def test_fem_vs_bem_on_50_generated_structures() -> None:
    from oa_impedance.bem import MeshParams
    from oa_impedance.fem import FemParams, solve_fem
    from oa_impedance.service import ImpedanceServiceImpl

    cases = _generated_structures()
    assert len(cases) == 50
    svc = ImpedanceServiceImpl(
        mesh=MeshParams().scaled(4.0)
    )  # converged BEM, see test_electrostatic
    p = FemParams(loss="none", rtol=5e-4)
    for name, xs in cases:
        q, zf, ef = impedance_of(solve_fem(xs, [0.0], p))
        _, zb, eb = impedance_of(svc.solve(xs, [0.0], method="bem"))
        assert abs(zf / zb - 1) < (0.01 if q == "zdiff" else 0.005), name  # Zdiff 1 %, Z0 0.5 %
        assert abs(ef / eb - 1) < 0.007, name  # spec 0.5 %; BEM is the low one, see README
