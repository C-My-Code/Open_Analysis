# SPDX-License-Identifier: GPL-3.0-or-later
"""Error hierarchy (interfaces.md section 12)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from oa_model.common import Diagnostic


class OAError(Exception):
    """Base class. `code` is a stable machine-readable code."""

    code: str = "OA_ERROR"

    def __init__(self, message: str = "", *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code


class IngestError(OAError):
    code = "INGEST_ERROR"


class BoardValidationError(OAError):
    code = "MODEL_INVALID"

    def __init__(self, message: str = "", diagnostics: Sequence[Diagnostic] = ()) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


class UnsupportedSchemaVersion(OAError):
    code = "SCHEMA_VERSION"


class SetupError(OAError):
    code = "SETUP_INVALID"

    def __init__(self, message: str = "", diagnostics: Sequence[Diagnostic] = ()) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics)


class ToolMissing(OAError):
    code = "TOOL_MISSING"

    def __init__(self, message: str = "", program: str = "", hint: str = "") -> None:
        super().__init__(message)
        self.program = program
        self.hint = hint


class ExternalToolFailed(OAError):
    code = "TOOL_FAILED"

    def __init__(self, message: str = "", returncode: int = 1, log_tail: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.log_tail = log_tail


class Cancelled(OAError):
    code = "CANCELLED"


class QualityGateMissing(OAError):
    code = "QUALITY_GATE_MISSING"


class UnsupportedFeature(OAError):
    code = "UNSUPPORTED_FEATURE"
