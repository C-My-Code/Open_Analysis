# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resources and resource templates (ai-integration section 8.4)."""

from __future__ import annotations

from collections.abc import Callable
from importlib import resources as ir
from typing import Any

import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource, ResourceTemplate

from oa_mcp.protocol import McpCallContext, OaServiceLike, ResourceContent

#: Bundled docs: resource name -> (file under ``oa_mcp/data``, mime type).
BUNDLED_DOCS: dict[str, tuple[str, str]] = {
    "oa://docs/agent-guide": ("agent-guide.md", "text/markdown"),
    "oa://docs/llms.txt": ("llms.txt", "text/plain"),
    "oa://docs/accuracy": ("accuracy.md", "text/markdown"),
}


def read_bundled(filename: str) -> str:
    """Text of a doc shipped inside the package."""
    return ir.files("oa_mcp").joinpath("data", filename).read_text(encoding="utf-8")


def _service_reader(service: OaServiceLike, ctx: McpCallContext, uri: str) -> Callable[[], Any]:
    def read() -> str | bytes:
        content: ResourceContent = service.read_resource(uri, ctx)
        if content.blob is not None:
            return content.blob
        return content.text or ""

    return read


def _template(
    service: OaServiceLike,
    ctx: McpCallContext,
    template: str,
    mime: str,
    description: str,
) -> ResourceTemplate:
    """A template whose function rebuilds the concrete URI from its parameters."""
    names = [p.split("}")[0] for p in template.split("{")[1:]]

    async def fn(**params: str) -> str | bytes:
        uri = template
        for key in names:
            uri = uri.replace("{" + key + "}", params[key])
        return await anyio.to_thread.run_sync(_service_reader(service, ctx, uri))

    return ResourceTemplate(
        uri_template=template,
        name=template.removeprefix("oa://").replace("/", "_").replace("{", "").replace("}", ""),
        description=description,
        mime_type=mime,
        fn=fn,
        context_kwarg=None,
        parameters={"type": "object", "properties": {n: {"type": "string"} for n in names}},
    )


_TEMPLATE_INFO: tuple[tuple[str, str, str], ...] = (
    ("oa://schemas/{schema_id}/{version}", "application/schema+json", "A published JSON Schema."),
    (
        "oa://docs/rules/{rule_id}",
        "text/markdown",
        "Rule title, explanation, config and references.",
    ),
    (
        "oa://projects/{pid}/board",
        "application/json",
        "Full board model JSON. Prefer the tools for boards over 1 MB.",
    ),
    (
        "oa://projects/{pid}/board/{part}",
        "application/json",
        "One part: meta, stackup, nets, net_classes, diff_pairs, components, outline, diagnostics.",
    ),
    ("oa://projects/{pid}/intent", "application/yaml", "Project intent file (read-only)."),
    ("oa://projects/{pid}/setups/{name}", "application/yaml", "A named setup (read-only)."),
    ("oa://projects/{pid}/physics", "application/yaml", "Physics sidecar (read-only)."),
    ("oa://results/{rid}", "application/json", "Result bundle manifest; arrays are $array refs."),
    (
        "oa://results/{rid}/items/{item}/arrays/{name}",
        "application/x-npy",
        "A raw array, at most 16 MiB; larger ones need export_report.",
    ),
    (
        "oa://results/{rid}/export/{format}",
        "application/octet-stream",
        "Same bytes as export_report.",
    ),
)


def register_resources(server: FastMCP, service: OaServiceLike, ctx: McpCallContext) -> None:
    """Register bundled docs, listed schemas, the catalog lists and every template."""
    for uri, (filename, mime) in BUNDLED_DOCS.items():
        server.add_resource(
            FunctionResource(
                uri=uri,  # type: ignore[arg-type]
                name=uri.removeprefix("oa://docs/"),
                description=f"Agent documentation ({filename}).",
                mime_type=mime,
                fn=lambda f=filename: read_bundled(f),  # type: ignore[misc]
            )
        )
    for uri, name, desc in (
        ("oa://analyses", "analyses", "Installed analyses ([AnalysisInfo])."),
        ("oa://rules", "rules", "Registered rules ([RuleInfo])."),
    ):
        server.add_resource(
            FunctionResource(
                uri=uri,  # type: ignore[arg-type]
                name=name,
                description=desc,
                mime_type="application/json",
                fn=_service_reader(service, ctx, uri),
            )
        )
    for schema_id in service.schema_ids():
        uri = f"oa://schemas/{schema_id}"
        server.add_resource(
            FunctionResource(
                uri=uri,  # type: ignore[arg-type]
                name=f"schema {schema_id}",
                description=f"JSON Schema {schema_id}.",
                mime_type="application/schema+json",
                fn=_service_reader(service, ctx, uri),
            )
        )
    for template, mime, desc in _TEMPLATE_INFO:
        t = _template(service, ctx, template, mime, desc)
        server._resource_manager._templates[template] = t
