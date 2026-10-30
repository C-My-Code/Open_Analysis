# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""B-EM-01 with openEMS: finite-thickness copper (``skin``) against R1 (WP-510, nightly, slow).

R1 is the converged 2D FEM RLGC cascade of the canonical microstrip
(``tests/benchmarks/references/xs/R1/reference.json``). The same problem is run here with lumped
ports (50 ohm, as the testkit fixture) and, via ``OA_OE_PORTS=wave``, with MSL wave ports.

Environment (all optional): ``OA_OE_MODEL`` (sheet | pec | skin, default skin), ``OA_OE_FMAX``
[Hz], ``OA_OE_NPTS``, ``OA_OE_PORTS`` (lumped | wave), ``OA_OE_SKIN_FREQ`` [Hz], ``OA_OE_PRESET`` (coarse | medium | fine), ``OA_OE_YHALF`` / ``OA_OE_AIR`` (lateral half width and air above, mm), ``OA_OE_OUT``
(directory that receives the Touchstone and a JSON summary).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oa_em_openems import OpenEMSAdapter
from oa_model.em import EMProblem, EMWavePort
from oa_model.materials import DielectricMaterial, DjordjevicSarkar
from oa_model.setup import AnalysisSetup
from oa_results.quality import QualityCriteria, QualityReport
from oa_testkit import builders
from oa_testkit.emproblems import microstrip_thru_problem
from oa_testkit.harness import InlineRunContext, run_inline_full

pytestmark = [pytest.mark.external("openems"), pytest.mark.slow]

REPO = Path(__file__).resolve().parents[3]
R1 = REPO / "tests" / "benchmarks" / "references" / "xs" / "R1" / "reference.json"
IL_TOL_DB = 0.05  # BENCHMARKS B-EM-01


class _Never:
    def is_cancelled(self) -> bool:
        return False


def _gate(net: Any) -> QualityReport:
    """Stand-in for the WP-106 gate (only used to fill the required field here)."""
    sv = float(np.linalg.svd(np.asarray(net.s), compute_uv=False).max())
    return QualityReport(
        verdict="pass" if sv <= 1.001 else "fail",
        pqm=100.0,
        rqm=100.0,
        cqm=100.0,
        max_singular_value=sv,
        worst_passivity_freq=None,
        reciprocity_max_err=0.0,
        dc="missing",
        f_min=float(net.f[0]),
        f_max=float(net.f[-1]),
        n_freqs=len(net.f),
        criteria=QualityCriteria(),
    )


def _problem() -> EMProblem:
    f_max = float(os.environ.get("OA_OE_FMAX", "10e9"))
    n_pts = int(os.environ.get("OA_OE_NPTS", "41"))
    p = microstrip_thru_problem(
        f_max=f_max,
        n_points=n_pts,
        y_half=float(os.environ.get("OA_OE_YHALF", "2.5")) * 1e-3,
        air=float(os.environ.get("OA_OE_AIR", "1.0")) * 1e-3,
    )
    # canonical dielectric (docs/BENCHMARKS.md B-EM-01): Djordjevic-Sarkar, not the fixture's constant
    mats = tuple(
        m.model_copy(update={"model": DjordjevicSarkar(dk=4.3, df=0.02, f_ref=1e9)})
        if isinstance(m, DielectricMaterial)
        else m
        for m in p.structure.materials
    )
    p = p.model_copy(update={"structure": p.structure.model_copy(update={"materials": mats})})
    preset = os.environ.get("OA_OE_PRESET")
    if preset:
        p = p.model_copy(update={"mesh": p.mesh.model_copy(update={"preset": preset})})
    expert: dict[str, Any] = {
        "openems.conductor_model": os.environ.get("OA_OE_MODEL", "skin"),
    }
    if "OA_OE_SKIN_FREQ" in os.environ:
        expert["openems.skin_freq"] = float(os.environ["OA_OE_SKIN_FREQ"])
    p = p.model_copy(update={"mesh": p.mesh.model_copy(update={"expert": expert})})
    if os.environ.get("OA_OE_PORTS", "lumped") == "wave":
        box = p.structure.bbox
        trace = next(c for c in p.structure.conductors if c.role == "signal")
        ports = tuple(
            EMWavePort(
                name=name,
                face=face,  # type: ignore[arg-type]
                rect=(box.ymin, box.ymax, box.zmin, box.zmax),
                line_type="microstrip",
                signal_conductors=(trace.id,),
                z0=None,
            )
            for name, face in (("P1", "xmin"), ("P2", "xmax"))
        )
        p = p.model_copy(update={"ports": ports, "name": "microstrip_thru_25mm_wave"})
    return p


