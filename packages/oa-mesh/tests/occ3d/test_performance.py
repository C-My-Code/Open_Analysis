# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-206 acceptance: the via region meshes in under 60 s at the medium preset."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from oa_geometry import BoardGeometry, RegionSpec
from oa_mesh.occ3d import OccOptions, build_occ_model, write_msh
from oa_model.em import MeshSettings
from oa_testkit.builders import via_transition_board


@pytest.mark.slow
@pytest.mark.parametrize("threshold", [0.0, 0.1e-3], ids=["solid", "sheet"])
def test_via_region_medium_under_60s(threshold: float, tmp_path: Path) -> None:
    geo = BoardGeometry.build(via_transition_board())
    cut = geo.cut_region(RegionSpec(nets=("SIG",), bbox=(5e-3, -4e-3, 20e-3, 4e-3)))
    t0 = time.perf_counter()
    model = build_occ_model(
        cut.structure, sheet_threshold=threshold, options=OccOptions(f_max=10e9)
    )
    stats = write_msh(model, tmp_path / "via.msh", sizes=MeshSettings(preset="medium"))
    elapsed = time.perf_counter() - t0
    assert elapsed < 60.0, f"{elapsed:.1f} s"
    assert stats.n_tets > 10_000
    assert stats.min_sicn > 0.2  # was 0.017 before copper-layer fills were capped (WP-211)
