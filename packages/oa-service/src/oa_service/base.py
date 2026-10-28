# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``ServiceBase``: the logic shared by the local and the fake service.

Subclasses supply project loading (``_open``) and a :class:`JobBackend`. Everything else (board
views, plugin discovery, job handles, summaries, exports, impedance) lives here, so the two
implementations cannot drift apart.
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from oa_framework.ingest import ReadOptions
from oa_framework.registry import Registry as PluginRegistry
from oa_model.setup import AnalysisSetup
from oa_results.bundle import ResultBundle
from oa_results.container import as_np
from oa_service import results_view
from oa_service.commands import CallContext
from oa_service.errors import ServiceError, close_matches
from oa_service.models import (
    AnalysisInfo,
    ExportIn,
    ExportInfo,
    JobBrief,
    JobEventView,
    JobHandle,
    JobKind,
    JobStatusOut,
    ProjectHandle,
    RuleInfo,
    RunAnalysisIn,
    RunDryRun,
    SetupPutOut,
    SetupValidation,
    XsImpedance,
    XsImpedanceIn,
)
from oa_service.quick import quick_xsection
from oa_service.session import BoardSession

__all__ = ["JobBackend", "JobRecord", "ServiceBase"]

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@dataclass
class JobRecord:
    """Backend-neutral job state."""

    brief: JobBrief
    error: dict[str, Any] | None = None
    budget_used: dict[str, float] = field(default_factory=dict)


class JobBackend(Protocol):
    """Job execution behind :class:`ServiceBase` (real: ``oa_jobs``; fake: in memory)."""

    def dry_run(self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext) -> RunDryRun: ...

    def submit(self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext) -> str: ...

    def status(self, job_id: str) -> JobRecord: ...

    def events(self, job_id: str, after_seq: int, limit: int) -> list[JobEventView]: ...

    def wait(self, job_id: str, timeout: float) -> None: ...

    def cancel(self, job_id: str) -> None: ...

    def list(self, project_id: str, state: JobKind | None, limit: int) -> list[JobBrief]: ...

    def bundle(self, result_id: str) -> ResultBundle: ...

    def export_dir(self, result_id: str) -> Path | None: ...

    def estimate_of(self, job_id: str) -> Any: ...


