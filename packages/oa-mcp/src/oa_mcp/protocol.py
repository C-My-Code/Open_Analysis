# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The narrow contract ``oa_mcp`` needs from ``oa_service`` (ai-integration section 2.2-2.3).

``oa_mcp`` is a thin adapter: it never imports ``oa_service``. Instead it talks to anything that
satisfies :class:`OaServiceLike`. The real catalog ``Registry`` (WP-207) is wrapped to fit, and the
tests use an in-memory fake. Every assumption made here is listed in the package README.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

SideEffect = Literal["read", "compute", "write"]
JsonDict = dict[str, Any]

#: Progress callback: ``(fraction_done 0..1, message)``. Called from the service's worker thread.
ProgressFn = Callable[[float, str | None], None]


@runtime_checkable
class CommandSpecLike(Protocol):
    """The subset of ``oa_service.commands.CommandSpec`` the adapter reads."""

    name: str
    mcp_tool: str | None
    title: str
    description: str
    side_effect: str
    idempotent: bool
    long_running: bool


@dataclass(frozen=True)
class McpCallContext:
    """Per-call context handed to ``dispatch`` (mirrors ``oa_service.CallContext``, section 2.2).

    Budgets are plain dicts with the section 10.4 field names so that the adapter needs no
    ``oa_jobs`` import.
    """

    roots: tuple[str, ...]
    allow_writes: bool
    budget_ceiling: Mapping[str, Any]
    default_budget: Mapping[str, Any]
    max_response_bytes: int = 24_576
    caller: Literal["mcp"] = "mcp"
    progress: ProgressFn | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ResourceContent:
    """One readable resource body."""

    mime_type: str
    text: str | None = None
    blob: bytes | None = None


class ResourceNotFound(LookupError):
    """Raised by ``read_resource`` for an unknown or out-of-roots URI."""


@runtime_checkable
class OaServiceLike(Protocol):
    """What ``oa_mcp`` calls. Everything returns plain JSON-compatible data."""

    def specs(self) -> Sequence[CommandSpecLike]:
        """All catalog commands (the adapter exposes those with an ``mcp_tool``)."""
        ...

    def input_schema(self, name: str) -> JsonDict:
        """JSON Schema of the command's input model."""
        ...

    def output_schema(self, name: str) -> JsonDict:
        """JSON Schema of the command's ``Envelope.data`` (not of the envelope)."""
        ...

    def dispatch(self, name: str, args: Mapping[str, Any], ctx: McpCallContext) -> JsonDict:
        """Run a command and return ``Envelope.model_dump(mode="json")``. Never raises."""
        ...

    def schema_ids(self) -> Sequence[str]:
        """Ids of every published schema (section 7.2), e.g. ``oa-setup/1``."""
        ...

    def read_resource(self, uri: str, ctx: McpCallContext) -> ResourceContent:
        """Read an ``oa://`` resource that is not served from bundled docs."""
        ...
