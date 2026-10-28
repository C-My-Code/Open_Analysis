# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Resources, resource templates and prompts."""

from __future__ import annotations

import pytest
from mcp.client.session import ClientSession
from mcp_helpers import BOARD_DIR
from pydantic import AnyUrl

from oa_mcp.contract import PROMPTS, RESOURCE_TEMPLATES, STATIC_RESOURCES

pytestmark = pytest.mark.anyio


async def test_resources_and_templates_are_advertised(client: ClientSession) -> None:
    listed = {str(r.uri) for r in (await client.list_resources()).resources}
    assert set(STATIC_RESOURCES) <= listed
    assert {"oa://schemas/board-model/1", "oa://schemas/oa-setup/1"} <= listed
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
    schema = await client.read_resource(AnyUrl("oa://schemas/oa-setup/1"))
    assert schema.contents[0].mimeType == "application/schema+json"
    rule = await client.read_resource(AnyUrl("oa://docs/rules/IM-002"))
    assert rule.contents[0].text.startswith("# IM-002")  # type: ignore[union-attr]
    opened = await client.call_tool("open_board", {"path": str(BOARD_DIR)})
    assert opened.structuredContent is not None
    pid = opened.structuredContent["data"]["project_id"]
    part = await client.read_resource(AnyUrl(f"oa://projects/{pid}/board/stackup"))
    assert "stackup" in part.contents[0].text  # type: ignore[union-attr]
    arr = await client.read_resource(AnyUrl("oa://results/r_1/items/i1/arrays/z0"))
    assert arr.contents[0].mimeType == "application/x-npy"
    assert arr.contents[0].blob  # type: ignore[union-attr]


async def test_unknown_resource_is_a_protocol_error(client: ClientSession) -> None:
    with pytest.raises(Exception, match="oa://results/nope"):
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
