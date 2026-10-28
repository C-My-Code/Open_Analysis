# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""kicad-cli / pcbnew bridge (Altium import, zone refill)."""

from oa_kicad_bridge.altium import AltiumReader
from oa_kicad_bridge.bridge import (
    DrcReport,
    ImportResult,
    drc,
    export_ipc2581,
    export_odb,
    import_foreign,
    import_foreign_report,
    inspect_zones,
    mark_refilled,
    prepare_read,
    refill_zones,
    upgrade,
)
from oa_kicad_bridge.tools import KiCadTools

__all__ = [
    "AltiumReader",
    "DrcReport",
    "ImportResult",
    "KiCadTools",
    "drc",
    "export_ipc2581",
    "export_odb",
    "import_foreign",
    "import_foreign_report",
    "inspect_zones",
    "mark_refilled",
    "prepare_read",
    "refill_zones",
    "upgrade",
]
