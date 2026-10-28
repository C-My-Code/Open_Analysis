# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``FakeService``: an in-memory ``OAService`` over ``oa_testkit`` boards and canned results.

Built for WP-210 (MCP) and the web layer to develop and test against the real command catalog
before every analysis exists. It shares :class:`oa_service.base.ServiceBase` with the real
service, so board views, paging, summaries, errors and size caps behave identically. Only
project loading and job execution differ:

- ``open(path)`` accepts a real ``.oab`` / ``.oab.json`` file, or a fixture name written to disk
  by :meth:`FakeService.fixture_path` (``microstrip``, ``stripline``, ``diff_pair``, ``cpwg``,
  ``split_plane``, ``via_transition``, ``power_plane``).
- jobs finish immediately (``auto_complete=True``) with a canned bundle: ``erc`` yields a
  ``ViolationSet`` on the board's nets, ``impedance.profile`` and ``dummy.work`` yield scalars.
  With ``auto_complete=False`` jobs stay ``running`` until :meth:`FakeService.finish_job`, which
  makes polling, ``wait_s`` and cancellation testable.
- ``estimate_seconds`` sets the pre-flight estimate so ``BUDGET_EXCEEDED`` can be provoked.

Importing this module requires ``oa-testkit`` (``pip install oa-service[testing]``).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import ValidationError

from oa_framework.analysis import Plan, PlanContext, RunContext, TaskOutput
from oa_framework.registry import Registry as PluginRegistry
from oa_jobs.models import BudgetExceeded, PlanEstimate
from oa_model._base import OAModel
from oa_model.board import Board
from oa_model.common import Diagnostic
from oa_model.io import dump_board, load_board
from oa_model.setup import DesignIntent, resolve_intent
from oa_results.bundle import Provenance, ResultBundle, ResultItem
from oa_results.scalars import Scalar, ScalarSet
from oa_results.violations import Remediation, Violation, ViolationSet
from oa_service.base import JobRecord, ServiceBase
from oa_service.commands import CallContext
from oa_service.errors import ServiceError, ServiceErrors, close_matches, from_validation_error
from oa_service.models import (
    JobBrief,
    JobEventView,
    JobKind,
    RunAnalysisIn,
    RunDryRun,
)
from oa_service.session import BoardSession, project_id_for
from oa_testkit import builders

__all__ = ["FIXTURES", "FakeErcAnalysis", "FakeService", "new_ulid"]

FIXTURES: dict[str, Callable[[], Board]] = {
    "microstrip": builders.microstrip_board,
    "stripline": builders.stripline_board,
    "diff_pair": builders.diff_pair_board,
    "cpwg": builders.cpwg_board,
    "split_plane": builders.split_plane_board,
    "via_transition": builders.via_transition_board,
    "power_plane": builders.power_plane_board,
}

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ulid_lock = threading.Lock()
_ulid_last = [0, 0]


def new_ulid() -> str:
    """A 26-character Crockford-base32 ULID (monotonic within the process)."""
    with _ulid_lock:
        ms = int(time.time() * 1000)
        if ms <= _ulid_last[0]:
            ms = _ulid_last[0]
            _ulid_last[1] += 1
        else:
            _ulid_last[0] = ms
            _ulid_last[1] = int.from_bytes(os.urandom(8), "big") >> 1
        n = (ms << 80) | (_ulid_last[1] & ((1 << 80) - 1))
    return "".join(_B32[(n >> (5 * i)) & 31] for i in reversed(range(26)))


# -- fake analyses (registered so describe/analyses/validation behave like the real thing) ----


class _ErcConfig(OAModel):
    """ERC setup (fake): rule overrides and net scope."""

    rules: dict[str, dict[str, Any]] = {}
    nets: dict[str, Any] | None = None


class _ProfileConfig(OAModel):
    """Impedance profile setup (fake)."""

    nets: dict[str, Any]
    method: str | None = None
    ds: float | None = None
    tolerance: float | None = None


class _WorkConfig(OAModel):
    """Dummy compute setup (fake)."""

    n_tasks: int = 4


class _Payload(OAModel):
    """Fake task payload."""

    i: int = 0


