# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rule discovery, direct rule execution, severity overrides, waivers and deterministic order."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from importlib.metadata import entry_points
from typing import Any

from oa_erc.base import ErcContext
from oa_erc.rules import BUILTIN_RULES
from oa_framework.registry import GROUP_RULES
from oa_framework.services import Services
from oa_geometry import BoardGeometry
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.setup import ResolvedIntent, Waiver, resolve_intent
from oa_model.setup.intent import DesignIntent
from oa_results.violations import Severity, Violation
from oa_results.violations import Waiver as ResultWaiver


def builtin_rules() -> dict[str, Any]:
    return {r.id: r for r in BUILTIN_RULES}


def discover_rules() -> dict[str, Any]:
    """Built-in rules plus third-party rules registered in the ``open_analysis.rules`` group.

    A third-party entry point must not reuse a built-in id with a different class."""
    rules = builtin_rules()
    for ep in entry_points(group=GROUP_RULES):
        cls = ep.load()
        known = rules.get(cls.id)
        if known is not None and known is not cls:
            raise ValueError(f"rule id {cls.id!r} registered twice ({ep.value})")
        rules[cls.id] = cls
    return dict(sorted(rules.items()))


def resolve_rule_ids(
    rules: Mapping[str, Any], wanted: Sequence[str], exclude: Sequence[str] = ()
) -> list[str]:
    """Rule ids for ``wanted`` (ids or aliases, empty = all) minus ``exclude``; raises KeyError."""
    alias = {a: rid for rid, r in rules.items() for a in getattr(r, "aliases", ())}

    def norm(x: str) -> str:
        rid = x if x in rules else alias.get(x)
        if rid is None:
            raise KeyError(x)
        return rid

    ids = [norm(x) for x in wanted] if wanted else list(rules)
    drop = {norm(x) for x in exclude}
    return sorted(dict.fromkeys(i for i in ids if i not in drop))


def run_rules(
    board: Board,
    *,
    intent: DesignIntent | ResolvedIntent | None = None,
    services: Services | None = None,
    rule_ids: Sequence[str] = (),
    rule_config: Mapping[str, Mapping[str, Any]] | None = None,
    geometry: BoardGeometry | None = None,
    rules: Mapping[str, Any] | None = None,
) -> list[Violation]:
    """Run rules directly (no job machinery) and return the violations, unsorted.

    ``services`` defaults to the entry-point services (``impedance``, ``via_model``)."""
    catalog = rules if rules is not None else discover_rules()
    resolved = (
        intent
        if isinstance(intent, ResolvedIntent)
        else resolve_intent(board, intent if intent is not None else DesignIntent())
    )
    if services is None:
        from oa_framework.registry import Registry

        services = Registry.load().services()
    geo = geometry if geometry is not None else BoardGeometry.build(board)
    out: list[Violation] = []
    for rid in resolve_rule_ids(catalog, rule_ids):
        cls = catalog[rid]
        if any(not services.has(s) for s in cls.requires):
            continue
        cfg = cls.Config.model_validate(dict((rule_config or {}).get(rid, {})))
        ctx = ErcContext(
            board, resolved, services, geometry=geo, selector=getattr(cfg, "nets", None)
        )
        out.extend(cls().check(ctx, cfg))
    return out


# ---------------------------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------------------------


def sort_violations(vs: Iterable[Violation]) -> tuple[Violation, ...]:
    """Deterministic order: rule id, layers, nets, location (rounded to 10 um), fingerprint."""
    return tuple(
        sorted(
            vs,
            key=lambda v: (
                v.rule_id,
                v.layers,
                v.nets,
                round(v.location[0] / 1e-5),
                round(v.location[1] / 1e-5),
                v.fingerprint,
            ),
        )
    )


def waiver_matches(w: Waiver, v: Violation) -> bool:
    """A waiver matches by exact fingerprint, or by rule id and/or net globs (all nets of the
    violation must match some glob; a violation without nets never matches a net waiver)."""
    if w.fingerprint is None and w.rule_id is None and not w.nets:
        return False
    if w.fingerprint is not None and w.fingerprint != v.fingerprint:
        return False
    if w.rule_id is not None and w.rule_id != v.rule_id:
        return False
    if w.nets:
        if not v.nets:
            return False
        if not all(any(fnmatch.fnmatchcase(n, g) for g in w.nets) for n in v.nets):
            return False
    return True


def apply_waivers(
    violations: Iterable[Violation], waivers: Sequence[Waiver], as_of: date
) -> tuple[tuple[Violation, ...], tuple[Diagnostic, ...]]:
    """Mark waived violations (first matching, unexpired waiver wins). An expired waiver
    (``expires`` earlier than ``as_of``) does not apply and yields a warning diagnostic."""
    diags: list[Diagnostic] = []
    live: list[Waiver] = []
    for i, w in enumerate(waivers):
        if w.expires is not None:
            try:
                exp = date.fromisoformat(w.expires)
            except ValueError:
                diags.append(
                    Diagnostic(
                        severity="error",
                        code="ERC.WAIVER_BAD_DATE",
                        message=f"Waiver {i} has an invalid expiry date {w.expires!r}.",
                        path=f"/waivers/{i}/expires",
                        hint="Use an ISO date, YYYY-MM-DD.",
                    )
                )
                continue
            if exp < as_of:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        code="ERC.WAIVER_EXPIRED",
                        message=f"Waiver {i} ({w.reason}) expired on {w.expires}.",
                        path=f"/waivers/{i}",
                        hint="Renew the expiry date or fix the violation.",
                    )
                )
                continue
        live.append(w)
    out: list[Violation] = []
    for v in violations:
        hit = next((w for w in live if waiver_matches(w, v)), None)
        if hit is not None:
            v = v.model_copy(
                update={
                    "waived": ResultWaiver(
                        fingerprint=hit.fingerprint,
                        rule_id=hit.rule_id,
                        nets=hit.nets,
                        reason=hit.reason,
                        author=hit.author,
                        expires=hit.expires,
                    )
                }
            )
        out.append(v)
    return tuple(out), tuple(diags)


def apply_severity(
    violations: Iterable[Violation], overrides: Mapping[str, Severity]
) -> tuple[Violation, ...]:
    return tuple(
        v.model_copy(update={"severity": overrides[v.rule_id]}) if v.rule_id in overrides else v
        for v in violations
    )
