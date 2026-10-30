# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interpreter startup hook for the development environment (see oa_devguard)."""

try:
    import oa_devguard

    oa_devguard.install()
except Exception:  # never block interpreter startup
    pass
