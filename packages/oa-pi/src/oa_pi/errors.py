# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured errors raised by ``oa_pi`` (``code``, ``message``, ``path``, ``hint``).

``oa_framework.errors.to_error_info`` reads ``code``/``path``/``hint`` from any ``OAError``, so
these convert to the wire-level ``ErrorInfo`` without extra glue.
"""

from __future__ import annotations

from oa_model.errors import OAError

__all__ = [
    "PiError",
    "PiFloatingError",
    "PiGeometryError",
    "PiSetupError",
    "PiUnfilledZoneError",
]


class PiError(OAError):
    """Base class of power-integrity errors. ``path`` locates the offending item or setup field."""

    code = "PI_ERROR"

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


class PiSetupError(PiError):
    """The pi.dc setup refers to something that is not on the board."""

    code = "SETUP_INVALID"


class PiUnfilledZoneError(PiError):
    """A zone on the rail or return net has no (or a stale) fill: results would be wrong."""

    code = "PI_ZONE_UNFILLED"


class PiGeometryError(PiError):
    """The copper cannot be turned into a solvable network (no path, mesh failure)."""

    code = "PI_GEOMETRY"


class PiFloatingError(PiError):
    """A load has no galvanic path to the regulator."""

    code = "PI_NO_PATH"
