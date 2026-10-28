# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Starlette deprecates its httpx-based TestClient in favour of an httpx2 package we do not use.

Importing it once here, with the warning silenced, caches the module for the test files.
"""

import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import fastapi.testclient  # noqa: F401