class _FakeBase:
    version: ClassVar[str] = "0.0-fake"
    TaskPayload: ClassVar[type[OAModel]] = _Payload

    def validate_setup(self, ctx: PlanContext, cfg: OAModel) -> tuple[Diagnostic, ...]:
        return ()

    def plan(self, ctx: PlanContext, cfg: OAModel) -> Plan:
        raise NotImplementedError("FakeService runs no real plans")

    def run_task(self, ctx: RunContext, payload: OAModel) -> TaskOutput:
        raise NotImplementedError("FakeService runs no real tasks")

    def reduce(
        self, ctx: RunContext, cfg: OAModel, outputs: Mapping[str, TaskOutput]
    ) -> ResultBundle:
        raise NotImplementedError("FakeService runs no real tasks")


class FakeErcAnalysis(_FakeBase):
    """Stands in for the ERC analysis (WP-201)."""

    kind: ClassVar[str] = "erc"
    title: ClassVar[str] = "Electrical rule check (fake)"
    Config: ClassVar[type[OAModel]] = _ErcConfig


class FakeProfileAnalysis(_FakeBase):
    """Stands in for the impedance profile analysis (WP-203)."""

    kind: ClassVar[str] = "impedance.profile"
    title: ClassVar[str] = "Impedance profile (fake)"
    Config: ClassVar[type[OAModel]] = _ProfileConfig


class FakeWorkAnalysis(_FakeBase):
    """A trivial analysis for exercising jobs."""

    kind: ClassVar[str] = "dummy.work"
    title: ClassVar[str] = "Dummy work (fake)"
    Config: ClassVar[type[OAModel]] = _WorkConfig


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def canned_bundle(
    analysis: str, board: Board, config: Mapping[str, Any], job_id: str
) -> ResultBundle:
    """A deterministic bundle for ``analysis`` on ``board``."""
    status: Literal["ok", "warning"]
    now = _now()
    prov = Provenance(
        analysis=analysis,
        analysis_version="0.0-fake",
        board_hash=board.content_hash(),
        physics_hash=None,
        setup_hash="sha256:" + hashlib.sha256(repr(sorted(config.items())).encode()).hexdigest(),
        config=dict(config),
        solver="fake",
        solver_version="0",
        started_utc=now,
        finished_utc=now,
    )
    if analysis == "erc":
        nets = sorted(n.id for n in board.nets if n.kind != "ground") or ["(none)"]
        rules = [
            ("IM-001", "error", 45.2, 50.0, "ohm"),
            ("RP-001", "warning", 0.1e-3, 0.2e-3, "m"),
            ("DP-003", "info", 2.0e-3, 2.5e-3, "m"),
        ]
        vs = []
        for k, (rid, sev, meas, lim, unit) in enumerate(rules):
            net = nets[k % len(nets)]
            x, y = 1e-3 * (k + 1), 2e-3 * (k + 1)
            fp = hashlib.sha1(f"{rid}|{net}|{x}|{y}".encode()).hexdigest()[:16]
            vs.append(
                Violation(
                    rule_id=rid,
                    severity=sev,  # type: ignore[arg-type]
                    message=f"{rid} on {net}: measured {meas:g} {unit} vs limit {lim:g} {unit}.",
                    nets=(net,),
                    layers=("L1",),
                    items=(),
                    location=(x, y),
                    measured=meas,
                    limit=lim,
                    unit=unit,
                    fix_hint="Adjust the geometry to meet the limit.",
                    remediation=Remediation(hint="Adjust the geometry to meet the limit."),
                    fingerprint=fp,
                )
            )
        data: Any = ViolationSet.from_violations(("IM-001", "RP-001", "DP-003"), tuple(vs))
        item = ResultItem(id="violations", title="Rule violations", kind="violations", data=data)
        text = data.digest()
        status = "warning"
    else:
        data = ScalarSet(values=(Scalar(name="n_nets", value=len(board.nets)),))
        item = ResultItem(id="scalars", title="Scalars", kind="scalars", data=data)
        text = f"{analysis}: fake run on {len(board.nets)} net(s)."
        status = "ok"
    return ResultBundle(
        id=job_id, status=status, summary=text, items=(item,), diagnostics=(), provenance=prov
    )


