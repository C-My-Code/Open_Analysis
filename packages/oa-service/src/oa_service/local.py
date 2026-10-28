# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""``LocalService``: the real ``OAService`` (readers via the plugin registry, ``oa_jobs`` jobs).

A project is a directory with ``oa-project.yaml``. A bare board file opens an *ephemeral* project:
the manifest and ``.oa/`` state live in the user cache directory under the board's content hash,
so nothing is written next to the board (ai-integration.md section 8.3).

Job services are created lazily, one per project root, and a small index
(``<cache>/projects.json``) lets later processes (``oa jobs status JOB``) find the project that
owns a job or result id.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from oa_framework.ingest import ReadOptions
from oa_framework.ingest import load as load_board_file
from oa_framework.jobs import JobState
from oa_framework.registry import Registry as PluginRegistry
from oa_jobs import (
    Budget,
    JobRequest,
    JobTimeout,
    LocalJobService,
    PlanEstimate,
    ToolsConfig,
)
from oa_jobs.models import JobNotFound
from oa_model.common import Diagnostic
from oa_model.errors import SetupError
from oa_model.io import load_board
from oa_model.physics import apply_physics, load_physics
from oa_model.setup import (
    AnalysisSetup,
    DesignIntent,
    ProjectManifest,
    load_intent,
    load_project,
    resolve_intent,
)
from oa_results.bundle import ResultBundle
from oa_results.io import read_bundle
from oa_results.io.bundle_io import MANIFEST
from oa_service.base import JobRecord, ServiceBase
from oa_service.commands import CallContext
from oa_service.errors import ServiceError, ServiceErrors, from_validation_error
from oa_service.models import (
    JobBrief,
    JobEventView,
    JobKind,
    RunAnalysisIn,
    RunDryRun,
)
from oa_service.session import BoardSession, project_id_for

__all__ = ["LocalService", "default_cache_dir"]

MANIFEST_NAMES = ("oa-project.yaml", "oa-project.yml")
BOARD_SUFFIXES = (".kicad_pcb", ".xml", ".pcbdoc", ".oab", ".json")


