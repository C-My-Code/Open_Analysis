# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open_Analysis meta-package: installs every M1 package and the ``oa`` / ``oa-mcp`` commands."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__"]

try:
    __version__ = version("open-analysis")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
