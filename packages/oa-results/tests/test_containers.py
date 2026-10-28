# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

import json
from typing import Any, cast

import numpy as np
import pytest

pytest.importorskip("oa_model")

from oa_model.arrays import Floats, NDArray
from oa_results import (
    ArtifactOnly,
    ArtifactRef,
    ColumnSpec,
    NetworkResult,
    PortMapEntry,
    Provenance,
    QualityCriteria,
    QualityReport,
    ResultBundle,
    ResultItem,
    Scalar,
    ScalarSet,
    Severity,
    SolverProvenance,
    TableResult,
    ValidityFlag,
    Violation,
    ViolationSet,
)


def _d(x: object) -> dict[str, Any]:
    return cast(dict[str, Any], x)


def _quality() -> QualityReport:
    return QualityReport(
        verdict="pass",
        pqm=99.9,
        rqm=99.9,
        cqm=95.0,
        max_singular_value=0.99,
        worst_passivity_freq=None,
        reciprocity_max_err=0.0,
        dc="present",
        f_min=1e6,
        f_max=1e9,
        n_freqs=3,
        criteria=QualityCriteria(),
    )


def _network() -> NetworkResult:
    s = np.zeros((3, 2, 2), dtype=complex)
    s[:, 0, 1] = s[:, 1, 0] = 0.9
    return NetworkResult(
        freqs=Floats([1e6, 1e8, 1e9]),
        s=NDArray(s),
        z0=NDArray(np.array([50.0, 50.0], dtype=complex)),
        port_names=("P1", "P2"),
        port_map=(PortMapEntry(port="P1", nets=("SIG",)), PortMapEntry(port="P2", nets=("SIG",))),
        quality=_quality(),
        accuracy_class="validated",
        validity_flags=(ValidityFlag(code="quasi_static_fmax", ok=True),),
        provenance=SolverProvenance(
            solver="openems", solver_version="0.0.37", inputs_hash="ab" * 32
        ),
    )


def _violation(i: int = 0, sev: Severity = "error") -> Violation:
    return Violation(
        rule_id="RP-001",
        severity=sev,
        message="Trace crosses a split.",
        nets=("CLK",),
        layers=("L1",),
        items=(f"k:{i}",),
        location=(0.001, 0.002),
        measured=0.2e-3,
        limit=0.1e-3,
        unit="m",
        fix_hint="Stitch the split.",
        fingerprint=f"fp{i}",
    )


def test_network_summary_is_bounded_json() -> None:
    net = _network()
    s = net.summary()
    json.dumps(s)
    assert s["kind"] == "network"
    assert s["accuracy_class"] == "validated"
    assert s["provenance"] == {
        "solver": "openems",
        "solver_version": "0.0.37",
        "inputs_hash": "ab" * 32,
    }
    assert isinstance(s["digest"], str) and len(s["digest"]) <= 240
    assert len(json.dumps(s)) <= 4000
    assert s["validity_flags"] == [{"code": "quasi_static_fmax", "ok": True, "message": ""}]


def test_summary_respects_max_chars() -> None:
    vs = ViolationSet.from_violations(("RP-001",), tuple(_violation(i) for i in range(500)))
    small = vs.summary(max_chars=600)
    assert len(json.dumps(small)) <= 600
    assert small["stats"] == {"truncated": True}
    big = vs.summary()
    assert len(json.dumps(big)) <= 4000
    assert _d(big["stats"])["counts"] == {"info": 0, "warning": 0, "error": 500}


def test_violation_carries_ai_fields() -> None:
    v = _violation()
    s = v.summary()
    assert s["rule_id"] == "RP-001"
    assert s["location"] == [0.001, 0.002]
    assert s["items"] == ["k:0"]
    assert s["measured"] == 0.2e-3 and s["limit"] == 0.1e-3
    assert s["remediation"] == "Stitch the split."
    assert "measured" in v.digest()


def test_bundle_summary_and_alias() -> None:
    table = TableResult(columns=(ColumnSpec(name="x", dtype="f8"),), inline=((1.0,),))
    art = ArtifactRef(sha256="0" * 64, media_type="text/plain", name="log", size=3)
    bundle = ResultBundle(
        id="01J0000000000000000000000",
        status="ok",
        summary="two items",
        items=(
            ResultItem(id="n", title="S", kind="network", data=_network()),
            ResultItem(id="t", title="T", kind="table", data=table),
            ResultItem(id="a", title="A", kind="artifact", data=ArtifactOnly(artifact=art)),
            ResultItem(
                id="s",
                title="Sc",
                kind="scalars",
                data=ScalarSet(values=(Scalar(name="z0", value=50.0, unit="ohm"),)),
            ),
        ),
        provenance=Provenance(
            analysis="em.sparams",
            analysis_version="1",
            board_hash="b",
            physics_hash=None,
            setup_hash="s",
            config={},
            started_utc="2026-01-01T00:00:00Z",
            finished_utc="2026-01-01T00:00:01Z",
            inputs_hash="c" * 64,
        ),
        accuracy_class="screening",
    )
    assert bundle.model_dump(by_alias=True)["schema"] == "oa-result/1"
    s = bundle.summary()
    json.dumps(s)
    assert _d(s["stats"])["n_items"] == 4
    assert _d(s["provenance"])["inputs_hash"] == "c" * 64
    again = ResultBundle.model_validate(bundle.model_dump(by_alias=True))
    assert again == bundle


def test_item_kind_must_match_data() -> None:
    sc = ScalarSet(values=())
    with pytest.raises(ValueError, match="does not match"):
        ResultItem(id="x", title="x", kind="network", data=sc)


def test_table_needs_rows_or_inline() -> None:
    with pytest.raises(ValueError, match="rows"):
        TableResult(columns=())


def test_to_skrf_roundtrip() -> None:
    net = _network().to_skrf()
    assert net.s.shape == (3, 2, 2)
    assert list(net.port_names) == ["P1", "P2"]
