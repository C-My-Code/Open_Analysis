# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""run_inline on a dummy analysis with 3 tasks and a reduce."""

from __future__ import annotations

import threading
from typing import ClassVar

import pytest
from pydantic import Field

from oa_framework.analysis import Plan, Task, TaskOutput
from oa_framework.registry import Registry
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_model.errors import Cancelled, SetupError
from oa_model.setup import AnalysisSetup
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.scalars import Scalar, ScalarSet
from oa_testkit import builders
from oa_testkit.harness import InlineRun, run_inline, run_inline_full

KIND = "testkit.dummy"


class DummyConfig(OAModel):
    n: int = Field(default=3, ge=0)
    factor: int = 1
    fail_validation: bool = False
    cycle: bool = False
    subset: bool = False


class DummyPayload(OAModel):
    index: int


class DummyAnalysis:
    """Plans ``n`` tasks (task i depends on task i-1); each computes i**2 * factor.

    ``reduce`` sums the outputs into a ScalarSet.
    """

    kind: ClassVar[str] = KIND
    version: ClassVar[str] = "1"
    title: ClassVar[str] = "Dummy"
    Config: ClassVar[type[OAModel]] = DummyConfig
    TaskPayload: ClassVar[type[OAModel]] = DummyPayload

    def validate_setup(self, ctx, cfg):  # type: ignore[no-untyped-def]
        if cfg.fail_validation:
            return (Diagnostic(severity="error", code="DUMMY.BAD", message="no"),)
        return (Diagnostic(severity="info", code="DUMMY.INFO", message="fine"),)

    def plan(self, ctx, cfg):  # type: ignore[no-untyped-def]
        tasks = tuple(
            Task(
                id=f"sq:{i}",
                analysis=KIND,
                payload={"index": i},
                depends_on=(f"sq:{i - 1}",) if i else (),
            )
            for i in range(cfg.n)
        )
        if cfg.cycle:
            tasks = (tasks[0].model_copy(update={"depends_on": ("sq:2",)}), *tasks[1:])
        return Plan(tasks=tasks, reduce_inputs=("sq:1", "sq:2") if cfg.subset else None)

    def run_task(self, ctx, payload):  # type: ignore[no-untyped-def]
        assert ctx.scratch.is_dir()
        assert ctx.board().meta.source_tool == "synthetic"
        ctx.check_cancelled()
        ctx.progress(0.5, "half")
        ctx.log("info", "computing", index=payload.index)
        (ctx.scratch / "work.txt").write_text("x")
        factor = ctx.services().get("factor")
        ref = ctx.artifacts().put_bytes(
            f"blob{payload.index}".encode(), name=f"b{payload.index}", media_type="text/plain"
        )
        return TaskOutput(
            task_id=ctx.task.id,
            data={"value": payload.index**2 * factor},
            artifacts=(ref,),
        )

    def reduce(self, ctx, cfg, outputs):  # type: ignore[no-untyped-def]
        total = sum(o.data["value"] for o in outputs.values())
        return ResultBundle(
            id="01DUMMY",
            status="ok",
            summary=f"{len(outputs)} tasks",
            items=(
                ResultItem(
                    id="total",
                    title="Total",
                    kind="scalars",
                    data=ScalarSet(
                        values=(Scalar(name="total", value=float(total), unit="", status="pass"),)
                    ),
                ),
            ),
            provenance=Provenance(
                analysis=KIND,
                analysis_version="1",
                board_hash=ctx.board().content_hash(),
                physics_hash=None,
                setup_hash="sha256:test",
                config=cfg.model_dump(mode="json"),
                started_utc="2026-01-01T00:00:00Z",
                finished_utc="2026-01-01T00:00:00Z",
            ),
        )


def _setup(**config: object) -> AnalysisSetup:
    return AnalysisSetup(schema="oa-setup/1", name="dummy", analysis=KIND, config=config)  # type: ignore[call-arg, arg-type]


@pytest.fixture
def registry() -> Registry:
    return Registry.for_tests(analyses=[DummyAnalysis], services={"factor": 10})


