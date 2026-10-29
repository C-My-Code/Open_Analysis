# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""EM validation: openEMS microstrip thru vs the BEM RLGC cascade; Palace slots (WP-301 pending)."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from val_model import REPO, Case, case_dir, load_case

sys.path.insert(0, str(REPO / "packages" / "oa-em" / "tests"))

CAT = "em"
DEFAULT_OPENEMS_ROOT = (
    REPO.parent / "agent-a42ff817b09dd0eb5"  # sibling worktree holding the WP-108 install
)


def _palace_slots() -> list[Case]:
    g = load_case("crosssolver", "palace_pending_wp301")
    cdir = case_dir("crosssolver", "palace_pending_wp301")
    slots = (
        ("em.palace.microstrip_z0", "Palace microstrip thru: Z0 vs BEM", "Z0 (<= 3 %)"),
        (
            "em.palace.microstrip_il",
            "Palace microstrip thru: IL vs RLGC at 10 GHz",
            "IL (<= 0.2 dB)",
        ),
        (
            "em.palace.via_vs_openems_s21",
            "Via transition 0-40 GHz, openEMS vs Palace: |dS21|",
            "|dS21| (<= 0.5 dB to 20 GHz, <= 1 dB to 40 GHz)",
        ),
        (
            "em.palace.via_vs_openems_s11",
            "Via transition 0-40 GHz, openEMS vs Palace: |dS11|",
            "|dS11| (<= 3 dB where S11 > -30 dB)",
        ),
    )
    return [
        Case(
            cid,
            CAT,
            f"Palace: pending WP-301 - {title}",
            g["source"],
            "cross-solver",
            True,
            q,
            "",
            None,
            None,
            "exact",
            0.0,
            cdir,
            note="Palace: pending WP-301",
            forced_status="pending",
        )
        for cid, title, q in slots
    ]


def _tutorial_slots() -> list[Case]:
    g = load_case("published", "pending_openems_tutorials")
    cdir = case_dir("published", "pending_openems_tutorials")
    return [
        Case(
            cid,
            CAT,
            f"[verify] {title}",
            g["source"],
            "published",
            True,
            q,
            "",
            None,
            None,
            "rel",
            0.01,
            cdir,
            note="not done: no reference script, pipeline cannot reproduce it yet",
            forced_status="pending",
        )
        for cid, title, q in (
            ("em.pub.msl_notch", "openEMS MSL notch filter tutorial", "notch frequency"),
            ("em.pub.patch_2p4", "openEMS simple 2.4 GHz patch tutorial", "S11 minimum frequency"),
        )
    ]


def _skipped(reason: str) -> list[Case]:
    g = load_case("crosssolver", "openems_msl_vs_bem_rlgc")
    cdir = case_dir("crosssolver", "openems_msl_vs_bem_rlgc")
    return [
        Case(
            f"em.openems.{k}",
            CAT,
            f"openEMS microstrip thru vs BEM RLGC: {k}",
            g["source"],
            "cross-solver",
            True,
            k,
            "",
            None,
            None,
            "exact",
            0.0,
            cdir,
            note=reason,
            forced_status="skipped",
        )
        for k in ("z0", "il_10ghz", "quality_pqm")
    ]


def _bem(cu_t: float, freqs: tuple[float, ...]) -> tuple[float, np.ndarray]:
    from oa_geometry import BoardGeometry
    from oa_impedance.service import ImpedanceServiceImpl
    from oa_testkit import builders as bld

    geo = BoardGeometry.build(bld.microstrip_board(cu_t=cu_t))
    seg = geo.segments("SIG")[0]
    xs = geo.cross_section_at(seg, 12e-3, include_aggressors=False)
    res = ImpedanceServiceImpl().solve(xs, freqs=list(freqs), method="bem")
    assert res.params.z0 is not None
    return (
        float(np.asarray(res.params.z0.array).ravel()[0]),
        np.asarray(res.params.loss_db_per_m.array).ravel(),
    )


