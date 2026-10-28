# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""FastAPI + WebSocket server: a thin REST adapter over ``oa_service`` (interfaces.md section 7)."""

from oa_server.app import ApiError, create_app

__all__ = ["ApiError", "create_app"]
