# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# ruff: noqa: E501
"""ERC validation: found violations against the WP-102 ``expected.yaml`` oracle, per board."""

from __future__ import annotations

import math
import sys
import time

from val_model import REPO, Case

sys.path.insert(0, str(REPO / "packages" / "oa-erc" / "tests"))

from erc_helpers import BOARD_NAMES, expected, loc_mm, run_board

CAT = "erc"
# Real findings the engine makes that expected.yaml does not list: the generated board geometry
# misses its own impedance targets per BEM (docs/FOLLOWUPS.md, CR WP-201-expected-impedance).
EXTRA_IM002 = {"CPW_A", "CPW_B", "DP_P", "USB_D+"}


def _match(e: dict, v) -> bool:  # type: ignore[no-untyped-def,type-arg]
    if e["rule_id"] != v.rule_id or sorted(e["nets"]) != sorted(v.nets):
        return False
    if e["layer"] not in v.layers:
        return False
    if e["location_mm"] is None:
        return True
    x, y = loc_mm(v)
    tol = max(e["tolerance_mm"], 0.05)  # the ROADMAP gate is 50 um
    return math.hypot(x - e["location_mm"][0], y - e["location_mm"][1]) <= tol


def run() -> list[Case]:
    out: list[Case] = []
    extra_total = 0
    for name in BOARD_NAMES:
        t0 = time.perf_counter()
        exp = expected(name)
        all_found = list(run_board(name))
        found = [v for v in all_found if not (v.rule_id == "IM-002" and v.nets[0] in EXTRA_IM002)]
        extra_total += len(all_found) - len(found)
        expected_items = exp["erc"]
        missed = [e for e in expected_items if not any(_match(e, v) for v in found)]
        forbidden = set(exp["must_not_report"])
        unspecified = {(u["rule_id"], tuple(sorted(u["nets"]))) for u in exp["unspecified"]}
        extras = [
            v
            for v in found
            if not any(_match(e, v) for e in expected_items)
            and (v.rule_id, tuple(sorted(v.nets))) not in unspecified
        ]
        bad_forbidden = [v for v in found if v.rule_id in forbidden]
        bad_negative = [
            n
            for n in exp["negative_cases"]
            if any(
                v.rule_id == n["rule_id"]
                and set(n["nets"]) & set(v.nets)
                and math.hypot(
                    loc_mm(v)[0] - n["location_mm"][0], loc_mm(v)[1] - n["location_mm"][1]
                )
                < 5.0
                for v in found
            )
        ]
        n_exp = len(expected_items)
        matched = n_exp - len(missed)
        problems = len(extras) + len(bad_forbidden) + len(bad_negative)
        note = (
            f"{len(found)} violations found; {len(missed)} missed, {len(extras)} extra, "
            f"{len(bad_forbidden)} forbidden, {len(bad_negative)} negative-case hits; "
            f"{n_exp} expected.yaml entries matched on rule id, nets, layer and location (50 um floor)"
        )
        # exact match required: matched must equal expected, and no extras or forbidden hits
        out.append(
            Case(
                f"erc.{name}",
                CAT,
                f"ERC on WP-102 board '{name}'",
                f"tests/boards/kicad10/{name}/expected.yaml (constructed by tools/boardgen, "
                "independent of the rule engine)",
                "analytic",
                True,
                "expected violations matched",
                "",
                float(n_exp),
                float(matched) if problems == 0 else float(matched) - problems,
                "exact",
                0.0,
                f"tests/boards/kicad10/{name}",
                note=note,
                runtime_s=time.perf_counter() - t0,
            )
        )
    out.append(
        Case(
            "erc.im002_geometry_vs_target",
            CAT,
            "IM-002 hits on WP-102 nets whose geometry misses its own impedance target",
            "docs/change-requests/WP-201-expected-impedance.md; docs/FOLLOWUPS.md (ERC/EMI)",
            "analytic",
            True,
            "unlisted IM-002 violations",
            "",
            0.0,
            float(extra_total),
            "exact",
            0.0,
            "tests/boards/kicad10",
            note="CPW_A/B 55.3 vs 50 ohm, DP 139 vs 100 ohm, USB_D 126 vs 90 ohm "
            "per BEM: either the boards or expected.yaml must change",
            forced_status="known_gap",
        )
    )
    return out
