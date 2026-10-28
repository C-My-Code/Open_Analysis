# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""AI/engine contract of the erc analysis on the combined board, plus rule-specific checks."""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import jsonschema
import pytest
from erc_helpers import INTENTS, load_board, run_board, services

from oa_erc import (
    BUILTIN_RULES,
    ErcAnalysis,
    TypedRemediation,
    apply_dru,
    apply_waivers,
    propose_dru,
    run_rules,
    sort_violations,
)
from oa_erc.dru import DruWriteRefused
from oa_erc.summary import compact_summary
from oa_framework.registry import Registry
from oa_geometry import BoardGeometry
from oa_model.setup import AnalysisSetup, Waiver
from oa_results.bundle import ResultBundle
from oa_results.io.sarif import sarif_document
from oa_results.violations import ViolationSet
from oa_testkit.harness import run_inline

SARIF_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "oa-results"
    / "tests"
    / "io"
    / "data"
    / "sarif-schema-2.1.0.json"
)


def _bundle(config: dict | None = None) -> ResultBundle:  # type: ignore[type-arg]
    setup = AnalysisSetup(
        schema="oa-setup/1",
        name="erc",
        analysis="erc",
        config={"as_of": "2026-10-28", **(config or {})},
    )
    reg = Registry.for_tests(analyses=[ErcAnalysis], services=services_map())
    return run_inline(
        ErcAnalysis,
        load_board("combined"),
        setup,
        reg,
        services=services_map(),
        geometry=BoardGeometry.build,
    )


def services_map() -> dict[str, object]:
    return {"impedance": services().get("impedance")}


@pytest.fixture(scope="module")
def bundle() -> ResultBundle:
    return _bundle()


@pytest.fixture(scope="module")
def vset(bundle: ResultBundle) -> ViolationSet:
    data = bundle.items[0].data
    assert isinstance(data, ViolationSet)
    return data


def test_analysis_matches_direct_run(vset: ViolationSet) -> None:
    direct = sort_violations(run_board("combined"))
    assert [v.fingerprint for v in vset.violations] == [v.fingerprint for v in direct]
    assert len(vset.rules_run) == len(BUILTIN_RULES)


def test_every_violation_has_remediation(vset: ViolationSet) -> None:
    assert vset.violations
    for v in vset.violations:
        assert v.remediation is not None and v.fix_hint == v.remediation.hint
        rem = TypedRemediation.from_model(v.remediation)
        assert len(rem.actions) >= 1 and len(rem.evidence) <= 16
        assert rem.summary == v.fix_hint


def test_threshold_rules_set_measured_limit_unit(vset: ViolationSet) -> None:
    for v in vset.violations:
        if v.rule_id == "RP-003":
            continue
        assert v.measured is not None and v.limit is not None and v.unit, v.rule_id
        assert math.isfinite(v.measured)


def test_im002_evidence_has_accuracy_class(vset: ViolationSet) -> None:
    im = [v for v in vset.violations if v.rule_id == "IM-002"]
    assert im
    for v in im:
        assert v.remediation is not None
        ev = TypedRemediation.from_model(v.remediation).evidence
        assert ev["accuracy_class"] == "validated" and ev["method"] == "bem"


def test_applies_via_distinguishes_board_edit_and_dru(vset: ViolationSet) -> None:
    seen: dict[str, set[str]] = {}
    for v in vset.violations:
        assert v.remediation is not None
        for a in TypedRemediation.from_model(v.remediation).actions:
            seen.setdefault(v.rule_id, set()).add(a.applies_via)
    assert "board_edit" in seen["RP-001"] and "dru_rule" not in seen["RP-001"]
    assert {"board_edit", "dru_rule"} <= seen["ED-002"]
    assert {"board_edit", "dru_rule"} <= seen["DP-002"]


def test_summary_within_2kib(vset: ViolationSet) -> None:
    s = compact_summary(vset)
    assert len(json.dumps(s, separators=(",", ":")).encode()) <= 2048
    assert s["by_rule"]["RP-001"] == 1
    assert len(s["worst"]) == 5


def test_order_identical_across_runs(vset: ViolationSet) -> None:
    again = _bundle().items[0].data
    assert isinstance(again, ViolationSet)
    assert again.model_dump_json() == vset.model_dump_json()


