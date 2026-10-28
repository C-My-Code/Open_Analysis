# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The built-in command catalog (ai-integration.md section 2.4).

Each handler is a few lines over :class:`oa_service.service.OAService` and
:mod:`oa_service.results_view`; there is no transport logic here. Commands marked as added by
WP-210 (``propose.*``) arrive through the ``open_analysis.commands`` entry point.

Example inputs use placeholders that the test-suite and ``oa describe`` document:
``$PROJECT_ID``, ``$RESULT_ID``, ``$JOB_ID``, ``$BOARD``, ``$NET``, ``$SETUP``, ``$ANALYSIS`` and
``$FINGERPRINT``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import JsonValue

from oa_framework.ingest import ReadOptions
from oa_jobs.models import Budget
from oa_model._base import OAModel
from oa_model.setup import AnalysisSetup
from oa_service import results_view
from oa_service.commands import (
    CallContext,
    CommandDef,
    CommandSpec,
    Outcome,
    SideEffect,
    require_write,
)
from oa_service.errors import CODE_EXIT, ErrorInfo, ServiceError, jdict
from oa_service.models import (
    BoardOpenIn,
    BoardSummary,
    BundleSummary,
    CommandSpecView,
    DescribeIn,
    Description,
    DoctorIn,
    DoctorOut,
    ErcRunIn,
    ExplainIn,
    ExportIn,
    ExportInfo,
    JobBrief,
    JobIn,
    JobsListIn,
    JobStatusIn,
    JobStatusOut,
    NetDetail,
    NetInfo,
    NetQuery,
    NetRefIn,
    ProfileRunIn,
    ProjectHandle,
    ProjectIn,
    ProjectInitIn,
    ProjectInitOut,
    ProjectList,
    ProjectListIn,
    ResultGetIn,
    ResultGetOut,
    ResultIn,
    ResultManifest,
    RunAnalysisIn,
    RunResult,
    SchemaIn,
    SchemaOut,
    SetupList,
    SetupPutIn,
    SetupPutOut,
    SetupRefIn,
    SetupsListIn,
    SetupValidation,
    StackupIn,
    StackupView,
    ViolationExplanation,
    ViolationPage,
    ViolationQuery,
    XsImpedance,
    XsImpedanceIn,
)
from oa_service.types import Page

if TYPE_CHECKING:
    from oa_service.commands import Registry
    from oa_service.service import OAService

__all__ = ["EXIT_CODE_TEXT", "builtin_commands", "check_finished", "input_schema_id"]

EXIT_CODE_TEXT = {
    "0": "ok (warnings allowed)",
    "1": "analysis completed, but violations or failures reached the --fail-on level",
    "2": "usage, setup, validation, not found or permission error",
    "3": "missing external tool or analysis",
    "4": "internal or solver failure",
    "5": "quality gate failed",
    "6": "budget exceeded, cancelled, or --timeout reached",
    "7": "confirmation required (write without --yes), or stale base file",
}

PLACEHOLDERS = "$PROJECT_ID, $RESULT_ID, $JOB_ID, $BOARD, $NET, $SETUP, $ANALYSIS, $FINGERPRINT"


def input_schema_id(name: str) -> str:
    return f"oa.cmd.{name.replace('.', '_')}.in/1"


def _spec(
    name: str,
    mcp: str | None,
    cli: tuple[str, ...],
    title: str,
    description: str,
    effect: SideEffect,
    output: str,
    *,
    idempotent: bool = True,
    long_running: bool = False,
    examples: tuple[dict[str, JsonValue], ...] = (),
) -> CommandSpec:
    return CommandSpec(
        name=name,
        mcp_tool=mcp,
        cli=cli,
        title=title,
        description=description,
        side_effect=effect,
        idempotent=idempotent,
        long_running=long_running,
        input_model=input_schema_id(name),
        output_model=output,
        examples=examples,
    )


# ---------------------------------------------------------------- helpers


def _brief_of(svc: OAService, job_id: str) -> JobBrief:
    return svc.job(job_id).status