class ServiceBase:
    """Implements ``OAService`` except project loading and the job backend."""

    def __init__(self, registry: PluginRegistry, backend: JobBackend) -> None:
        self.registry = registry
        self.backend = backend
        self._sessions: dict[str, BoardSession] = {}

    # -- to implement ------------------------------------------------------------------------

    def _open(
        self,
        path: Path,
        read_options: ReadOptions | None,
        physics: Path | None,
        intent: Path | None,
    ) -> BoardSession:
        raise NotImplementedError

    def _reopen(self, session: BoardSession) -> BoardSession:
        raise NotImplementedError

    # -- projects --------------------

    def open(
        self,
        path: Path,
        *,
        read_options: ReadOptions | None = None,
        physics: Path | None = None,
        intent: Path | None = None,
    ) -> ProjectHandle:
        session = self._open(path, read_options, physics, intent)
        self._sessions[session.id] = session
        return session.handle()

    def projects(self) -> Sequence[ProjectHandle]:
        return [self._sessions[k].handle() for k in sorted(self._sessions)]

    def project(self, project_id: str) -> BoardSession:
        try:
            return self._sessions[project_id]
        except KeyError:
            cands = close_matches(project_id, self._sessions)
            raise ServiceError(
                "NOT_FOUND",
                f"No open project {project_id!r}.",
                path="/project_id",
                hint="Open it first with board.open; open projects: "
                + (", ".join(sorted(self._sessions)) or "none")
                + ".",
                details={"candidates": cands},
            ) from None

    def reload(self, project_id: str) -> ProjectHandle:
        session = self._reopen(self.project(project_id))
        self._sessions[session.id] = session
        return session.handle()

    # -- plugins --------------------

    def analyses(self) -> tuple[AnalysisInfo, ...]:
        out = []
        for kind in sorted(self.registry.analyses):
            a = self.registry.analyses[kind]
            out.append(
                AnalysisInfo(
                    kind=kind,
                    title=str(getattr(a, "title", kind)),
                    version=str(getattr(a, "version", "")),
                    config_schema=a.Config.model_json_schema(),
                )
            )
        return tuple(out)

    def rules(self) -> tuple[RuleInfo, ...]:
        out = []
        for rid in sorted(self.registry.rules):
            r = self.registry.rules[rid]
            out.append(
                RuleInfo(
                    id=rid,
                    title=str(getattr(r, "title", rid)),
                    category=str(getattr(r, "category", "")),
                    aliases=tuple(getattr(r, "aliases", ())),
                    default_severity=getattr(r, "default_severity", "warning"),
                    config_schema=r.Config.model_json_schema(),
                    references=tuple(getattr(r, "references", ())),
                    explanation=str(getattr(r, "explanation", "")),
                )
            )
        return tuple(out)

    def _require_analysis(self, kind: str) -> None:
        if kind not in self.registry.analyses:
            known = sorted(self.registry.analyses)
            raise ServiceError(
                "ANALYSIS_UNAVAILABLE" if kind in _KNOWN_KINDS else "NOT_FOUND",
                f"Analysis {kind!r} is not installed.",
                path="/analysis",
                hint="Installed analyses: " + (", ".join(known) or "none") + ".",
                details={"candidates": close_matches(kind, known), "installed": known},
            )

    # -- setups --------------------

    def validate_setup(self, project_id: str, name: str) -> SetupValidation:
        session = self.project(project_id)
        setup = session.setup(name)
        return self._validate(session, setup)

    def _validate(self, session: BoardSession, setup: AnalysisSetup) -> SetupValidation:
        req = RunAnalysisIn(
            project_id=session.id,
            analysis=setup.analysis,
            config=dict(setup.config),
            ports=tuple(p.model_dump(mode="json") for p in setup.ports),
        )
        self._require_analysis(setup.analysis)
        dr = self.backend.dry_run(session, req, CallContext(caller="test"))
        return SetupValidation(name=setup.name, valid=dr.valid, diagnostics=dr.diagnostics)

    def put_setup(self, project_id: str, setup: AnalysisSetup) -> SetupPutOut:
        return self.project(project_id).put_setup(setup)

    # -- jobs --------------------

    def dry_run(self, project_id: str, req: RunAnalysisIn, ctx: CallContext) -> RunDryRun:
        session = self.project(project_id)
        if req.analysis is not None:
            self._require_analysis(req.analysis)
        return self.backend.dry_run(session, req, ctx)

    def submit(self, project_id: str, req: RunAnalysisIn, ctx: CallContext) -> JobHandle:
        session = self.project(project_id)
        if req.analysis is not None:
            self._require_analysis(req.analysis)
        job_id = self.backend.submit(session, req, ctx)
        if req.wait_s > 0:
            self.backend.wait(job_id, req.wait_s)
        return self._handle(job_id)

    def _handle(self, job_id: str) -> JobHandle:
        rec = self.backend.status(job_id)
        b = rec.brief
        summary = None
        if b.state == "succeeded" and b.result_id is not None:
            summary = results_view.bundle_summary(self.backend.bundle(b.result_id))
        return JobHandle(
            job_id=b.job_id,
            state=b.state,
            progress=b.progress,
            estimate=self.backend.estimate_of(job_id),
            result_id=b.result_id,
            summary=summary,
            status=b,
        )

    def job(
        self, job_id: str, *, wait_s: float = 0.0, events_after_seq: int | None = None
    ) -> JobStatusOut:
        if wait_s > 0:
            self.backend.wait(job_id, wait_s)
        rec = self.backend.status(job_id)
        events = (
            self.backend.events(job_id, events_after_seq, 50)
            if events_after_seq is not None
            else []
        )
        summary = None
        if rec.brief.state == "succeeded" and rec.brief.result_id is not None:
            summary = results_view.bundle_summary(self.backend.bundle(rec.brief.result_id))
        return JobStatusOut(
            status=rec.brief,
            error=rec.error,
            events=tuple(events),
            budget_used=rec.budget_used,
            summary=summary,
        )

    def jobs(
        self, project_id: str, *, state: JobKind | None = None, limit: int = 100
    ) -> Sequence[JobBrief]:
        self.project(project_id)
        return self.backend.list(project_id, state, limit)

    def cancel(self, job_id: str) -> JobBrief:
        self.backend.cancel(job_id)
        return self.backend.status(job_id).brief

    # -- results --------------------

    def result(self, result_id: str) -> ResultBundle:
        return self.backend.bundle(result_id)

    def rule_table(self) -> dict[str, RuleInfo]:
        return {r.id: r for r in self.rules()}

    def export(self, req: ExportIn, ctx: CallContext) -> ExportInfo:
        bundle = self.backend.bundle(req.result_id)
        base = self.backend.export_dir(req.result_id)
        if req.out_path is not None:
            dest = ctx.check_path(req.out_path)
        else:
            root = (
                base if base is not None else Path(tempfile.gettempdir()) / "oa-exports"
            ) / bundle.id
            dest = ctx.check_path(root / results_view.export_name(bundle, req.format))
        art = None
        if base is not None:
            from oa_results.io import bundle_artifact_path

            rdir = base.parent / "results" / bundle.id
            art = lambda ref: bundle_artifact_path(rdir, ref)  # noqa: E731
        return results_view.export_bundle(
            bundle,
            req.format,
            dest,
            item_id=req.item_id,
            overwrite=req.overwrite,
            artifact_path=art,
        )

    # -- impedance --------------------

    def xs_impedance(self, req: XsImpedanceIn, ctx: CallContext) -> XsImpedance:
        services = self.registry.services()
        if not services.has("impedance"):
            raise ServiceError(
                "ANALYSIS_UNAVAILABLE",
                "No impedance service is installed.",
                hint="Install oa-impedance (WP-105); it registers the 'impedance' service.",
            )
        if req.xsection is not None:
            xs = req.xsection
        elif req.quick is not None:
            xs = quick_xsection(req.quick)
        else:
            assert req.sample is not None
            xs = self._sample_xs(req)
        res = services.get("impedance").solve(xs, list(req.freqs) or None, method=req.method)
        p = res.params

        def arr(x: Any) -> tuple[float, ...] | None:
            return None if x is None else tuple(float(v) for v in as_np(x).ravel())

        def mode0(x: Any) -> tuple[float, ...]:
            a = np.real(as_np(x))
            return tuple(float(v) for v in (a[:, 0] if a.ndim > 1 else a))

        return XsImpedance(
            xs_key=res.xs_key,
            method=res.method,
            model_name=res.model_name,
            freqs_hz=arr(p.freqs) or (),
            z0_ohm=arr(p.z0),
            zdiff_ohm=arr(p.z_diff),
            zcomm_ohm=arr(p.z_comm),
            zodd_ohm=arr(p.z_odd),
            zeven_ohm=arr(p.z_even),
            eps_eff=mode0(p.eps_eff_modal),
            delay_s_per_m=mode0(p.delay_per_m),
            loss_db_per_m=mode0(p.loss_db_per_m),
            accuracy_class=res.accuracy_class,
            notes=tuple(res.notes),
        )

    def _sample_xs(self, req: XsImpedanceIn) -> Any:
        smp = req.sample
        assert smp is not None
        session = self.project(smp.project_id)
        net = session.resolve_net(smp.net)
        geom = session.geometry()
        segs = [s for s in geom.segments(net) if smp.layer is None or s.layer == smp.layer]
        if not segs:
            raise ServiceError(
                "NOT_FOUND",
                f"Net {net} has no routed segments"
                + (f" on {smp.layer}" if smp.layer else "")
                + ".",
                path="/sample",
                hint="Pick a routed signal net; see board.net.",
            )
        segs.sort(key=lambda s: (s.s_start, s.id))
        if smp.s_m is not None:
            seg = next((s for s in segs if s.s_start <= smp.s_m <= s.s_start + s.length), segs[-1])
            local = min(max(smp.s_m - seg.s_start, 0.0), seg.length)
        elif smp.at_m is not None:
            best = min(((s, *_local_s(s, smp.at_m)) for s in segs), key=lambda t: t[2])
            seg, local = best[0], best[1]
        else:
            seg, local = segs[0], segs[0].length / 2
        return geom.cross_section_at(seg, local)


_KNOWN_KINDS = frozenset(
    {"impedance.xsection", "impedance.profile", "erc", "pi.dc", "em.sparams", "qs.extract"}
)


def _local_s(seg: Any, p: tuple[float, float]) -> tuple[float, float]:
    """(arc length along the segment closest to ``p``, distance to it) for lines and arcs."""
    s = seg.seg
    if s.kind == "line":
        ax, ay = s.start
        bx, by = s.end
        dx, dy = bx - ax, by - ay
        n2 = dx * dx + dy * dy
        t = 0.0 if n2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / n2))
        d = math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))
        return t * seg.length, d
    d0 = math.hypot(p[0] - s.start[0], p[1] - s.start[1])
    d1 = math.hypot(p[0] - s.end[0], p[1] - s.end[1])
    return (0.0, d0) if d0 <= d1 else (seg.length, d1)
