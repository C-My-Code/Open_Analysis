# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tools generated from the catalog (ai-integration section 8.2). No analysis logic lives here."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

import anyio
import anyio.from_thread
import anyio.to_thread
import mcp.types as mt

from oa_mcp.protocol import CommandSpecLike, JsonDict, McpCallContext, OaServiceLike

_ERROR_INFO_SCHEMA: JsonDict = {
    "type": "object",
    "description": "Structured error or warning (ErrorInfo, section 3.2).",
    "properties": {
        "code": {"type": "string", "description": "Stable UPPER_SNAKE code (section 6)."},
        "message": {"type": "string", "description": "One sentence, no stack trace."},
        "path": {
            "type": ["string", "null"],
            "description": "RFC 6901 JSON Pointer into the command input, if applicable.",
        },
        "hint": {
            "type": ["string", "null"],
            "description": "One actionable sentence: what to change or call next.",
        },
        "source": {
            "type": ["object", "null"],
            "description": "File and line/column for file errors.",
        },
        "details": {"type": "object", "description": "Code-specific details."},
    },
    "required": ["code", "message"],
}


def envelope_schema(data_schema: JsonDict) -> JsonDict:
    """Specialize the ``oa-envelope/1`` schema to one command's ``data`` schema (section 8.2).

    The ``$defs`` of ``data_schema`` are hoisted to the root so that ``#/$defs/...`` references
    keep resolving.
    """
    data = copy.deepcopy(data_schema)
    defs = data.pop("$defs", {})
    defs["ErrorInfo"] = _ERROR_INFO_SCHEMA
    err_ref: JsonDict = {"$ref": "#/$defs/ErrorInfo"}
    return {
        "type": "object",
        "description": "Command envelope (oa-envelope/1).",
        "properties": {
            "schema": {
                "type": "string",
                "description": "Schema id of data, e.g. oa.board_summary/1.",
            },
            "version": {"type": "string", "description": "Producing Open_Analysis version."},
            "ok": {"type": "boolean", "description": "False iff errors is non-empty."},
            "data": {
                "description": "The command output; null when ok is false.",
                "anyOf": [data, {"type": "null"}],
            },
            "warnings": {
                "type": "array",
                "description": "Non-fatal notices such as truncation.",
                "items": err_ref,
            },
            "errors": {"type": "array", "description": "Fatal errors.", "items": err_ref},
        },
        "required": ["schema", "version", "ok", "data", "warnings", "errors"],
        "$defs": defs,
    }


