# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The ``erc`` analysis: one task per rule, reduced to a ``ViolationSet`` (interfaces.md 3.3)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, ClassVar

from pydantic import Field, JsonValue

from oa_erc.base import ErcContext
from oa_erc.engine import (
    apply_severity,
    apply_waivers,
    discover_rules,
    resolve_rule_ids,
    sort_violations,
)
from oa_framework.analysis import Plan, PlanContext, RunContext, Task, TaskOutput
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_model.setup import ResolvedIntent, Waiver
from oa_model.setup.intent import NetIntentResolved
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.container import ValidityFlag
from oa_results.violations import Severity, Violation, ViolationSet

KIND = "erc"
VERSION = "1"


class ErcConfig(OAModel):
    rules: tuple[str, ...] = Field(
        default=(), description="Rule ids or aliases to run; empty = every registered rule."
    )
    exclude: tuple[str, ...] = Field(default=(), description="Rule ids or aliases to skip.")
    rule_config: dict[str, dict[str, JsonValue]] = Field(
        default={},
        description="Per-rule threshold overrides, keyed by rule id; validated against the "
        "rule's Config (see `oa describe` / the rule catalogue).",
    )
    severity: dict[str, Severity] = Field(
        default={}, description="Severity override per rule id or alias."
    )
    waivers: tuple[Waiver, ...] = Field(
        default=(), description="Waivers: by fingerprint or rule id / net globs, with expiry."
    )
    as_of: str | None = Field(
        default=None,
        description="ISO date used to judge waiver expiry; None = today (the only "
        "non-deterministic input; set it for reproducible output).",
    )


class ErcTaskPayload(OAModel):
    rule_id: str = Field(description="Rule id.")
    config: dict[str, JsonValue] = Field(default={}, description="Rule config overrides.")
    intent: list[dict[str, JsonValue]] = Field(
        default=[], description="Non-default entries of the resolved intent."
    )


def _compact_intent(ctx: PlanContext) -> list[dict[str, JsonValue]]:
    kinds = {n.id: n.kind for n in ctx.board.nets}
    out: list[dict[str, JsonValue]] = []
    for name, ni in ctx.intent.by_net:
        d = ni.model_dump(mode="json", exclude_defaults=True)
        if set(d) <= {"net", "kind"} and d.get("kind", "unknown") == kinds.get(name):
            continue
        d["net"] = name
        out.append(d)
    return out


def _expand_intent(board: Any, entries: list[dict[str, JsonValue]]) -> ResolvedIntent:
    by = {str(e["net"]): e for e in entries}
    out = []
    for n in board.nets:
        e = by.get(n.id)
        if e is None:
            ni = NetIntentResolved(net=n.id, kind=n.kind, high_speed=False)
        else:
            ni = NetIntentResolved.model_validate({"kind": n.kind, "high_speed": False, **e})
        out.append((n.id, ni))
    return ResolvedIntent(by_net=tuple(out))


