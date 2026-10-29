# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resources and resource templates (ai-integration section 8.4), backed by ``oa_service``."""

from __future__ import annotations

import io
import json
import re
import tempfile
from dataclasses import dataclass
from importlib import resources as ir
from pathlib import Path
from typing import Any

import anyio.to_thread
import numpy as np
import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource, ResourceTemplate

from oa_mcp.contract import MAX_ARRAY_BYTES
from oa_model.io import dumps_board
from oa_service import CallContext, Registry, ServiceError
from oa_service.results_view import export_bundle, item_arrays, manifest
from oa_service.schemas import schema_entries, schema_for

#: Bundled docs: resource uri -> (file under ``oa_mcp/data``, mime type).
BUNDLED_DOCS: dict[str, tuple[str, str]] = {
    "oa://docs/agent-guide": ("agent-guide.md", "text/markdown"),
    "oa://docs/llms.txt": ("llms.txt", "text/plain"),
    "oa://docs/accuracy": ("accuracy.md", "text/markdown"),
}
BOARD_PARTS = (
    "meta",
    "stackup",
    "nets",
    "net_classes",
    "diff_pairs",
    "components",
    "outline",
    "diagnostics",
)
_MEDIA = {"json": "application/json", "md": "text/markdown", "html": "text/html"}


def read_bundled(filename: str) -> str:
    """Text of a doc shipped inside the package."""
    return ir.files("oa_mcp").joinpath("data", filename).read_text(encoding="utf-8")


@dataclass(frozen=True)
class ResourceContent:
    """One readable resource body."""

    mime_type: str
    text: str | None = None
    blob: bytes | None = None


class ResourceNotFound(LookupError):
    """An unknown, unreadable or out-of-roots URI."""


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False)


class ResourceReader:
    """Resolves ``oa://`` URIs through the registry and its service. Read-only."""

    def __init__(self, registry: Registry, ctx: CallContext) -> None:
        self.registry = registry
        self.svc = registry.service
        self.ctx = ctx

    def schema_ids(self) -> list[str]:
        return [e.id for e in schema_entries(self.registry)]

    def read(self, uri: str) -> ResourceContent:
        try:
            return self._read(uri)
        except ServiceError as exc:
            info = exc.info()
            raise ResourceNotFound(
                f"{info.code}: {info.message} {info.hint or ''}".strip()
            ) from exc

    def _read(self, uri: str) -> ResourceContent:
        if m := re.fullmatch(r"oa://schemas/(.+)", uri):
            return ResourceContent(
                "application/schema+json", _json(schema_for(self.registry, m.group(1)))
            )
        if uri == "oa://analyses":
            return ResourceContent(
                "application/json", _json([a.model_dump(mode="json") for a in self.svc.analyses()])
            )
        if uri == "oa://rules":
            return ResourceContent(
                "application/json", _json([r.model_dump(mode="json") for r in self.svc.rules()])
            )
        if m := re.fullmatch(r"oa://docs/rules/([^/]+)", uri):
            return ResourceContent("text/markdown", self._rule_doc(m.group(1)))
        if m := re.fullmatch(r"oa://projects/([^/]+)/(.+)", uri):
            return self._project(m.group(1), m.group(2))
        if m := re.fullmatch(r"oa://results/([^/]+)", uri):
            bundle = self.svc.result(m.group(1))
            return ResourceContent("application/json", manifest(bundle).model_dump_json(indent=1))
        if m := re.fullmatch(r"oa://results/([^/]+)/items/([^/]+)/arrays/([^/]+)", uri):
            return self._array(*m.groups())
        if m := re.fullmatch(r"oa://results/([^/]+)/export/([^/]+)", uri):
            return self._export(m.group(1), m.group(2))
        raise ResourceNotFound(f"Unknown resource {uri}")

    def _rule_doc(self, rule_id: str) -> str:
        for r in self.svc.rules():
            if rule_id == r.id or rule_id in r.aliases:
                refs = "\n".join(f"- {x}" for x in r.references) or "- none"
                return (
                    f"# {r.id}: {r.title}\n\nCategory: {r.category}. "
                    f"Default severity: {r.default_severity}.\n\n{r.explanation}\n\n"
                    f"## Config schema\n\n```json\n{_json(r.config_schema)}\n```\n\n"
                    f"## References\n\n{refs}\n"
                )
        raise ResourceNotFound(f"No rule {rule_id!r}; see oa://rules")

    def _project(self, pid: str, rest: str) -> ResourceContent:
        session = self.svc.project(pid)
        if rest == "board":
            return ResourceContent("application/json", dumps_board(session.board))
        if m := re.fullmatch(r"board/(\w+)", rest):
            part = m.group(1)
            if part not in BOARD_PARTS:
                raise ResourceNotFound(
                    f"Unknown board part {part!r}; parts: {', '.join(BOARD_PARTS)}"
                )
            doc = json.loads(dumps_board(session.board))
            if part not in doc:
                raise ResourceNotFound(f"The board has no {part!r} section.")
            return ResourceContent("application/json", _json(doc[part]))
        if m := re.fullmatch(r"setups/([^/]+)", rest):
            doc = session.setup(m.group(1)).model_dump(mode="json", by_alias=True)
            return ResourceContent("application/yaml", yaml.safe_dump(doc, sort_keys=False))
        if rest == "intent":
            root = getattr(self.svc, "root_of", None)
            path = Path(root(session)) / "oa" / "intent.yaml" if callable(root) else None
            if path is not None and path.is_file():
                self.ctx.check_path(path)
                return ResourceContent("application/yaml", path.read_text(encoding="utf-8"))
            raise ResourceNotFound("This project has no oa/intent.yaml.")
        raise ResourceNotFound(f"Resource oa://projects/{pid}/{rest} is not available.")

    def _array(self, rid: str, item: str, name: str) -> ResourceContent:
        arrays = item_arrays(self.svc.result(rid), item)
        if name not in arrays:
            raise ResourceNotFound(
                f"No array {name!r} in item {item}; arrays: {', '.join(sorted(arrays)) or 'none'}"
            )
        arr = arrays[name]
        if arr.nbytes > MAX_ARRAY_BYTES:
            raise ResourceNotFound(
                f"Array {name!r} is {arr.nbytes} bytes (> {MAX_ARRAY_BYTES}); use export_report."
            )
        buf = io.BytesIO()
        np.save(buf, arr, allow_pickle=False)
        return ResourceContent("application/x-npy", blob=buf.getvalue())

    def _export(self, rid: str, fmt: str) -> ResourceContent:
        if fmt not in ("html", "sarif", "touchstone", "csv", "vtu", "json", "md"):
            raise ResourceNotFound(f"Unknown export format {fmt!r}.")
        bundle = self.svc.result(rid)
        with tempfile.TemporaryDirectory() as td:
            info = export_bundle(bundle, fmt, Path(td) / f"export.{fmt}")  # type: ignore[arg-type]
            data = Path(info.path).read_bytes()
        mime = _MEDIA.get(fmt, info.media_type)
        return (
            ResourceContent(mime, text=data.decode("utf-8"))
            if fmt in _MEDIA
            else ResourceContent(info.media_type, blob=data)
        )


