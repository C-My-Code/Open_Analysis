# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command model, call context and the dispatching registry (ai-integration.md section 2.2).

``Registry.dispatch`` is the only place that validates arguments, enforces roots and write
permission, maps exceptions to ``ErrorInfo`` and wraps results in the ``Envelope``. CLI, REST,
MCP and the assistant call it (or the typed ``OAService`` methods behind it) and add no logic.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, JsonValue, ValidationError

from oa_jobs.models import Budget
from oa_model._base import OAModel
from oa_service.errors import (
    ErrorInfo,
    ServiceError,
    close_matches,
    from_validation_error,
    map_exception,
)
from oa_service.types import Envelope, oa_version

if TYPE_CHECKING:
    from oa_service.service import OAService

__all__ = [
    "GROUP_COMMANDS",
    "CallContext",
    "CommandDef",
    "CommandSpec",
    "Outcome",
    "Registry",
    "SideEffect",
    "require_write",
]

GROUP_COMMANDS = "open_analysis.commands"
"""Entry-point group for third-party commands. Each entry point loads a ``CommandDef`` or a
zero-argument callable returning an iterable of ``CommandDef``."""

SideEffect = Literal["read", "compute", "write"]
Caller = Literal["cli", "rest", "mcp", "assistant", "ui", "test"]


class CommandSpec(OAModel):
    """One catalog entry. The tool name, CLI path and schema ids are stable public names."""

    name: str = Field(description="Dotted stable name, for example 'board.open'.")
    mcp_tool: str | None = Field(
        description="snake_case MCP tool name; null when not exposed over MCP."
    )
    cli: tuple[str, ...] = Field(description="CLI path, for example ('results', 'violations').")
    title: str = Field(description="Short title.")
    description: str = Field(
        max_length=1024, description="What it does, when to use it, cost, side effects, units."
    )
    side_effect: SideEffect = Field(
        description="read: no state change; compute: may write under .oa/ and start jobs; "
        "write: modifies user files (two-step)."
    )
    idempotent: bool = Field(description="Repeating the call with the same input is harmless.")
    long_running: bool = Field(description="May return a JobHandle instead of data.")
    input_model: str = Field(description="Schema id of the input, 'oa.cmd.<name>.in/1'.")
    output_model: str = Field(description="Schema id of the output, 'oa.<output>/1'.")
    examples: tuple[dict[str, JsonValue], ...] = Field(
        default=(), description="Valid example inputs; the test suite runs every one."
    )


class CallContext(OAModel):
    """Who is calling and what they may do."""

    caller: Caller = Field(description="Calling surface; recorded in job provenance.")
    roots: tuple[Path, ...] = Field(
        default=(),
        description="Allow-listed directories. Empty means unrestricted (trusted local CLI/UI).",
    )
    allow_writes: bool = Field(
        default=False, description="Process-level switch for write commands."
    )
    budget_ceiling: Budget = Field(
        default_factory=Budget, description="Hard budget limits; per-call budgets are clamped."
    )
    default_budget: Budget = Field(
        default_factory=Budget, description="Budget used when a call gives none."
    )
    max_response_bytes: int = Field(
        default=24_576,
        ge=1024,
        description="Structured payload cap [B] enforced for mcp and assistant callers.",
    )

    def check_path(self, path: str | Path, *, must_exist: bool = False) -> Path:
        """Resolve ``path`` (symlinks included) and require it inside a root."""
        p = Path(path).expanduser()
        try:
            resolved = p.resolve()
        except OSError as exc:  # pragma: no cover - platform specific
            raise ServiceError("NOT_FOUND", f"Cannot resolve {path}: {exc}") from exc
        if self.roots and not any(
            resolved == r.resolve() or resolved.is_relative_to(r.resolve()) for r in self.roots
        ):
            raise ServiceError(
                "PERMISSION_DENIED",
                f"{resolved} is outside the allowed roots.",
                path="/path",
                hint="Open a path inside one of: " + ", ".join(str(r) for r in self.roots),
                details={"roots": [str(r) for r in self.roots]},
            )
        if must_exist and not resolved.exists():
            raise ServiceError(
                "NOT_FOUND",
                f"No such path: {resolved}",
                path="/path",
                hint="Check the spelling; the path must exist.",
            )
        return resolved

    def clamp(self, requested: Budget | None) -> Budget:
        """Effective budget: the request (or the default) clamped to the ceiling."""
        from oa_jobs.models import clamp_budget

        return clamp_budget(
            requested if requested is not None else self.default_budget, self.budget_ceiling
        )


