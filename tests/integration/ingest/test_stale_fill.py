# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stale and missing zone fills: detect them on read, repair them with ``refill_zones`` (WP-208).

The edits are made to a copy of the WP-102 ``split_plane`` board as plain text, the way a user
would leave a file after changing a zone and saving without pressing B:

- ``emptied``: every ``filled_polygon`` removed;
- ``track``: a track of another net laid across the GND fill (fill predates the track);
- ``outline``: the GND zone outline halved (fill predates the outline edit).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import ingest_xval as xv
import pytest

from oa_kicad_bridge import inspect_zones, mark_refilled, prepare_read, refill_zones
from oa_model import Board
from oa_model.setup import ReadOptions

pytestmark = pytest.mark.kicad

SRC = xv.SYNTHETIC / "split_plane"
GND_LAYER = "L4"  # B.Cu: the full-board GND zone
GND_ZONE_OUTLINE = "(xy 0 0) (xy 100 0) (xy 100 70) (xy 0 70)"
TRACK = (
    '\t(segment\n\t\t(start 10 65)\n\t\t(end 30 65)\n\t\t(width 0.5)\n\t\t(layer "B.Cu")\n'
    '\t\t(net "SIG_SLOT")\n\t\t(uuid "00000000-0000-4000-8000-000000000208")\n\t)\n'
)
TRACK_LENGTH = 20e-3
TRACK_WIDTH = 0.5e-3


def _balanced_end(text: str, start: int) -> int:
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    raise ValueError("unbalanced")


def strip_fills(text: str) -> str:
    """Remove every ``(filled_polygon ...)`` block."""
    out, pos = [], 0
    while True:
        i = text.find("(filled_polygon", pos)
        if i < 0:
            out.append(text[pos:])
            return "".join(out)
        out.append(text[pos:i].rstrip("\t "))
        pos = _balanced_end(text, i)


def with_track(text: str) -> str:
    i = text.index("\t(zone\n")
    return text[:i] + TRACK + text[i:]


def with_half_outline(text: str) -> str:
    zone = text.index('(zone\n\t\t(net "GND")\n\t\t(layer "B.Cu")')
    i = text.index(GND_ZONE_OUTLINE, zone)
    return text[:i] + "(xy 0 0) (xy 50 0) (xy 50 70) (xy 0 70)" + text[i + len(GND_ZONE_OUTLINE) :]


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("stale")
    text = (SRC / "board.kicad_pcb").read_text(encoding="utf-8")
    for name, fn in {
        "clean": lambda t: t,
        "emptied": strip_fills,
        "track": with_track,
        "outline": with_half_outline,
    }.items():
        (d / f"{name}.kicad_pcb").write_text(fn(text), encoding="utf-8", newline="\n")
        # the project file (clearances, net classes) must sit next to every board copy
        shutil.copy(SRC / "board.kicad_pro", d / f"{name}.kicad_pro")
    return d


def _read(path: Path) -> Board:
    return xv.read_native(path)


def _gnd(board: Board) -> float:
    return xv.AreaCache(board).area[GND_LAYER]["GND"]


def _codes(board: Board) -> set[str]:
    return {d.code for d in board.diagnostics}


def _refill(src: Path, tmp: Path) -> Board:
    out = tmp / f"{src.stem}_refilled.kicad_pcb"
    refill_zones(src, out, tools=xv.kicad_tools())
    return mark_refilled(_read(out))


def test_clean_board_is_reported_fresh(work: Path) -> None:
    b = _read(work / "clean.kicad_pcb")
    assert {z.fill_state for z in b.zones if z.keepout is None} == {"filled"}
    assert not {"INGEST.ZONE_UNFILLED", "INGEST.ZONE_STALE"} & _codes(b)
    assert xv.fill_outside_outline(b) == []
    want = xv.load_expected(SRC)["copper_area"]["layers"][GND_LAYER]["GND"]
    assert _gnd(b) == pytest.approx(want, rel=1e-3)


