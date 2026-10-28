# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The FastAPI application (interfaces.md section 7, ai-integration.md A11/A17).

Every endpoint is a few lines over ``oa_service``: it builds catalog arguments, dispatches, and
returns the raw typed body (REST does not use the envelope, except ``POST /commands/{name}``).
Errors are ``{"error": ErrorInfo, "errors": [...]}`` with the status of the error code.
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

import anyio
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from oa_model.io import dumps_board
from oa_model.stackup import Stackup
from oa_server import arrow
from oa_server.models import (
    CommandCall,
    ErrorBody,
    Health,
    JobBody,
    OpenBody,
    ProjectSummary,
    RestJobStatus,
    RestNetInfo,
    ToolStatus,
    XsBody,
)
from oa_service import CallContext, Registry
from oa_service.errors import ErrorInfo, ServiceError, http_status_for
from oa_service.models import (
    AnalysisInfo,
    BoardSummary,
    BundleSummary,
    JobBrief,
    NetDetail,
    RuleInfo,
    SetupList,
    SetupValidation,
    StackupView,
    ViolationExplanation,
    ViolationPage,
    XsImpedance,
)
from oa_service.service import OAService
from oa_service.types import Envelope, oa_version

__all__ = ["ApiError", "create_app"]

API = "/api/v1"
_ERR = {"model": ErrorBody}
ERRORS: dict[int | str, dict[str, Any]] = {400: _ERR, 403: _ERR, 404: _ERR, 409: _ERR, 422: _ERR}


class ApiError(Exception):
    """Carries catalog errors to the exception handler."""

    def __init__(self, errors: tuple[ErrorInfo, ...]) -> None:
        super().__init__(errors[0].message if errors else "error")
        self.errors = errors


def _error_response(errors: tuple[ErrorInfo, ...]) -> JSONResponse:
    first = errors[0]
    body = ErrorBody(error=first, errors=errors)
    return JSONResponse(
        status_code=http_status_for(first.code), content=body.model_dump(mode="json")
    )


def _status(b: JobBrief) -> RestJobStatus:
    d = b.model_dump(mode="json")
    d["id"] = d.pop("job_id")
    err = d.pop("error")
    return RestJobStatus.model_validate(
        {**d, "error": ErrorInfo.model_validate(err) if err else None}
    )


def _arrow(data: bytes) -> Response:
    return Response(data, media_type="application/vnd.apache.arrow.stream")