@pytest.fixture(scope="module")
def run(
    tmp_path_factory: pytest.TempPathFactory,
    install: Any,
    local_runner: Any,
    inline_analysis_cls: Any,
) -> dict[str, Any]:
    problem = _problem()
    work = tmp_path_factory.mktemp("openems_skin")
    analysis = inline_analysis_cls(
        OpenEMSAdapter(assessor=_gate), problem, work, install.tool_info()
    )
    original = InlineRunContext.run_external
    InlineRunContext.run_external = lambda self, cmd: local_runner.run(  # type: ignore[method-assign,assignment]
        cmd, lambda frac, msg: None, _Never()
    )
    try:
        setup = AnalysisSetup(
            schema="oa-setup/1",
            name="thru",
            analysis=analysis.kind,
            config={},  # type: ignore[call-arg,arg-type]
        )
        run_inline_full(analysis, builders.microstrip_board(), setup)
    finally:
        InlineRunContext.run_external = original  # type: ignore[method-assign]
    out = analysis.output
    assert out is not None and out.network is not None, out.warnings if out else None
    f = np.asarray(out.network.freqs.array)
    s = np.asarray(out.network.s.array)
    walls = [analysis.results[i].wall_s for i in sorted(analysis.results)]
    summary = {
        "model": os.environ.get("OA_OE_MODEL", "skin"),
        "ports": os.environ.get("OA_OE_PORTS", "lumped"),
        "freqs_hz": f.tolist(),
        "s21_re": s[:, 1, 0].real.tolist(),
        "s21_im": s[:, 1, 0].imag.tolist(),
        "s11_re": s[:, 0, 0].real.tolist(),
        "s11_im": s[:, 0, 0].imag.tolist(),
        "wall_s": walls,
        "peak_rss_mb": local_runner.peak_rss_mb,
        "cells": analysis.prepared.mesh_stats["cells"] + analysis.prepared.mesh_stats["pml_cells"],
    }
    if "OA_OE_OUT" in os.environ:
        d = Path(os.environ["OA_OE_OUT"])
        d.mkdir(parents=True, exist_ok=True)
        tag = os.environ.get("OA_OE_TAG") or f"{summary['ports']}_{summary['model']}"
        (d / f"openems_{tag}.json").write_text(json.dumps(summary), encoding="utf-8")
    return {"f": f, "s": s, "summary": summary}


def test_il_against_r1(run: dict[str, Any], record_property: Any) -> None:
    if not R1.exists():
        pytest.skip("R1 reference not generated")
    ref = json.loads(R1.read_text(encoding="utf-8"))
    rf = np.array(ref["freqs_hz"])
    r_il = np.array(ref["reference"]["il_50_db"] if run["summary"]["ports"] == "lumped" else ref["reference"]["il_line_db"])
    f, s = run["f"], run["s"]
    il = -20 * np.log10(np.abs(s[:, 1, 0]))
    sel = (f >= rf[0]) & (f <= rf[-1])
    err = il[sel] - np.interp(f[sel], rf, r_il)
    record_property("max_abs_il_err_db", float(np.abs(err).max()))
    record_property("il_err_at_f_max_db", float(err[-1]))
    # lumped ports see the launch discontinuity (pads/gap), which R1 does not contain
    tol = IL_TOL_DB if run["summary"]["ports"] == "wave" else 2 * IL_TOL_DB
    assert np.abs(err).max() <= tol
