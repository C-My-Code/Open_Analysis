# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resources, resource templates and prompts."""

from __future__ import annotations

import json

import pytest
from mcp.client.session import ClientSession
from mcp_helpers import BOARD_FILE
from pydantic import AnyUrl

from oa_mcp.contract import PROMPTS, RESOURCE_TEMPLATES, STATIC_RESOURCES

pytestmark = pytest.mark.anyio


async def test_resources_and_templates_are_advertised(client: ClientSession) -> None:
    listed = {str(r.uri) for r in (await client.list_resources()).resources}
    assert set(STATIC_RESOURCES) <= listed
    assert {"oa://schemas/oa-envelope/1", "oa://schemas/oa.cmd.board_open.in/1"} <= listed
    templates = {t.uriTemplate for t in (await client.list_resource_templates()).resourceTemplates}
    assert templates == set(RESOURCE_TEMPLATES)


async def test_docs_resources_are_served_from_the_package(client: ClientSession) -> None:
    guide = await client.read_resource(AnyUrl("oa://docs/agent-guide"))
    assert guide.contents[0].mimeType == "text/markdown"
    assert "propose" in guide.contents[0].text.lower()  # type: ignore[union-attr]
    llms = await client.read_resource(AnyUrl("oa://docs/llms.txt"))
    assert llms.contents[0].text.startswith("# Open_Analysis")  # type: ignore[union-attr]
    acc = await client.read_resource(AnyUrl("oa://docs/accuracy"))
    assert "closed_form" in acc.contents[0].text  # type: ignore[union-attr]


async def test_service_backed_resources(client: ClientSession) -> None:
    schema = await client.read_resource(AnyUrl("oa://schemas/oa.cmd.board_open.in/1"))
    assert schema.contents[0].mimeType == "application/schema+json"
    assert "path" in schema.contents[0].text  # type: ignore[union-attr]
    rules = json.loads((await client.read_resource(AnyUrl("oa://rules"))).contents[0].text)  # type: ignore[union-attr]
    assert isinstance(rules, list)  # the FakeService has no ERC rules installed
    with pytest.raises(Exception, match="No rule 'NOPE-1'"):
        await client.read_resource(AnyUrl("oa://docs/rules/NOPE-1"))
    opened = await client.call_tool("open_board", {"path": str(BOARD_FILE)})
    assert opened.structuredContent is not None
    pid = opened.structuredContent["data"]["project_id"]
    part = await client.read_resource(AnyUrl(f"oa://projects/{pid}/board/stackup"))
    assert json.loads(part.contents[0].text)  # type: ignore[union-attr]
    board = await client.read_resource(AnyUrl(f"oa://projects/{pid}/board"))
    assert "stackup" in json.loads(board.contents[0].text)  # type: ignore[union-attr]
    erc = await client.call_tool("run_erc", {"project_id": pid, "wait_s": 5})
    assert erc.structuredContent is not None
    rid = erc.structuredContent["data"]["job"]["result_id"]
    manifest = await client.read_resource(AnyUrl(f"oa://results/{rid}"))
    assert json.loads(manifest.contents[0].text)["analysis"] == "erc"  # type: ignore[union-attr]
    export = await client.read_resource(AnyUrl(f"oa://results/{rid}/export/md"))
    assert export.contents[0].text  # type: ignore[union-attr]


async def test_unknown_resource_is_a_protocol_error(client: ClientSession) -> None:
    with pytest.raises(Exception, match=r"NOT_FOUND: No result .nope."):
        await client.read_resource(AnyUrl("oa://results/nope"))


async def test_prompts(client: ClientSession) -> None:
    listed = (await client.list_prompts()).prompts
    assert [p.name for p in listed] == list(PROMPTS)
    power = next(p for p in listed if p.name == "check_power_rail")
    assert [(a.name, a.required) for a in power.arguments or []] == [
        ("project_id", True),
        ("rail", True),
        ("return", False),
        ("current", True),
    ]
    rendered = await client.get_prompt(
        "review_high_speed_nets", {"project_id": "p_1", "nets": "DDR*"}
    )
    text = rendered.messages[0].content.text  # type: ignore[union-attr]
    for tool in ("board_summary", "list_nets", "impedance_profile", "run_erc", "list_violations"):
        assert tool in text
    assert "accuracy class" in text
    assert "p_1" in text


async def test_prompt_tool_names_exist(client: ClientSession) -> None:
    from oa_mcp.contract import TOOLS

    for p in (await client.list_prompts()).prompts:
        args = {a.name: "x" for a in p.arguments or [] if a.required}
        text = (await client.get_prompt(p.name, args)).messages[0].content.text  # type: ignore[union-attr]
        for tool in TOOLS:
            if tool in text:
                assert tool in TOOLS
        assert "accuracy" in text
