# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""Prompt templates (ai-integration section 8.5). Templates only: no hidden logic."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.prompts.base import Prompt, PromptArgument

CAVEAT = (
    "Report the accuracy class and tolerance for every number you quote. Do not claim more "
    "precision than the result's accuracy states, and say so when a value is extrapolated."
)


@dataclass(frozen=True)
class PromptDef:
    """One prompt: name, arguments ``(name, description, required)`` and a text renderer."""

    name: str
    title: str
    description: str
    args: tuple[tuple[str, str, bool], ...]
    render: Callable[[dict[str, str]], str]


def _opt(kw: dict[str, str], key: str, label: str) -> str:
    return f" ({label}: {kw[key]})" if kw.get(key) else ""


def _review(kw: dict[str, str]) -> str:
    return (
        f"Review the high-speed nets of project {kw['project_id']}{_opt(kw, 'nets', 'nets')}.\n"
        "Plan:\n"
        "1. board_summary\n"
        "2. list_nets(high_speed_only=true)\n"
        "3. impedance_profile on those nets\n"
        "4. run_erc with rules RP-*, IM-*, DP-*, VI-*, XT-001 enabled\n"
        "5. list_violations on the result, paging until the summary counts are covered\n"
        "6. A table of findings: rule, net, measured vs limit, accuracy class\n"
        "7. propose_rule_changes for netclass-level fixes. Never call apply_proposal yourself; "
        "show the diff to the user.\n" + CAVEAT
    )


def _triage(kw: dict[str, str]) -> str:
    return (
        f"Triage the violations of result {kw['result_id']}.\n"
        "Plan: result_summary, then list_violations grouped by rule and net, ranked by severity "
        "and margin. Call explain_violation on the top 10. Split the findings into three lists: "
        "board edit needed, rule or netclass change, likely waiver. " + CAVEAT
    )


def _power(kw: dict[str, str]) -> str:
    ret = kw.get("return") or "the ground net"
    return (
        f"Check power rail {kw['rail']} (return: {ret}, current: {kw['current']}) in project "
        f"{kw['project_id']}.\n"
        "Plan: get_net for the rail, draft a pi.dc setup, run_analysis with dry_run=true to see "
        "the estimate, run it, then summarize the worst drop, via currents and the PD-001 decap "
        "findings. " + CAVEAT
    )


def _stackup(kw: dict[str, str]) -> str:
    return (
        f"Review the stackup of project {kw['project_id']}.\n"
        "Plan: stackup, then compute_xsection_impedance(quick=...) for each net class target. "
        "Flag classes that are off target and propose width and gap values. " + CAVEAT
    )


def _em(kw: dict[str, str]) -> str:
    return (
        f"Set up an EM region for nets {kw['nets']} of project {kw['project_id']} up to "
        f"{kw['f_max']}.\n"
        "Plan: inspect the nets with get_net, draft an em.sparams setup with trace_end ports, "
        "run_analysis(dry_run=true) for the estimate, and ask the user before running if the "
        "estimate is over the default budget. " + CAVEAT
    )


def _explain(kw: dict[str, str]) -> str:
    return (
        f"Explain result {kw['result_id']} in plain language.\n"
        "Plan: result_summary, then a short explanation with the accuracy caveats. " + CAVEAT
    )


def _m1(kw: dict[str, str]) -> str:
    return (
        f"Run the M1 sign-off workflow for project {kw['project_id']}.\n"
        "Checklist: open_board and read board_summary; stackup; impedance_profile on high-speed "
        "nets; run_erc with the default rule set; list_violations and explain the errors; "
        "propose_rule_changes where a rule or netclass change applies; summarize pass/fail per "
        "check with accuracy. " + CAVEAT
    )


_P = ("project_id", "Project id returned by open_board.", True)
_R = ("result_id", "Result id returned by a run.", True)

PROMPT_DEFS: tuple[PromptDef, ...] = (
    PromptDef(
        "review_high_speed_nets",
        "Review high-speed nets",
        "Impedance and rule review of the high-speed nets.",
        (_P, ("nets", "Optional net names or globs.", False)),
        _review,
    ),
    PromptDef(
        "triage_violations", "Triage violations", "Group and rank violations.", (_R,), _triage
    ),
    PromptDef(
        "check_power_rail",
        "Check power rail",
        "DC drop check of one rail.",
        (
            _P,
            ("rail", "Rail net name.", True),
            ("return", "Return net name.", False),
            ("current", "Load current, e.g. 2A.", True),
        ),
        _power,
    ),
    PromptDef("stackup_review", "Stackup review", "Check stackup vs targets.", (_P,), _stackup),
    PromptDef(
        "setup_em_region",
        "Set up an EM region",
        "Draft an EM S-parameter setup.",
        (
            _P,
            ("nets", "Net names to include.", True),
            ("f_max", "Upper frequency, e.g. 10GHz.", True),
        ),
        _em,
    ),
    PromptDef(
        "explain_result", "Explain a result", "Plain-language result summary.", (_R,), _explain
    ),
    PromptDef("m1_signoff", "M1 sign-off", "The full M1 workflow checklist.", (_P,), _m1),
)


def register_prompts(server: FastMCP) -> None:
    """Add every prompt to ``server``."""
    for d in PROMPT_DEFS:

        def make(render: Callable[[dict[str, str]], str]) -> Callable[..., str]:
            def fn(**kw: str) -> str:
                return render(kw)

            return fn

        server.add_prompt(
            Prompt(
                name=d.name,
                title=d.title,
                description=d.description,
                arguments=[PromptArgument(name=n, description=t, required=r) for n, t, r in d.args],
                fn=make(d.render),
                context_kwarg=None,
            )
        )
