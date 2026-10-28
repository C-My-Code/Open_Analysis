# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# mypy: disable-error-code="union-attr,arg-type,import-untyped"
"""Backdrill conventions end to end (WP-208 goal 3).

KiCad lists a backdrill as ``(layers "B.Cu" "In6.Cu")`` and drills the end layer too. The KiCad
reader, the IPC-2581 reader and ``oa_geometry.via_stub`` must agree on that, and the copper they
produce must follow it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import ingest_xval as xv
import pytest

from oa_geometry import BoardGeometry
from oa_geometry.vias import barrel_z_range, via_copper_layers
from oa_model import Board, Via
from oa_model.index import BoardIndex

BOARDS = ("via_transition", "combined")


def _se(board: Board, net: str) -> Via:
    (v,) = [v for v in board.vias if v.net == net]
    return v


def _boards(name: str) -> dict[str, Board]:
    d = xv.SYNTHETIC / name
    return {
        "kicad": xv.read_native(d / "board.kicad_pcb"),
        "ipc2581": xv.read_ipc(d / "board.ipc2581.xml"),
    }


@pytest.mark.parametrize("name", BOARDS)
def test_readers_agree_on_the_backdrill_layers(name: str) -> None:
    exp = xv.load_expected(xv.SYNTHETIC / name)
    bd = exp["geometry"]["via_transition"]["single_ended_backdrilled"]["backdrill"]
    assert bd["end_layer_cut"] is True
    for reader, board in _boards(name).items():
        (got,) = _se(board, "SE2").backdrills
        assert got.side == bd["side"], reader
        # the layer named as the drill end is itself removed ...
        assert got.last_removed_layer == bd["end_layer"] == "L7", reader
        # ... and the next layer inward is the first one that must keep its barrel
        assert got.must_not_cut == "L6", reader
        assert _se(board, "SE1").backdrills == (), reader


@pytest.mark.parametrize("name", BOARDS)
def test_via_stub_matches_expected_and_is_equal_across_readers(name: str) -> None:
    exp = xv.load_expected(xv.SYNTHETIC / name)["geometry"]["via_transition"]
    want_se2 = exp["single_ended_backdrilled"]["backdrill"]["residual_barrel_below_last_used_layer"]
    want_se1 = exp["single_ended"]["stub_length"]
    stubs: dict[str, tuple[float, float]] = {}
    for reader, board in _boards(name).items():
        geo = BoardGeometry.build(board)
        se1 = geo.via_stub(_se(board, "SE1"), ["L1", "L6"])
        se2 = geo.via_stub(_se(board, "SE2"), ["L1", "L6"])
        assert se1 == pytest.approx(want_se1, abs=1e-9), reader
        assert se2 == pytest.approx(want_se2, abs=1e-9), reader
        stubs[reader] = (se1, se2)
    assert stubs["kicad"] == pytest.approx(stubs["ipc2581"], abs=1e-9)


@pytest.mark.parametrize("name", BOARDS)
def test_barrel_ends_at_the_top_of_the_end_layer(name: str) -> None:
    for reader, board in _boards(name).items():
        idx = BoardIndex.of(board)
        l7 = idx.layer("L7")
        z_bottom, z_top = barrel_z_range(board, _se(board, "SE2"))
        assert z_bottom == pytest.approx(l7.z_top, abs=1e-12), reader
        assert z_top == pytest.approx(idx.layer("L1").z_top, abs=1e-12), reader
        # the un-drilled twin keeps the whole barrel down to the bottom of L8
        b2, _ = barrel_z_range(board, _se(board, "SE1"))
        assert b2 == pytest.approx(idx.layer("L8").z_bottom, abs=1e-12), reader


@pytest.mark.parametrize("name", BOARDS)
def test_via_pads_follow_the_drill(name: str) -> None:
    for reader, board in _boards(name).items():
        idx = BoardIndex.of(board)
        v = _se(board, "SE2")
        ps = next(p for p in board.padstacks if p.id == v.padstack)
        layers = via_copper_layers(idx, v, ps)
        assert layers == ("L1", "L2", "L3", "L4", "L5", "L6"), reader
        geo = BoardGeometry.build(board)
        assert geo.copper("L6", "SE2").area > 0, reader
        # a layer the drill removed has no via pad: the via contributes nothing there
        barrel = geo.drills()
        assert any(math.dist(dr.position, v.position) < 1e-9 for dr in barrel), reader


def test_drilled_layers_lose_exactly_one_via_pad_against_expected() -> None:
    """expected.yaml (pcbnew) keeps the pad on the drilled layers; the readers drop it."""
    d = xv.SYNTHETIC / "via_transition"
    exp = xv.load_expected(d)["copper_area"]["layers"]
    for reader, board in _boards("via_transition").items():
        v = _se(board, "SE2")
        ps = next(p for p in board.padstacks if p.id == v.padstack)
        geo = BoardGeometry.build(board)
        for lid in ("L7", "L8"):
            pad = next(layer.pad for layer in ps.layers if layer.layer == lid and layer.pad)
            pad_area = math.pi * (pad.diameter / 2) ** 2
            got = geo.copper(lid, "SE2").area
            assert got + pad_area == pytest.approx(exp[lid]["SE2"], rel=2e-3), (reader, lid)


@pytest.mark.kicad
def test_pcbnew_cuts_the_end_layer_too(tmp_path: Path) -> None:
    """The oracle: pcbnew's own ``IsBackdrilledOrPostMachined`` on the real board file."""
    tools = xv.kicad_tools()
    py = tools.require_python()
    probe = Path(__file__).with_name("pcbnew_backdrill_probe.py")
    res = tools.run(py, [probe, xv.SYNTHETIC / "via_transition" / "board.kicad_pcb"], timeout=300)
    rows = json.loads([ln for ln in res.stdout.splitlines() if ln.startswith("[")][-1])
    assert len(rows) == 1 and rows[0]["net"] == "SE2"
    cut = {name for name, on in rows[0]["cut"].items() if on}
    board = xv.read_native(xv.SYNTHETIC / "via_transition" / "board.kicad_pcb")
    (bd,) = _se(board, "SE2").backdrills
    names = {layer.id: layer.source_name for layer in xv.conductor_layers(board)}
    order = [layer.id for layer in xv.conductor_layers(board)]
    removed = {names[lid] for lid in order[order.index(bd.last_removed_layer) :]}
    assert cut == removed == {"In6.Cu", "B.Cu"}