def _reader_fn(reader: ResourceReader, uri: str) -> Any:
    def read() -> str | bytes:
        c = reader.read(uri)
        return c.blob if c.blob is not None else (c.text or "")

    return read


_TEMPLATE_INFO: tuple[tuple[str, str, str], ...] = (
    ("oa://schemas/{schema_id}/{version}", "application/schema+json", "A published JSON Schema."),
    ("oa://docs/rules/{rule_id}", "text/markdown", "Rule title, explanation, config, references."),
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
    ("oa://results/{rid}", "application/json", "Result bundle manifest."),
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


def _template(reader: ResourceReader, template: str, mime: str, desc: str) -> ResourceTemplate:
    names = [p.split("}")[0] for p in template.split("{")[1:]]

    async def fn(**params: str) -> str | bytes:
        uri = template
        for key in names:
            uri = uri.replace("{" + key + "}", params[key])
        return await anyio.to_thread.run_sync(_reader_fn(reader, uri))

    return ResourceTemplate(
        uri_template=template,
        name=template.removeprefix("oa://").replace("/", "_").replace("{", "").replace("}", ""),
        description=desc,
        mime_type=mime,
        fn=fn,
        context_kwarg=None,
        parameters={"type": "object", "properties": {n: {"type": "string"} for n in names}},
    )


def register_resources(server: FastMCP, reader: ResourceReader) -> None:
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
                fn=_reader_fn(reader, uri),
            )
        )
    for schema_id in reader.schema_ids():
        uri = f"oa://schemas/{schema_id}"
        server.add_resource(
            FunctionResource(
                uri=uri,  # type: ignore[arg-type]
                name=f"schema {schema_id}",
                description=f"JSON Schema {schema_id}.",
                mime_type="application/schema+json",
                fn=_reader_fn(reader, uri),
            )
        )
    for template, mime, desc in _TEMPLATE_INFO:
        server._resource_manager._templates[template] = _template(reader, template, mime, desc)