class ErcAnalysis:
    kind: ClassVar[str] = KIND
    version: ClassVar[str] = VERSION
    title: ClassVar[str] = "Electrical/EMI rule check"
    Config: ClassVar[type[OAModel]] = ErcConfig
    TaskPayload: ClassVar[type[OAModel]] = ErcTaskPayload

    def _rules(self, cfg: ErcConfig) -> tuple[dict[str, Any], list[str]]:
        rules = discover_rules()
        return rules, resolve_rule_ids(rules, cfg.rules, cfg.exclude)

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        assert isinstance(cfg, ErcConfig)
        diags: list[Diagnostic] = []
        rules = discover_rules()
        try:
            ids = resolve_rule_ids(rules, cfg.rules, cfg.exclude)
        except KeyError as exc:
            return (
                Diagnostic(
                    severity="error",
                    code="ERC.UNKNOWN_RULE",
                    message=f"Unknown rule {exc.args[0]!r}.",
                    path="/config/rules",
                    hint=f"Known rules: {', '.join(rules)}.",
                ),
            )
        for key, overrides in cfg.rule_config.items():
            try:
                rid = resolve_rule_ids(rules, [key])[0]
                rules[rid].Config.model_validate(dict(overrides))
            except KeyError:
                diags.append(
                    Diagnostic(
                        severity="error",
                        code="ERC.UNKNOWN_RULE",
                        message=f"rule_config names unknown rule {key!r}.",
                        path=f"/config/rule_config/{key}",
                        hint=f"Known rules: {', '.join(rules)}.",
                    )
                )
            except ValueError as exc:
                diags.append(
                    Diagnostic(
                        severity="error",
                        code="ERC.BAD_RULE_CONFIG",
                        message=f"Invalid config for {key}: {exc}",
                        path=f"/config/rule_config/{key}",
                        hint="Check the field names and units in the rule's Config schema.",
                    )
                )
        for rid in ids:
            missing = [s for s in rules[rid].requires if not ctx.services().has(s)]
            if missing:
                diags.append(
                    Diagnostic(
                        severity="warning",
                        code="ERC.SERVICE_MISSING",
                        message=f"{rid} needs service(s) {', '.join(missing)}; it is skipped.",
                        path="/config/rules",
                        hint="Install the providing package (e.g. oa-impedance).",
                    )
                )
        if cfg.as_of is not None:
            try:
                date.fromisoformat(cfg.as_of)
            except ValueError:
                diags.append(
                    Diagnostic(
                        severity="error",
                        code="ERC.BAD_AS_OF",
                        message=f"as_of {cfg.as_of!r} is not an ISO date.",
                        path="/config/as_of",
                        hint="Use YYYY-MM-DD.",
                    )
                )
        return tuple(diags)

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, ErcConfig)
        rules, ids = self._rules(cfg)
        services = ctx.services()
        intent = _compact_intent(ctx)
        alias = {a: rid for rid, r in rules.items() for a in getattr(r, "aliases", ())}
        tasks = []
        for rid in ids:
            if any(not services.has(s) for s in rules[rid].requires):
                continue
            own: dict[str, JsonValue] = {}
            for key, ov in cfg.rule_config.items():
                if key == rid or alias.get(key) == rid:
                    own.update(ov)
            payload = ErcTaskPayload(rule_id=rid, config=own, intent=intent)
            tasks.append(
                Task(
                    id=f"rule:{rid}",
                    analysis=KIND,
                    payload=payload.model_dump(mode="json"),
                )
            )
        return Plan(tasks=tuple(tasks))

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, ErcTaskPayload)
        rules = discover_rules()
        cls = rules[payload.rule_id]
        rcfg = cls.Config.model_validate(payload.config)
        board = ctx.board()
        ectx = ErcContext(
            board,
            _expand_intent(board, payload.intent),
            ctx.services(),
            geometry=ctx.geometry(),
            selector=getattr(rcfg, "nets", None),
        )
        vs = sort_violations(cls().check(ectx, rcfg))
        return TaskOutput(
            task_id=ctx.task.id,
            data={"violations": [v.model_dump(mode="json") for v in vs]},
        )

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        assert isinstance(cfg, ErcConfig)
        started = datetime.now(UTC)
        rules = discover_rules()
        found: list[Violation] = []
        ran: list[str] = []
        for tid in sorted(outputs):
            ran.append(tid.removeprefix("rule:"))
            for raw in outputs[tid].data.get("violations", []):  # type: ignore[union-attr]
                found.append(Violation.model_validate(raw))
        sev: dict[str, Severity] = {}
        for key, s in cfg.severity.items():
            sev[resolve_rule_ids(rules, [key])[0]] = s
        as_of = date.fromisoformat(cfg.as_of) if cfg.as_of else date.today()
        waived, diags = apply_waivers(apply_severity(found, sev), cfg.waivers, as_of)
        vs_sorted = sort_violations(waived)
        vset = ViolationSet.from_violations(tuple(sorted(ran)), vs_sorted).model_copy(
            update={
                "accuracy_class": "screening",
                "validity_flags": (
                    ValidityFlag(
                        code="accuracy",
                        ok=True,
                        message="screening: geometry rules with documented default thresholds; "
                        "IM-002 carries the solver accuracy class in its evidence.",
                    ),
                ),
            }
        )
        board = ctx.board()
        cfg_json = cfg.model_dump(mode="json")
        setup_hash = (
            "sha256:" + hashlib.sha256(json.dumps(cfg_json, sort_keys=True).encode()).hexdigest()
        )
        board_hash = board.content_hash()
        rid = "erc-" + hashlib.sha256(f"{board_hash}{setup_hash}".encode()).hexdigest()[:16]
        open_err = sum(1 for v in vs_sorted if v.severity == "error" and v.waived is None)
        status = "warning" if open_err else "ok"
        summary = vset.digest()
        item = ResultItem(id="violations", title="ERC/EMI violations", kind="violations", data=vset)
        return ResultBundle.model_validate(
            {
                "id": rid,
                "status": status,
                "summary": summary,
                "items": [item],
                "diagnostics": list(diags),
                "provenance": Provenance(
                    analysis=KIND,
                    analysis_version=VERSION,
                    board_hash=board_hash,
                    physics_hash=None,
                    setup_hash=setup_hash,
                    config=cfg_json,
                    started_utc=started.isoformat(timespec="seconds"),
                    finished_utc=datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            }
        )
