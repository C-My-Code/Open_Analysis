# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test analyses registered into the plugin registry (stand-ins for WP-105/201/204)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

from oa_framework.analysis import Plan, PlanContext, Resources, RunContext, Task, TaskOutput
from oa_framework.registry import Registry
from oa_jobs.dummy import DummyAnalysis
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.violations import Remediation, Violation, ViolationSet

REPO = Path(__file__).resolve().parents[3]
BOARDS = REPO / "tests" / "boards" / "kicad10"


class StubErcConfig(OAModel):
    """Stub ERC: flags every net whose name starts with `prefix`."""

    prefix: str = "USB"
    rules: dict[str, dict[str, float]] = {}
    nets: dict[str, list[str]] | None = None


class StubPayload(OAModel):
    """Stub payload."""

    prefix: str = "USB"


class StubErc:
    """An `erc` analysis: one error per net starting with the prefix, so `combined` fails."""

    kind: ClassVar[str] = "erc"
    version: ClassVar[str] = "stub-1"
    title: ClassVar[str] = "Stub ERC"
    Config: ClassVar[type[OAModel]] = StubErcConfig
    TaskPayload: ClassVar[type[OAModel]] = StubPayload

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        return ()

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, StubErcConfig)
        return Plan(
            tasks=(
                Task(
                    id="erc",
                    analysis=self.kind,
                    payload={"prefix": cfg.prefix},
                    resources=Resources(est_seconds=1.0),
                ),
            )
        )

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, StubPayload)
        nets = sorted(n.id for n in ctx.board().nets if n.id.startswith(payload.prefix))
        return TaskOutput(task_id=ctx.task.id, data={"nets": nets})

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        nets = [str(n) for n in outputs["erc"].data["nets"]]  # type: ignore[attr-defined]
        vs = []
        for k, net in enumerate(nets):
            fp = hashlib.sha1(net.encode()).hexdigest()[:16]
            vs.append(
                Violation(
                    rule_id="IM-001",
                    severity="error",
                    message=f"Impedance of {net} is off target.",
                    nets=(net,),
                    layers=("L1",),
                    location=(1e-3 * (k + 1), 0.0),
                    measured=45.0,
                    limit=50.0,
                    unit="ohm",
                    fix_hint="Widen the trace.",
                    remediation=Remediation(hint="Widen the trace."),
                    fingerprint=fp,
                )
            )
        data = ViolationSet.from_violations(("IM-001",), tuple(vs))
        return ResultBundle(
            id="01STUB000000000000000000000",
            status="warning" if vs else "ok",
            summary=data.digest(),
            items=(ResultItem(id="violations", title="Violations", kind="violations", data=data),),
            provenance=Provenance(
                analysis=self.kind,
                analysis_version=self.version,
                board_hash="",
                physics_hash=None,
                setup_hash="",
                config={},
                started_utc="2026-01-01T00:00:00Z",
                finished_utc="2026-01-01T00:00:00Z",
            ),
        )


class StubGate(StubErc):
    """An analysis whose bundle fails its quality gate (exit code 5 fixture)."""

    kind: ClassVar[str] = "gate.stub"
    title: ClassVar[str] = "Stub failing quality gate"

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        b = super().reduce(ctx, cfg, outputs)
        return b.model_copy(update={"status": "failed_quality"})


def make_registry() -> Registry:
    """Installed readers (entry points) plus the stub analyses, and nothing else.

    No real analyses, rules or services are loaded, so these tests do not depend on which
    analyses (erc, impedance, pi.dc, ...) happen to be installed.
    """
    readers = tuple(Registry.load().readers.values())
    return Registry.for_tests(analyses=(DummyAnalysis, StubErc, StubGate), readers=readers)
