# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ERC/EMI rule engine and the M1 rules (WP-201). See the package README."""

from oa_erc.analysis import ErcAnalysis, ErcConfig
from oa_erc.base import ErcContext, ErcRule, RuleConfig
from oa_erc.dru import DruProposal, apply_dru, propose_dru
from oa_erc.engine import (
    apply_severity,
    apply_waivers,
    builtin_rules,
    discover_rules,
    run_rules,
    sort_violations,
    waiver_matches,
)
from oa_erc.remediation import TypedAction, TypedRemediation
from oa_erc.rules import BUILTIN_RULES
from oa_erc.summary import compact_summary

__all__ = [
    "BUILTIN_RULES",
    "DruProposal",
    "ErcAnalysis",
    "ErcConfig",
    "ErcContext",
    "ErcRule",
    "RuleConfig",
    "TypedAction",
    "TypedRemediation",
    "apply_dru",
    "apply_severity",
    "apply_waivers",
    "builtin_rules",
    "compact_summary",
    "discover_rules",
    "propose_dru",
    "run_rules",
    "sort_violations",
    "waiver_matches",
]
