# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ground truth from the WP-102 synthetic boards (``tests/boards/kicad10/*/expected.yaml``).

Skipped when the boards are absent. The expected-values format is YAML (schema
``expected.schema.json``), not TOML as the task text first suggested.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from oa_ingest_kicad import KiCadReader
from oa_model import Board, dumps_board, loads_board, validate_board

ROOT = Path(__file__).resolve().parents[3] / "tests" / "boards" / "kicad10"
NAMES = sorted(p.parent.name for p in ROOT.glob("*/expected.yaml")) if ROOT.is_dir() else []

if not NAMES:
    pytest.skip("synthetic boards (WP-102) not present", allow_module_level=True)


def _load(name: str) -> tuple[Board, dict]:
    exp = yaml.safe_load((ROOT / name / "expected.yaml").read_text(encoding="utf-8"))
    return KiCadReader().read(ROOT / name / "board.kicad_pcb"), exp


@pytest.fixture(scope="module", params=NAMES)
def case(request: pytest.FixtureRequest) -> tuple[Board, dict]:
    return _load(request.param)


def test_valid_and_no_errors(case: tuple[Board, dict]) -> None:
    b, _ = case
    validate_board(b, strict=True)
    bad = [d for d in b.diagnostics if d.severity != "info"]
    assert not bad, bad[:3]


def test_roundtrip(case: tuple[Board, dict]) -> None:
    b, _ = case
    assert loads_board(dumps_board(b)) == b


def test_counts(case: tuple[Board, dict]) -> None:
    b, exp = case
    c = exp["counts"]
    assert len(b.nets) == c["nets"]
    assert len(b.pads) == c["pads"]
    assert sum(v.kind == "through" for v in b.vias) == c["vias"].get("through", 0)
    for k in ("blind", "buried", "micro"):
        assert sum(v.kind == k for v in b.vias) == c["vias"].get(k, 0), k
    assert len(b.tracks) == c["tracks"] + c.get("arcs", 0)
    assert len([z for z in b.zones if z.keepout is None]) == c["zones"]
    assert len([z for z in b.zones if z.keepout is not None]) == c.get("rule_areas", 0)
    assert len(b.components) == c["footprints"]
    assert sum(lyr.kind == "conductor" for lyr in b.stackup.layers) == c["copper_layers"]


def test_stackup_z_to_1nm(case: tuple[Board, dict]) -> None:
    b, exp = case
    want = exp["stackup"]["layers"]
    got = {lyr.id: lyr for lyr in b.stackup.layers}
    assert [lyr.id for lyr in b.stackup.layers] == [w["id"] for w in want]
    for w in want:
        lyr = got[w["id"]]
        assert lyr.source_name == w["source_name"]
        assert lyr.kind == w["kind"]
        for f in ("thickness", "z_bottom", "z_top"):
            assert getattr(lyr, f) == pytest.approx(w[f], abs=1e-9), (w["id"], f)
    assert b.stackup.total_thickness == pytest.approx(exp["stackup"]["board_thickness"], abs=1e-9)
    assert b.stackup.known


def test_nets_classes_pairs(case: tuple[Board, dict]) -> None:
    b, exp = case
    assert sorted(n.id for n in b.nets) == sorted(exp["nets"])
    assert sorted(c.name for c in b.net_classes) == sorted(
        {"Default", *[c["name"] if isinstance(c, dict) else c for c in exp.get("net_classes", [])]}
    ) or not exp.get("net_classes")
    pairs = {d.id: (d.p_net, d.n_net, d.origin) for d in b.diff_pairs}
    for d in exp.get("diff_pairs", []):
        assert pairs[d["id"]] == (d["p_net"], d["n_net"], d["origin"])
    assert set(pairs) == {d["id"] for d in exp.get("diff_pairs", [])}


def test_v9_matches_v10_copper() -> None:
    if "microstrip_v9" not in NAMES or "microstrip" not in NAMES:
        pytest.skip("variants missing")
    b9, _ = _load("microstrip_v9")
    b10, _ = _load("microstrip")
    assert b9.meta.source_version == "20241229"
    assert [(t.net, t.layer, t.width, t.path) for t in b9.tracks] == [
        (t.net, t.layer, t.width, t.path) for t in b10.tracks
    ]
    assert [n.id for n in b9.nets] == [n.id for n in b10.nets]


def test_via_transition_backdrill_and_kinds() -> None:
    if "via_transition" not in NAMES:
        pytest.skip("board missing")
    b, exp = _load("via_transition")
    g = exp["geometry"]["via_transition"]
    se2 = next(v for v in b.vias if v.net == "SE2")
    bd = g["single_ended_backdrilled"]["backdrill"]
    (got,) = se2.backdrills
    assert got.side == bd["side"]
    assert got.last_removed_layer == bd["end_layer"]  # the end layer is itself cut
    assert got.diameter == pytest.approx(bd["size"], abs=1e-9)
    assert got.must_not_cut == "L6"  # IPC-2581 MUST_NOT_CUT = In5.Cu
    se1 = next(v for v in b.vias if v.net == "SE1")
    assert (se1.start_layer, se1.end_layer, se1.kind) == ("L1", "L8", "through")
    for net, (s, e, kind) in {
        "BLIND1": ("L1", "L3", "blind"),
        "BURIED1": ("L3", "L6", "buried"),
        "UVIA1": ("L1", "L2", "micro"),
    }.items():
        v = next(x for x in b.vias if x.net == net)
        assert (v.start_layer, v.end_layer, v.kind) == (s, e, kind)


def test_keepout_rule_areas_have_no_net() -> None:
    for name in NAMES:
        b, exp = _load(name)
        for z in b.zones:
            if z.keepout is not None:
                assert z.net is None
        if exp["counts"].get("rule_areas"):
            return