def create_app(
    service: OAService | None,
    *,
    token: str = "",
    ctx: CallContext | None = None,
    web_dist: Path | None = None,
    watch: bool = False,
) -> FastAPI:
    """Build the app. ``service=None`` yields a schema-only app (used to export OpenAPI)."""
    call_ctx = ctx or CallContext(caller="rest", allow_writes=True)
    registry = Registry.load(service) if service is not None else Registry(None)  # type: ignore[arg-type]
    svc: Any = service

    app = FastAPI(
        title="Open_Analysis API",
        version=oa_version(),
        description="Local REST API of Open_Analysis (interfaces.md section 7).",
    )

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.errors)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        infos = []
        for e in exc.errors():
            loc = [str(p) for p in e.get("loc", ())]
            infos.append(
                ErrorInfo(
                    code="SETUP_INVALID",
                    message=str(e.get("msg", "invalid request")),
                    path="/" + "/".join(loc[1:] or loc),
                    hint="See the OpenAPI schema for the request shape.",
                    details={"type": str(e.get("type", "")), "where": loc[0] if loc else ""},
                )
            )
        return _error_response(tuple(infos) or (ErrorInfo(code="USAGE", message="bad request"),))

    def auth(request: Request) -> None:
        got = request.headers.get("authorization", "")
        ok = got.startswith("Bearer ") and secrets.compare_digest(got[7:], token)
        if token and not ok:
            raise ApiError(
                (
                    ErrorInfo(
                        code="PERMISSION_DENIED",
                        message="Missing or wrong bearer token.",
                        hint="Send `Authorization: Bearer <token>` (printed by `oa serve`).",
                    ),
                )
            )

    def run(name: str, args: dict[str, Any]) -> Any:
        """Dispatch a catalog command; return its data or raise ApiError."""
        if registry is None:  # pragma: no cover - schema-only app
            raise ServiceError("INTERNAL", "no service")
        env = registry.dispatch(name, args, call_ctx)
        if not env.ok:
            raise ApiError(env.errors)
        return env.data

    r = APIRouter(prefix=API, dependencies=[Depends(auth)], responses=ERRORS)

    # -- meta --------------------

    @r.get("/health", response_model=Health)
    def health() -> Health:
        from oa_jobs import doctor

        tools = doctor().get("tools", {})
        return Health(
            version=oa_version(),
            tools=tuple(
                ToolStatus(name=n, available=bool(t.get("ok")), version=t.get("version"))
                for n, t in sorted(tools.items())
            ),
        )

    @r.get("/commands", response_model=list[dict[str, Any]])
    def commands() -> list[dict[str, Any]]:
        assert registry is not None
        return [s.model_dump(mode="json") for s in registry.specs()]

    @r.post("/commands/{name}", response_model=Envelope[Any])
    def command(name: str, body: CommandCall) -> JSONResponse:
        assert registry is not None
        env = registry.dispatch(name, body.args, call_ctx)
        return JSONResponse(env.model_dump(mode="json", by_alias=True))

    @r.get("/schemas/{schema_id:path}", response_model=dict[str, Any])
    def schema(schema_id: str) -> dict[str, Any]:
        out = run("meta.schema", {"id": schema_id})
        assert out.schema_doc is not None
        doc: dict[str, Any] = out.schema_doc
        return doc

    @r.get("/analyses", response_model=list[AnalysisInfo])
    def analyses() -> Any:
        return svc.analyses()

    @r.get("/rules", response_model=list[RuleInfo])
    def rules() -> Any:
        return svc.rules()

    # -- projects --------------------

    def summary_of(h: Any) -> ProjectSummary:
        return ProjectSummary(
            id=h.project_id,
            name=h.name,
            board_hash=h.board_hash,
            diagnostics=h.diagnostics.top,
            ephemeral=h.ephemeral,
            project_path=h.project_path,
            board_path=h.board_path,
            source_tool=h.source_tool,
        )

    @r.get("/projects", response_model=list[ProjectSummary])
    def projects() -> list[ProjectSummary]:
        return [summary_of(h) for h in run("project.list", {}).projects]

    @r.post("/projects/open", response_model=ProjectSummary)
    def open_project(body: OpenBody) -> ProjectSummary:
        return summary_of(run("board.open", body.model_dump()))

    @r.post("/projects/{pid}/reload", response_model=ProjectSummary)
    def reload(pid: str) -> ProjectSummary:
        return summary_of(run("project.reload", {"project_id": pid}))

    @r.get("/projects/{pid}/board", response_model=BoardSummary)
    def board(pid: str) -> Any:
        return run("board.summary", {"project_id": pid})

    @r.get("/projects/{pid}/board/full", response_model=dict[str, Any])
    def board_full(pid: str) -> Response:
        """Additive convenience: the whole board model JSON (same as MCP `oa://projects/{pid}/board`)."""
        run("board.summary", {"project_id": pid})
        text = dumps_board(svc.project(pid).board)
        return Response(text, media_type="application/json")

    @r.get("/projects/{pid}/board/stackup", response_model=Stackup | StackupView)
    def stackup(pid: str, frequency: str | None = None) -> Any:
        if frequency is not None:
            return run("board.stackup", {"project_id": pid, "frequency": frequency})
        run("board.summary", {"project_id": pid})
        return svc.project(pid).board.stackup

    @r.get("/projects/{pid}/board/nets", response_model=list[RestNetInfo])
    def nets(
        pid: str,
        response: Response,
        query: str | None = None,
        kind: str | None = None,
        net_class: str | None = None,
        layer: str | None = None,
        high_speed_only: bool = False,
        offset: int = Query(0, ge=0),
        limit: int = Query(500, ge=1, le=500),
    ) -> list[RestNetInfo]:
        args: dict[str, Any] = {
            "project_id": pid,
            "query": query,
            "kind": kind,
            "net_class": net_class,
            "layer": layer,
            "high_speed_only": high_speed_only,
            "page": {"offset": offset, "limit": limit},
        }
        page = run("board.nets", {k: v for k, v in args.items() if v is not None})
        response.headers["X-Total-Count"] = str(page.total)
        if page.next_offset is not None:
            response.headers["X-Next-Offset"] = str(page.next_offset)
        out = []
        for n in page.items:
            d = n.model_dump()
            d["length"] = d.pop("length_m")
            d.pop("name")
            out.append(RestNetInfo.model_validate(d))
        return out

    @r.get("/projects/{pid}/board/nets/{net}", response_model=NetDetail)
    def net(pid: str, net: str) -> Any:
        return run("board.net", {"project_id": pid, "net": net})

    def _layer(pid: str, layer: str) -> Any:
        sess = svc.project(pid)
        geom = sess.geometry()
        if layer not in geom.layer_ids():
            raise ApiError(
                (
                    ErrorInfo(
                        code="NOT_FOUND",
                        message=f"No copper layer {layer!r}.",
                        path="/layer",
                        hint="Layers: " + ", ".join(geom.layer_ids()) + ".",
                    ),
                )
            )
        return geom

    @r.get("/projects/{pid}/board/layers/{layer}/geometry")
    def layer_geometry(pid: str, layer: str, nets: str | None = None, lod: int = 0) -> Response:
        geom = _layer(pid, layer)
        only = [n for n in nets.split(",") if n] if nets else None
        return _arrow(arrow.polygons_ipc(dict(geom.copper_by_net(layer)), layer, only))

    @r.get("/projects/{pid}/board/layers/{layer}/tracks")
    def layer_tracks(pid: str, layer: str) -> Response:
        _layer(pid, layer)
        return _arrow(arrow.tracks_ipc(svc.project(pid).board, layer))

    @r.get("/projects/{pid}/board/drills")
    def drills(pid: str) -> Response:
        run("board.summary", {"project_id": pid})
        return _arrow(arrow.drills_ipc(svc.project(pid).geometry().drills()))

    @r.get("/projects/{pid}/board/items/{item_id:path}", response_model=dict[str, Any])
    def item(pid: str, item_id: str) -> Any:
        run("board.summary", {"project_id": pid})
        b = svc.project(pid).board
        for kind, seq in (
            ("track", b.tracks),
            ("via", b.vias),
            ("pad", b.pads),
            ("zone", b.zones),
            ("shape", b.shapes),
        ):
            for it in seq:
                if it.id == item_id:
                    doc = it.model_dump(mode="json")
                    return {"kind": kind, "source": doc.get("source"), "item": doc}
        raise ApiError(
            (
                ErrorInfo(
                    code="NOT_FOUND",
                    message=f"No board item {item_id!r}.",
                    path="/item_id",
                    hint="Item ids come from violations and net details.",
                ),
            )
        )

    @r.get("/projects/{pid}/board/structure3d")
    def structure3d(pid: str, bbox: str | None = None) -> Response:
        run("board.summary", {"project_id": pid})
        services = svc.registry.services()
        if not services.has("structure3d_glb"):
            raise ApiError(
                (
                    ErrorInfo(
                        code="ANALYSIS_UNAVAILABLE",
                        message="No 3D structure exporter is installed.",
                        hint="Install oa-mesh with the OCC extra (WP-206); it registers "
                        "the 'structure3d_glb' service.",
                    ),
                )
            )
        box = tuple(float(v) for v in bbox.split(",")) if bbox else None
        glb: bytes = services.get("structure3d_glb").glb(svc.project(pid).board, box)
        return Response(glb, media_type="model/gltf-binary")

    # -- setups --------------------

    @r.get("/projects/{pid}/setups", response_model=SetupList)
    def setups(pid: str) -> Any:
        return run("setups.list", {"project_id": pid})

    @r.get("/projects/{pid}/setups/{name}", response_model=dict[str, Any])
    def setup_get(pid: str, name: str) -> Any:
        return run("setups.get", {"project_id": pid, "name": name}).model_dump(
            mode="json", by_alias=True
        )

    @r.put("/projects/{pid}/setups/{name}", response_model=dict[str, Any])
    def setup_put(pid: str, name: str, body: dict[str, Any]) -> Any:
        if body.get("name", name) != name:
            raise ApiError(
                (
                    ErrorInfo(
                        code="USAGE",
                        message="Body name differs from the URL name.",
                        path="/name",
                        hint="Use the same name in the URL and the body.",
                    ),
                )
            )
        args = {
            "project_id": pid,
            "name": name,
            "analysis": body.get("analysis", ""),
            "config": body.get("config", {}),
            "description": body.get("description", ""),
            "ports": body.get("ports", []),
            "confirm": True,
        }
        return run("setups.put", args).model_dump(mode="json")

    @r.delete("/projects/{pid}/setups/{name}", status_code=204)
    def setup_delete(pid: str, name: str) -> Response:
        sess = svc.project(pid)
        sess.setup(name)  # NOT_FOUND if absent
        if sess.setups_dir is None:
            sess._memory_setups.pop(name, None)
        else:
            for ext in (".yaml", ".yml"):
                p = sess.setups_dir / f"{name}{ext}"
                if p.is_file():
                    p.unlink()
        return Response(status_code=204)

    @r.post("/projects/{pid}/setups/{name}/validate", response_model=list[dict[str, Any]])
    def setup_validate(pid: str, name: str) -> Any:
        out: SetupValidation = run("setups.validate", {"project_id": pid, "name": name})
        return [d.model_dump(mode="json") for d in out.diagnostics]

    @r.post("/projects/{pid}/impedance/xs", response_model=XsImpedance)
    def impedance_xs(pid: str, body: XsBody) -> Any:
        args = body.model_dump(exclude_none=True)
        if "sample" in args:
            args["sample"] = {"project_id": pid, **args["sample"]}
        return run("impedance.xs", args)

    # -- jobs --------------------

    @r.post("/projects/{pid}/jobs", response_model=RestJobStatus)
    def create_job(pid: str, body: JobBody) -> RestJobStatus:
        args = body.model_dump(exclude_none=True)
        if body.dry_run:
            raise ApiError(
                (
                    ErrorInfo(
                        code="USAGE",
                        message="dry_run is a catalog option; use POST /commands/analysis.run.",
                        path="/dry_run",
                    ),
                )
            )
        args.pop("dry_run", None)
        out = run("analysis.run", {"project_id": pid, **args})
        assert out.job is not None and out.job.status is not None
        return _status(out.job.status)

    @r.get("/projects/{pid}/jobs", response_model=list[RestJobStatus])
    def list_jobs(pid: str, state: str | None = None) -> list[RestJobStatus]:
        args: dict[str, Any] = {"project_id": pid, "page": {"limit": 500}}
        if state:
            args["state"] = state
        return [_status(j) for j in run("jobs.list", args).items]

    @r.get("/jobs/{jid}", response_model=RestJobStatus)
    def job(jid: str) -> RestJobStatus:
        return _status(run("jobs.status", {"job_id": jid}).status)

    @r.post("/jobs/{jid}/cancel", response_model=RestJobStatus)
    def cancel(jid: str) -> RestJobStatus:
        return _status(run("jobs.cancel", {"job_id": jid}))

    # -- results --------------------

    @r.get("/results/{rid}", response_model=dict[str, Any])
    def result(rid: str) -> Any:
        run("results.summary", {"result_id": rid})  # NOT_FOUND mapping
        return svc.result_manifest(rid)

    @r.get("/results/{rid}/summary", response_model=BundleSummary)
    def result_summary(rid: str) -> Any:
        return run("results.summary", {"result_id": rid})

    @r.get("/results/{rid}/violations", response_model=ViolationPage)
    def violations(
        rid: str,
        item_id: str | None = None,
        severity_min: str = "info",
        rule_id: str | None = None,
        net: str | None = None,
        layer: str | None = None,
        include_waived: bool = False,
        fields: str = "brief",
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=500),
    ) -> Any:
        args: dict[str, Any] = {
            "result_id": rid,
            "item_id": item_id,
            "severity_min": severity_min,
            "rule_id": rule_id,
            "net": net,
            "layer": layer,
            "include_waived": include_waived,
            "fields": fields,
            "page": {"offset": offset, "limit": limit},
        }
        return run("results.violations", {k: v for k, v in args.items() if v is not None})

    @r.get("/results/{rid}/violations/{fp}", response_model=ViolationExplanation)
    def explain(rid: str, fp: str, item_id: str | None = None) -> Any:
        args: dict[str, Any] = {"result_id": rid, "fingerprint": fp}
        if item_id:
            args["item_id"] = item_id
        return run("results.explain", args)

    @r.get("/results/{rid}/items/{item}/arrays/{name:path}")
    def array(rid: str, item: str, name: str) -> Response:
        try:
            data = svc.result_array(rid, item, name)
        except Exception as exc:
            from oa_service.errors import map_exception

            raise ApiError(map_exception(exc)) from exc
        return Response(data, media_type="application/x-npy")

    @r.get("/results/{rid}/items/{item}/table")
    def table(
        rid: str,
        item: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(1000, ge=1, le=100000),
        sort: str | None = None,
    ) -> Response:
        try:
            cols, rows = svc.result_table(rid, item)
        except Exception as exc:
            from oa_service.errors import map_exception

            raise ApiError(map_exception(exc)) from exc
        if sort:
            key = sort.lstrip("-")
            if key not in cols:
                raise ApiError(
                    (
                        ErrorInfo(
                            code="USAGE",
                            message=f"No column {key!r}.",
                            path="/sort",
                            hint="Columns: " + ", ".join(cols) + ".",
                        ),
                    )
                )
            i = cols.index(key)
            rows = sorted(rows, key=lambda r_: (r_[i] is None, r_[i]), reverse=sort.startswith("-"))
        resp = _arrow(arrow.rows_ipc(cols, rows[offset : offset + limit]))
        resp.headers["X-Total-Count"] = str(len(rows))
        return resp

    @r.get("/results/{rid}/export/{fmt}")
    def export(rid: str, fmt: str, item_id: str | None = None) -> FileResponse:
        args: dict[str, Any] = {"result_id": rid, "format": fmt, "overwrite": True}
        if item_id:
            args["item_id"] = item_id
        info = run("results.export", args)
        return FileResponse(info.path, media_type=info.media_type, filename=Path(info.path).name)

    app.include_router(r)

    # -- events (WS) --------------------

    @app.websocket(API + "/events")
    async def events(ws: WebSocket, token_q: str = Query("", alias="token")) -> None:
        if token and not secrets.compare_digest(token_q, token):
            await ws.close(code=4401)
            return
        await ws.accept()
        subs: dict[str, int] = {}
        projects_seen: dict[str, str] = {}

        async def reader() -> None:
            while True:
                msg = await ws.receive_json()
                for jid in msg.get("subscribe", []):
                    subs.setdefault(str(jid), 0)

        async def pusher() -> None:
            while True:
                for jid, seq in list(subs.items()):
                    try:
                        evs = await anyio.to_thread.run_sync(svc.events, jid, seq, 200)
                    except Exception as exc:
                        from oa_service.errors import map_exception

                        info = map_exception(exc)[0]
                        await ws.send_json(
                            {"type": "error", "job_id": jid, "error": info.model_dump(mode="json")}
                        )
                        subs.pop(jid, None)
                        continue
                    for e in evs:
                        await ws.send_json({"job_id": jid, **e.model_dump(mode="json")})
                        subs[jid] = e.seq
                for h in svc.projects():
                    if projects_seen.setdefault(h.project_id, h.board_hash) != h.board_hash:
                        projects_seen[h.project_id] = h.board_hash
                        await ws.send_json(
                            {
                                "type": "project_changed",
                                "project_id": h.project_id,
                                "board_hash": h.board_hash,
                            }
                        )
                await asyncio.sleep(0.05)

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(reader)
                tg.start_soon(pusher)
        except (WebSocketDisconnect, BaseExceptionGroup):
            pass

    if watch and svc is not None:
        from oa_server.watch import ProjectWatcher

        watcher = ProjectWatcher(svc)

        @app.on_event("startup")
        def _start() -> None:  # pragma: no cover - thread lifecycle
            watcher.start()

        @app.on_event("shutdown")
        def _stop() -> None:  # pragma: no cover
            watcher.stop()

    dist = web_dist
    if dist is not None and dist.is_dir():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")

    app.state.registry = registry
    return app
