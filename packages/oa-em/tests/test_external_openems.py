# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""End to end on a real openEMS (nightly): board region -> job (with budget) -> S-parameters.

Uses ``oa_jobs.LocalJobService`` with a ``tools.toml``-style tool table. openEMS is found by
``oa_em_openems.discover`` (``OA_OPENEMS_PYTHON`` / ``OA_OPENEMS_HOME``, ``tools.toml`` or the
project-local ``.oa/tools``); put the openEMS folder on ``PATH`` so the ``external`` marker does
not skip the tests (see the oa-em-openems README).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from em_helpers import run_job
from oa_em_openems import discover
from oa_geometry import BoardGeometry
from oa_impedance.service import ImpedanceServiceImpl
from oa_jobs import Budget
from oa_results.network import NetworkResult
from oa_testkit import builders as bld

pytestmark = [pytest.mark.external("openems"), pytest.mark.slow]

REPO = Path(__file__).resolve().parents[3]
LENGTH = 23e-3  # region 1 .. 24 mm of the 25 mm microstrip board


@pytest.fixture(scope="module")
def tools() -> dict[str, dict[str, str]]:
    found = discover(cwd=REPO)
    if found is None:
        pytest.skip("openEMS >= 0.37 not found (see packages/oa-em-openems/README.md)")
    return {"openems-python": {"runner": "local", "path": str(found.python)}}


def _bem(cu_t: float, freqs: tuple[float, ...]) -> tuple[float, np.ndarray]:
    geo = BoardGeometry.build(bld.microstrip_board(cu_t=cu_t))
    seg = geo.segments("SIG")[0]
    xs = geo.cross_section_at(seg, 12e-3, include_aggressors=False)
    res = ImpedanceServiceImpl().solve(xs, freqs=list(freqs), method="bem")
    assert res.params.z0 is not None
    return (
        float(np.asarray(res.params.z0.array).ravel()[0]),
        np.asarray(res.params.loss_db_per_m.array).ravel(),
    )


def _line_reference(freqs: tuple[float, ...]) -> tuple[float, np.ndarray]:
    """BEM Z0 and RLGC-cascade loss per metre [dB/m] of the board's microstrip.

    openEMS models the 35 um copper as a zero-thickness conducting sheet, so Z0 is compared with
    the BEM thin-strip limit (1 um); the loss reference uses the real 35 um section.
    """
    return _bem(1e-6, freqs)[0], _bem(35e-6, freqs)[1]


@pytest.fixture(scope="module")
def thru(tools: dict[str, dict[str, str]], tmp_path_factory: pytest.TempPathFactory) -> Any:
    cfg = {
        "region": {"nets": ["SIG"], "bbox": [1e-3, -3e-3, 24e-3, 3e-3]},
        "frequency": {"f_min": 0, "f_max": 10e9, "n_points": 101},
        "refinement_check": True,
    }
    budget = Budget(max_runtime_s=1200, max_memory_gb=8, allow_external=True)
    return run_job(
        tmp_path_factory.mktemp("thru"), bld.microstrip_board(), cfg, tools=tools, budget=budget
    )


def test_microstrip_thru_matches_the_analytic_line(thru: Any, record_property: Any) -> None:
    bundle, _status, wall = thru
    net = bundle.items[0].data
    assert isinstance(net, NetworkResult)
    f = np.asarray(net.freqs.array)
    s = np.asarray(net.s.array)
    z0_ref, loss_pm = _line_reference((5e9, 10e9))
    k10 = int(np.argmin(np.abs(f - 10e9)))
    il10 = -20 * np.log10(abs(s[k10, 1, 0]))
    il_ref = loss_pm[1] * LENGTH
    record_property("IL_10GHz_dB", float(il10))
    record_property("IL_RLGC_10GHz_dB", float(il_ref))
    record_property("wall_s", float(wall))
    assert il10 == pytest.approx(il_ref, abs=0.25)  # ~0.2 dB per 25 mm of line
    sel = (f >= 1e9) & (f <= 10e9)
    s11 = 20 * np.log10(np.abs(s[sel, 0, 0]))
    assert s11.max() < -25.0
    # the port impedance table (modal Z0 of the TL probe) vs BEM
    table = next(i.data for i in bundle.items if i.id == "port_z")
    cols = [c.name for c in table.columns]  # type: ignore[union-attr]
    z = np.array([r[cols.index("P1.z_re")] for r in table.inline])  # type: ignore[union-attr]
    z0_meas = float(np.median(z[len(z) // 4 :]))
    record_property("Z0_ohm", z0_meas)
    record_property("Z0_BEM_ohm", z0_ref)
    assert z0_meas == pytest.approx(z0_ref, rel=0.03)


def test_microstrip_bundle_quality_and_convergence(thru: Any) -> None:
    bundle, status, _wall = thru
    net = bundle.items[0].data
    assert bundle.status in ("ok", "warning"), [d.message for d in bundle.diagnostics]
    assert not any(
        d.code in ("EM.NOT_CONVERGED", "EM.MESH_NOT_CONVERGED") for d in bundle.diagnostics
    )
    assert net.quality.verdict == "pass"  # type: ignore[union-attr]
    assert net.touchstone is not None  # type: ignore[union-attr]
    flags = {f.code: f for f in net.validity_flags}  # type: ignore[union-attr]
    assert flags["converged"].ok and "3d_full_wave" in flags["accuracy"].message
    assert status.state == "succeeded"
    s = np.asarray(net.s.array)  # type: ignore[union-attr]
    assert np.linalg.svd(s, compute_uv=False).max() < 1.01


def test_via_transition_plausible_passive_and_timed(
    tools: dict[str, dict[str, str]], tmp_path: Path, record_property: Any
) -> None:
    cfg = {
        "region": {"nets": ["SIG"], "bbox": [9e-3, -2e-3, 16e-3, 2e-3]},
        "frequency": {"f_min": 0, "f_max": 40e9, "n_points": 201},
        "fix_quality": True,
    }
    budget = Budget(max_runtime_s=3000, max_memory_gb=8)
    bundle, _status, wall = run_job(
        tmp_path, bld.via_transition_board(), cfg, tools=tools, budget=budget
    )
    net = bundle.items[0].data
    assert isinstance(net, NetworkResult)
    f = np.asarray(net.freqs.array)
    s = np.asarray(net.s.array)
    s21 = 20 * np.log10(np.abs(s[:, 1, 0]))
    s11 = 20 * np.log10(np.abs(s[:, 0, 0]))
    for k in (len(f) // 8, len(f) // 2, -1):
        record_property(f"S21_dB_{f[k] / 1e9:.0f}GHz", float(s21[k]))
        record_property(f"S11_dB_{f[k] / 1e9:.0f}GHz", float(s11[k]))
    record_property("wall_s", float(wall))
    record_property("quality", net.quality.model_dump(mode="json"))
    assert net.touchstone is not None and net.touchstone.name.endswith(".s2p")
    assert s21[f <= 10e9].min() > -3.0  # 25 mm of line plus one via: modest loss
    assert s21[-1] < s21[len(f) // 8] + 0.5  # loss grows with frequency
    assert s11[f <= 10e9].max() < -10.0
    assert np.linalg.svd(s, compute_uv=False).max() <= 1.0 + 1e-3  # passive after the gate
    assert net.quality.verdict in ("pass", "warn")
    assert bundle.status in ("ok", "warning")
    assert any(d.code == "EM.RUNTIME" for d in bundle.diagnostics)
