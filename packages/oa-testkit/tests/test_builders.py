# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Board builders: validity, determinism, round trip and builder-specific geometry."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from oa_model.board import Board
from oa_model.validate import validate_board
from oa_testkit import builders as bld

BUILDERS: dict[str, Callable[..., Board]] = {
    "microstrip": bld.microstrip_board,
    "stripline": bld.stripline_board,
    "diff_pair": bld.diff_pair_board,
    "cpwg": bld.cpwg_board,
    "split_plane": bld.split_plane_board,
    "via_transition": bld.via_transition_board,
    "power_plane": bld.power_plane_board,
}


@pytest.fixture(params=sorted(BUILDERS))
def builder(request: pytest.FixtureRequest) -> Callable[..., Board]:
    return BUILDERS[request.param]


def test_returns_board_and_strict_valid(builder: Callable[..., Board]) -> None:
    board = builder()
    assert isinstance(board, Board)
    diags = validate_board(board, strict=True)
    assert not [d for d in diags if d.severity == "error"]


def test_deterministic(builder: Callable[..., Board]) -> None:
    a, b = builder(), builder()
    assert a == b
    assert a.content_hash() == b.content_hash()


def test_json_round_trip(builder: Callable[..., Board]) -> None:
    board = builder()
    again = Board.model_validate_json(board.model_dump_json())
    assert again == board
    assert again.content_hash() == board.content_hash()


def test_meta_is_synthetic(builder: Callable[..., Board]) -> None:
    meta = builder().meta
    assert meta.source_tool == "synthetic"


def test_microstrip_parameters() -> None:
    b = bld.microstrip_board(length=10e-3, width=0.5e-3, h=0.1e-3, er=3.5)
    (trk,) = b.tracks
    assert trk.width == 0.5e-3
    assert trk.path[0].end == (10e-3, 0.0)
    d1 = next(layer for layer in b.stackup.layers if layer.id == "D1")
    assert d1.thickness == pytest.approx(0.1e-3)
    mats = {m.name: m for m in b.stackup.materials}
    assert mats["FR4_generic"].model.dk == 3.5
    validate_board(b, strict=True)


def test_stripline_symmetry_and_offset() -> None:
    b = bld.stripline_board()
    z = {layer.id: layer for layer in b.stackup.layers}
    assert b.tracks[0].layer == "L2"
    assert z["D1"].thickness == z["D2"].thickness
    off = bld.stripline_board(h1=0.1e-3, h2=0.3e-3)
    zo = {layer.id: layer for layer in off.stackup.layers}
    assert zo["D1"].thickness < zo["D2"].thickness
    assert {zn.layers for zn in off.zones} == {("L1",), ("L3",)}


def test_diff_pair_nets_and_gap() -> None:
    b = bld.diff_pair_board(width=0.1e-3, gap=0.2e-3)
    (dp,) = b.diff_pairs
    assert (dp.p_net, dp.n_net) == ("DP_P", "DP_N")
    ys = {t.net: t.path[0].start[1] for t in b.tracks}
    assert ys["DP_P"] - ys["DP_N"] == pytest.approx(0.3e-3)  # pitch = width + gap


def test_cpwg_pour_has_gap_and_via_fence() -> None:
    b = bld.cpwg_board(width=0.3e-3, gap=0.15e-3)
    top = next(z for z in b.zones if z.layers == ("L1",))
    (fill,) = top.fills
    assert len(fill.polygons) == 2  # left and right pour
    inner = min(abs(p.exterior.array[:, 1]).min() for p in fill.polygons)
    assert inner == pytest.approx(0.15e-3 + 0.15e-3)
    assert len(b.vias) >= 8
    assert all(v.net == "GND" and v.end_layer == "L2" for v in b.vias)


def test_split_plane_has_slot() -> None:
    b = bld.split_plane_board(length=20e-3, split_gap=1e-3)
    gnd = next(z for z in b.zones if z.net == "GND")
    vdd = next(z for z in b.zones if z.net == "VDD")
    gx = gnd.outline.exterior.array[:, 0].max()
    vx = vdd.outline.exterior.array[:, 0].min()
    assert vx - gx == pytest.approx(1e-3)
    assert 0.0 < gx < 20e-3 < vdd.outline.exterior.array[:, 0].max()


def test_via_transition_structure() -> None:
    b = bld.via_transition_board()
    sig = next(v for v in b.vias if v.net == "SIG")
    assert (sig.start_layer, sig.end_layer, sig.kind) == ("L1", "L4", "through")
    assert {t.layer for t in b.tracks} == {"L1", "L4"}
    assert sum(v.net == "GND" for v in b.vias) == 4
    for z in b.zones:
        assert len(z.fills[0].polygons[0].holes) == 1  # antipad


def test_power_plane_variants() -> None:
    rect = bld.power_plane_board()
    neck = bld.power_plane_board(neck_width=6e-3)
    vdd = lambda b: next(z for z in b.zones if z.net == "VDD")  # noqa: E731
    assert len(vdd(rect).outline.exterior) == 4
    assert len(vdd(neck).outline.exterior) == 12
    assert {c.refdes for c in rect.components} >= {"U1", "U2", "C1", "C4"}
    validate_board(neck, strict=True)
    with pytest.raises(ValueError, match="neck_width"):
        bld.power_plane_board(neck_width=1e-3)


def test_stack_z_contiguous(builder: Callable[..., Board]) -> None:
    st = builder().stackup
    assert st.layers[-1].z_bottom == 0.0
    for upper, lower in zip(st.layers, st.layers[1:], strict=False):
        assert lower.z_top == upper.z_bottom
    assert st.total_thickness == st.layers[0].z_top
