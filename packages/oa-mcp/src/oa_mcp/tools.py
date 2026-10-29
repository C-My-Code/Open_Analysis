# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tools generated from the catalog (ai-integration section 8.2). No analysis logic lives here."""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import anyio
import anyio.to_thread
import mcp.types as mt

from oa_service import CallContext, Registry
from oa_service.commands import CommandSpec

JsonDict = dict[str, Any]
ProgressFn = Callable[[float, float | None, str | None], Awaitable[None]]
#: Asks the user to confirm a write: ``(message) -> accepted``; None when the client cannot.
ConfirmFn = Callable[[str], Awaitable[bool]]

POLL_S = 1.0
VERSION_FALLBACK = "0.0.0"


def envelope_schema(data_schema: JsonDict) -> JsonDict:
    """Specialize the ``oa-envelope/1`` schema to one command's ``data`` schema (section 8.2).

    The ``$defs`` of ``data_schema`` are hoisted to the root so that ``#/$defs/...`` references
    keep resolving. ``ErrorInfo`` is shared with the command's own definition when it has one.
    """
    data = copy.deepcopy(data_schema)
    defs = data.pop("$defs", {})
    err_ref: JsonDict = {"$ref": "#/$defs/ErrorInfo"}
    defs.setdefault(
        "ErrorInfo",
        {
            "type": "object",
            "description": "Structured error or warning.",
            "properties": {
                "code": {"type": "string", "description": "Stable UPPER_SNAKE code."},
                "message": {"type": "string", "description": "One sentence."},
                "path": {"type": ["string", "null"], "description": "JSON Pointer into the input."},
                "hint": {"type": ["string", "null"], "description": "What to do next."},
                "details": {"type": "object", "description": "Code-specific details."},
            },
            "required": ["code", "message"],
        },
    )
    return {
        "type": "object",
        "description": "Command envelope (oa-envelope/1).",
        "properties": {
            "schema": {"type": "string", "description": "Schema id of data."},
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


def error_envelope(schema: str, code: str, message: str, **kw: Any) -> JsonDict:
    """An ``ok=false`` envelope for faults detected by the adapter itself."""
    err: JsonDict = {
        "code": code,
        "message": message,
        "path": kw.get("path"),
        "hint": kw.get("hint"),
        "details": kw.get("details", {}),
    }
    return {
        "schema": schema,
        "version": VERSION_FALLBACK,
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
            for key in ("summary", "job"):
                sub = data.get(key)
                if isinstance(sub, Mapping):
                    inner = sub.get("summary") if key == "job" else sub
                    text = inner.get("text") if isinstance(inner, Mapping) else None
                    if isinstance(text, str):
                        break
        if isinstance(text, str) and text:
            return text
    return f"ok ({envelope.get('schema', '')})"


def _size(envelope: Mapping[str, Any]) -> int:
    return len(json.dumps(envelope, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class ToolAdapter:
    """Turns catalog commands into MCP tools and routes calls back to ``Registry.dispatch``."""

    def __init__(self, registry: Registry, ctx: CallContext, default_executor: str = "process"):
        self._registry = registry
        self._ctx = ctx
        self._executor = default_executor
        self._specs: dict[str, CommandSpec] = {
            s.mcp_tool: s for s in registry.specs() if s.mcp_tool
        }

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    def input_schema(self, spec: CommandSpec) -> JsonDict:
        return self._registry.input_type(spec.name).model_json_schema(by_alias=True)

    def list_tools(self) -> list[mt.Tool]:
        """Tools sorted by name, so ``tools/list`` is stable across restarts."""
        tools: list[mt.Tool] = []
        for tool_name in self.tool_names:
            spec = self._specs[tool_name]
            out = self._registry.output_type(spec.name).model_json_schema(
                by_alias=True, mode="serialization"
            )
            tools.append(
                mt.Tool(
                    name=tool_name,
                    title=spec.title,
                    description=spec.description,
                    inputSchema=self.input_schema(spec),
                    outputSchema=envelope_schema(out),
                    annotations=self._annotations(spec),
                )
            )
        return tools

    @staticmethod
    def _annotations(spec: CommandSpec) -> mt.ToolAnnotations:
        if spec.side_effect == "read":
            return mt.ToolAnnotations(
                title=spec.title, readOnlyHint=True, idempotentHint=True, openWorldHint=False
            )
        return mt.ToolAnnotations(
            title=spec.title,
            readOnlyHint=False,
            destructiveHint=spec.side_effect == "write",
            idempotentHint=spec.idempotent,
            openWorldHint=False,
        )

    async def call(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        report_progress: ProgressFn | None = None,
        confirm: ConfirmFn | None = None,
    ) -> mt.CallToolResult:
        """Run one tool. ``report_progress``/``confirm`` come from the MCP request context."""
        spec = self._specs.get(tool_name)
        if spec is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        args = dict(arguments)
        if "executor" in self.input_schema(spec).get("properties", {}):
            args.setdefault("executor", self._executor)
        if spec.side_effect == "write" and confirm is not None:
            refusal = await self._ask_user(spec, args, confirm)
            if refusal is not None:
                return self._result(spec, refusal)
        env = await self._dispatch(spec, args, report_progress)
        return self._result(spec, env)

    def _result(self, spec: CommandSpec, envelope: JsonDict) -> mt.CallToolResult:
        envelope = self._bound(spec, envelope)
        return mt.CallToolResult(
            content=[mt.TextContent(type="text", text=digest(envelope))],
            structuredContent=envelope,
            isError=not envelope.get("ok", False),
        )

    async def _dispatch(
        self, spec: CommandSpec, args: JsonDict, report_progress: ProgressFn | None = None
    ) -> JsonDict:
        def run() -> JsonDict:
            try:
                env = self._registry.dispatch(spec.name, args, self._ctx)
                dumped: JsonDict = env.model_dump(mode="json", by_alias=True)
                return dumped
            except Exception as exc:  # dispatch "never raises"; stay safe anyway
                return error_envelope(
                    spec.output_model,
                    "INTERNAL",
                    f"Unexpected failure in {spec.name}: {type(exc).__name__}.",
                    hint="Report this with the server log (stderr).",
                )

        if report_progress is None or not spec.long_running:
            return await anyio.to_thread.run_sync(run)
        result: list[JsonDict] = []

        async def work() -> None:
            result.append(await anyio.to_thread.run_sync(run))
            tg.cancel_scope.cancel()

        async with anyio.create_task_group() as tg:
            tg.start_soon(work)
            await self._poll_progress(args, report_progress)
        return result[0]

    async def _poll_progress(self, args: JsonDict, report: ProgressFn) -> None:
        """Forward the progress of the project's running jobs while a long call blocks."""
        project_id = args.get("project_id")
        if not isinstance(project_id, str):
            return
        svc = self._registry.service
        while True:
            await anyio.sleep(POLL_S)
            try:
                running = await anyio.to_thread.run_sync(
                    lambda: svc.jobs(project_id, state="running", limit=1)
                )
            except Exception:  # polling is best effort
                continue
            if running:
                job = running[0]
                await report(
                    float(job.progress), 1.0, f"{job.analysis} {job.job_id}: {job.message}"
                )

    async def _ask_user(
        self, spec: CommandSpec, args: JsonDict, confirm: ConfirmFn
    ) -> JsonDict | None:
        """Elicit confirmation with the diff. Returns a refusal envelope, or None to proceed."""
        probe = {k: v for k, v in args.items() if k not in ("confirm_token", "confirm")}
        env = await self._dispatch(spec, probe)
        errors = env.get("errors") or []
        if not errors or errors[0].get("code") != "CONFIRMATION_REQUIRED":
            return None if env.get("ok") else env  # e.g. PERMISSION_DENIED, NOT_FOUND
        files = (errors[0].get("details") or {}).get("files", [])
        diff = "\n".join(f"--- {f.get('path')}\n{f.get('unified_diff', '')}" for f in files)
        if not await confirm(f"Apply this change to your KiCad rule files?\n\n{diff[:6000]}"):
            return error_envelope(
                spec.output_model,
                "CONFIRMATION_REQUIRED",
                "The user declined the change.",
                hint="Do not retry without asking the user.",
            )
        return None

    def _bound(self, spec: CommandSpec, envelope: JsonDict) -> JsonDict:
        """Last-resort size cap. The command's own pagination should make this a no-op."""
        limit = self._ctx.max_response_bytes
        size = _size(envelope)
        if size <= limit:
            return envelope
        return error_envelope(
            str(envelope.get("schema", spec.output_model)),
            "RESULT_TRUNCATED",
            f"The response ({size} bytes) exceeds the {limit}-byte limit and was dropped.",
            hint="Narrow the request: lower limit, add filters, or use result_summary.",
            details={"bytes": size, "max_response_bytes": limit},
        )
