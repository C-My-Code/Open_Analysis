# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-208: native KiCad reader vs IPC-2581 reader vs expected.yaml on the WP-102 boards."""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path

import ingest_xval as xv
import pytest

from oa_model import Board
from oa_model.io import dump_board, load_board
from oa_testkit.golden import assert_close_to_golden

NAMES = [d.name for d in xv.synthetic_boards()]
pytestmark = pytest.mark.skipif(not NAMES, reason="WP-102 synthetic boards not present")


def _fails(findings: list[xv.Finding]) -> list[str]:
    return [
        f"{f.source}/{f.reader} {f.check} [{f.key}] native={f.native} other={f.other} "
        f"delta={f.delta} tol={f.tol}"
        for f in findings
        if f.status == "fail"
    ]


@pytest.mark.parametrize("name", NAMES)
def test_native_reader_matches_expected_yaml(
    name: str, synthetic_results: dict[str, xv.BoardResult]
) -> None:
    """Counts exact, z <= 1 nm, per-net per-layer copper area <= 0.1 %."""
    j = synthetic_results[name].native_expected
    assert j is not None
    assert not _fails(j.findings)
    exp = xv.load_expected(xv.SYNTHETIC / name)
    n_area = sum(len(nets) for nets in exp["copper_area"]["layers"].values())
    # the check is not vacuous: every net/layer of expected.yaml was compared
    assert sum(f.check == "copper_area" for f in j.findings) == n_area
    assert sum(f.check == "stackup.z_top" for f in j.findings) == len(exp["stackup"]["layers"])


@pytest.mark.parametrize("name", NAMES)
def test_ipc2581_reader_matches_expected_yaml(
    name: str, synthetic_results: dict[str, xv.BoardResult]
) -> None:
    j = synthetic_results[name].ipc_expected
    assert j is not None
    assert not _fails(j.findings)


@pytest.mark.parametrize("name", NAMES)
def test_native_vs_ipc2581_has_no_unexplained_difference(
    name: str, synthetic_results: dict[str, xv.BoardResult]
) -> None:
    j = synthetic_results[name].cross
    assert j is not None
    assert not _fails(j.findings)
    # counts compared exactly: nets, vias, pads, tracks, components, zones
    for check in ("nets.count", "vias.count", "pads.count", "tracks.count", "components.count"):
        rows = [f for f in j.findings if f.check == check]
        assert rows, check
        assert all(f.status == "pass" for f in rows), check


def test_exceptions_file_documents_every_entry(
    synthetic_results: dict[str, xv.BoardResult],
) -> None:
    excs = xv.load_exceptions()
    assert len({e.id for e in excs}) == len(excs)
    for e in excs:
        assert len(e.cause) > 40, e.id
        assert len(e.evidence) > 10, e.id
        assert e.scope in ("synthetic", "demo"), e.id
    used = {
        f.exception
        for r in synthetic_results.values()
        for f in r.all_findings()
        if f.status == "whitelisted"
    }
    stale = {e.id for e in excs if e.scope == "synthetic"} - used
    assert not stale, f"exceptions that no synthetic board needs any more: {sorted(stale)}"
    assert used <= {e.id for e in excs}


def test_whitelist_is_not_hiding_growth(synthetic_results: dict[str, xv.BoardResult]) -> None:
    """Each whitelisted difference must have the documented direction and size."""
    for name, r in synthetic_results.items():
        assert r.cross is not None and r.native is not None
        by_id = {c.refdes: c for c in r.native.components}
        for f in r.cross.findings:
            if f.status != "whitelisted":
                continue
            if f.exception == "ipc-zone-outline-is-fill-hull" and name != "microstrip_v9":
                # a hull of the fill sits inside the drawn outline
                assert float(f.other) <= float(f.native) * (1 + 1e-6), (name, f.key)
            elif f.exception == "ipc-bottom-component-rotation":
                assert by_id[f.key].side == "bottom", (name, f.key)
                assert f.delta == pytest.approx(math.pi, abs=1e-6), (name, f.key)
            elif f.exception == "ipc-bottom-mask-shifts-z":
                assert float(f.other) == pytest.approx(1e-5, abs=1e-9), name
            elif f.exception == "ipc-backdrill-diameter-missing":
                assert float(f.other) <= float(f.native), name
            elif f.exception == "ipc-micro-via-flag-lost":
                assert int(f.native) == sum(v.kind == "micro" for v in r.native.vias)
            elif f.exception == "ipc-v9-export-fill-differs":
                assert abs(f.delta or 0.0) < 0.011, (name, f.key)


def test_ipc_export_of_v9_syntax_file_differs_only_in_the_fill() -> None:
    """microstrip_v9 vs microstrip: identical copper in the files, a 1 % different export."""
    if "microstrip_v9" not in NAMES:
        pytest.skip("variants missing")
    a = xv.read_ipc(xv.SYNTHETIC / "microstrip" / "board.ipc2581.xml")
    b = xv.read_ipc(xv.SYNTHETIC / "microstrip_v9" / "board.ipc2581.xml")
    fills = [
        f for f in xv.compare_cross("microstrip_v9", a, b).findings if f.check == "copper_area"
    ]
    bad = [f for f in fills if f.status == "whitelisted"]
    assert {f.key for f in bad} == {"L2/GND", "L3/GND", "L4/GND"}
    assert all(f.status in ("pass", "whitelisted") for f in fills)


