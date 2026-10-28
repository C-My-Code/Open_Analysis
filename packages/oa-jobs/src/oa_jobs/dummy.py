# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``dummy.work``: a configurable fake analysis for tests and demos of the job system.

Modes: ``compute`` (N tasks that sleep cooperatively and add numbers), ``external_sleep`` (one
task running a sleeping Python child through ``ctx.run_external``) and ``external_alloc`` (one
task whose child allocates memory until killed). Not registered as an entry point; use
``make_registry`` (``registry_factory="oa_jobs.dummy:make_registry"``).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import ClassVar, Literal

from oa_framework.analysis import (
    Plan,
    PlanContext,
    Resources,
    RunContext,
    Task,
    TaskOutput,
)
from oa_framework.external import ExternalCommand
from oa_framework.registry import Registry
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.scalars import Scalar, ScalarSet

__all__ = ["DummyAnalysis", "DummyConfig", "DummyPayload", "make_registry"]

_ALLOC = (
    "import time\n"
    "b = []\n"
    "for _ in range(400):\n"
    "    b.append(bytearray(b'\\x01') * (25 << 20))\n"
    "    time.sleep(0.02)\n"
    "time.sleep(30)\n"
)


class DummyConfig(OAModel):
    n_tasks: int = 4
    sleep_s: float = 0.0
    work: int = 1000
    est_seconds: float | None = None
    memory_gb: float = 0.05
    log: str | None = None  # file that gets one line per executed task ("i")
    mode: Literal["compute", "external_sleep", "external_alloc"] = "compute"
    chain: bool = False  # task i depends on task i-1
    tag: str = ""  # changes the payload (and so the cache key) without changing behavior


class DummyPayload(OAModel):
    i: int
    sleep_s: float = 0.0
    work: int = 1000
    log: str | None = None
    mode: Literal["compute", "external_sleep", "external_alloc"] = "compute"
    tag: str = ""


class DummyAnalysis:
    kind: ClassVar[str] = "dummy.work"
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "Dummy workload"
    Config: ClassVar[type[OAModel]] = DummyConfig
    TaskPayload: ClassVar[type[OAModel]] = DummyPayload

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        return ()

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        assert isinstance(cfg, DummyConfig)
        n = 1 if cfg.mode != "compute" else cfg.n_tasks
        tasks = []
        for i in range(n):
            payload = DummyPayload(
                i=i, sleep_s=cfg.sleep_s, work=cfg.work, log=cfg.log, mode=cfg.mode, tag=cfg.tag
            )
            tasks.append(
                Task(
                    id=f"t{i:03d}",
                    analysis=self.kind,
                    payload=payload.model_dump(mode="json"),
                    depends_on=(f"t{i - 1:03d}",) if cfg.chain and i else (),
                    resources=Resources(memory_gb=cfg.memory_gb, est_seconds=cfg.est_seconds),
                    cache=cfg.mode == "compute",
                )
            )
        return Plan(tasks=tuple(tasks))

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        assert isinstance(payload, DummyPayload)
        tid = ctx.task.id
        if payload.mode != "compute":
            code = "import time; time.sleep(60)" if payload.mode == "external_sleep" else _ALLOC
            ctx.run_external(
                ExternalCommand(program="python", args=("-c", code), workdir=ctx.scratch)
            )
            return TaskOutput(task_id=tid, data={"i": payload.i})
        deadline = time.monotonic() + payload.sleep_s
        while time.monotonic() < deadline:
            ctx.check_cancelled()
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        ctx.check_cancelled()
        value = sum(k * k for k in range(payload.work))
        ctx.progress(1.0, "done")
        if payload.log:
            with open(payload.log, "a", encoding="utf-8") as fh:
                fh.write(f"{payload.i}\n")
        return TaskOutput(task_id=tid, data={"i": payload.i, "value": value})

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        total = 0.0
        for o in outputs.values():
            v = o.data.get("value", 0)
            total += float(v) if isinstance(v, int | float) else 0.0
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return ResultBundle(
            id="01DUMMY000000000000000000",
            status="ok",
            summary=f"dummy: {len(outputs)} task(s)",
            items=(
                ResultItem(
                    id="total",
                    title="Total",
                    kind="scalars",
                    data=ScalarSet(
                        values=(
                            Scalar(name="total", value=total),
                            Scalar(name="n", value=len(outputs)),
                        )
                    ),
                ),
            ),
            provenance=Provenance(
                analysis=self.kind,
                analysis_version=self.version,
                board_hash="",
                physics_hash=None,
                setup_hash="",
                config={},
                started_utc=now,
                finished_utc=now,
            ),
        )


def make_registry() -> Registry:
    return Registry.for_tests(analyses=[DummyAnalysis])