def require_write(
    ctx: CallContext, confirm: bool, *, what: str, details: dict[str, JsonValue] | None = None
) -> None:
    """Gate for write commands: process switch first, then explicit confirmation."""
    if not ctx.allow_writes:
        raise ServiceError(
            "PERMISSION_DENIED",
            f"Writing {what} is disabled for this process.",
            hint="Start the server with writes enabled (for example `oa-mcp --allow-writes`).",
        )
    if not confirm:
        raise ServiceError(
            "CONFIRMATION_REQUIRED",
            f"Writing {what} needs confirmation.",
            path="/confirm",
            hint="Repeat the call with confirm=true (CLI: --yes) after reviewing the change.",
            details=details or {},
        )


@dataclass(frozen=True)
class Outcome:
    """A handler result with non-fatal warnings (truncation, extrapolation, defaults)."""

    data: OAModel
    warnings: tuple[ErrorInfo, ...] = ()


Handler = Callable[["OAService", Any, CallContext], "OAModel | Outcome"]


@dataclass(frozen=True)
class CommandDef:
    """A command: its spec, typed input/output models and the handler."""

    spec: CommandSpec
    input_type: type[OAModel]
    output_type: type[OAModel]
    handler: Handler = field(compare=False)
    cli_args: tuple[str, ...] = ()
    """Input fields the CLI takes as positional arguments, in order (all others are options)."""


def _sort_infos(infos: Iterable[ErrorInfo]) -> tuple[ErrorInfo, ...]:
    return tuple(sorted(infos, key=lambda e: (e.code, e.path or "", e.message)))


