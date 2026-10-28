# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""End to end on a real openEMS (nightly): the microstrip thru fixture through the inline harness.

Skipped when openEMS is not installed. Run with the openEMS folder on ``PATH`` (or importable)
so the ``external("openems")`` marker does not skip it; see the package README.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from oa_em_openems import OpenEMSAdapter
from oa_model.materials import DjordjevicSarkar
from oa_model.setup import AnalysisSetup
from oa_results.quality import QualityCriteria, QualityReport
from oa_testkit import analytic, builders
from oa_testkit.emproblems import microstrip_thru_problem
from oa_testkit.harness import InlineRunContext, run_inline_full

pytestmark = [pytest.mark.external("openems"), pytest.mark.slow]

C0 = 299792458.0


class _Never:
    def is_cancelled(self) -> bool:
        return False


def _gate(net: Any) -> QualityReport:
    """Stand-in for the WP-106 gate (only used to fill the required field here)."""
    s = np.asarray(net.s)
    sv = float(np.linalg.svd(s, compute_uv=False).max())
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


@pytest.fixture(scope="module")
def thru(
    tmp_path_factory: pytest.TempPathFactory,
    install: Any,
    local_runner: Any,
    inline_analysis_cls: Any,
) -> dict[str, Any]:
    runner = local_runner
    problem = microstrip_thru_problem()
    work = tmp_path_factory.mktemp("openems_thru")
    analysis = inline_analysis_cls(
        OpenEMSAdapter(assessor=_gate), problem, work, install.tool_info()
    )
    original = InlineRunContext.run_external
    InlineRunContext.run_external = lambda self, cmd: runner.run(  # type: ignore[method-assign,assignment]
        cmd, lambda frac, msg: None, _Never()
    )
    try:
        setup = AnalysisSetup(
            schema="oa-setup/1",
            name="thru",
            analysis=analysis.kind,
            config={},  # type: ignore[call-arg,arg-type]
        )
        run = run_inline_full(analysis, builders.microstrip_board(), setup)
    finally:
        InlineRunContext.run_external = original  # type: ignore[method-assign]
    out = analysis.output
    assert out is not None and out.network is not None, out.warnings if out else None
    walls = [analysis.results[i].wall_s for i in sorted(analysis.results)]
    return {
        "net": out.network,
        "out": out,
        "walls": walls,
        "peak_mb": runner.peak_rss_mb,
        "run": run,
        "stats": analysis.prepared.mesh_stats,
    }


def _expected_loss_db(f: float, length: float, w: float, h: float) -> tuple[float, float]:
    """Pozar closed-form dielectric + conductor loss [dB] and eps_eff of the fixture line."""
    ds = DjordjevicSarkar(dk=4.3, df=0.02, f_ref=1e9)
    from oa_em_openems.dispersion import eval_eps

    e = complex(eval_eps(ds, np.array([f]))[0])
    er, tand = e.real, -e.imag / e.real
    z0, eeff = analytic.hammerstad_jensen(w / h, er)
    k0 = 2 * math.pi * f / C0
    a_d = k0 * er * (eeff - 1) * tand / (2 * math.sqrt(eeff) * (er - 1))
    rs = math.sqrt(math.pi * f * 4e-7 * math.pi / 5.8e7)
    a_c = rs / (z0 * w)
    return 8.686 * (a_d + a_c) * length, eeff


def test_matched_line_s11_and_s21(thru: dict[str, Any], record_property: Any) -> None:
    net = thru["net"]
    f = np.asarray(net.freqs.array)
    s = np.asarray(net.s.array)
    s11 = 20 * np.log10(np.abs(s[:, 0, 0]))
    s21 = 20 * np.log10(np.abs(s[:, 1, 0]))
    assert s11.max() < -20.0, f"|S11| worst {s11.max():.1f} dB at {f[s11.argmax()] / 1e9:.2f} GHz"
    loss, _ = _expected_loss_db(10e9, 25e-3, 0.38e-3, 0.2e-3)
    # S21 = 0 dB minus the line loss; Pozar's closed forms are good to a few tenths of a dB
    assert s21[-1] == pytest.approx(-loss, abs=0.4)
    assert np.all(np.diff(s21[10:]) < 0.05)  # loss grows with frequency
    assert s21[0] > -0.3
    record_property("S21_10GHz_dB", float(s21[-1]))
    record_property("S11_worst_dB", float(s11.max()))
    record_property("expected_loss_10GHz_dB", float(loss))


def test_delay_matches_effective_permittivity(thru: dict[str, Any], record_property: Any) -> None:
    net = thru["net"]
    f = np.asarray(net.freqs.array)
    s21 = np.asarray(net.s.array)[:, 1, 0]
    sel = (f >= 2e9) & (f <= 9e9)
    phase = np.unwrap(np.angle(s21))
    tau = -np.polyfit(2 * np.pi * f[sel], phase[sel], 1)[0]
    _, eeff = _expected_loss_db(5e9, 25e-3, 0.38e-3, 0.2e-3)
    expected = 25e-3 * math.sqrt(eeff) / C0
    record_property("delay_ps", tau * 1e12)
    record_property("expected_delay_ps", expected * 1e12)
    assert tau == pytest.approx(expected, rel=0.05)


def test_network_is_passive_reciprocal_and_reports_cost(
    thru: dict[str, Any], record_property: Any
) -> None:
    net = thru["net"]
    s = np.asarray(net.s.array)
    assert np.linalg.svd(s, compute_uv=False).max() < 1.01
    assert np.abs(s[:, 0, 1] - s[:, 1, 0]).max() < 0.01
    assert Path(thru["out"].network.touchstone.name).suffix == ".s2p"
    st = thru["stats"]
    record_property("cells", st["cells"] + st["pml_cells"])
    record_property("runtime_s_per_excitation", [round(w, 1) for w in thru["walls"]])
    record_property("peak_rss_mb", round(thru["peak_mb"], 1))
    record_property("est_memory_gb", st["est_memory_gb"])
    assert thru["out"].convergence_log is not None
    assert net.provenance is not None and net.provenance.solver == "openems"
