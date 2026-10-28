# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``impedance.xsection`` through the inline harness: accuracy, validity and summaries."""

from __future__ import annotations

import _xs
import pytest

from oa_impedance.analysis import KIND, ImpedanceXsection
from oa_model.errors import SetupError
from oa_model.setup import AnalysisSetup
from oa_model.xsection import CrossSection
from oa_results.bundle import ResultBundle
from oa_results.rlgc import RLGCResultSet
from oa_results.scalars import ScalarSet
from oa_testkit import builders
from oa_testkit.harness import run_inline, run_inline_full


def _setup(xs: CrossSection | None, **cfg: object) -> AnalysisSetup:
    config: dict[str, object] = dict(cfg)
    if xs is not None:
        config["xsection"] = xs.model_dump(mode="json", by_alias=True)
    return AnalysisSetup(schema="oa-setup/1", name="xs", analysis=KIND, config=config)  # type: ignore[arg-type,unused-ignore]


def _run(xs: CrossSection, **cfg: object) -> ResultBundle:
    return run_inline(ImpedanceXsection, builders.microstrip_board(), _setup(xs, **cfg))


def test_closed_form_bundle_has_accuracy_validity_provenance() -> None:
    b = _run(_xs.microstrip(1.0, 4.3), freqs=[0.0])
    assert b.status == "ok"
    rl = next(i for i in b.items if i.kind == "rlgc").data
    assert isinstance(rl, RLGCResultSet)
    assert rl.accuracy_class == "screening"
    flag = next(f for f in rl.validity_flags if f.code == "accuracy")
    assert "accuracy_class=closed_form" in flag.message and "tolerance_basis=" in flag.message
    assert b.provenance.analysis == KIND and b.provenance.solver_version
    assert b.provenance.inputs_hash and b.provenance.setup_hash.startswith("sha256:")
    sc = next(i for i in b.items if i.kind == "scalars").data
    assert isinstance(sc, ScalarSet)
    assert any(s.name.startswith("z0@") and 60 < s.value < 80 for s in sc.values)
    assert b.summary_text[:60] in b.digest()
    summ = b.summary()
    assert isinstance(summ, dict) and "digest" in summ


def test_bem_bundle_is_2d_field_with_tolerance_basis() -> None:
    b = _run(_xs.pcb_microstrip(pair=True), freqs=[0.0, 1e9], method="bem")
    rl = next(i for i in b.items if i.kind == "rlgc").data
    assert isinstance(rl, RLGCResultSet)
    assert rl.accuracy_class == "validated"
    flag = next(f for f in rl.validity_flags if f.code == "accuracy")
    assert "2d_field" in flag.message and "golden:impedance_bem" in flag.message
    sc = next(i for i in b.items if i.kind == "scalars").data
    assert isinstance(sc, ScalarSet)
    names = {s.name.split("@")[0] for s in sc.values}
    assert {"z_diff", "z_comm", "z_odd", "z_even", "eps_eff_mode0"} <= names
    stats = b.summary()["stats"]
    assert isinstance(stats, dict) and len(stats["items"]) == 2  # type: ignore[arg-type]


def test_out_of_range_gives_warning_status_and_code() -> None:
    b = _run(_xs.microstrip(0.002, 4.3), method="closed_form")
    assert b.status == "warning"
    assert any(
        d.code in ("IMPEDANCE.EXTRAPOLATED", "RESULT.INVALID_INPUT_RANGE") for d in b.diagnostics
    )
    rl = next(i for i in b.items if i.kind == "rlgc").data
    assert any(not f.ok for f in rl.validity_flags)


def test_missing_xsection_is_a_structured_setup_error() -> None:
    with pytest.raises(SetupError) as ei:
        run_inline(ImpedanceXsection, builders.microstrip_board(), _setup(None))
    diags = ei.value.diagnostics  # type: ignore[attr-defined,unused-ignore]
    assert diags[0].code == "IMPEDANCE.NO_XSECTION" and diags[0].hint


def test_plan_is_one_cacheable_task_and_deterministic() -> None:
    xs = _xs.microstrip(1.0, 4.3)
    r1 = run_inline_full(ImpedanceXsection, builders.microstrip_board(), _setup(xs))
    r2 = run_inline_full(ImpedanceXsection, builders.microstrip_board(), _setup(xs))
    assert len(r1.plan.tasks) == 1 and r1.plan.tasks[0].id == f"xs:{xs.key()}"
    assert r1.outputs == r2.outputs


def test_uses_registered_service_when_available() -> None:
    from oa_impedance.service import ImpedanceServiceImpl

    svc = ImpedanceServiceImpl()
    xs = _xs.pcb_microstrip()
    run_inline(
        ImpedanceXsection,
        builders.microstrip_board(),
        _setup(xs, method="bem"),
        services={"impedance": svc},
    )
    assert svc.stats["bem"] == 1
