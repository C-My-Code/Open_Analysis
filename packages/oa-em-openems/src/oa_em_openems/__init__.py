# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""openEMS solver adapter (WP-108): ``EMProblem`` to an out-of-process FDTD run and back."""

from oa_em_openems.adapter import OpenEMSAdapter, default_assessor
from oa_em_openems.discovery import OpenEMSInstall, discover

__all__ = ["OpenEMSAdapter", "OpenEMSInstall", "default_assessor", "discover"]
