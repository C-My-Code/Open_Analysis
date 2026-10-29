# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Palace solver adapter (WP-301): ``EMProblem`` to an out-of-process FEM run and back."""

from oa_em_palace.adapter import PalaceAdapter, default_assessor
from oa_em_palace.config import PalaceOptions, build_config
from oa_em_palace.discovery import discover
from oa_em_palace.paths import host_to_wsl, wsl_to_host

__all__ = [
    "PalaceAdapter",
    "PalaceOptions",
    "build_config",
    "default_assessor",
    "discover",
    "host_to_wsl",
    "wsl_to_host",
]