def _budget_warnings(requested: Budget | None, effective: Budget) -> tuple[ErrorInfo, ...]:
    if requested is None:
        return ()
    cut = [
        f
        for f in ("max_runtime_s", "max_memory_gb", "max_cpus")
        if getattr(requested, f) is not None and getattr(requested, f) != getattr(effective, f)
    ]
    if requested.allow_external and not effective.allow_external:
        cut.append("allow_external")
    if not cut:
        return ()
    return (
        ErrorInfo(
            code="BUDGET_CLAMPED",
            message="The requested budget exceeds the server ceiling and was clamped.",
            path="/budget",
            hint="Raise the ceiling on the server if more resources are intended.",
            details=jdict({"fields": cut, "effective": effective.model_dump(mode="json")}),
        ),
    )


def check_finished(svc: OAService, job_state: str, job: Any) -> None:
    """Turn a finished-but-unsuccessful job into the documented error."""
    if job_state == "failed":
        st = svc.job(job.job_id)
        err = st.error or {}
        raise ServiceError(
            str(err.get("code", "TOOL_FAILED")),
            str(err.get("message", "The job failed.")),
            path=str(err["path"]) if err.get("path") else None,
            hint=str(err["hint"]) if err.get("hint") else None,
            details={"job_id": job.job_id},
        )
    if job_state == "cancelled":
        st = svc.job(job.job_id)
        reason = st.status.cancel_reason or "user"
        code = "BUDGET_EXCEEDED" if reason.startswith("budget") else "CANCELLED"
        raise ServiceError(
            code,
            f"The job was cancelled ({reason}).",
            hint="Raise the budget or shrink the problem." if code == "BUDGET_EXCEEDED" else None,
            details={"job_id": job.job_id, "cancel_reason": reason},
        )
    if job.summary is not None and job.summary.status == "failed_quality":
        raise ServiceError(
            "QUALITY_GATE_FAILED",
            "The result failed its quality gate.",
            hint="Inspect the summary diagnostics; the numbers are not trustworthy.",
            details={"job_id": job.job_id, "result_id": job.result_id},
        )


def _run(svc: OAService, ctx: CallContext, req: RunAnalysisIn) -> Outcome:
    eff = ctx.clamp(req.budget)
    warnings = _budget_warnings(req.budget, eff)
    req = req.model_copy(update={"budget": eff})
    if req.dry_run:
        dr = svc.dry_run(req.project_id, req, ctx)
        return Outcome(
            RunResult(
                dry_run=True, valid=dr.valid, diagnostics=dr.diagnostics, estimate=dr.estimate
            ),
            warnings,
        )
    job = svc.submit(req.project_id, req, ctx)
    if req.wait_s > 0:
        check_finished(svc, job.state, job)
    return Outcome(
        RunResult(dry_run=False, valid=True, diagnostics=(), estimate=job.estimate, job=job),
        warnings,
    )


def _stored_setup_kind(svc: OAService, project_id: str, setup: str, expect: str) -> None:
    got = svc.project(project_id).setup(setup).analysis
    if got != expect:
        raise ServiceError(
            "USAGE",
            f"Setup {setup!r} is a {got!r} setup, not {expect!r}.",
            path="/setup",
            hint="Use analysis.run for other analyses.",
        )


# ---------------------------------------------------------------- handlers


def _open(svc: OAService, a: BoardOpenIn, ctx: CallContext) -> ProjectHandle:
    path = ctx.check_path(a.path, must_exist=True)
    opts = ReadOptions(refill_zones=a.refill_zones)
    physics = ctx.check_path(a.physics, must_exist=True) if a.physics else None
    intent = ctx.check_path(a.intent, must_exist=True) if a.intent else None
    return svc.open(path, read_options=opts, physics=physics, intent=intent)


def _projects(svc: OAService, a: ProjectListIn, ctx: CallContext) -> ProjectList:
    return ProjectList(projects=tuple(svc.projects()))


def _reload(svc: OAService, a: ProjectIn, ctx: CallContext) -> ProjectHandle:
    return svc.reload(a.project_id)


def _summary(svc: OAService, a: ProjectIn, ctx: CallContext) -> BoardSummary:
    return svc.project(a.project_id).summary()


def _nets(svc: OAService, a: NetQuery, ctx: CallContext) -> Page[NetInfo]:
    return svc.project(a.project_id).nets(a)


def _net(svc: OAService, a: NetRefIn, ctx: CallContext) -> NetDetail:
    return svc.project(a.project_id).net(a.net)


