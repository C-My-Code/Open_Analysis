# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""KiCad .kicad_pcb reader (WP-101). Public API: :class:`KiCadReader`."""

from oa_ingest_kicad.reader import READER_VERSION, KiCadIngestError, KiCadReader

__all__ = ["READER_VERSION", "KiCadIngestError", "KiCadReader"]
