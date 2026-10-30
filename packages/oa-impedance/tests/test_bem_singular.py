# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WP-504: coincident conductors (the cm5 HDMI_PI.D2_N section) are a structured error."""

from __future__ import annotations

import _xs
import pytest

from oa_impedance.bem import solve_bem
from oa_impedance.errors import ImpedanceUnsupported
from oa_model.xsection import CrossSection, XsConductor, XsPlane, XsTrapezoid

UM = 1e-6


def _trace(cid: str) -> XsConductor:
    return XsConductor(
        id=cid,
        role="signal",
        shape=XsTrapezoid(
            u_center=0.0, z_bottom=100 * UM, w_bottom=130 * UM, w_top=130 * UM, thickness=35 * UM
        ),
    )


def test_coincident_conductors_give_a_structured_error() -> None:
    xs = CrossSection(
        conductors=(
            XsConductor(
                id="g1", role="ground", shape=XsPlane(z_bottom=-15 * UM, thickness=15 * UM)
            ),
            _trace("s1"),
            _trace("s2"),
        ),
        dielectrics=(_xs.slab(0.0, 100 * UM, 4.4), _xs.slab(100 * UM, 135 * UM, 3.3)),
        topology_hint="edge_coupled_microstrip",
    )
    with pytest.raises(ImpedanceUnsupported) as ei:
        solve_bem(xs, [0.0])
    info = ei.value.info()
    assert info.code == "IMPEDANCE_UNSUPPORTED"
    assert info.path == "xsection.conductors" and info.hint
    assert "s2" in info.message or "s1" in info.message
