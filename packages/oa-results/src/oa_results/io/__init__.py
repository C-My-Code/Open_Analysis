# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Result writers and readers (interfaces.md section 4.2)."""

from oa_results.io.bundle_io import bundle_artifact_path, read_bundle, write_bundle
from oa_results.io.sarif import sarif_document, to_sarif
from oa_results.io.tables import read_table_rows, to_csv, write_table_parquet
from oa_results.io.touchstone import TouchstoneError, from_touchstone, to_touchstone
from oa_results.io.vtu import to_vtu

__all__ = [
    "TouchstoneError",
    "bundle_artifact_path",
    "from_touchstone",
    "read_bundle",
    "read_table_rows",
    "sarif_document",
    "to_csv",
    "to_sarif",
    "to_touchstone",
    "to_vtu",
    "write_bundle",
    "write_table_parquet",
]