def _stackup(svc: OAService, a: StackupIn, ctx: CallContext) -> StackupView:
    return svc.project(a.project_id).stackup(a.frequency)


def _xs(svc: OAService, a: XsImpedanceIn, ctx: CallContext) -> XsImpedance:
    return svc.xs_impedance(a, ctx)


def _erc(svc: OAService, a: ErcRunIn, ctx: CallContext) -> Outcome:
    if a.setup is not None:
        if a.rules is not None or a.nets is not None:
            raise ServiceError("USAGE", "Give `setup` or `rules`/`nets`, not both.", path="/setup")
        _stored_setup_kind(svc, a.project_id, a.setup, "erc")
        req = RunAnalysisIn(project_id=a.project_id, setup=a.setup)
    else:
        cfg: dict[str, JsonValue] = {}
        if a.rules is not None:
            cfg["rules"] = a.rules
        if a.nets is not None:
            cfg["nets"] = a.nets
        req = RunAnalysisIn(project_id=a.project_id, analysis="erc", config=cfg)
    req = req.model_copy(
        update={
            "dry_run": a.dry_run,
            "budget": a.budget,
            "wait_s": a.wait_s,
            "executor": a.executor,
        }
    )
    return _run(svc, ctx, req)


def _profile(svc: OAService, a: ProfileRunIn, ctx: CallContext) -> Outcome:
    cfg: dict[str, JsonValue] = {"nets": a.nets}
    if a.method is not None:
        cfg["method"] = a.method
    if a.ds is not None:
        cfg["ds"] = a.ds
    if a.tolerance is not None:
        cfg["tolerance"] = a.tolerance
    req = RunAnalysisIn(
        project_id=a.project_id,
        analysis="impedance.profile",
        config=cfg,
        dry_run=a.dry_run,
        budget=a.budget,
        wait_s=a.wait_s,
        executor=a.executor,
    )
    return _run(svc, ctx, req)


def _analysis_run(svc: OAService, a: RunAnalysisIn, ctx: CallContext) -> Outcome:
    return _run(svc, ctx, a)


def _setups(svc: OAService, a: SetupsListIn, ctx: CallContext) -> SetupList:
    return SetupList(setups=svc.project(a.project_id).setups())


def _setup_get(svc: OAService, a: SetupRefIn, ctx: CallContext) -> AnalysisSetup:
    return svc.project(a.project_id).setup(a.name)


def _setup_validate(svc: OAService, a: SetupRefIn, ctx: CallContext) -> SetupValidation:
    return svc.validate_setup(a.project_id, a.name)


def _setup_put(svc: OAService, a: SetupPutIn, ctx: CallContext) -> SetupPutOut:
    setup = AnalysisSetup.model_validate(
        {
            "schema": "oa-setup/1",
            "name": a.name,
            "analysis": a.analysis,
            "description": a.description,
            "config": a.config,
            "ports": list(a.ports),
        }
    )
    require_write(ctx, a.confirm, what=f"setup {a.name!r}", details={"name": a.name})
    return svc.put_setup(a.project_id, setup)


def _jobs(svc: OAService, a: JobsListIn, ctx: CallContext) -> Page[JobBrief]:
    from oa_service.types import paginate

    rows = list(svc.jobs(a.project_id, state=a.state, limit=500))
    rows.sort(key=lambda j: (_neg(j.created_utc), j.job_id))
    return Page[JobBrief](**paginate(rows, a.page, "created_utc desc, job_id"))


def _neg(s: str) -> tuple[int, ...]:
    return tuple(-ord(c) for c in s)


def _job_status(svc: OAService, a: JobStatusIn, ctx: CallContext) -> JobStatusOut:
    return svc.job(a.job_id, wait_s=a.wait_s, events_after_seq=a.events_after_seq)


def _cancel(svc: OAService, a: JobIn, ctx: CallContext) -> JobBrief:
    return svc.cancel(a.job_id)


def _result_get(svc: OAService, a: ResultGetIn, ctx: CallContext) -> ResultGetOut:
    return results_view.get_result(svc.result(a.result_id), a)


def _result_summary(svc: OAService, a: ResultIn, ctx: CallContext) -> BundleSummary:
    return results_view.bundle_summary(svc.result(a.result_id))


def _manifest(svc: OAService, a: ResultIn, ctx: CallContext) -> ResultManifest:
    return results_view.manifest(svc.result(a.result_id))


