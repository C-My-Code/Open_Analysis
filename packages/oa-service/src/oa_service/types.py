# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Envelope, pagination and determinism helpers (ai-integration.md section 3)."""

from __future__ import annotations

import re
from typing import Any, Generic, TypeVar

from pydantic import Field, JsonValue

from oa_model._base import OAModel
from oa_service.errors import ErrorInfo

__all__ = [
    "ENVELOPE_SCHEMA",
    "VOLATILE_KEYS",
    "Envelope",
    "Page",
    "PageRequest",
    "mask_volatile",
    "oa_version",
    "paginate",
]

ENVELOPE_SCHEMA = "oa-envelope/1"

T = TypeVar("T")

# ai-integration.md section 3.5: fields whose value legitimately differs between runs.
VOLATILE_KEYS = frozenset(
    {
        "elapsed_s",
        "host",
        "version",
        "job_id",
        "result_id",
        "proposal_id",
        "created_utc",
        "updated_utc",
        "started_utc",
        "finished_utc",
        "expires_utc",
        "utc",
        "budget_used",
        "path_hash",
    }
)
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def oa_version() -> str:
    """Producing Open_Analysis version (PEP 440)."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("oa-service")
    except PackageNotFoundError:  # pragma: no cover - source checkout without install
        return "0.0.0"


class Envelope(OAModel, Generic[T]):
    """Stable result wrapper used by the CLI (``--json``), MCP and the assistant.

    ``ok`` is False iff ``errors`` is non-empty, and then ``data`` is None. ``warnings`` and
    ``errors`` are sorted by ``(code, path)``.
    """

    schema: str = Field(  # type: ignore[assignment]
        description="Schema id of `data` (see `oa schema ID`), e.g. 'oa.board_summary/1'."
    )
    version: str = Field(description="Producing Open_Analysis version (PEP 440).")
    ok: bool = Field(description="True iff `errors` is empty.")
    data: T | None = Field(description="The command output; null when ok is false.")
    warnings: tuple[ErrorInfo, ...] = Field(
        default=(), description="Non-fatal findings: truncation, extrapolation, defaulted stackup."
    )
    errors: tuple[ErrorInfo, ...] = Field(default=(), description="Failures; empty when ok.")


class PageRequest(OAModel):
    """Paging window shared by every list command."""

    offset: int = Field(default=0, ge=0, description="Index of the first item to return [1].")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum items to return [1].")


class Page(OAModel, Generic[T]):
    """One page of a filtered, totally ordered list."""

    items: tuple[T, ...] = Field(description="The items of this page.")
    offset: int = Field(description="Index of the first item [1].")
    limit: int = Field(description="Requested page size [1].")
    total: int = Field(description="Total matching items after filtering [1].")
    next_offset: int | None = Field(description="Offset of the next page; null when exhausted.")
    sort: str = Field(description="The effective sort key, documented per command.")


def paginate(items: list[Any], req: PageRequest, sort: str) -> dict[str, Any]:
    """Slice an already sorted list into ``Page`` constructor kwargs."""
    total = len(items)
    end = req.offset + req.limit
    return {
        "items": tuple(items[req.offset : end]),
        "offset": req.offset,
        "limit": req.limit,
        "total": total,
        "next_offset": end if end < total else None,
        "sort": sort,
    }


def mask_volatile(obj: JsonValue, mask: str = "<volatile>") -> JsonValue:
    """Replace the volatile fields of a JSON document (ai-integration.md section 3.5).

    Keys in :data:`VOLATILE_KEYS`, any key ending in ``_utc``, and ULID-valued ``id`` fields are
    masked, so two runs of the same command compare equal.
    """
    if isinstance(obj, dict):
        out: dict[str, JsonValue] = {}
        for k, v in obj.items():
            if (
                k in VOLATILE_KEYS
                or k.endswith("_utc")
                or (k == "id" and isinstance(v, str) and _ULID.match(v))
            ):
                out[k] = mask
            else:
                out[k] = mask_volatile(v, mask)
        return out
    if isinstance(obj, list):
        return [mask_volatile(v, mask) for v in obj]
    return obj