def openems_cases(openems_root: Path | None = None) -> list[Case]:
    from oa_em_openems import discover

    root = (
        Path(os.environ.get("OA_VALIDATION_OPENEMS_ROOT", ""))
        if os.environ.get("OA_VALIDATION_OPENEMS_ROOT")
        else (openems_root or DEFAULT_OPENEMS_ROOT)
    )
    found = discover(cwd=root)
    if found is None:
        return _skipped(f"openEMS >= 0.37 not found (searched from {root})")
    from em_helpers import run_job

    from oa_jobs import Budget
    from oa_testkit import builders as bld

    g = load_case("crosssolver", "openems_msl_vs_bem_rlgc")
    cdir = case_dir("crosssolver", "openems_msl_vs_bem_rlgc")
    tol = g["tolerance"]
    length = g["inputs"]["length_m"]
    tools = {"openems-python": {"runner": "local", "path": str(found.python)}}
    cfg: dict[str, Any] = {
        "region": {"nets": ["SIG"], "bbox": [1e-3, -3e-3, 24e-3, 3e-3]},
        "frequency": {"f_min": 0, "f_max": 10e9, "n_points": 101},
        "refinement_check": True,
    }
    budget = Budget(max_runtime_s=1200, max_memory_gb=8, allow_external=True)
    t0 = time.perf_counter()
    # ignore_cleanup_errors: on Windows the job store's sqlite file can stay locked a moment
    with tempfile.TemporaryDirectory(prefix="oa-val-em-", ignore_cleanup_errors=True) as tmp:
        bundle, _status, wall = run_job(
            Path(tmp), bld.microstrip_board(), cfg, tools=tools, budget=budget
        )
        net = bundle.items[0].data
        f = np.asarray(net.freqs.array)
        s = np.asarray(net.s.array)
        k10 = int(np.argmin(np.abs(f - g["inputs"]["f_il"])))
        il10 = float(-20 * np.log10(abs(s[k10, 1, 0])))
        sel = (f >= 1e9) & (f <= 10e9)
        s11_max = float((20 * np.log10(np.abs(s[sel, 0, 0]))).max())
        table = next(i.data for i in bundle.items if i.id == "port_z")
        cols = [c.name for c in table.columns]
        z = np.array([r[cols.index("P1.z_re")] for r in table.inline])
        z0_meas = float(np.median(z[len(z) // 4 :]))
        pqm = float(net.quality.pqm)
        verdict = net.quality.verdict
    z0_thin, _ = _bem(1e-6, (5e9, 10e9))
    _, loss_pm = _bem(35e-6, (5e9, 10e9))
    z0_35, _ = _bem(35e-6, (5e9, 10e9))
    il_ref = float(loss_pm[1] * length)
    dt = time.perf_counter() - t0
    src = g["source"]
    return [
        Case(
            "em.openems.z0",
            CAT,
            "openEMS microstrip Z0 vs BEM thin-strip Z0",
            src,
            "cross-solver",
            True,
            "Z0",
            "ohm",
            z0_thin,
            z0_meas,
            "rel",
            tol["z0_rel"],
            cdir,
            note="openEMS models the copper as a zero-thickness sheet, so the thin-strip BEM is "
            "the like-for-like reference",
            runtime_s=dt,
        ),
        Case(
            "em.openems.z0_35um",
            CAT,
            "openEMS microstrip Z0 vs BEM with the real 35 um copper",
            src,
            "cross-solver",
            True,
            "Z0",
            "ohm",
            z0_35,
            z0_meas,
            "rel",
            tol["z0_rel"],
            cdir,
            note="known gap: sheet copper vs 35 um trapezoid (docs/FOLLOWUPS.md, WP-205); needs "
            "thick-copper modelling",
            forced_status="known_gap",
        ),
        Case(
            "em.openems.il_10ghz",
            CAT,
            "openEMS insertion loss at 10 GHz vs BEM RLGC cascade (23 mm)",
            src,
            "cross-solver",
            True,
            "IL",
            "dB",
            il_ref,
            il10,
            "abs",
            tol["il_abs_db"],
            cdir,
            note=f"ROADMAP gate 0.2 dB per 25 mm; wall {wall:.0f} s, 4 threads",
        ),
        Case(
            "em.openems.s11_max",
            CAT,
            "openEMS max |S11| over 1-10 GHz",
            src,
            "cross-solver",
            True,
            "max S11",
            "dB",
            tol["s11_max_db"],
            s11_max,
            "max",
            tol["s11_max_db"],
            cdir,
            note="matched-line sanity bound used by the WP-205 test",
        ),
        Case(
            "em.openems.quality_pqm",
            CAT,
            "openEMS S-parameter quality, PQM",
            "ROADMAP quality gate: real solver outputs PQM >= 99",
            "cross-solver",
            True,
            "PQM",
            "",
            99.0,
            pqm,
            "min",
            99.0,
            cdir,
            note=f"gate verdict '{verdict}'; implementation-defined metric (docs/FOLLOWUPS.md)",
        ),
    ]


def run(openems_root: Path | None = None, with_openems: bool = True) -> list[Case]:
    cases = openems_cases(openems_root) if with_openems else _skipped("--skip-openems given")
    return cases + _palace_slots() + _tutorial_slots()