def _shrink_page(data: OAModel, cap: int) -> tuple[OAModel, ErrorInfo | None]:
    """Cut a Page-shaped output to fit ``cap`` bytes; never splits an item."""
    items = getattr(data, "items", None)
    if not isinstance(items, tuple) or not hasattr(data, "next_offset"):
        return data, None
    n = len(items)
    offset = int(getattr(data, "offset", 0))
    keep = n
    while keep > 1 and len(data.model_copy(update={"items": items[:keep]}).model_dump_json()) > cap:
        keep = max(1, keep // 2) if keep > 2 else keep - 1
    if keep == n:
        return data, None
    cut = data.model_copy(update={"items": items[:keep], "next_offset": offset + keep})
    warn = ErrorInfo(
        code="RESULT_TRUNCATED",
        message=f"Returned {keep} of {n} requested items to stay within {cap} bytes.",
        hint="Request the next page with offset=next_offset, or use a smaller limit.",
        details={"next_offset": offset + keep, "returned": keep},
    )
    return cut, warn


class Registry:
    """The command catalog bound to one service. ``specs`` is sorted by name."""

    def __init__(self, service: OAService, defs: Iterable[CommandDef] | None = None) -> None:
        from oa_service.catalog import builtin_commands

        self.service = service
        table: dict[str, CommandDef] = {}
        for d in [*builtin_commands(self), *(defs or ())]:
            if d.spec.name in table:
                raise ValueError(f"duplicate command {d.spec.name!r}")
            table[d.spec.name] = d
        self._defs = dict(sorted(table.items()))

    @classmethod
    def load(cls, service: OAService) -> Registry:
        """Built-in commands plus the ``open_analysis.commands`` entry points."""
        extra: list[CommandDef] = []
        for ep in sorted(entry_points(group=GROUP_COMMANDS), key=lambda e: e.name):
            obj = ep.load()
            produced = obj() if callable(obj) and not isinstance(obj, CommandDef) else obj
            extra.extend([produced] if isinstance(produced, CommandDef) else list(produced))
        return cls(service, extra)

    # -- introspection -----------------------------------------------------------------

    def definitions(self) -> tuple[CommandDef, ...]:
        return tuple(self._defs.values())

    def specs(self) -> tuple[CommandSpec, ...]:
        return tuple(d.spec for d in self._defs.values())

    def definition(self, name: str) -> CommandDef:
        try:
            return self._defs[name]
        except KeyError:
            raise ServiceError(
                "NOT_FOUND",
                f"Unknown command {name!r}.",
                path="/command",
                hint="Run `oa describe` for the catalog. Close matches: "
                + (", ".join(close_matches(name, self._defs)) or "none")
                + ".",
                details={"candidates": close_matches(name, self._defs)},
            ) from None

    def input_type(self, name: str) -> type[OAModel]:
        return self.definition(name).input_type

    def output_type(self, name: str) -> type[OAModel]:
        return self.definition(name).output_type

    def by_cli(self, path: tuple[str, ...]) -> CommandDef | None:
        for d in self._defs.values():
            if d.spec.cli == path:
                return d
        return None

    def by_mcp_tool(self, tool: str) -> CommandDef | None:
        for d in self._defs.values():
            if d.spec.mcp_tool == tool:
                return d
        return None

    # -- dispatch ------------------------------------------------------------------------

    def dispatch(self, name: str, args: Mapping[str, JsonValue], ctx: CallContext) -> Envelope[Any]:
        """Run one command and wrap the outcome. Never raises."""
        try:
            d = self.definition(name)
        except ServiceError as exc:
            return self._envelope("oa-error/1", None, (), (exc.info(),))
        schema = d.spec.output_model
        try:
            try:
                inp = d.input_type.model_validate(dict(args))
            except ValidationError as exc:
                return self._envelope(schema, None, (), from_validation_error(exc))
            if d.spec.side_effect == "write" and not ctx.allow_writes:
                require_write(ctx, False, what=d.spec.name)
            res = d.handler(self.service, inp, ctx)
            outcome = res if isinstance(res, Outcome) else Outcome(res)
            if not isinstance(outcome.data, d.output_type):
                raise TypeError(
                    f"{d.spec.name} returned {type(outcome.data).__name__}, "
                    f"expected {d.output_type.__name__}"
                )
            data, warnings = outcome.data, list(outcome.warnings)
            if ctx.caller in ("mcp", "assistant"):
                data, warn = _shrink_page(data, ctx.max_response_bytes)
                if warn is not None:
                    warnings.append(warn)
            return self._envelope(schema, data, tuple(warnings), ())
        except Exception as exc:
            return self._envelope(schema, None, (), map_exception(exc))

    def call_json(self, name: str, args: Mapping[str, JsonValue], ctx: CallContext) -> str:
        """Dispatch and return the compact envelope JSON (what ``oa --json`` prints)."""
        return envelope_json(self.dispatch(name, args, ctx))

    @staticmethod
    def _envelope(
        schema: str,
        data: OAModel | None,
        warnings: tuple[ErrorInfo, ...],
        errors: tuple[ErrorInfo, ...],
    ) -> Envelope[Any]:
        return Envelope[Any](
            schema=schema,
            version=oa_version(),
            ok=not errors,
            data=data,
            warnings=_sort_infos(warnings),
            errors=_sort_infos(errors),
        )


def envelope_json(env: Envelope[Any], *, indent: int | None = None) -> str:
    """Serialize an envelope: model key order, ``repr`` floats, no NaN."""
    doc = env.model_dump(mode="json", by_alias=True)
    return json.dumps(doc, indent=indent, ensure_ascii=False, allow_nan=False)
