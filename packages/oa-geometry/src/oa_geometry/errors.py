# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured errors raised by ``oa_geometry`` (``code``, ``message``, ``path``, ``hint``).

``oa_framework.errors.to_error_info`` reads ``code``/``path``/``hint`` from any ``OAError``, so
these convert to the wire-level ``ErrorInfo`` without ``oa_geometry`` importing the framework.
"""

from __future__ import annotations

from oa_model.errors import OAError


class GeometryError(OAError):
    """Base class of geometry-service errors. ``path`` locates the offending board item."""

    code = "GEOM_ERROR"

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        path: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.path = path
        self.hint = hint


class UnknownItem(GeometryError):
    """A net, layer, via, pad or segment id is not on the board."""

    code = "GEOM_UNKNOWN_ITEM"


class TerminalError(GeometryError):
    """A terminal could not be resolved to copper."""

    code = "GEOM_TERMINAL"


class RegionError(GeometryError):
    """A region specification cannot be cut (empty nets, degenerate bbox, ...)."""

    code = "GEOM_REGION"
