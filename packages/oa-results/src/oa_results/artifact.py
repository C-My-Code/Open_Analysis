# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Content-addressed artifact reference (interfaces.md section 4.1)."""

from __future__ import annotations

from oa_model._base import OAModel


class ArtifactRef(OAModel):
    sha256: str
    media_type: str  # "application/x-npy", "application/vnd.apache.parquet", ...
    name: str  # logical name inside the bundle
    size: int
