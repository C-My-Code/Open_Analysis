# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""The public MCP surface fixed by ai-integration sections 2.4 and 8.3-8.5."""

from __future__ import annotations

from typing import Final

#: MCP tool name -> (catalog command, side effect). Section 2.4.
TOOLS: Final[dict[str, tuple[str, str]]] = {
    "describe": ("meta.describe", "read"),
    "open_board": ("board.open", "compute"),
    "board_summary": ("board.summary", "read"),
    "list_nets": ("board.nets", "read"),
    "get_net": ("board.net", "read"),
    "stackup": ("board.stackup", "read"),
    "compute_xsection_impedance": ("impedance.xs", "compute"),
    "impedance_profile": ("impedance.profile", "compute"),
    "run_erc": ("erc.run", "compute"),
    "list_violations": ("results.violations", "read"),
    "explain_violation": ("results.explain", "read"),
    "run_analysis": ("analysis.run", "compute"),
    "job_status": ("jobs.status", "read"),
    "cancel_job": ("jobs.cancel", "compute"),
    "get_result": ("results.get", "read"),
    "result_summary": ("results.summary", "read"),
    "export_report": ("results.export", "compute"),
    "propose_rule_changes": ("propose.rules", "compute"),
    "apply_proposal": ("propose.apply", "write"),
}

#: Resource URI templates (concrete URIs are listed separately). Section 8.4.
RESOURCE_TEMPLATES: Final[tuple[str, ...]] = (
    "oa://schemas/{schema_id}/{version}",
    "oa://docs/rules/{rule_id}",
    "oa://projects/{pid}/board",
    "oa://projects/{pid}/board/{part}",
    "oa://projects/{pid}/intent",
    "oa://projects/{pid}/setups/{name}",
    "oa://projects/{pid}/physics",
    "oa://results/{rid}",
    "oa://results/{rid}/items/{item}/arrays/{name}",
    "oa://results/{rid}/export/{format}",
)

#: Concrete resources served by the adapter or the service.
STATIC_RESOURCES: Final[tuple[str, ...]] = (
    "oa://docs/agent-guide",
    "oa://docs/llms.txt",
    "oa://docs/accuracy",
    "oa://analyses",
    "oa://rules",
)

PROMPTS: Final[tuple[str, ...]] = (
    "review_high_speed_nets",
    "triage_violations",
    "check_power_rail",
    "stackup_review",
    "setup_em_region",
    "explain_result",
    "m1_signoff",
)

#: Largest raw array a resource read may return [bytes]. Section 8.4.
MAX_ARRAY_BYTES: Final[int] = 16 * 1024 * 1024