def _violations(svc: OAService, a: ViolationQuery, ctx: CallContext) -> ViolationPage:
    return results_view.violations_page(svc.result(a.result_id), a)


def _explain(svc: OAService, a: ExplainIn, ctx: CallContext) -> ViolationExplanation:
    rules = {r.id: r for r in svc.rules()}
    return results_view.explain_violation(svc.result(a.result_id), a, rules)


def _doctor(svc: OAService, a: DoctorIn, ctx: CallContext) -> DoctorOut:
    from oa_service.projectops import doctor_report

    return DoctorOut(report=json.loads(json.dumps(doctor_report(), default=str)))


def _init(svc: OAService, a: ProjectInitIn, ctx: CallContext) -> ProjectInitOut:
    from oa_service.projectops import init_project

    directory = ctx.check_path(a.dir)
    require_write(ctx, a.confirm, what=f"project files in {directory}")
    created = init_project(directory, board=a.board, name=a.name)
    return ProjectInitOut(
        project_path=str(directory / "oa-project.yaml"), created=tuple(str(p) for p in created)
    )


def _export(svc: OAService, a: ExportIn, ctx: CallContext) -> ExportInfo:
    return svc.export(a, ctx)


# ---------------------------------------------------------------- catalog


def builtin_commands(registry: Registry | None = None) -> list[CommandDef]:
    """All built-in command definitions (``registry`` binds the meta commands)."""
    P = "$PROJECT_ID"
    R = "$RESULT_ID"
    defs: list[CommandDef] = []

    def add(
        spec: CommandSpec,
        inp: type[OAModel],
        out: type[OAModel],
        fn: Callable[..., Any],
        cli_args: tuple[str, ...] = (),
    ) -> None:
        defs.append(CommandDef(spec, inp, out, fn, cli_args))

    def describe(svc: OAService, a: DescribeIn, ctx: CallContext) -> Description:
        assert registry is not None
        specs = [d.spec for d in registry.definitions()]
        if a.command is not None:
            specs = [registry.definition(a.command).spec]
        return Description(
            commands=tuple(CommandSpecView(**s.model_dump()) for s in specs),
            analyses=svc.analyses() if a.analyses else None,
            rules=svc.rules() if a.rules else None,
            exit_codes=dict(EXIT_CODE_TEXT),
            error_codes=dict(sorted(CODE_EXIT.items())),
        )

    def schema(svc: OAService, a: SchemaIn, ctx: CallContext) -> SchemaOut:
        from oa_service.schemas import schema_entries, schema_for

        assert registry is not None
        if a.id is None:
            return SchemaOut(entries=schema_entries(registry), schema_doc=None)
        return SchemaOut(entries=(), schema_doc=schema_for(registry, a.id))

    add(
        _spec(
            "meta.describe",
            "describe",
            ("describe",),
            "Describe the command catalog",
            "List every command with its schema ids, side effect and examples; with `command` "
            "describe one. Set `analyses`/`rules` to also list installed analyses and rules. "
            "Start here. Read-only, instant. Example inputs use placeholders: "
            + PLACEHOLDERS
            + ".",
            "read",
            "oa.description/1",
            examples=({}, {"command": "erc.run"}),
        ),
        DescribeIn,
        Description,
        describe,
        ("command",),
    )
    add(
        _spec(
            "meta.schema",
            None,
            ("schema",),
            "Fetch a JSON Schema",
            "Without `id` list all published schema ids; with `id` return that JSON Schema "
            "(command inputs 'oa.cmd.<name>.in/1', outputs 'oa.<name>/1', 'oa-envelope/1'). "
            "Read-only, instant.",
            "read",
            "oa.schema_out/1",
            examples=({}, {"id": "oa-envelope/1"}),
        ),
        SchemaIn,
        SchemaOut,
        schema,
        ("id",),
    )
    add(
        _spec(
            "meta.doctor",
            "doctor",
            ("doctor",),
            "Environment report",
            "Offline report of the platform, external tools (openEMS, Palace, KiCad), KiCad and "
            "gmsh versions and whether oa-mcp / oa-assistant are installed. Runs in under 3 s. "
            "Read-only.",
            "read",
            "oa.doctor_out/1",
            examples=({},),
        ),
        DoctorIn,
        DoctorOut,
        _doctor,
    )
    add(
        _spec(
            "project.init",
            None,
            ("init",),
            "Create a project",
            "Write oa-project.yaml plus physics and intent templates for a directory that holds "
            "one board file. A WRITE (needs confirm=true); never overwrites existing files.",
            "write",
            "oa.project_init_out/1",
            idempotent=False,
            examples=({"dir": "$BOARD_DIR"},),
        ),
        ProjectInitIn,
        ProjectInitOut,
        _init,
        ("dir",),
    )
    add(
        _spec(
            "board.open",
            "open_board",
            ("open",),
            "Open a board or project",
            "Open a project directory (oa-project.yaml) or a bare board file (.kicad_pcb, IPC-2581 "
            ".xml, .PcbDoc, .oab, .oab.json) and return its project_id plus a board "
            "summary. A bare "
            "board file opens an ephemeral project: nothing is written next to it. Cost: reads and "
            "parses the file (seconds for large boards). Run this first; every other command needs "
            "the project_id. Lengths in the summary are metres.",
            "compute",
            "oa.project_handle/1",
            long_running=False,
            examples=({"path": "$BOARD"},),
        ),
        BoardOpenIn,
        ProjectHandle,
        _open,
        ("path",),
    )
    add(
        _spec(
            "project.list",
            "list_projects",
            ("projects",),
            "List open projects",
            "List the projects opened in this session. Read-only, instant.",
            "read",
            "oa.project_list/1",
            examples=({},),
        ),
        ProjectListIn,
        ProjectList,
        _projects,
    )
    add(
        _spec(
            "project.reload",
            "reload_project",
            ("reload",),
            "Re-read a project from disk",
            "Re-ingest the board, physics and intent files of an open project (after editing them "
            "in KiCad). The project_id stays the same. Cost: same as board.open.",
            "compute",
            "oa.project_handle/1",
            examples=({"project_id": P},),
        ),
        ProjectIn,
        ProjectHandle,
        _reload,
        ("project_id",),
    )
    add(
        _spec(
            "board.summary",
            "board_summary",
            ("board", "summary"),
            "Board summary",
            "Size, layer count, stackup thickness, net and via counts, net classes, zone fill "
            "state, design intent and ingest diagnostics. Read this before listing nets. "
            "Read-only. Lengths are metres.",
            "read",
            "oa.board_summary/1",
            examples=({"project_id": P},),
        ),
        ProjectIn,
        BoardSummary,
        _summary,
        ("project_id",),
    )
    add(
        _spec(
            "board.nets",
            "list_nets",
            ("board", "nets"),
            "List nets",
            "Paged list of nets sorted by name, with kind, class, routed length [m], layers, via "
            "count and impedance target [ohm]. Filter by glob, kind, class, layer or "
            "high_speed_only. Read-only. Sort: name.",
            "read",
            "oa.net_page/1",
            examples=({"project_id": P}, {"project_id": P, "query": "*", "page": {"limit": 10}}),
        ),
        NetQuery,
        Page[NetInfo],
        _nets,
        ("project_id",),
    )
    add(
        _spec(
            "board.net",
            "get_net",
            ("board", "net"),
            "Net detail",
            "One net: routed length per layer [m], distinct widths, up to 50 vias, up to 100 pads "
            "and the resolved intent. An unknown name returns NOT_FOUND with close matches. "
            "Read-only.",
            "read",
            "oa.net_detail/1",
            examples=({"project_id": P, "net": "$NET"},),
        ),
        NetRefIn,
        NetDetail,
        _net,
        ("project_id", "net"),
    )
    add(
        _spec(
            "board.stackup",
            "stackup",
            ("board", "stackup"),
            "Stackup",
            "Layers top first with z [m], thickness [m], material, and Dk/Df evaluated at "
            "`frequency` [Hz] (default 1 GHz). Read-only.",
            "read",
            "oa.stackup_view/1",
            examples=({"project_id": P, "frequency": "2GHz"},),
        ),
        StackupIn,
        StackupView,
        _stackup,
        ("project_id",),
    )
    add(
        _spec(
            "impedance.xs",
            "compute_xsection_impedance",
            ("impedance", "xs"),
            "Cross-section impedance",
            "Z0/Zdiff, effective permittivity, delay [s/m] and loss [dB/m] of ONE cross-section, "
            "given as a geometry `sample` on the board, an inline `xsection`, or a closed-form "
            "`quick` topology. Runs synchronously. Needs the impedance analysis package; "
            "otherwise returns ANALYSIS_UNAVAILABLE. Check accuracy_class before quoting numbers.",
            "compute",
            "oa.xs_impedance/1",
            examples=(
                {
                    "quick": {
                        "topology": "microstrip",
                        "w": "5mil",
                        "h": "4mil",
                        "t": "1.4mil",
                        "er": 4.2,
                    }
                },
            ),
        ),
        XsImpedanceIn,
        XsImpedance,
        _xs,
    )
    add(
        _spec(
            "impedance.profile",
            "impedance_profile",
            ("impedance", "profile"),
            "Impedance profile",
            "Impedance along selected nets (analysis impedance.profile). Starts a job; wait_s>0 "
            "waits for it. dry_run=true returns the estimate only. Subject to the compute budget. "
            "Result: results.summary / results.get.",
            "compute",
            "oa.run_result/1",
            long_running=True,
            idempotent=False,
            examples=({"project_id": P, "nets": {"names": ["SIG*"]}, "dry_run": True},),
        ),
        ProfileRunIn,
        RunResult,
        _profile,
        ("project_id",),
    )
    add(
        _spec(
            "erc.run",
            "run_erc",
            ("erc",),
            "Run the electrical rule check",
            "Run ERC rules (return paths, impedance, diff pairs, vias...) on the board. Starts a "
            "job; wait_s waits for it. Then use results.summary and results.violations. "
            "dry_run=true estimates only. Violations carry measured/limit/unit and remediation.",
            "compute",
            "oa.run_result/1",
            long_running=True,
            idempotent=False,
            examples=({"project_id": P, "dry_run": True},),
        ),
        ErcRunIn,
        RunResult,
        _erc,
        ("project_id",),
    )
    add(
        _spec(
            "analysis.run",
            "run_analysis",
            ("run",),
            "Run any analysis",
            "Run a registered analysis from a stored `setup` or an inline `analysis`+`config`. "
            "List installed analyses with describe(analyses=true). dry_run=true validates and "
            "estimates without running. Refused with BUDGET_EXCEEDED when the estimate exceeds "
            "the effective budget. Returns a job; poll jobs.status.",
            "compute",
            "oa.run_result/1",
            long_running=True,
            idempotent=False,
            examples=({"project_id": P, "analysis": "$ANALYSIS", "config": {}, "dry_run": True},),
        ),
        RunAnalysisIn,
        RunResult,
        _analysis_run,
        ("project_id",),
    )
    add(
        _spec(
            "setups.list",
            "list_setups",
            ("setups", "list"),
            "List stored setups",
            "Stored analysis setups of a project (oa/setups/*.yaml), sorted by name. Read-only.",
            "read",
            "oa.setup_list/1",
            examples=({"project_id": P},),
        ),
        SetupsListIn,
        SetupList,
        _setups,
        ("project_id",),
    )
    add(
        _spec(
            "setups.get",
            "get_setup",
            ("setups", "show"),
            "Read a stored setup",
            "Return one stored setup document (oa-setup/1). Read-only.",
            "read",
            "oa-setup/1",
            examples=({"project_id": P, "name": "$SETUP"},),
        ),
        SetupRefIn,
        AnalysisSetup,
        _setup_get,
        ("project_id", "name"),
    )
    add(
        _spec(
            "setups.validate",
            "validate_setup",
            ("setups", "validate"),
            "Validate a stored setup",
            "Validate a stored setup against its analysis (config schema and analysis checks) "
            "without running it. Read-only.",
            "read",
            "oa.setup_validation/1",
            examples=({"project_id": P, "name": "$SETUP"},),
        ),
        SetupRefIn,
        SetupValidation,
        _setup_validate,
        ("project_id", "name"),
    )
    add(
        _spec(
            "setups.put",
            None,
            ("setups", "put"),
            "Write a setup",
            "Create or replace oa/setups/NAME.yaml. A WRITE: needs the process to allow writes "
            "and confirm=true (CLI --yes); otherwise CONFIRMATION_REQUIRED. The replaced file is "
            "backed up under .oa/backups/.",
            "write",
            "oa.setup_put_out/1",
            idempotent=True,
            examples=({"project_id": P, "name": "demo", "analysis": "erc", "config": {}},),
        ),
        SetupPutIn,
        SetupPutOut,
        _setup_put,
        ("project_id", "name"),
    )
    add(
        _spec(
            "jobs.list",
            "list_jobs",
            ("jobs", "list"),
            "List jobs",
            "Paged jobs of a project, newest first. Sort: created_utc desc, job_id. Read-only.",
            "read",
            "oa.job_page/1",
            examples=({"project_id": P},),
        ),
        JobsListIn,
        Page[JobBrief],
        _jobs,
        ("project_id",),
    )
    add(
        _spec(
            "jobs.status",
            "job_status",
            ("jobs", "status"),
            "Job status",
            "State, progress and error of a job, optionally waiting up to wait_s (max 60) and "
            "returning events after a sequence number. Includes the result summary once "
            "succeeded. Read-only.",
            "read",
            "oa.job_status/1",
            examples=({"job_id": "$JOB_ID"},),
        ),
        JobStatusIn,
        JobStatusOut,
        _job_status,
        ("job_id",),
    )
    add(
        _spec(
            "jobs.cancel",
            "cancel_job",
            ("jobs", "cancel"),
            "Cancel a job",
            "Cancel a queued or running job (external processes get a 10 s grace period). "
            "Idempotent.",
            "compute",
            "oa.job_brief/1",
            examples=({"job_id": "$JOB_ID"},),
        ),
        JobIn,
        JobBrief,
        _cancel,
        ("job_id",),
    )
    add(
        _spec(
            "results.get",
            "get_result",
            ("results", "show"),
            "Fetch a result",
            "Without item_id the bundle manifest; with item_id one item: summary, decimated "
            "series (max_points, endpoints kept), ArrayRefs for large arrays, and a row window "
            "for tables. Never inlines more than 1000 elements. Read-only.",
            "read",
            "oa.result_get/1",
            examples=({"result_id": R},),
        ),
        ResultGetIn,
        ResultGetOut,
        _result_get,
        ("result_id",),
    )
    add(
        _spec(
            "results.summary",
            "result_summary",
            ("results", "summary"),
            "Summarize a result",
            "Bounded (<= 8 KiB) summary of a result bundle: status, per-item digest and "
            "statistics, accuracy class, top diagnostics and provenance. Read this before "
            "listing violations or fetching items. Read-only.",
            "read",
            "oa.bundle_summary/1",
            examples=({"result_id": R},),
        ),
        ResultIn,
        BundleSummary,
        _result_summary,
        ("result_id",),
    )
    add(
        _spec(
            "results.violations",
            "list_violations",
            ("results", "violations"),
            "List violations",
            "Paged violations of a result. Filter by severity_min, rule glob, net glob, layer; "
            "waived are hidden unless include_waived. fields=full adds items, bbox and "
            "remediation. Sort: severity desc, rule_id, nets, x, y, fingerprint. Read-only.",
            "read",
            "oa.violation_page/1",
            examples=({"result_id": R, "page": {"limit": 20}},),
        ),
        ViolationQuery,
        ViolationPage,
        _violations,
        ("result_id",),
    )
    add(
        _spec(
            "results.explain",
            "explain_violation",
            ("results", "explain"),
            "Explain a violation",
            "Deterministic explanation (no LLM): the violation, rule rationale and references, "
            "measured vs limit margin, related violations, remediation and a ready-to-paste "
            "waiver snippet. Read-only.",
            "read",
            "oa.violation_explanation/1",
            examples=({"result_id": R, "fingerprint": "$FINGERPRINT"},),
        ),
        ExplainIn,
        ViolationExplanation,
        _explain,
        ("result_id", "fingerprint"),
    )
    add(
        _spec(
            "results.export",
            "export_report",
            ("results", "export"),
            "Export a result",
            "Write a result as html, sarif, touchstone, csv, vtu, json or md to "
            ".oa/exports/<result_id>/ or to out_path (inside the allowed roots). Existing files "
            "are not overwritten unless overwrite=true.",
            "compute",
            "oa.export_info/1",
            idempotent=True,
            examples=({"result_id": R, "format": "json"},),
        ),
        ExportIn,
        ExportInfo,
        _export,
        ("result_id",),
    )
    return defs
