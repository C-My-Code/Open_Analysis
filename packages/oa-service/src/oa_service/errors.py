# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Error envelope, code registry and exception mapping (ai-integration.md section 6).

Nothing in the service raises a bare exception across its boundary: every failure becomes one or
more :class:`ErrorInfo` records. ``ErrorInfo`` extends the framework's ``{code, message, path,
hint}`` with ``details`` (code-specific data such as candidates or the budget estimate).
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import Field, JsonValue, ValidationError

from oa_framework.errors import ErrorInfo as _FrameworkErrorInfo
from oa_framework.errors import FrameworkError
from oa_model.errors import (
    BoardValidationError,
    Cancelled,
    ExternalToolFailed,
    IngestError,
    OAError,
    QualityGateMissing,
    SetupError,
    ToolMissing,
    UnsupportedFeature,
    UnsupportedSchemaVersion,
)

__all__ = [
    "CODE_EXIT",
    "CODE_HTTP",
    "ErrorInfo",
    "ServiceError",
    "ServiceErrors",
    "close_matches",
    "exit_code_for",
    "from_validation_error",
    "http_status_for",
    "jdict",
    "map_exception",
    "pointer",
]


class ErrorInfo(_FrameworkErrorInfo):
    """Structured error or warning: stable ``code``, one-sentence ``message``, JSON-Pointer
    ``path``,
    one actionable ``hint`` and code-specific ``details``."""

    details: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Code-specific data, documented per code (candidates, estimate, next_offset).",
    )


# code -> CLI exit code / HTTP status (ai-integration.md section 6). Unlisted codes map via prefix.
CODE_EXIT: dict[str, int] = {
    "USAGE": 2,
    "SETUP_INVALID": 2,
    "MODEL_INVALID": 2,
    "SCHEMA_VERSION": 2,
    "NOT_FOUND": 2,
    "AMBIGUOUS_REF": 2,
    "PERMISSION_DENIED": 2,
    "UNSUPPORTED_FEATURE": 2,
    "TOOL_MISSING": 3,
    "ANALYSIS_UNAVAILABLE": 3,
    "TOOL_FAILED": 4,
    "INTERNAL": 4,
    "QUALITY_GATE_FAILED": 5,
    "QUALITY_GATE_MISSING": 5,
    "BUDGET_EXCEEDED": 6,
    "CANCELLED": 6,
    "TIMEOUT": 6,
    "CONFIRMATION_REQUIRED": 7,
    "STALE_BASE": 7,
}
CODE_HTTP: dict[str, int] = {
    "USAGE": 400,
    "SETUP_INVALID": 422,
    "MODEL_INVALID": 422,
    "SCHEMA_VERSION": 422,
    "NOT_FOUND": 404,
    "AMBIGUOUS_REF": 409,
    "PERMISSION_DENIED": 403,
    "UNSUPPORTED_FEATURE": 422,
    "TOOL_MISSING": 424,
    "ANALYSIS_UNAVAILABLE": 424,
    "TOOL_FAILED": 500,
    "INTERNAL": 500,
    "QUALITY_GATE_FAILED": 200,
    "QUALITY_GATE_MISSING": 200,
    "BUDGET_EXCEEDED": 409,
    "CANCELLED": 409,
    "TIMEOUT": 409,
    "CONFIRMATION_REQUIRED": 409,
    "STALE_BASE": 409,
}


def exit_code_for(code: str) -> int:
    """CLI exit code of an error code (``INGEST_*`` -> 2, unknown -> 4)."""
    if code in CODE_EXIT:
        return CODE_EXIT[code]
    if code.startswith("INGEST"):
        return 2
    return 4


def http_status_for(code: str) -> int:
    """HTTP status of an error code (``INGEST_*`` -> 422, unknown -> 500)."""
    if code in CODE_HTTP:
        return CODE_HTTP[code]
    if code.startswith("INGEST"):
        return 422
    return 500


class ServiceError(Exception):
    """Raised inside the service; carries the structured fields and is mapped by the dispatcher."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.hint = hint
        self.details: dict[str, Any] = details or {}

    def info(self) -> ErrorInfo:
        return ErrorInfo(
            code=self.code,
            message=self.message,
            path=self.path,
            hint=self.hint,
            details=self.details,
        )


class ServiceErrors(Exception):
    """Several structured errors at once (for example one per invalid setup field)."""

    def __init__(self, infos: Iterable[ErrorInfo]) -> None:
        self.infos = tuple(infos)
        super().__init__("; ".join(i.message for i in self.infos) or "invalid input")


def jdict(d: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Typed view of a plain dict for ``ErrorInfo.details`` (pydantic validates at runtime)."""
    return dict(d)


def pointer(loc: Iterable[Any], prefix: str = "") -> str:
    """RFC 6901 JSON Pointer from a pydantic ``loc`` tuple."""
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in loc]
    return prefix + "".join("/" + p for p in parts)


_VALIDATION_HINTS = {
    "extra_forbidden": "Remove the field or check its spelling; see `oa schema` for the model.",
    "missing": "Add the required field; see `oa schema` for the model.",
    "literal_error": "Use one of the allowed values listed in the message.",
}


def close_matches(name: str, candidates: Iterable[str], n: int = 5) -> list[str]:
    """Close spellings of ``name`` for NOT_FOUND hints (deterministic)."""
    pool = sorted(set(candidates))
    low = {c.lower(): c for c in pool}
    hits = difflib.get_close_matches(name.lower(), list(low), n=n, cutoff=0.5)
    sub = [c for c in pool if name.lower() in c.lower() and c not in {low[h] for h in hits}]
    return [low[h] for h in hits] + sub[: max(0, n - len(hits))]


