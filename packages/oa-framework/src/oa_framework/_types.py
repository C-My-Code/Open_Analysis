# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Forward declarations for types owned by packages that do not exist yet."""

from __future__ import annotations

from typing import Any, TypeAlias

# ``oa_geometry.BoardGeometry`` (WP-104). Replace this alias with the real import when it lands;
# the protocols only hand the object through, so ``Any`` is safe until then.
BoardGeometry: TypeAlias = Any