@pytest.fixture
def board():  # type: ignore[no-untyped-def]
    return builders.microstrip_board()


def _total(bundle: ResultBundle) -> float:
    return bundle.items[0].data.values[0].value  # type: ignore[union-attr]


def test_three_tasks_and_reduce(board, registry) -> None:  # type: ignore[no-untyped-def]
    run = run_inline_full(DummyAnalysis(), board, _setup(), registry, services={"factor": 10})
    assert isinstance(run, InlineRun)
    assert run.order == ("sq:0", "sq:1", "sq:2")
    assert [run.outputs[t].data["value"] for t in run.order] == [0, 10, 40]
    assert _total(run.bundle) == 50.0
    assert run.bundle.summary_text == "3 tasks"
    assert run.bundle.provenance.board_hash == board.content_hash()
    assert len(run.artifacts) == 3
    assert run.artifacts.get(run.outputs["sq:2"].artifacts[0]) == b"blob2"
    assert [p[0] for p in run.progress] == ["sq:0", "sq:1", "sq:2"]
    assert len(run.logs) == 3
    assert [d.code for d in run.diagnostics] == ["DUMMY.INFO"]


def test_run_inline_returns_bundle(board, registry) -> None:  # type: ignore[no-untyped-def]
    bundle = run_inline(DummyAnalysis, board, _setup(factor=2), registry, services={"factor": 1})
    assert _total(bundle) == 5.0  # 0 + 1 + 4, factor service = 1


def test_config_n(board, registry) -> None:  # type: ignore[no-untyped-def]
    run = run_inline_full(DummyAnalysis(), board, _setup(n=5), registry, services={"factor": 1})
    assert len(run.order) == 5
    assert _total(run.bundle) == 0 + 1 + 4 + 9 + 16


def test_reduce_inputs_subset(board, registry) -> None:  # type: ignore[no-untyped-def]
    run = run_inline_full(
        DummyAnalysis(), board, _setup(subset=True), registry, services={"factor": 1}
    )
    assert _total(run.bundle) == 1 + 4


def test_setup_for_other_analysis(board, registry) -> None:  # type: ignore[no-untyped-def]
    bad = AnalysisSetup(schema="oa-setup/1", name="x", analysis="other", config={})  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="other"):
        run_inline(DummyAnalysis(), board, bad, registry)


def test_validate_setup_error_raises(board, registry) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SetupError, match=r"DUMMY\.BAD"):
        run_inline(DummyAnalysis(), board, _setup(fail_validation=True), registry)


def test_cycle_detected(board, registry) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError, match="cycle"):
        run_inline(DummyAnalysis(), board, _setup(cycle=True), registry, services={"factor": 1})


def test_cancel(board, registry) -> None:  # type: ignore[no-untyped-def]
    ev = threading.Event()
    ev.set()
    with pytest.raises(Cancelled):
        run_inline(DummyAnalysis(), board, _setup(), registry, cancel=ev, services={"factor": 1})


def test_missing_service_is_keyerror(board, registry) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(KeyError, match="factor"):
        run_inline(DummyAnalysis(), board, _setup(), None)


def test_geometry_not_provided(board, registry, tmp_path) -> None:  # type: ignore[no-untyped-def]
    class NeedsGeometry(DummyAnalysis):
        def run_task(self, ctx, payload):  # type: ignore[no-untyped-def]
            ctx.geometry()
            raise AssertionError("unreachable")

    with pytest.raises(NotImplementedError, match="geometry"):
        run_inline(NeedsGeometry(), board, _setup(), registry, services={"factor": 1})


def test_geometry_factory(board, registry) -> None:  # type: ignore[no-untyped-def]
    seen = []

    class UsesGeometry(DummyAnalysis):
        def run_task(self, ctx, payload):  # type: ignore[no-untyped-def]
            seen.append(ctx.geometry() is ctx.geometry())
            return super().run_task(ctx, payload)

    run_inline(
        UsesGeometry(),
        board,
        _setup(),
        registry,
        services={"factor": 1},
        geometry=lambda b: object(),
    )
    assert seen == [True, True, True]
