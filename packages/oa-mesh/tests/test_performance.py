# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Power-plane-sized performance runs (``slow``); the timing is printed in the test report."""

from __future__ import annotations

import math
import time

import pytest
from mesh_helpers import circle, piece, poly, random_centres, rect

from oa_mesh import Contact, SizeField, mesh_copper_sheets


@pytest.mark.slow
def test_power_plane_with_hundreds_of_vias(capsys: pytest.CaptureFixture[str]) -> None:
    """100 x 80 mm plane, 600 antipad holes + 300 via contact rings, 24-vertex rings."""
    w, h = 100e-3, 80e-3
    cs = random_centres(900, w, h, 3e-3, 2.2e-3, seed=7)
    holes = [circle(x, y, 0.45e-3, 24, cw=True) for x, y in cs[:600]]
    contacts = [
        Contact(id=f"v{i}", x=x, y=y, radius=0.3e-3, segments=24)
        for i, (x, y) in enumerate(cs[600:])
    ]
    pc = piece([poly(rect(0, 0, w, h), *holes)], contacts)
    sf = [SizeField(kind="contacts", size=0.15e-3)]

    t0 = time.perf_counter()
    (m,) = mesh_copper_sheets([pc], h_default=2e-3, size_fields=sf)
    dt = time.perf_counter() - t0

    area = w * h - 600 * 0.5 * 24 * 0.45e-3**2 * math.sin(2 * math.pi / 24)
    assert m.quality.area == pytest.approx(area, rel=1e-9)
    assert m.cleaning.n_holes == 600
    assert len(m.contacts) == 300
    assert all(len(c.nodes.array) >= 16 for c in m.contacts)
    assert max(c.radial_error for c in m.contacts) <= 1e-9
    assert m.quality.min_angle_deg > 20
    with capsys.disabled():
        print(
            f"\n[perf] 100x80 mm plane, 600 holes + 300 contacts: {dt:.2f} s, "
            f"{m.quality.n_triangles} triangles, {m.quality.n_nodes} nodes, "
            f"min angle {m.quality.min_angle_deg:.1f} deg"
        )
    assert dt < 60.0