def test_sarif_validates(vset: ViolationSet) -> None:
    doc = sarif_document(vset, load_board("combined"))
    schema = json.loads(SARIF_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(doc, schema)
    assert len(doc["runs"][0]["results"]) == len(vset.violations)


def test_waiver_by_fingerprint_rule_nets_and_expiry(vset: ViolationSet) -> None:
    vs = vset.violations
    target = next(v for v in vs if v.rule_id == "RP-001")
    out, diags = apply_waivers(
        vs,
        [
            Waiver(fingerprint=target.fingerprint, reason="slot is intentional"),
            Waiver(rule_id="IM-002", nets=("MS_*",), reason="prototype", expires="2099-01-01"),
            Waiver(rule_id="ED-002", reason="old", expires="2020-01-01"),
        ],
        date(2026, 10, 28),
    )
    waived = {(v.rule_id, v.nets) for v in out if v.waived is not None}
    assert ("RP-001", target.nets) in waived
    assert any(r == "IM-002" and n[0].startswith("MS_") for r, n in waived)
    assert not any(r == "ED-002" for r, _ in waived)  # expired waiver does not apply
    assert [d.code for d in diags] == ["ERC.WAIVER_EXPIRED"]


def test_severity_override_and_rule_selection() -> None:
    b = _bundle({"rules": ["E2", "RP-007"], "severity": {"E2": "info"}})
    data = b.items[0].data
    assert isinstance(data, ViolationSet)
    assert data.rules_run == ("RP-001", "RP-007")
    assert {v.rule_id: v.severity for v in data.violations} == {"RP-001": "info", "RP-007": "error"}


def test_bad_rule_config_is_a_setup_error() -> None:
    from oa_model.errors import SetupError

    with pytest.raises(SetupError):
        _bundle({"rule_config": {"RP-001": {"no_such_field": 1}}})


def test_rule_metadata_complete() -> None:
    ids = [r.id for r in BUILTIN_RULES]
    assert len(ids) == len(set(ids))
    for r in BUILTIN_RULES:
        assert r.explanation and r.references and r.title
        r.Config()  # every field has a default
        for f in r.Config.model_fields.values():
            assert f.description, (r.id, f)
    assert {a for r in BUILTIN_RULES for a in r.aliases} >= {"E1", "E2", "E3", "E4", "E5", "E6"}
    assert "E11" in {a for r in BUILTIN_RULES for a in r.aliases}


def test_via_stub_resonance_within_1_percent() -> None:
    v = next(v for v in run_board("via_transition") if v.rule_id == "VI-001" and v.nets == ("SE1",))
    assert v.remediation is not None
    ev = TypedRemediation.from_model(v.remediation).evidence
    expect = 299_792_458.0 / (4 * v.measured * math.sqrt(4.3))  # type: ignore[operator]
    assert ev["stub_resonance_hz"] == pytest.approx(expect, rel=0.01)


def test_cavity_resonance_40x30_plane_pair() -> None:
    board = load_board("power_plane")
    f10 = 299_792_458.0 / (2 * 0.04 * math.sqrt(4.3))
    assert f10 == pytest.approx(1.807e9, rel=1e-3)
    vs = run_rules(
        board,
        services=services(),
        rule_ids=["EM-011"],
        rule_config={"EM-011": {"clock_frequencies": [f10 / 2]}},
    )
    modes = {}
    for v in vs:
        assert v.remediation is not None
        ev = TypedRemediation.from_model(v.remediation).evidence
        modes[tuple(ev["mode"])] = v.measured  # type: ignore[arg-type]
    assert modes[(1, 0)] == pytest.approx(f10, rel=0.005)
    assert not run_rules(board, services=services(), rule_ids=["EM-011"])  # no clock, no report


def test_dru_proposal_is_dry_run_by_default(tmp_path: Path, vset: ViolationSet) -> None:
    target = tmp_path / "board.kicad_dru"
    prop = propose_dru(vset.violations, target, existing=None)
    assert prop.dry_run and prop.rule_names
    assert "(constraint edge_clearance (min 1mm))" in prop.text
    assert "OA_ED-002_edge_clearance_SIG_BOARDEDGE" in prop.rule_names
    assert apply_dru(prop) is None and not target.exists()
    with pytest.raises(DruWriteRefused) as exc:
        apply_dru(prop, dry_run=False)
    assert exc.value.code == "WRITES_DISABLED"
    assert apply_dru(prop, allow_writes=True, dry_run=False) == target
    assert target.read_text(encoding="utf-8") == prop.text
    again = propose_dru(vset.violations, target, existing=target.read_text(encoding="utf-8"))
    assert not again.rule_names  # idempotent
    stale = propose_dru(vset.violations, target, existing=None)
    with pytest.raises(DruWriteRefused) as exc2:
        apply_dru(stale, allow_writes=True, dry_run=False)
    assert exc2.value.code == "STALE_BASE"


def test_intent_marked_nets_limit_scope() -> None:
    from oa_model.setup.intent import DesignIntent, NetSelector, SignalIntent

    intent = DesignIntent(signals=(SignalIntent(select=NetSelector(names=("SIG_SLOT",))),))
    vs = run_rules(load_board("combined"), intent=intent, services=services())
    assert {n for v in vs for n in v.nets} <= {"SIG_SLOT", "GND", "+1V0", "+3V3"}
    assert INTENTS  # helper import used
