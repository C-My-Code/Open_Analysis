# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Structured errors: every error surfaces as ``{code, message, path, hint}``.

``ErrorInfo`` is the wire form used by ``JobStatus.error``, the REST API, the CLI ``--json``
output and the MCP server, so humans and AI agents see the same shape. ``path`` locates the
problem (a setup field, a board item id, a registry entry); ``hint`` says what to do next.
"""

from __future__ import annotations

from oa_model._base import OAModel
from oa_model.errors import OAError


class ErrorInfo(OAModel):
    code: str  # stable machine code, e.g. "REGISTRY_DUPLICATE"
    message: str  # one human-readable sentence
    path: str | None = None  # where: "setup.frequency.f_max", "analyses/impedance.xsection", ...
    hint: str | None = None  # how to fix it


class FrameworkError(OAError):
    """Base of errors raised by ``oa_framework``; carries the structured fields."""

    code = "FRAMEWORK_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        path: str | None = None,
        hint: str | None = None,
    ) -> None:
        Exception.__init__(self, message)
        if code is not None:
            self.code = code
        self.message = message
        self.path = path
        self.hint = hint

    def info(self) -> ErrorInfo:
        return ErrorInfo(code=self.code, message=self.message, path=self.path, hint=self.hint)


class RegistryError(FrameworkError):
    code = "REGISTRY_ERROR"


class DuplicateRegistration(RegistryError):
    code = "REGISTRY_DUPLICATE"


class UnknownPlugin(RegistryError):
    code = "REGISTRY_UNKNOWN"


class PluginLoadError(RegistryError):
    code = "REGISTRY_LOAD_FAILED"


class NoReader(FrameworkError):
    code = "INGEST_NO_READER"


def to_error_info(exc: BaseException) -> ErrorInfo:
    """Convert any exception to the structured form.

    Uses ``code``/``path``/``hint`` attributes when the exception has them (all
    ``FrameworkError`` and, where available, other ``OAError`` subclasses); anything else
    becomes ``INTERNAL_ERROR`` with the exception type in the message.
    """
    if isinstance(exc, FrameworkError):
        return exc.info()
    if isinstance(exc, OAError):
        path = getattr(exc, "path", None)
        hint = getattr(exc, "hint", None)
        return ErrorInfo(
            code=str(getattr(exc, "code", "OA_ERROR")),
            message=str(exc) or type(exc).__name__,
            path=path if isinstance(path, str) else None,
            hint=hint if isinstance(hint, str) else None,
        )
    return ErrorInfo(
        code="INTERNAL_ERROR",
        message=f"{type(exc).__name__}: {exc}",
        hint="This is a bug; report it with the job log.",
    )
