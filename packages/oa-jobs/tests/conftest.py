# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from oa_framework.jobs import AnalysisRequest
from oa_jobs import Budget, JobRequest, LocalJobService, ToolsConfig
from oa_jobs.dummy import make_registry
from oa_model.io import dump_board
from oa_model.setup import AnalysisSetup
from oa_testkit.builders import microstrip_board

FACTORY = "oa_jobs.dummy:make_registry"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An ``oa-project.yaml`` next to a tiny board; returns the manifest path."""
    d = tmp_path / "proj"
    d.mkdir()
    dump_board(microstrip_board(), d / "board.oab.json")
    (d / "oa-project.yaml").write_text(
        "schema: oa-project/1\nname: demo\nboard: board.oab.json\n", encoding="utf-8"
    )
    return d / "oa-project.yaml"


def make_request(
    project: Path,
    config: dict[str, Any],
    *,
    executor: str = "inline",
    budget: Budget | None = None,
    **kw: Any,
) -> JobRequest:
    setup = AnalysisSetup(schema="oa-setup/1", name="s", analysis="dummy.work", config=config)
    return JobRequest(
        project=project,
        setup="inline",
        inline_setup=setup,
        executor=executor,  # type: ignore[arg-type]
        budget=budget,
        **kw,
    )


@pytest.fixture
def req_factory(project: Path) -> Callable[..., AnalysisRequest]:
    def make(config: dict[str, Any], **kw: Any) -> AnalysisRequest:
        return make_request(project, config, **kw)

    return make


@pytest.fixture
def service(project: Path) -> Iterator[LocalJobService]:
    svc = LocalJobService(
        project.parent,
        make_registry(),
        registry_factory=FACTORY,
        tools=ToolsConfig({}),
        workers=3,
        grace_s=3.0,
        watchdog_interval=0.05,
    )
    yield svc
    svc.close()
