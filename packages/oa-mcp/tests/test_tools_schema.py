# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""tools/list: names, order, annotations and schemas."""

from __future__ import annotations

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from oa_mcp.config import Settings
from oa_mcp.contract import TOOLS
from oa_mcp.server import ContractError, build_server
from oa_service import Registry
from oa_service.testing import FakeService

pytestmark = pytest.mark.anyio


async def test_lists_every_contract_tool_sorted(client: ClientSession) -> None:
    names = [t.name for t in (await client.list_tools()).tools]
    assert names == sorted(names)
    assert set(TOOLS) <= set(names)
    # the catalog's extra read tools are exposed too, never anything unnamed
    assert {"list_jobs", "list_projects"} <= set(names)


async def test_order_is_stable_across_restarts(registry: Registry, settings: Settings) -> None:
    seen = []
    for _ in range(2):
        async with create_connected_server_and_client_session(
            build_server(registry, settings)
        ) as c:
            seen.append([t.model_dump_json() for t in (await c.list_tools()).tools])
    assert seen[0] == seen[1]


async def test_annotations_follow_side_effect(client: ClientSession, registry: Registry) -> None:
    by_tool = {s.mcp_tool: s for s in registry.specs()}
    for tool in (await client.list_tools()).tools:
        effect = by_tool[tool.name].side_effect
        ann = tool.annotations
        assert ann is not None and ann.openWorldHint is False
        if effect == "read":
            assert ann.readOnlyHint is True and ann.idempotentHint is True
        else:
            assert ann.readOnlyHint is False
            assert ann.destructiveHint is (effect == "write")
    assert by_tool["apply_proposal"].side_effect == "write"
    assert by_tool["propose_rule_changes"].side_effect == "compute"


async def test_schemas_come_from_the_catalog(client: ClientSession, registry: Registry) -> None:
    by_tool = {s.mcp_tool: s for s in registry.specs()}
    for tool in (await client.list_tools()).tools:
        spec = by_tool[tool.name]
        assert tool.inputSchema == registry.input_type(spec.name).model_json_schema(by_alias=True)
        assert tool.description == spec.description


async def test_output_schema_is_the_specialized_envelope(client: ClientSession) -> None:
    for tool in (await client.list_tools()).tools:
        out = tool.outputSchema
        assert out is not None
        assert out["required"] == ["schema", "version", "ok", "data", "warnings", "errors"]
        data = out["properties"]["data"]["anyOf"]
        assert data[-1] == {"type": "null"}
        assert "ErrorInfo" in out["$defs"]


async def test_spec_input_fields(client: ClientSession) -> None:
    tools = {t.name: t for t in (await client.list_tools()).tools}
    props = tools["run_erc"].inputSchema["properties"]
    assert {"project_id", "setup", "rules", "nets", "budget", "wait_s"} <= set(props)
    assert tools["apply_proposal"].inputSchema["required"] == ["proposal_id"]
    assert "confirm_token" in tools["apply_proposal"].inputSchema["properties"]
    assert tools["job_status"].inputSchema["properties"]["wait_s"]["maximum"] == 60


async def test_every_tool_property_has_a_description(client: ClientSession) -> None:
    """Section 7.1 lint over the tool input schemas (top-level properties)."""
    missing = [
        f"{t.name}.{name}"
        for t in (await client.list_tools()).tools
        for name, prop in t.inputSchema.get("properties", {}).items()
        if "description" not in prop
    ]
    assert missing == []


def test_missing_contract_tool_fails_fast(service: FakeService, settings: Settings) -> None:
    class Partial(Registry):
        def specs(self):  # type: ignore[no-untyped-def]
            return tuple(s for s in super().specs() if s.mcp_tool != "run_erc")

    with pytest.raises(ContractError, match="run_erc"):
        build_server(Partial(service), settings)
