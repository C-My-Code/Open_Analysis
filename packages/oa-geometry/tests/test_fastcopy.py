# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``oa_geometry._fastcopy.replace`` is ``model_copy(update=...)`` without the generic machinery
(WP-515), and the batched sampling equals sampling one segment at a time."""

from __future__ import annotations

from oa_geometry import BoardGeometry
from oa_geometry._fastcopy import replace
from oa_geometry.sampling import _piece_of, _sections, _spans_of
from oa_testkit.builders import microstrip_board, split_plane_board


def test_replace_equals_model_copy() -> None:
    xs = BoardGeometry.build(microstrip_board()).sample_net("SIG", ds=2e-3)[0].xs
    prov = xs.provenance
    assert prov is not None
    a = replace(prov, position=(1.0, 2.0))
    b = prov.model_copy(update={"position": (1.0, 2.0)})
    assert a == b
    assert a.model_fields_set == b.model_fields_set
    assert prov.position != (1.0, 2.0)  # the source is untouched
    assert a.model_dump() == b.model_dump()


def test_batched_sections_equal_per_segment() -> None:
    for board in (microstrip_board(), split_plane_board()):
        geo = BoardGeometry.build(board)
        samples = geo.sample_net("SIG", ds=1e-3)
        by_seg: dict[str, list] = {}  # type: ignore[type-arg]
        for s in samples:
            by_seg.setdefault(s.seg_id, []).append(s)
        for seg in geo.segments("SIG"):
            mine = by_seg[seg.id]
            one = _sections(
                geo,
                seg,
                _piece_of(geo, seg),
                _spans_of(geo, seg),
                [s.s - seg.s_start for s in mine],
                None,
                None,
                True,
                None,
            )
            assert len(one) == len(mine)
            assert [x.xs.key() for x in mine] == [o.key() for o in one]