def default_cache_dir() -> Path:
    """``$OA_CACHE_DIR``, else the per-user cache directory for Open_Analysis."""
    env = os.environ.get("OA_CACHE_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return base / "open_analysis" / "cache"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "open_analysis"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "open_analysis"


def _diag(exc: ValidationError, prefix: str = "/config") -> tuple[Diagnostic, ...]:
    return tuple(
        Diagnostic(severity="error", code=i.code, message=i.message, path=i.path, hint=i.hint)
        for i in from_validation_error(exc, prefix=prefix)
    )


class _LocalJobs:
    """``JobBackend`` over one ``LocalJobService`` per project root."""

    def __init__(self, svc: LocalService) -> None:
        self.svc = svc
        self._services: dict[str, LocalJobService] = {}
        self._job_root: dict[str, str] = {}
        self._lock = threading.Lock()

    # -- service lookup -------------------------------------------------------------------

    def service_for_root(self, root: Path) -> LocalJobService:
        key = str(root.resolve())
        with self._lock:
            js = self._services.get(key)
            if js is None:
                js = LocalJobService(
                    root,
                    self.svc.registry,
                    registry_factory=self.svc.registry_factory,
                    tools=self.svc.tools,
                    workers=self.svc.workers,
                    ceiling=self.svc.ceiling,
                )
                self._services[key] = js
            return js

    def _candidate_roots(self) -> list[Path]:
        roots = [Path(k) for k in self._services]
        for r in self.svc.known_roots():
            if r not in roots and (r / ".oa" / "jobs.sqlite").is_file():
                roots.append(r)
        return roots

    def _locate_job(self, job_id: str) -> LocalJobService:
        root = self._job_root.get(job_id)
        if root is not None:
            return self.service_for_root(Path(root))
        for r in self._candidate_roots():
            js = self.service_for_root(r)
            if js.store.job(job_id) is not None:
                self._job_root[job_id] = str(r)
                return js
        raise ServiceError(
            "NOT_FOUND",
            f"No job {job_id!r}.",
            path="/job_id",
            hint="Open the project that owns the job (board.open) and list jobs with jobs.list.",
        )

    def _locate_result_root(self, result_id: str) -> Path:
        for r in self._candidate_roots():
            if (r / ".oa" / "results" / result_id).is_dir():
                return r
        raise ServiceError(
            "NOT_FOUND",
            f"No result {result_id!r}.",
            path="/result_id",
            hint="Result ids come from finished jobs (jobs.status); open the owning project first.",
        )

    # -- JobBackend -----------------------------------------------------------------------------

    def _request(
        self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext
    ) -> tuple[JobRequest, LocalJobService]:
        js = self.service_for_root(self.svc.root_of(session))
        manifest = self.svc.manifest_path(session)
        if req.setup is not None:
            jr = JobRequest(
                project=manifest,
                setup=req.setup,
                executor=req.executor,
                budget=req.budget or ctx.default_budget,
                caller=ctx.caller,
            )
        else:
            assert req.analysis is not None and req.config is not None
            try:
                inline = AnalysisSetup.model_validate(
                    {
                        "schema": "oa-setup/1",
                        "name": "inline",
                        "analysis": req.analysis,
                        "config": req.config,
                        "ports": list(req.ports),
                    }
                )
            except ValidationError as exc:
                raise ServiceErrors(from_validation_error(exc, prefix="/ports")) from exc
            jr = JobRequest(
                project=manifest,
                setup="inline",
                inline_setup=inline,
                executor=req.executor,
                budget=req.budget or ctx.default_budget,
                caller=ctx.caller,
            )
        return jr, js

    def dry_run(self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext) -> RunDryRun:
        jr, js = self._request(session, req, ctx)
        try:
            est = js.estimate(jr)
        except SetupError as exc:
            diags = tuple(exc.diagnostics) or (
                Diagnostic(severity="error", code="SETUP_INVALID", message=str(exc)),
            )
            return RunDryRun(valid=False, diagnostics=diags, estimate=None)
        except ValidationError as exc:
            return RunDryRun(valid=False, diagnostics=_diag(exc), estimate=None)
        return RunDryRun(valid=True, diagnostics=(), estimate=est)

    def submit(self, session: BoardSession, req: RunAnalysisIn, ctx: CallContext) -> str:
        jr, js = self._request(session, req, ctx)
        try:
            job_id = js.submit(jr)
        except ValidationError as exc:
            raise ServiceErrors(from_validation_error(exc, prefix="/config")) from exc
        self._job_root[job_id] = str(self.svc.root_of(session).resolve())
        return job_id

    @staticmethod
    def _brief(st: Any) -> JobBrief:
        return JobBrief(
            job_id=st.id,
            state=st.state,
            analysis=st.analysis,
            setup=st.setup,
            progress=st.progress,
            message=st.message,
            result_id=st.result_id,
            tasks_total=st.tasks_total,
            tasks_done=st.tasks_done,
            tasks_cached=st.tasks_cached,
            tasks_failed=st.tasks_failed,
            error=st.error.model_dump(mode="json") if st.error is not None else None,
            cancel_reason=getattr(st, "cancel_reason", None),
            created_utc=st.created_utc,
            updated_utc=st.updated_utc,
        )

    def status(self, job_id: str) -> JobRecord:
        js = self._locate_job(job_id)
        try:
            st = js.status(job_id)
        except JobNotFound as exc:
            raise ServiceError("NOT_FOUND", exc.message, path="/job_id") from exc
        err = st.error.model_dump(mode="json") if st.error is not None else None
        return JobRecord(brief=self._brief(st), error=err, budget_used=dict(st.budget_used))

    def events(self, job_id: str, after_seq: int, limit: int) -> list[JobEventView]:
        js = self._locate_job(job_id)
        out: list[JobEventView] = []
        for ev in js.events(job_id, after_seq):
            out.append(
                JobEventView(seq=ev.seq, type=ev.type, task_id=ev.task_id, data=ev.data, utc=ev.utc)
            )
            if len(out) >= limit:
                break
        return out

    def wait(self, job_id: str, timeout: float) -> None:
        js = self._locate_job(job_id)
        try:
            js.wait(job_id, timeout)
        except JobTimeout:
            return

    def cancel(self, job_id: str) -> None:
        self._locate_job(job_id).cancel(job_id)

    def list(self, project_id: str, state: JobKind | None, limit: int) -> list[JobBrief]:
        session = self.svc.project(project_id)
        js = self.service_for_root(self.svc.root_of(session))
        st: JobState | None = state
        return [self._brief(js.status(s.id)) for s in js.list(state=st, limit=limit)]

    def bundle(self, result_id: str) -> ResultBundle:
        root = self._locate_result_root(result_id)
        return read_bundle(root / ".oa" / "results" / result_id)

    def export_dir(self, result_id: str) -> Path | None:
        return self._locate_result_root(result_id) / ".oa" / "exports"

    def estimate_of(self, job_id: str) -> PlanEstimate | None:
        js = self._locate_job(job_id)
        row = js.store.job(job_id)
        if row is None:
            return None
        meta = json.loads(row["meta_json"])
        est = meta.get("estimate")
        return PlanEstimate.model_validate(est) if est else None

    def manifest(self, result_id: str) -> dict[str, Any]:
        root = self._locate_result_root(result_id)
        doc = json.loads((root / ".oa" / "results" / result_id / MANIFEST).read_text("utf-8"))
        assert isinstance(doc, dict)
        return doc

    def close(self) -> None:
        for js in list(self._services.values()):
            js.close()
        self._services.clear()


class LocalService(ServiceBase):
    """Real service: opens boards through the reader registry and runs jobs with ``oa_jobs``."""

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        *,
        cache_dir: Path | None = None,
        registry_factory: str | None = None,
        tools: ToolsConfig | None = None,
        workers: int | None = None,
        ceiling: Budget | None = None,
    ) -> None:
        self.cache_dir = cache_dir or default_cache_dir()
        if registry is None and registry_factory is None:
            registry_factory = os.environ.get("OA_REGISTRY_FACTORY") or None
        if registry is None and registry_factory is not None:
            mod, _, attr = registry_factory.partition(":")
            registry = getattr(importlib.import_module(mod), attr)()
        self.registry_factory = registry_factory
        self.tools = tools
        self.workers = workers
        self.ceiling = ceiling
        self._roots: dict[str, Path] = {}
        self._jobs = _LocalJobs(self)
        super().__init__(registry if registry is not None else PluginRegistry.load(), self._jobs)

    # -- project index ----------------------------------------------------------------------

    @property
    def _index_file(self) -> Path:
        return self.cache_dir / "projects.json"

    def known_roots(self) -> list[Path]:
        roots = list(self._roots.values())
        try:
            data = json.loads(self._index_file.read_text(encoding="utf-8"))
            roots += [Path(p) for p in data.get("projects", {}).values()]
        except (OSError, ValueError):
            pass
        seen, out = set(), []
        for r in roots:
            if str(r) not in seen and r.is_dir():
                seen.add(str(r))
                out.append(r)
        return sorted(out)

    def _remember(self, project_id: str, root: Path) -> None:
        self._roots[project_id] = root
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(self._index_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {"projects": {}}
            data.setdefault("projects", {})[project_id] = str(root)
            tmp = self._index_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, sort_keys=True, indent=1), encoding="utf-8")
            tmp.replace(self._index_file)
        except OSError:  # a read-only cache directory must not break read-only use
            pass

    def root_of(self, session: BoardSession) -> Path:
        return self._roots[session.id]

    def manifest_path(self, session: BoardSession) -> Path:
        return self._roots[session.id] / "oa-project.yaml"

    # -- opening ------------------------------------------------------------------------------

    def _open(
        self,
        path: Path,
        read_options: ReadOptions | None,
        physics: Path | None,
        intent: Path | None,
    ) -> BoardSession:
        path = path.resolve()
        manifest_file: Path | None = None
        if path.is_dir():
            for n in MANIFEST_NAMES:
                if (path / n).is_file():
                    manifest_file = path / n
                    break
            if manifest_file is None:
                raise ServiceError(
                    "NOT_FOUND",
                    f"{path} has no oa-project.yaml.",
                    path="/path",
                    hint="Open a board file instead, or create a project with `oa init`.",
                )
        elif path.name in MANIFEST_NAMES:
            manifest_file = path
        elif path.suffix.lower() not in BOARD_SUFFIXES and not path.name.endswith(".oab.json"):
            raise ServiceError(
                "INGEST_NO_READER",
                f"{path.name} is not a project or a known board file.",
                path="/path",
                hint="Supported: oa-project.yaml, .kicad_pcb, IPC-2581 .xml, .PcbDoc, .oab(.json).",
            )
        if manifest_file is not None:
            return self._open_project(manifest_file, read_options)
        return self._open_bare(path, read_options, physics, intent)

    def _read_board(self, board_path: Path, opts: ReadOptions) -> Any:
        if board_path.suffix == ".oab" or board_path.name.endswith(".oab.json"):
            return load_board(board_path)
        return load_board_file(board_path, opts, registry=self.registry)

    def _open_project(self, manifest_file: Path, read_options: ReadOptions | None) -> BoardSession:
        try:
            manifest: ProjectManifest = load_project(manifest_file)
        except ValidationError as exc:
            raise ServiceErrors(from_validation_error(exc, prefix="/project")) from exc
        pdir = manifest_file.parent
        board_path = (pdir / manifest.board).resolve()
        if not board_path.is_file():
            raise ServiceError(
                "NOT_FOUND",
                f"Board {board_path} not found.",
                path="/board",
                hint=f"Fix `board:` in {manifest_file.name}.",
            )
        opts = read_options or ReadOptions.model_validate(manifest.read_options.model_dump())
        raw = self._read_board(board_path, opts)
        phys = load_physics(pdir / manifest.physics) if manifest.physics else None
        board = apply_physics(raw, phys) if phys is not None else raw
        dsn = load_intent(pdir / manifest.intent) if manifest.intent else DesignIntent()
        pid = project_id_for(manifest_file)
        self._remember(pid, pdir)
        return BoardSession(
            project_id=pid,
            name=manifest.name,
            board=board,
            raw_board_hash=raw.content_hash(),
            intent=resolve_intent(board, dsn),
            board_path=str(board_path),
            project_path=str(manifest_file),
            ephemeral=False,
            physics_hash=("sha256:" + hashlib.sha256(phys.model_dump_json().encode()).hexdigest())
            if phys is not None
            else None,
            setups_dir=pdir / manifest.setups_dir,
            watch_paths=tuple(
                p
                for p in (
                    manifest_file,
                    board_path,
                    pdir / manifest.physics if manifest.physics else None,
                    pdir / manifest.intent if manifest.intent else None,
                    pdir / manifest.setups_dir,
                )
                if p is not None
            ),
        )

    def _open_bare(
        self,
        board_path: Path,
        read_options: ReadOptions | None,
        physics: Path | None,
        intent: Path | None,
    ) -> BoardSession:
        opts = read_options or ReadOptions()
        raw = self._read_board(board_path, opts)
        phys = load_physics(physics) if physics else None
        board = apply_physics(raw, phys) if phys is not None else raw
        dsn = load_intent(intent) if intent else DesignIntent()
        digest = hashlib.sha256(board_path.read_bytes()).hexdigest()[:16]
        ws = self.cache_dir / "ephemeral" / digest
        ws.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "schema": "oa-project/1",
            "name": raw.meta.name,
            "board": board_path.as_posix(),
            "read_options": {"refill_zones": opts.refill_zones},
        }
        if physics:
            manifest["physics"] = physics.resolve().as_posix()
        if intent:
            manifest["intent"] = intent.resolve().as_posix()
        mf = ws / "oa-project.yaml"
        text = yaml.safe_dump(manifest, sort_keys=False)
        if not mf.exists() or mf.read_text(encoding="utf-8") != text:
            mf.write_text(text, encoding="utf-8", newline="\n")
        pid = project_id_for(board_path)
        self._remember(pid, ws)
        return BoardSession(
            project_id=pid,
            name=raw.meta.name,
            board=board,
            raw_board_hash=raw.content_hash(),
            intent=resolve_intent(board, dsn),
            board_path=str(board_path),
            project_path=None,
            ephemeral=True,
            physics_hash=("sha256:" + hashlib.sha256(phys.model_dump_json().encode()).hexdigest())
            if phys is not None
            else None,
            setups_dir=None,
            watch_paths=tuple(p for p in (board_path, physics, intent) if p is not None),
        )

    def _reopen(self, session: BoardSession) -> BoardSession:
        if session.project_path is not None:
            return self._open_project(Path(session.project_path), None)
        root = self._roots[session.id]
        manifest = load_project(root / "oa-project.yaml")
        return self._open_bare(
            Path(session.board_path),
            ReadOptions.model_validate(manifest.read_options.model_dump()),
            Path(manifest.physics) if manifest.physics else None,
            Path(manifest.intent) if manifest.intent else None,
        )

    def project(self, project_id: str) -> BoardSession:
        # Sessions are per process; a known id from the index can be reopened on demand.
        if project_id not in self._sessions:
            try:
                data = json.loads(self._index_file.read_text(encoding="utf-8"))
                root = data.get("projects", {}).get(project_id)
            except (OSError, ValueError):
                root = None
            if root is not None and (Path(root) / "oa-project.yaml").is_file():
                mf = Path(root) / "oa-project.yaml"
                manifest = load_project(mf)
                board = (Path(root) / manifest.board).resolve()
                # Ephemeral workspaces live in the cache directory; real projects elsewhere.
                if Path(root).resolve().is_relative_to((self.cache_dir / "ephemeral").resolve()):
                    self._sessions[project_id] = self._open_bare(
                        board,
                        ReadOptions.model_validate(manifest.read_options.model_dump()),
                        Path(manifest.physics) if manifest.physics else None,
                        Path(manifest.intent) if manifest.intent else None,
                    )
                else:
                    self._sessions[project_id] = self._open_project(mf, None)
        return super().project(project_id)

    def close(self) -> None:
        """Stop job watchdogs. Safe to call twice."""
        self._jobs.close()

    def __enter__(self) -> LocalService:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
