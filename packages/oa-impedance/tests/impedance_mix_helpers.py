# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sections like those the IM-002 rule meets on the ``gen_synth_board.py --mix`` board.

A run of parallel traces of different widths on one layer, a reference plane under (and, for
inner layers, over) them, and a stack of ``ConstantDkDf(4.3, 0.02)`` slabs (loss tangent: the
permittivity is complex, so the lossy system is solved). The generator is seeded and does not need
the KiCad reader.
"""

from __future__ import annotations

import _xs
import numpy as np

from oa_model.xsection import CrossSection

T_CU = 35e-6
_WIDTHS = (50e-6, 75e-6, 100e-6, 150e-6, 200e-6, 400e-6)
_PITCHES = (0.3e-3, 0.4e-3, 0.5e-3)


def mix_section(
    seed: int, *, n_traces: int | None = None, inner: bool | None = None
) -> CrossSection:
    """One deterministic mix-like section (3 to 7 conductors)."""
    rng = np.random.default_rng(seed)
    n_sig = int(n_traces if n_traces is not None else rng.integers(1, 6))
    inner = bool(rng.integers(0, 2)) if inner is None else inner
    h = 200e-6  # core
    z_trace = h + T_CU if not inner else 2 * h + 2 * T_CU
    pitch = float(rng.choice(_PITCHES))
    widths = [float(rng.choice(_WIDTHS)) for _ in range(n_sig)]
    centers = [(k - (n_sig - 1) / 2) * pitch for k in range(n_sig)]
    conductors = [_xs.plane("g1", 0.0, T_CU)]
    for k, (u, w) in enumerate(zip(centers, widths, strict=True)):
        conductors.append(_xs.strip(f"s{k + 1}", "signal", u, z_trace, w, T_CU))
    top = z_trace + T_CU
    dielectrics = [_xs.slab(T_CU, z_trace, 4.3, 0.02)]
    if inner:
        conductors.append(_xs.plane("g2", z_trace + h, T_CU))
        dielectrics.append(_xs.slab(z_trace, z_trace + h, 4.3, 0.02))
        top = z_trace + h + T_CU
    else:
        dielectrics.append(_xs.slab(z_trace, top, 4.3, 0.02))
    return CrossSection(
        conductors=tuple(conductors),
        dielectrics=tuple(dielectrics),
        topology_hint="general",
        symmetric_about_u0=False,
    )


def mix_sections(count: int, seed0: int = 100) -> list[CrossSection]:
    return [mix_section(seed0 + i) for i in range(count)]
