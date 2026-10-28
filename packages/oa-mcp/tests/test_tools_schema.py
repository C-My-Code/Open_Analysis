# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""tools/list: names, order, annotations and schemas."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp_fake_service import FakeOaService

from oa_mcp.config import Settings
from oa_mcp.contract import TOOLS
from oa_mcp.server import ContractError, build_server

pytestmark = pytest.mark.anyio


def _walk_props(
    schema: Any, defs: dict[str, Any], path: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    if isinstance(schema, dict):
        for name, prop in schema.get("properties", {}).items():
            out.append((f"{path}/{name}", prop))
            out.extend(_walk_props(prop, defs, f"{path}/{name}"))
        for key in ("anyOf", "oneOf", "allOf"):
            for sub in schema.get(key, []):
                out.extend(_walk_props(sub, defs, path))
        if "items" in schema:
            out.extend(_walk_props(schema["items"], defs, path))
    return out


async def test_lists_exactly_the_contract_tools_sorted(client: ClientSession) -> None:
    tools = (await client.list_tools()).tools
    names = [t.name for t in tools]
    assert names == sorted(TOOLS)
    assert len(names) == 19


async def test_order_is_stable_across_restarts(service: FakeOaService, settings: Settings) -> None:
    from mcp.shared.memory import create_connected_server_and_client_session

    seen = []
    for _ in range(2):
        async with create_connected_server_and_client_session(build_server(service, settings)) as c:
            seen.append([t.model_dump_json() for t in (await c.list_tools()).tools])
    assert seen[0] == seen[1]


async def test_annotations_follow_side_effect(client: ClientSession) -> None:
    for tool in (await client.list_tools()).tools:
        effect = TOOLS[tool.name][1]
        ann = tool.annotations
        assert ann is not None
        assert ann.openWorldHint is False
        if effect == "read":
            assert ann.readOnlyHint is True
            assert ann.idempotentHint is True
        elif effect == "compute":
            assert ann.readOnlyHint is False
            assert ann.destructiveHint is False
        else:
            assert tool.name == "apply_proposal"
            assert ann.destructiveHint is True


async def test_input_schemas_come_from_the_service(
    client: ClientSession, service: FakeOaService
) -> None:
    by_cmd = {s.mcp_tool: s.name for s in service.specs()}
    for tool in (await client.list_tools()).tools:
        assert tool.inputSchema == service.input_schema(by_cmd[tool.name])
        assert tool.description


async def test_output_schema_is_the_specialized_envelope(client: ClientSession) -> None:
    for tool in (await client.list_tools()).tools:
        out = tool.outputSchema
        assert out is not None
        assert out["required"] == ["schema", "version", "ok", "data", "warnings", "errors"]
        data = out["properties"]["data"]["anyOf"]
        assert data[-1] == {"type": "null"}
        assert "ErrorInfo" in out["$defs"]
        assert set(out["properties"]) == {"schema", "version", "ok", "data", "warnings", "errors"}


async def test_spec_input_fields(client: ClientSession) -> None:
    """A few §8.3 inputs, checked by name."""
    tools = {t.name: t for t in (await client.list_tools()).tools}
    props = tools["run_erc"].inputSchema["properties"]
    assert {"project_id", "setup", "rules", "nets", "budget", "wait_s"} <= set(props)
    assert props["wait_s"]["maximum"] == 120
    assert tools["apply_proposal"].inputSchema["required"] == ["proposal_id", "confirm_token"]
    assert tools["job_status"].inputSchema["properties"]["wait_s"]["maximum"] == 60


async def test_schema_descriptions_and_units(client: ClientSession) -> None:
    """Section 7.1 lint: every property has a description, numerics carry [unit]."""
    problems: list[str] = []
    for tool in (await client.list_tools()).tools:
        for label, schema in (("in", tool.inputSchema), ("out", tool.outputSchema or {})):
            for path, prop in _walk_props(schema, schema.get("$defs", {})):
                if "description" not in prop and "$ref" not in prop and "anyOf" not in prop:
                    problems.append(f"{tool.name}.{label}{path}: no description")
                kinds = {prop.get("type")} | {a.get("type") for a in prop.get("anyOf", [])}
                if kinds & {"integer", "number"} and not prop.get("x-oa-unitless"):
                    desc = prop.get("description", "")
                    if "[" not in desc and prop.get("type") != "array":
                        problems.append(f"{tool.name}.{label}{path}: numeric without [unit]")
    assert problems == []


def test_missing_contract_tool_fails_fast(settings: Settings) -> None:
    class Partial(FakeOaService):
        def specs(self):  # type: ignore[no-untyped-def]
            return [s for s in super().specs() if s.mcp_tool != "run_erc"]

    with pytest.raises(ContractError, match="run_erc"):
        build_server(Partial(), settings)