def test_microstrip_and_microstrip_v9_boards_are_equal_except_ids_and_meta() -> None:
    """Acceptance: v9 and v10 syntax give the same Board (modulo item ids, order and meta)."""
    if "microstrip_v9" not in NAMES or "microstrip" not in NAMES:
        pytest.skip("variants missing")
    a = xv.read_native(xv.SYNTHETIC / "microstrip" / "board.kicad_pcb")
    b = xv.read_native(xv.SYNTHETIC / "microstrip_v9" / "board.kicad_pcb")
    assert xv.canonical_board(a, drop=("protection",)) == xv.canonical_board(
        b, drop=("protection",)
    )
    # The one real difference: the v10 file writes (tenting (front yes) (back yes)) on each via,
    # the v9 file carries no tenting token and the reader reads that as "not tented".
    # See docs/change-requests/WP-208-ingest-cross-findings.md finding 4.
    assert all(v.protection.tented_top and v.protection.tented_bottom for v in a.vias)
    assert not any(v.protection.tented_top or v.protection.tented_bottom for v in b.vias)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("reader", ["kicad", "ipc2581"])
def test_oab_round_trip(
    name: str, reader: str, synthetic_results: dict[str, xv.BoardResult], tmp_path: Path
) -> None:
    """Board -> .oab -> Board is lossless for both readers (and the canonical JSON form too)."""
    r = synthetic_results[name]
    board: Board | None = r.native if reader == "kicad" else r.ipc
    assert board is not None
    for suffix in (".oab", ".oab.json"):
        p = tmp_path / f"{name}{suffix}"
        dump_board(board, p)
        back = load_board(p)
        assert back == board, suffix
        assert back.content_hash() == board.content_hash(), suffix


def test_matrix_golden(synthetic_results: dict[str, xv.BoardResult]) -> None:
    """Pass / whitelisted / fail counts per board and comparison are pinned."""
    actual: dict[str, object] = {}
    for name, r in sorted(synthetic_results.items()):
        actual[name] = {
            "native_vs_expected": xv.status_counts(r.native_expected.findings)
            if r.native_expected
            else {},
            "ipc2581_vs_expected": xv.status_counts(r.ipc_expected.findings)
            if r.ipc_expected
            else {},
            "native_vs_ipc2581": xv.status_counts(r.cross.findings) if r.cross else {},
        }
    assert_close_to_golden(
        actual,
        "ingest_cross",
        "synthetic_matrix",
        source=(
            "WP-208 harness run on the WP-102 boards: number of passed / whitelisted / failed "
            "findings. Regenerate with --update-golden after changing the harness or a reader."
        ),
        rtol=0.0,
    )


def test_csv_diff_report_has_one_row_per_finding(
    synthetic_results: dict[str, xv.BoardResult],
) -> None:
    findings = [f for r in synthetic_results.values() for f in r.all_findings()]
    text = xv.findings_to_csv(findings)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == len(findings) > 1000
    assert tuple(rows[0]) == xv.CSV_FIELDS
    assert {r["status"] for r in rows} <= set(xv.STATUSES)
    assert all(r["cause"] for r in rows if r["status"] == "whitelisted")


# ---------------------------------------------------------------------------- sensitivity
def _micro() -> tuple[Board, Board]:
    d = xv.SYNTHETIC / "via_transition"
    return xv.read_native(d / "board.kicad_pcb"), xv.read_ipc(d / "board.ipc2581.xml")


def _statuses(j: xv.Judge, check: str) -> set[str]:
    return {f.status for f in j.findings if f.check == check}


def test_harness_detects_injected_faults() -> None:
    """A comparison that cannot fail proves nothing: break the IPC board and expect failures."""
    native, ipc = _micro()
    base = xv.compare_cross("t", native, ipc)
    assert not [f for f in base.findings if f.status == "fail"]

    dropped = ipc.model_copy(update={"tracks": ipc.tracks[1:]})
    assert "fail" in _statuses(xv.compare_cross("t", native, dropped), "tracks.count")

    no_via = ipc.model_copy(update={"vias": ipc.vias[1:]})
    j = xv.compare_cross("t", native, no_via)
    assert "fail" in _statuses(j, "vias.count") | _statuses(j, "vias.match")

    layers = list(ipc.stackup.layers)
    i = next(k for k, layer in enumerate(layers) if layer.id == "L3")
    layers[i] = layers[i].model_copy(update={"z_top": layers[i].z_top + 5e-6})
    shifted = ipc.model_copy(update={"stackup": ipc.stackup.model_copy(update={"layers": layers})})
    assert "fail" in _statuses(xv.compare_cross("t", native, shifted), "stackup.z_top")

    moved = ipc.model_copy(
        update={
            "pads": (
                ipc.pads[0].model_copy(
                    update={"position": (ipc.pads[0].position[0] + 1e-5, ipc.pads[0].position[1])}
                ),
                *ipc.pads[1:],
            )
        }
    )
    j = xv.compare_cross("t", native, moved)
    assert "fail" in _statuses(j, "pads.position") | _statuses(j, "pads.unmatched")

    # without the exception list every known difference is a failure
    assert [
        f for f in xv.compare_cross("t", native, ipc, exceptions=[]).findings if f.status == "fail"
    ]
