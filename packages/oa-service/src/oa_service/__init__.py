# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Open_Analysis command catalog (ADR-0007, ai-integration.md section 2).

Public API (the contract WP-210 and the adapters build against):

- :class:`Registry` with :meth:`Registry.dispatch` returns an :class:`Envelope`.
- :class:`CommandSpec`, :class:`CallContext`, :class:`Envelope`, :class:`Page`, :class:`ErrorInfo`.
- :class:`OAService` / :class:`ProjectSession` protocols; :class:`oa_service.local.LocalService`
  (real) and :class:`oa_service.testing.FakeService` (in-memory, needs ``oa-service[testing]``).
- Third-party commands: entry-point group ``open_analysis.commands`` (see :class:`CommandDef`).
"""

from oa_service.commands import (
    GROUP_COMMANDS,
    CallContext,
    CommandDef,
    CommandSpec,
    Outcome,
    Registry,
    envelope_json,
    require_write,
)
from oa_service.errors import (
    CODE_EXIT,
    CODE_HTTP,
    ErrorInfo,
    ServiceError,
    ServiceErrors,
    exit_code_for,
    http_status_for,
    map_exception,
)
from oa_service.service import OAService, ProjectSession, RunRequest
from oa_service.types import Envelope, Page, PageRequest, mask_volatile

__all__ = [
    "CODE_EXIT",
    "CODE_HTTP",
    "GROUP_COMMANDS",
    "CallContext",
    "CommandDef",
    "CommandSpec",
    "Envelope",
    "ErrorInfo",
    "OAService",
    "Outcome",
    "Page",
    "PageRequest",
    "ProjectSession",
    "Registry",
    "RunRequest",
    "ServiceError",
    "ServiceErrors",
    "envelope_json",
    "exit_code_for",
    "http_status_for",
    "map_exception",
    "mask_volatile",
    "require_write",
]