def test_emptied_zone_is_detected_and_refill_restores_the_area(work: Path, tmp_path: Path) -> None:
    emptied = work / "emptied.kicad_pcb"
    b = _read(emptied)
    zones = [z for z in b.zones if z.keepout is None]
    assert {z.fill_state for z in zones} == {"unfilled"}
    assert [d for d in b.diagnostics if d.code == "INGEST.ZONE_UNFILLED"]
    assert all(d.hint for d in b.diagnostics if d.code == "INGEST.ZONE_UNFILLED")
    assert _gnd(b) < 1e-4  # only the tracks and pads that remain, no plane

    rep = inspect_zones(emptied, tools=xv.kicad_tools())
    assert rep["unfilled"] == len(zones)

    # ReadOptions.refill_zones: "never" leaves the file alone, "if_unfilled" repairs it
    kept, refilled = prepare_read(
        emptied, ReadOptions(refill_zones="never"), tools=xv.kicad_tools(), work_dir=tmp_path
    )
    assert kept == emptied and not refilled
    path, refilled = prepare_read(
        emptied, ReadOptions(refill_zones="if_unfilled"), tools=xv.kicad_tools(), work_dir=tmp_path
    )
    assert refilled and path != emptied
    fixed = mark_refilled(_read(path))
    assert {z.fill_state for z in fixed.zones if z.keepout is None} == {"refilled_by_tool"}
    assert "INGEST.ZONE_UNFILLED" not in _codes(fixed)
    exp = xv.load_expected(SRC)
    j = xv.compare_expected("split_plane_refilled", "kicad", fixed, exp)
    assert not [f for f in j.findings if f.status == "fail" and f.check == "copper_area"]
    assert inspect_zones(path, tools=xv.kicad_tools())["unfilled"] == 0


def test_foreign_track_over_the_fill_is_flagged_stale_and_refill_cuts_it(
    work: Path, tmp_path: Path
) -> None:
    clean = _gnd(_read(work / "clean.kicad_pcb"))
    stale = _read(work / "track.kicad_pcb")
    gnd = next(z for z in stale.zones if z.net == "GND" and z.layers == (GND_LAYER,))
    assert gnd.fill_state == "stale_suspected"
    assert "INGEST.ZONE_STALE" in _codes(stale)
    assert _gnd(stale) == pytest.approx(clean, rel=1e-9)  # the old fill is still what we see

    fixed = _refill(work / "track.kicad_pcb", tmp_path)
    assert "INGEST.ZONE_STALE" not in _codes(fixed)
    assert {z.fill_state for z in fixed.zones if z.net == "GND"} == {"refilled_by_tool"}
    cut = clean - _gnd(fixed)
    # at least the copper of the track is gone, and not more than a generous clearance band
    assert TRACK_LENGTH * TRACK_WIDTH < cut < TRACK_LENGTH * (TRACK_WIDTH + 2 * 1.5e-3)


def test_edited_outline_leaves_a_fill_outside_it_and_refill_trims_it(
    work: Path, tmp_path: Path
) -> None:
    clean = _gnd(_read(work / "clean.kicad_pcb"))
    stale = _read(work / "outline.kicad_pcb")
    # the harness check sees what the reader's own foreign-copper test cannot
    outside = dict(xv.fill_outside_outline(stale))
    gnd = next(z for z in stale.zones if z.net == "GND" and z.layers == (GND_LAYER,))
    assert outside.get(gnd.id, 0.0) > 0.002  # about half of the 0.007 m^2 plane
    assert _gnd(stale) == pytest.approx(clean, rel=1e-9)

    fixed = _refill(work / "outline.kicad_pcb", tmp_path)
    assert xv.fill_outside_outline(fixed) == []
    assert 0.40 * clean < _gnd(fixed) < 0.55 * clean
