# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Structured errors for oa_accel.

``ErrorInfo`` mirrors the fields of ``oa_framework.errors.ErrorInfo`` (code, message, path, hint).
oa_accel sits next to oa_physics, below oa_framework, so it cannot import that class; see
docs/change-requests/WP-503-errorinfo-layer.md. Adapters convert with ``ErrorInfo.to_dict()``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ErrorInfo:
    """Machine-readable failure: stable ``ACCEL_*`` code, one sentence, optional path and hint."""

    code: str
    message: str
    path: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class AccelError(Exception):
    """Raised by oa_accel entry points; always carries an :class:`ErrorInfo`."""

    def __init__(self, info: ErrorInfo) -> None:
        super().__init__(f"{info.code}: {info.message}")
        self.info = info