def from_validation_error(
    exc: ValidationError, *, code: str = "SETUP_INVALID", prefix: str = ""
) -> tuple[ErrorInfo, ...]:
    """One ``ErrorInfo`` per pydantic issue; ``loc`` becomes the JSON-Pointer ``path``."""
    out = []
    for issue in exc.errors(include_url=False):
        etype = str(issue.get("type", ""))
        out.append(
            ErrorInfo(
                code=code,
                message=str(issue.get("msg", "invalid value")),
                path=pointer(issue.get("loc", ()), prefix),
                hint=_VALIDATION_HINTS.get(etype),
                details={"type": etype},
            )
        )
    return tuple(out)


def _diag_errors(diags: Sequence[Any], code: str) -> tuple[ErrorInfo, ...]:
    out = []
    for d in diags:
        if getattr(d, "severity", "error") != "error":
            continue
        out.append(
            ErrorInfo(
                code=code,
                message=f"{d.code}: {d.message}",
                path=getattr(d, "path", None) or getattr(d, "item", None),
                hint=getattr(d, "hint", None),
                details={"diagnostic": str(d.code)},
            )
        )
    return tuple(out)


def map_exception(exc: BaseException) -> tuple[ErrorInfo, ...]:
    """Map any exception to ``ErrorInfo`` records. Never raises."""
    # Local imports keep this module importable without oa_jobs at type-check time.
    from oa_jobs.models import BudgetExceeded, JobNotFinished, JobNotFound, JobTimeout

    if isinstance(exc, ServiceError):
        return (exc.info(),)
    if isinstance(exc, ServiceErrors):
        return exc.infos
    if isinstance(exc, ValidationError):
        return from_validation_error(exc)
    if isinstance(exc, BudgetExceeded):
        return (
            ErrorInfo(
                code="BUDGET_EXCEEDED",
                message=exc.message,
                path=exc.path,
                hint=exc.hint,
                details=exc.details,
            ),
        )
    if isinstance(exc, JobNotFound):
        return (
            ErrorInfo(
                code="NOT_FOUND", message=exc.message, path=exc.path, hint=exc.hint, details={}
            ),
        )
    if isinstance(exc, JobTimeout):
        return (ErrorInfo(code="TIMEOUT", message=exc.message, hint=exc.hint),)
    if isinstance(exc, JobNotFinished):
        info = exc.error
        details: dict[str, JsonValue] = {"job_error": info.model_dump(mode="json")} if info else {}
        return (ErrorInfo(code="NOT_FOUND", message=exc.message, hint=exc.hint, details=details),)
    if isinstance(exc, SetupError):
        issues = _diag_errors(exc.diagnostics, "SETUP_INVALID")
        return issues or (ErrorInfo(code="SETUP_INVALID", message=str(exc) or "invalid setup"),)
    if isinstance(exc, BoardValidationError):
        issues = _diag_errors(exc.diagnostics, "MODEL_INVALID")
        return issues or (ErrorInfo(code="MODEL_INVALID", message=str(exc) or "invalid board"),)
    if isinstance(exc, ToolMissing):
        return (
            ErrorInfo(
                code="TOOL_MISSING",
                message=str(exc) or f"{exc.program} is not configured",
                hint=exc.hint or "Run `oa doctor` to see which tools are found.",
                details={"program": exc.program},
            ),
        )
    if isinstance(exc, ExternalToolFailed):
        return (
            ErrorInfo(
                code="TOOL_FAILED",
                message=str(exc),
                details={"returncode": exc.returncode, "log_tail": exc.log_tail[-2000:]},
            ),
        )
    if isinstance(exc, Cancelled):
        return (ErrorInfo(code="CANCELLED", message=str(exc) or "cancelled"),)
    if isinstance(exc, QualityGateMissing):
        return (ErrorInfo(code="QUALITY_GATE_MISSING", message=str(exc)),)
    if isinstance(exc, UnsupportedFeature):
        return (ErrorInfo(code="UNSUPPORTED_FEATURE", message=str(exc)),)
    if isinstance(exc, UnsupportedSchemaVersion):
        return (ErrorInfo(code="SCHEMA_VERSION", message=str(exc)),)
    if isinstance(exc, FrameworkError):
        base = exc.info()
        code = base.code
        if code == "REGISTRY_UNKNOWN":
            code = "NOT_FOUND"
        return (ErrorInfo(code=code, message=base.message, path=base.path, hint=base.hint),)
    if isinstance(exc, IngestError):
        return (
            ErrorInfo(
                code=exc.code if exc.code.startswith("INGEST") else "INGEST_ERROR",
                message=str(exc) or "ingest failed",
            ),
        )
    if isinstance(exc, FileNotFoundError):
        return (
            ErrorInfo(
                code="NOT_FOUND",
                message=f"No such file: {exc.filename or exc}",
                hint="Check the path; it must exist and lie inside an allowed root.",
            ),
        )
    if isinstance(exc, OAError):
        return (ErrorInfo(code=str(exc.code), message=str(exc) or type(exc).__name__),)
    return (
        ErrorInfo(
            code="INTERNAL",
            message=f"{type(exc).__name__}: {exc}",
            hint="This is a bug; report it with the command and its inputs.",
        ),
    )