class _FakeJobs:
    """In-memory ``JobBackend``."""

    def __init__(self, svc: FakeService) -> None:
        self.svc = svc
        self.jobs: dict[str, dict[str, Any]] = {}
        self.bundles: dict[str, ResultBundle] = {}
        self._lock = threading.Lock()

    def _validate(
        self, req: RunAnalysisIn, session: BoardSession
    ) -> tuple[str, dict[str, Any], str]:
        if req.setup is not None:
            setup = session.setup(req.setup)
            kind, cfg, name = setup.analysis, dict(setup.config), setup.name
        else:
            assert req.analysis is not None and req.config is not None
            kind, cfg, name = req.analysis, dict(req.config), "inline"
        cls = self.svc.registry.analyses.get(kind)
        if cls is None:
            raise ServiceError("NOT_FOUND", f"Unknown analysis {kind!r}.", path="/analysis")
        try:
            cls.Config.model_validate(cfg)
        except ValidationError as exc:
            raise ServiceErrors(from_validation_error(exc, prefix="/config")) from exc
        return kind, cfg, name

    def _estimate(self, req: RunAnalysisIn, ctx: CallContext) -> PlanEstimate:
        eff = ctx.clamp(req.budget)
        secs = self.svc.estimate_seconds
        reasons = []
        if eff.max_runtime_s is not None and secs > eff.max_runtime_s:
            reasons.append(f"estimated {secs:g} s exceeds max_runtime_s {eff.max_runtime_s:g} s")
        return PlanEstimate(
            tasks=1,
            est_seconds=secs,
            est_wall_seconds=secs,
            est_peak_memory_gb=0.1,
            cpus=1,
            within_budget=not reasons,
            budget=eff,
            reasons=tuple(reasons),
        )

    def dry_run(self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext) -> RunDryRun:
        try:
            self._validate(req, session)
        except ServiceErrors as exc:
            diags = tuple(
                Diagnostic(
                    severity="error", code=i.code, message=i.message, path=i.path, hint=i.hint
                )
                for i in exc.infos
            )
            return RunDryRun(valid=False, diagnostics=diags, estimate=None)
        return RunDryRun(valid=True, diagnostics=(), estimate=self._estimate(req, ctx))

    def submit(self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext) -> str:
        kind, cfg, name = self._validate(req, session)
        est = self._estimate(req, ctx)
        if not est.within_budget:
            raise BudgetExceeded(
                "Estimated resources exceed the budget: " + "; ".join(est.reasons), est
            )
        job_id = new_ulid()
        now = _now()
        rec = {
            "project": session.id,
            "analysis": kind,
            "setup": name,
            "state": "running",
            "progress": 0.0,
            "created": now,
            "updated": now,
            "result_id": None,
            "cancel_reason": None,
            "estimate": est,
            "cfg": cfg,
            "events": [],
            "board": session.board,
        }
        with self._lock:
            self.jobs[job_id] = rec
        self._event(job_id, "state", {"state": "queued"})
        if self.svc.auto_complete:
            self.finish_job(job_id)
        return job_id

    def _event(self, job_id: str, type_: str, data: dict[str, Any]) -> None:
        rec = self.jobs[job_id]
        rec["events"].append(
            JobEventView(seq=len(rec["events"]) + 1, type=type_, data=data, utc=_now())
        )

    def finish_job(self, job_id: str) -> None:
        rec = self._get(job_id)
        if rec["state"] != "running":
            return
        bundle = canned_bundle(rec["analysis"], rec["board"], rec["cfg"], new_ulid())
        self.bundles[bundle.id] = bundle
        rec.update(state="succeeded", progress=1.0, result_id=bundle.id, updated=_now())
        self._event(job_id, "result", {"result_id": bundle.id, "status": bundle.status})

    def _get(self, job_id: str) -> dict[str, Any]:
        try:
            return self.jobs[job_id]
        except KeyError:
            cands = close_matches(job_id, self.jobs)
            raise ServiceError(
                "NOT_FOUND",
                f"No job {job_id!r}.",
                path="/job_id",
                hint="List jobs with jobs.list.",
                details={"candidates": cands},
            ) from None

    def _brief(self, job_id: str) -> JobBrief:
        r = self._get(job_id)
        return JobBrief(
            job_id=job_id,
            state=r["state"],
            analysis=r["analysis"],
            setup=r["setup"],
            progress=r["progress"],
            message="",
            result_id=r["result_id"],
            cancel_reason=r["cancel_reason"],
            created_utc=r["created"],
            updated_utc=r["updated"],
        )

    def status(self, job_id: str) -> JobRecord:
        return JobRecord(
            brief=self._brief(job_id), budget_used={"elapsed_s": 0.0, "peak_memory_gb": 0.0}
        )

    def events(self, job_id: str, after_seq: int, limit: int) -> list[JobEventView]:
        return [e for e in self._get(job_id)["events"] if e.seq > after_seq][:limit]

    def wait(self, job_id: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while self._get(job_id)["state"] not in ("succeeded", "failed", "cancelled"):
            if time.monotonic() >= deadline:
                return
            time.sleep(0.01)

    def cancel(self, job_id: str) -> None:
        r = self._get(job_id)
        if r["state"] == "running":
            r.update(state="cancelled", cancel_reason="user", updated=_now())
            self._event(job_id, "state", {"state": "cancelled"})

    def list(self, project_id: str, state: JobKind | None, limit: int) -> list[JobBrief]:
        out = [self._brief(j) for j, r in self.jobs.items() if r["project"] == project_id]
        return [b for b in out if state is None or b.state == state][:limit]

    def bundle(self, result_id: str) -> ResultBundle:
        try:
            return self.bundles[result_id]
        except KeyError:
            raise ServiceError(
                "NOT_FOUND",
                f"No result {result_id!r}.",
                path="/result_id",
                hint="Result ids come from finished jobs (jobs.status).",
                details={"candidates": close_matches(result_id, self.bundles)},
            ) from None

    def export_dir(self, result_id: str) -> Path | None:
        return self.svc.workdir / "exports"

    def estimate_of(self, job_id: str) -> PlanEstimate:
        est: PlanEstimate = self._get(job_id)["estimate"]
        return est


class FakeService(ServiceBase):
    """In-memory ``OAService``. See the module docstring."""

    def __init__(
        self,
        *,
        auto_complete: bool = True,
        estimate_seconds: float = 1.0,
        workdir: Path | None = None,
        registry: PluginRegistry | None = None,
    ) -> None:
        self.auto_complete = auto_complete
        self.estimate_seconds = estimate_seconds
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="oa-fake-"))
        self.workdir.mkdir(parents=True, exist_ok=True)
        reg = registry or PluginRegistry.for_tests(
            analyses=[FakeErcAnalysis, FakeProfileAnalysis, FakeWorkAnalysis]
        )
        self._jobs = _FakeJobs(self)
        super().__init__(reg, self._jobs)

    # -- fixtures --------------------

    def fixture_path(self, name: str) -> Path:
        """Write fixture board ``name`` to ``workdir`` (once) and return the ``.oab.json`` path."""
        if name not in FIXTURES:
            raise ServiceError(
                "NOT_FOUND",
                f"No fixture board {name!r}.",
                path="/path",
                hint="Fixtures: " + ", ".join(sorted(FIXTURES)) + ".",
                details={"candidates": close_matches(name, FIXTURES)},
            )
        path = self.workdir / f"{name}.oab.json"
        if not path.exists():
            dump_board(FIXTURES[name](), path)
        return path

    def open_fixture(self, name: str) -> Any:
        """Open fixture ``name``; returns the ``ProjectHandle``."""
        return self.open(self.fixture_path(name))

    def finish_job(self, job_id: str) -> None:
        """Complete a job held back by ``auto_complete=False``."""
        self._jobs.finish_job(job_id)

    # -- ServiceBase hooks --------------------

    def _open(
        self,
        path: Path,
        read_options: Any,
        physics: Path | None,
        intent: Path | None,
    ) -> BoardSession:
        board = load_board(path)
        return BoardSession(
            project_id=project_id_for(path),
            name=board.meta.name,
            board=board,
            raw_board_hash=board.content_hash(),
            intent=resolve_intent(board, DesignIntent()),
            board_path=str(path.resolve()),
            project_path=None,
            ephemeral=True,
        )

    def _reopen(self, session: BoardSession) -> BoardSession:
        return self._open(Path(session.board_path), None, None, None)