def error_envelope(
    schema: str,
    code: str,
    message: str,
    *,
    hint: str | None = None,
    path: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> JsonDict:
    """Build an ``ok=false`` envelope for faults detected by the adapter itself."""
    err: JsonDict = {
        "code": code,
        "message": message,
        "path": path,
        "hint": hint,
        "source": None,
        "details": dict(details or {}),
    }
    return {
        "schema": schema,
        "version": "0.0.0",
        "ok": False,
        "data": None,
        "warnings": [],
        "errors": [err],
    }


def digest(envelope: Mapping[str, Any]) -> str:
    """Plain-text digest for clients that ignore structured output (section 8.2)."""
    if not envelope.get("ok", False):
        lines = [
            f"{e.get('code', 'ERROR')}: {e.get('message', '')}"
            + (f" Hint: {e['hint']}" if e.get("hint") else "")
            for e in envelope.get("errors", [])
        ]
        return "\n".join(lines) or "error"
    data = envelope.get("data")
    if isinstance(data, Mapping):
        text = data.get("text")
        if not isinstance(text, str):
            summary = data.get("summary")
            text = summary.get("text") if isinstance(summary, Mapping) else None
        if isinstance(text, str) and text:
            return text
    return f"ok ({envelope.get('schema', '')})"


def _size(envelope: Mapping[str, Any]) -> int:
    return len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class ToolAdapter:
    """Turns catalog commands into MCP tools and routes calls back to the service."""

    def __init__(self, service: OaServiceLike, ctx: McpCallContext) -> None:
        self._service = service
        self._ctx = ctx
        self._specs: dict[str, CommandSpecLike] = {
            s.mcp_tool: s for s in service.specs() if s.mcp_tool
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def list_tools(self) -> list[mt.Tool]:
        """Tools sorted by name, so ``tools/list`` is stable across restarts."""
        tools: list[mt.Tool] = []
        for tool_name in self.tool_names:
            spec = self._specs[tool_name]
            tools.append(
                mt.Tool(
                    name=tool_name,
                    title=spec.title,
                    description=spec.description,
                    inputSchema=self._service.input_schema(spec.name),
                    outputSchema=envelope_schema(self._service.output_schema(spec.name)),
                    annotations=self._annotations(spec),
                )
            )
        return tools

    @staticmethod
    def _annotations(spec: CommandSpecLike) -> mt.ToolAnnotations:
        if spec.side_effect == "read":
            return mt.ToolAnnotations(
                title=spec.title, readOnlyHint=True, idempotentHint=True, openWorldHint=False
            )
        if spec.side_effect == "write":
            return mt.ToolAnnotations(
                title=spec.title,
                readOnlyHint=False,
                destructiveHint=True,
                idempotentHint=spec.idempotent,
                openWorldHint=False,
            )
        return mt.ToolAnnotations(
            title=spec.title,
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=spec.idempotent,
            openWorldHint=False,
        )

    async def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        report_progress: Any = None,
    ) -> mt.CallToolResult:
        """Run one tool. ``report_progress`` is an async ``(progress, total, message)`` callable."""
        spec = self._specs.get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        envelope = await self._dispatch(spec, arguments, report_progress)
        envelope = self._bound(spec, envelope)
        ok = bool(envelope.get("ok", False))
        return mt.CallToolResult(
            content=[mt.TextContent(type="text", text=digest(envelope))],
            structuredContent=envelope,
            isError=not ok,
        )

    async def _dispatch(
        self, spec: CommandSpecLike, arguments: Mapping[str, Any], report_progress: Any
    ) -> JsonDict:
        schema = f"oa.cmd.{spec.name}/1"
        if spec.side_effect == "write":
            if not self._ctx.allow_writes:
                return error_envelope(
                    schema,
                    "PERMISSION_DENIED",
                    "This server was started without write access.",
                    hint="Restart oa-mcp with --allow-writes to enable apply_proposal.",
                )
            if not arguments.get("confirm_token"):
                return error_envelope(
                    schema,
                    "CONFIRMATION_REQUIRED",
                    "A write needs the confirm_token returned by the proposal step.",
                    path="/confirm_token",
                    hint="Call propose_rule_changes, review the diff, then pass its confirm_token.",
                )
        ctx = self._ctx
        if report_progress is not None:

            def progress(fraction: float, message: str | None) -> None:
                # Runs in the service's worker thread; hop back to the event loop.
                anyio.from_thread.run(report_progress, fraction, 1.0, message)

            ctx = McpCallContext(
                roots=ctx.roots,
                allow_writes=ctx.allow_writes,
                budget_ceiling=ctx.budget_ceiling,
                default_budget=ctx.default_budget,
                max_response_bytes=ctx.max_response_bytes,
                progress=progress,
            )
        try:
            return await anyio.to_thread.run_sync(
                lambda: self._service.dispatch(spec.name, dict(arguments), ctx)
            )
        except Exception as exc:  # the service contract says "never raises"; stay safe anyway
            return error_envelope(
                schema,
                "INTERNAL",
                f"Unexpected failure in {spec.name}: {type(exc).__name__}.",
                hint="Report this with the server log (stderr).",
            )

    def _bound(self, spec: CommandSpecLike, envelope: JsonDict) -> JsonDict:
        """Last-resort size cap. The command's own pagination should make this a no-op."""
        limit = self._ctx.max_response_bytes
        size = _size(envelope)
        if size <= limit:
            return envelope
        return error_envelope(
            str(envelope.get("schema", f"oa.cmd.{spec.name}/1")),
            "RESULT_TRUNCATED",
            f"The response ({size} bytes) exceeds the {limit}-byte limit and was dropped.",
            hint="Narrow the request: lower limit, add filters, or use result_summary.",
            details={"bytes": size, "max_response_bytes": limit},
        )
