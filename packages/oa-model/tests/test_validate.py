# SPDX-License-Identifier: GPL-3.0-or-later
"""Every MODEL.* code has a negative test; builders must validate strictly."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from boards import MM, make_board, rect
from oa_model import (
    ArcSeg,
    Backdrill,
    Board,
    BoardIndex,
    BoardValidationError,
    Diagnostic,
    DiffPair,
    LineSeg,
    Points,
    Polygon,
    Track,
    ZoneFill,
    validate_board,
)


def codes(diags: tuple[Diagnostic, ...], severity: str | None = None) -> set[str]:
    return {d.code for d in diags if severity is None or d.severity == severity}


def test_clean_board_is_strictly_valid() -> None:
    assert validate_board(make_board(), strict=True) == ()


def test_arc_radius_validator() -> None:
    with pytest.raises(ValueError, match="equidistant"):
        ArcSeg(start=(0, 0), end=(1, 1), center=(0.5, 0), ccw=True)


def _dup_id(b: Board) -> Board:
    return b.model_copy(update={"tracks": (*b.tracks, b.tracks[0])})


def _dangling(b: Board) -> Board:
    pad = b.pads[0].model_copy(update={"net": "NOPE"})
    return b.model_copy(update={"pads": (pad, *b.pads[1:])})


def _layer_kind(b: Board) -> Board:
    t = b.tracks[0].model_copy(update={"layer": "D1"})
    return b.model_copy(update={"tracks": (t, *b.tracks[1:])})


def _via_span(b: Board) -> Board:
    v = b.vias[0].model_copy(update={"end_layer": "L3"})  # 'through' but not to L4
    return b.model_copy(update={"vias": (v,)})


def _via_span_reversed(b: Board) -> Board:
    v = b.vias[0].model_copy(update={"start_layer": "L4", "end_layer": "L1"})
    return b.model_copy(update={"vias": (v,)})


def _backdrill(b: Board) -> Board:
    bd = Backdrill(side="top", diameter=0.5 * MM, last_removed_layer="L4")
    v = b.vias[0].model_copy(update={"backdrills": (bd,)})
    return b.model_copy(update={"vias": (v,)})


def _poly_orient(b: Board) -> Board:
    cw = Polygon(exterior=Points([0, 0, 0, 10 * MM, 20 * MM, 10 * MM, 20 * MM, 0]))
    return b.model_copy(update={"outline": b.outline.model_copy(update={"polygon": cw})})


def _poly_invalid(b: Board) -> Board:
    bow = Polygon(exterior=Points([0, 0, 1, 1, 1, 0, 0, 1]))  # bow-tie
    s = b.shapes[0].model_copy(update={"polygon": bow})
    return b.model_copy(update={"shapes": (s,)})


def _track_path(b: Board) -> Board:
    t = Track(
        id="gap",
        net="SIG_P",
        layer="L1",
        width=0.1 * MM,
        path=(
            LineSeg(start=(0, 0), end=(1 * MM, 0)),
            LineSeg(start=(2 * MM, 0), end=(3 * MM, 0)),
        ),
    )
    return b.model_copy(update={"tracks": (*b.tracks, t)})


def _diffpair(b: Board) -> Board:
    d = DiffPair(id="dp:bad", p_net="SIG_P", n_net="SIG_P", origin="user")
    return b.model_copy(update={"diff_pairs": (d,)})


def _diffpair_missing(b: Board) -> Board:
    d = DiffPair(id="dp:bad", p_net="SIG_P", n_net="NOPE", origin="user")
    return b.model_copy(update={"diff_pairs": (d,)})


def _zone_fill_layer(b: Board) -> Board:
    z = b.zones[0]
    fill = ZoneFill(layer="L3", polygons=z.fills[0].polygons)
    return b.model_copy(update={"zones": (z.model_copy(update={"fills": (fill,)}),)})


def _stackup_z(b: Board) -> Board:
    st = b.stackup.model_copy(update={"total_thickness": b.stackup.total_thickness + 5e-6})
    return b.model_copy(update={"stackup": st})


def _stackup_gap(b: Board) -> Board:
    layers = list(b.stackup.layers)
    layers[2] = layers[2].model_copy(update={"z_top": layers[2].z_top + 1e-6})
    return b.model_copy(update={"stackup": b.stackup.model_copy(update={"layers": tuple(layers)})})


def _outline(b: Board) -> Board:
    deg = Polygon(exterior=Points([0, 0, 1 * MM, 0, 2 * MM, 0]))
    return b.model_copy(update={"outline": b.outline.model_copy(update={"polygon": deg})})


CASES: list[tuple[str, Callable[[Board], Board], str]] = [
    ("MODEL.DUP_ID", _dup_id, "error"),
    ("MODEL.DANGLING_REF", _dangling, "error"),
    ("MODEL.LAYER_KIND", _layer_kind, "error"),
    ("MODEL.VIA_SPAN", _via_span, "error"),
    ("MODEL.VIA_SPAN", _via_span_reversed, "error"),
    ("MODEL.BACKDRILL", _backdrill, "error"),
    ("MODEL.POLY_ORIENT", _poly_orient, "error"),
    ("MODEL.POLY_INVALID", _poly_invalid, "error"),
    ("MODEL.TRACK_PATH", _track_path, "error"),
    ("MODEL.DIFFPAIR", _diffpair, "error"),
    ("MODEL.DIFFPAIR", _diffpair_missing, "error"),
    ("MODEL.ZONE_FILL_LAYER", _zone_fill_layer, "error"),
    ("MODEL.STACKUP_Z", _stackup_z, "error"),
    ("MODEL.STACKUP_Z", _stackup_gap, "error"),
    ("MODEL.OUTLINE", _outline, "warning"),
]


@pytest.mark.parametrize(("code", "mutate", "severity"), CASES, ids=[c[1].__name__ for c in CASES])
def test_negative(code: str, mutate: Callable[[Board], Board], severity: str) -> None:
    diags = validate_board(mutate(make_board()))
    hits = [d for d in diags if d.code == code]
    assert hits, f"{code} not raised; got {codes(diags)}"
    d = hits[0]
    assert d.severity == severity
    # structured, agent-friendly fields
    assert d.path is not None
    assert d.path.startswith("/")
    assert d.hint
    assert d.message
    if severity == "error":
        with pytest.raises(BoardValidationError) as ei:
            validate_board(mutate(make_board()), strict=True)
        assert code in codes(ei.value.diagnostics)
    else:
        validate_board(mutate(make_board()), strict=True)  # warnings do not raise


def test_diagnostic_paths_point_at_the_problem() -> None:
    (d,) = [d for d in validate_board(_layer_kind(make_board())) if d.code == "MODEL.LAYER_KIND"]
    assert d.path == "/tracks/0/layer"
    assert d.item == "t1"
    assert d.layer == "D1"
    (d2,) = [d for d in validate_board(_dangling(make_board())) if d.code == "MODEL.DANGLING_REF"]
    assert d2.path == "/pads/0/net"
    assert "NOPE" in d2.message


def test_every_documented_code_is_covered() -> None:
    documented = {
        "MODEL.DUP_ID",
        "MODEL.DANGLING_REF",
        "MODEL.LAYER_KIND",
        "MODEL.VIA_SPAN",
        "MODEL.BACKDRILL",
        "MODEL.POLY_ORIENT",
        "MODEL.POLY_INVALID",
        "MODEL.TRACK_PATH",
        "MODEL.DIFFPAIR",
        "MODEL.ZONE_FILL_LAYER",
        "MODEL.STACKUP_Z",
        "MODEL.OUTLINE",
    }
    assert documented == {c[0] for c in CASES}


def test_board_index() -> None:
    b = make_board()
    idx = BoardIndex.of(b)
    assert BoardIndex.of(b) is idx
    assert idx.net("GND").kind == "ground"
    assert [layer.id for layer in idx.conductor_layers] == ["L1", "L2", "L3", "L4"]
    assert idx.component("U1").pads == ("p1", "p2")
    assert idx.padstack("ps_via").drill is not None
    nets = {(r.kind, r.id) for r in idx.items_by_net("GND")}
    assert ("via", "v1") in nets
    assert ("zone", "z1") in nets
    assert {r.id for r in idx.items_by_layer("L3")} >= {"v1"}
    assert idx.items_by_net("nothing") == ()
    assert BoardIndex.of(make_board()) is not idx


def test_validate_rect_helper_orientation() -> None:
    assert validate_board(make_board().model_copy(update={"zones": ()}), strict=True) == ()
    assert rect(0, 0, 1, 1).exterior.array.shape == (4, 2)
