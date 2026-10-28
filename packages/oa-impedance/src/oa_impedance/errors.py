# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured errors of the impedance package (``{code, message, path, hint}``)."""

from __future__ import annotations

from oa_framework.errors import ErrorInfo
from oa_model.errors import UnsupportedFeature

__all__ = ["ImpedanceUnsupported", "MethodNotAvailable"]


class ImpedanceUnsupported(UnsupportedFeature):
    """The requested method / topology combination cannot be solved."""

    code = "IMPEDANCE_UNSUPPORTED"

    def __init__(self, message: str, *, path: str | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.hint = hint

    def info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, path=self.path, hint=self.hint)


class MethodNotAvailable(ImpedanceUnsupported):
    """``method="fem"`` (or another registered name) has no solver registered yet."""

    code = "IMPEDANCE_METHOD_UNAVAILABLE"
