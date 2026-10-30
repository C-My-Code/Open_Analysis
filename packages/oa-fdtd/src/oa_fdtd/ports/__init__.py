# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mode (wave) ports, quasi-TEM templates and de-embedding (ADR-0010 section 6)."""

from oa_fdtd.ports.deembed import (
    DeembedHook,
    PlaneSet,
    add_wave_planes,
    apply_hook,
    gamma_from_planes,
    shift_reference,
    wave_sparams,
)
from oa_fdtd.ports.modal import ModeSet, solve_modes

__all__ = [
    "DeembedHook",
    "ModeSet",
    "PlaneSet",
    "add_wave_planes",
    "apply_hook",
    "gamma_from_planes",
    "shift_reference",
    "solve_modes",
    "wave_sparams",
]
