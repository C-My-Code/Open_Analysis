# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later

"""In-memory ``OaServiceLike`` used by the oa-mcp tests (stand-in for ``oa_service.FakeService``).

It opens the WP-102 ``microstrip`` board (4 copper layers) or a ``.oab.json`` fixture, serves a
canned ERC result and implements the propose/apply protocol, so the whole MCP surface can be
tested without ``oa_service``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oa_mcp.protocol import JsonDict, McpCallContext, ResourceContent, ResourceNotFound

UNITLESS = {"x-oa-unitless": True}


class In(BaseModel):
    """Base of all input models: unknown fields are errors (so typos are reported)."""

    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    """Base of loosely typed outputs."""

    model_config = ConfigDict(extra="allow")


def F(desc: str, default: Any = ..., **kw: Any) -> Any:
    return Field(default, description=desc, **kw)


# --------------------------------------------------------------------------------- inputs
class DescribeIn(In):
    """Input of describe."""

    command: str | None = F("Catalog command name, or omit for all.", None)


class OpenBoardIn(In):
    """Input of open_board."""

    path: str = F("Project directory or board file path.")
    refill_zones: Literal["never", "if_unfilled"] = F("Zone refill policy.", "never")
    physics: str | None = F("Path of a physics sidecar.", None)
    intent: str | None = F("Path of an intent file.", None)


class ProjectIn(In):
    """Input of tools that take only a project."""

    project_id: str = F("Project id returned by open_board.")


class ListNetsIn(In):
    """Input of list_nets."""

    project_id: str = F("Project id returned by open_board.")
    query: str | None = F("Case-sensitive glob on the net name.", None)
    kind: str | None = F("Net kind filter.", None)
    net_class: str | None = F("Net class filter.", None)
    layer: str | None = F("Layer id filter.", None)
    high_speed_only: bool = F("Only nets tagged high speed.", False)
    offset: int = F("Index of the first item to return [1].", 0, ge=0, json_schema_extra=UNITLESS)
    limit: int = F("Maximum items to return [1].", 50, ge=1, le=500, json_schema_extra=UNITLESS)


class GetNetIn(In):
    """Input of get_net."""

    project_id: str = F("Project id returned by open_board.")
    net: str = F("Net id or name.")


class StackupIn(In):
    """Input of stackup."""

    project_id: str = F("Project id returned by open_board.")
    frequency: float | str | None = F("Evaluation frequency, SI float [Hz] or e.g. '1GHz'.", None)


class XsIn(In):
    """Input of compute_xsection_impedance."""

    sample: dict[str, Any] | None = F("Geometry sample on the board.", None)
    xsection: dict[str, Any] | None = F("Inline oa-xsection/1 document.", None)
    quick: dict[str, Any] | None = F("Quick topology parameters.", None)
    freqs: list[float | str] | None = F("Frequencies [Hz], at most 50.", None, max_length=50)
    method: Literal["auto", "closed_form", "bem", "fem"] = F("Solver method.", "auto")


class ProfileIn(In):
    """Input of impedance_profile."""

    project_id: str = F("Project id returned by open_board.")
    nets: Any = F("Net selector: names, globs, classes or intent tag.")
    method: str | None = F("Solver method.", None)
    ds: float | str | None = F("Sampling step along the net [m].", None)
    tolerance: float | None = F("Relative impedance tolerance [1].", None)
    budget: dict[str, Any] | None = F("Per-call budget, clamped to the ceiling.", None)
    wait_s: int = F("Seconds to wait for completion [s].", 20, ge=0, le=120)


class RunErcIn(In):
    """Input of run_erc."""

    project_id: str = F("Project id returned by open_board.")
    setup: str | None = F("Setup name.", None)
    rules: dict[str, Any] | None = F("Per-rule overrides keyed by rule id.", None)
    nets: Any = F("Net selector.", None)
    budget: dict[str, Any] | None = F("Per-call budget, clamped to the ceiling.", None)
    wait_s: int = F("Seconds to wait for completion [s].", 20, ge=0, le=120)


class ListViolationsIn(In):
    """Input of list_violations."""

    result_id: str = F("Result id.")
    severity_min: Literal["info", "warning", "error"] | None = F("Minimum severity.", None)
    rule_id: str | None = F("Glob on the rule id.", None)
    net: str | None = F("Glob on net names.", None)
    layer: str | None = F("Layer id.", None)
    include_waived: bool = F("Include waived violations.", False)
    offset: int = F("Index of the first item to return [1].", 0, ge=0, json_schema_extra=UNITLESS)
    limit: int = F("Maximum items to return [1].", 50, ge=1, le=500, json_schema_extra=UNITLESS)
    fields: Literal["brief", "full"] = F("Detail level.", "brief")


class ExplainIn(In):
    """Input of explain_violation."""

    result_id: str = F("Result id.")
    fingerprint: str = F("Violation fingerprint.")


class RunAnalysisIn(In):
    """Input of run_analysis."""

    project_id: str = F("Project id returned by open_board.")
    analysis: str = F("Analysis kind, e.g. pi.dc.")
    setup: str | None = F("Setup name.", None)
    config: dict[str, Any] | None = F("Inline analysis config.", None)
    ports: list[dict[str, Any]] | None = F("Ports.", None)
    dry_run: bool = F("Only estimate, do not run.", False)
    budget: dict[str, Any] | None = F("Per-call budget, clamped to the ceiling.", None)
    wait_s: int = F("Seconds to wait for completion [s].", 20, ge=0, le=120)


class JobStatusIn(In):
    """Input of job_status."""

    job_id: str = F("Job id.")
    wait_s: int = F("Seconds to wait for a state change [s].", 0, ge=0, le=60)
    events_after_seq: int | None = F(
        "Return events after this sequence number [1].", None, json_schema_extra=UNITLESS
    )


class JobIn(In):
    """Input of cancel_job."""

    job_id: str = F("Job id.")


class GetResultIn(In):
    """Input of get_result."""

    result_id: str = F("Result id.")
    item_id: str | None = F("Item id, or omit for the manifest.", None)
    select: list[str] | None = F("Fields to include.", None)
    max_points: int = F("Maximum points per series [1].", 201, ge=2, json_schema_extra=UNITLESS)
    offset: int = F("Table page start [1].", 0, ge=0, json_schema_extra=UNITLESS)
    limit: int = F("Table page size [1].", 50, ge=1, le=500, json_schema_extra=UNITLESS)


class ResultIn(In):
    """Input of result_summary."""

    result_id: str = F("Result id.")


class ExportIn(In):
    """Input of export_report."""

    result_id: str = F("Result id.")
    format: Literal["html", "sarif", "touchstone", "csv", "vtu", "json", "md"] = F("Format.")
    item_id: str | None = F("Item id.", None)
    out_path: str | None = F("Destination inside the roots.", None)
    overwrite: bool = F("Overwrite an existing file.", False)


class ProposeIn(In):
    """Input of propose_rule_changes."""

    project_id: str = F("Project id returned by open_board.")
    from_violations: dict[str, Any] | None = F("{result_id, fingerprints}.", None)
    changes: list[dict[str, Any]] | None = F("Explicit RuleChange list.", None)
    target: Literal["kicad_dru", "kicad_pro_netclasses", "auto"] = F("Target file kind.", "auto")


class ApplyIn(In):
    """Input of apply_proposal."""

    proposal_id: str = F("Proposal id.")
    confirm_token: str = F("Single-use token from the proposal.")


# -------------------------------------------------------------------------------- outputs
class DiagCounts(Out):
    """Diagnostic counts."""

    counts: dict[str, int] = F("Counts by severity [1].")


class CopperLayer(Out):
    """One copper layer."""

    id: str = F("Layer id.")
    name: str = F("Layer name.")


class BoardSummary(Out):
    """Board summary (subset)."""

    layer_count: int = F("Number of copper layers [1].", json_schema_extra=UNITLESS)
    copper_layers: list[CopperLayer] = F("Copper layers, top to bottom.")
    total_thickness_m: float | None = F("Board thickness [m].", None)
    diagnostics: DiagCounts = F("Ingest diagnostics.")
    text: str = F("One-paragraph digest.")


class ProjectHandle(Out):
    """Handle of an opened project."""

    project_id: str = F("Stable project id.")
    name: str = F("Project name.")
    project_path: str = F("Project directory.")
    board_path: str = F("Board file.")
    ephemeral: bool = F("True for a bare board file; nothing is written next to it.")
    board_hash: str = F("Content hash of the board.")
    summary: BoardSummary = F("Board summary.")
    text: str = F("One-line digest.")


class BundleSummary(Out):
    """Bundle summary (subset)."""

    result_id: str = F("Result id.")
    analysis: str = F("Analysis kind.")
    status: Literal["ok", "warning", "failed_quality", "error"] = F("Overall status.")
    text: str = F("Digest, at most 1500 characters.")
    diagnostics: dict[str, int] = F("Violation counts by severity [1].")


class ErcOut(Out):
    """Output of run_erc."""

    result_id: str = F("Result id.")
    counts: dict[str, int] = F("Counts by severity [1].")
    by_rule: list[dict[str, Any]] = F("Counts per rule.")
    summary: BundleSummary = F("Bundle summary.")


class ViolationBrief(Out):
    """Violation brief."""

    fingerprint: str = F("Stable fingerprint.")
    rule_id: str = F("Rule id.")
    severity: Literal["info", "warning", "error"] = F("Severity.")
    message: str = F("One sentence.")
    nets: list[str] = F("Nets involved.")


class ViolationPage(Out):
    """A page of violations."""

    items: list[ViolationBrief] = F("Violations on this page.")
    offset: int = F("Page start [1].", json_schema_extra=UNITLESS)
    limit: int = F("Page size [1].", json_schema_extra=UNITLESS)
    total: int = F("Total matching [1].", json_schema_extra=UNITLESS)
    next_offset: int | None = F(
        "Next page start [1], null when exhausted.", json_schema_extra=UNITLESS
    )
    sort: str = F("Effective sort key.")


class Proposal(Out):
    """Change proposal (subset)."""

    proposal_id: str = F("Proposal id.")
    files: list[dict[str, Any]] = F("File changes with unified diffs.")
    confirm_token: str = F("Single-use confirmation token.")
    text: str = F("Digest.")


class Loose(Out):
    """Loosely typed output used for tools the tests do not exercise in depth."""

    text: str = F("Digest.", "")


# --------------------------------------------------------------------------------- catalog
@dataclass(frozen=True)
class FakeSpec:
    name: str
    mcp_tool: str | None
    title: str
    description: str
    side_effect: str
    idempotent: bool
    long_running: bool
    input_model: type[BaseModel]
    output_model: type[BaseModel]


def _s(
    name: str, tool: str, side: str, i: type[BaseModel], o: type[BaseModel], *, long: bool = False
) -> FakeSpec:
    return FakeSpec(
        name=name,
        mcp_tool=tool,
        title=tool.replace("_", " ").capitalize(),
        description=f"{tool}: fake catalog command ({side}).",
        side_effect=side,
        idempotent=side == "read",
        long_running=long,
        input_model=i,
        output_model=o,
    )


SPECS: tuple[FakeSpec, ...] = tuple(
    sorted(
        (
            _s("meta.describe", "describe", "read", DescribeIn, Loose),
            _s("board.open", "open_board", "compute", OpenBoardIn, ProjectHandle),
            _s("board.summary", "board_summary", "read", ProjectIn, BoardSummary),
            _s("board.nets", "list_nets", "read", ListNetsIn, Loose),
            _s("board.net", "get_net", "read", GetNetIn, Loose),
            _s("board.stackup", "stackup", "read", StackupIn, Loose),
            _s("impedance.xs", "compute_xsection_impedance", "compute", XsIn, Loose),
            _s("impedance.profile", "impedance_profile", "compute", ProfileIn, Loose, long=True),
            _s("erc.run", "run_erc", "compute", RunErcIn, ErcOut, long=True),
            _s("results.violations", "list_violations", "read", ListViolationsIn, ViolationPage),
            _s("results.explain", "explain_violation", "read", ExplainIn, Loose),
            _s("analysis.run", "run_analysis", "compute", RunAnalysisIn, Loose, long=True),
            _s("jobs.status", "job_status", "read", JobStatusIn, Loose),
            _s("jobs.cancel", "cancel_job", "compute", JobIn, Loose),
            _s("results.get", "get_result", "read", GetResultIn, Loose),
            _s("results.summary", "result_summary", "read", ResultIn, BundleSummary),
            _s("results.export", "export_report", "compute", ExportIn, Loose),
            _s("propose.rules", "propose_rule_changes", "read", ProposeIn, Proposal),
            _s("propose.apply", "apply_proposal", "write", ApplyIn, Loose),
        ),
        key=lambda s: s.name,
    )
)
_BY_NAME = {s.name: s for s in SPECS}

_VIOLATIONS: tuple[dict[str, Any], ...] = (
    {"rule_id": "IM-002", "severity": "error", "nets": ["SIG"], "message": "Z0 41 ohm vs 50 ohm."},
    {"rule_id": "RP-001", "severity": "error", "nets": ["SIG"], "message": "Gap in reference."},
    {"rule_id": "VI-001", "severity": "warning", "nets": ["SIG"], "message": "Via stub 0.4 mm."},
    {"rule_id": "DP-003", "severity": "warning", "nets": ["SIG"], "message": "Gap varies."},
    {"rule_id": "XT-001", "severity": "info", "nets": ["SIG"], "message": "Parallel run 12 mm."},
)
_SEV = {"info": 0, "warning": 1, "error": 2}


def _fp(rule: str, nets: Sequence[str]) -> str:
    return hashlib.sha1(f"{rule}|{','.join(nets)}".encode()).hexdigest()[:12]


class FakeOaService:
    """Implements :class:`oa_mcp.protocol.OaServiceLike` with canned data."""

    def __init__(self, roots: Sequence[str] = (), executor: str = "inline") -> None:
        self.roots = tuple(roots)
        self.executor = executor
        self._secret = secrets.token_bytes(16)
        self._projects: dict[str, JsonDict] = {}
        self._proposals: dict[str, dict[str, Any]] = {}
        self.dispatched: list[str] = []
        self.inflate_bytes = 0
        self.applied: list[str] = []

    # ------------------------------------------------------------------ OaServiceLike
    def specs(self) -> Sequence[FakeSpec]:
        return SPECS

    def input_schema(self, name: str) -> JsonDict:
        return _BY_NAME[name].input_model.model_json_schema()

    def output_schema(self, name: str) -> JsonDict:
        return _BY_NAME[name].output_model.model_json_schema()

    def schema_ids(self) -> Sequence[str]:
        return ("board-model/1", "oa-setup/1", "oa.cmd.board_open.in/1")

    def dispatch(self, name: str, args: Mapping[str, Any], ctx: McpCallContext) -> JsonDict:
        self.dispatched.append(name)
        spec = _BY_NAME[name]
        try:
            model = spec.input_model.model_validate(dict(args))
        except ValidationError as exc:
            return self._fail(spec, _issues(exc))
        handler = getattr(self, "_" + name.replace(".", "_"))
        result = handler(model, ctx)
        if isinstance(result, list):  # errors
            return self._fail(spec, result)
        schema = f"oa.{spec.output_model.__name__}/1"
        return {
            "schema": schema,
            "version": "0.1.0",
            "ok": True,
            "data": result,
            "warnings": [],
            "errors": [],
        }

    def read_resource(self, uri: str, ctx: McpCallContext) -> ResourceContent:
        m = re.fullmatch(r"oa://schemas/(.+)", uri)
        if m and m.group(1) in self.schema_ids():
            return ResourceContent("application/schema+json", text=json.dumps({"$id": m.group(1)}))
        if uri == "oa://analyses":
            return ResourceContent("application/json", text='[{"kind": "erc"}]')
        if uri == "oa://rules":
            return ResourceContent("application/json", text='[{"id": "IM-002"}]')
        if (m := re.fullmatch(r"oa://docs/rules/([A-Z]+-\d+)", uri)) and any(
            v["rule_id"] == m.group(1) for v in _VIOLATIONS
        ):
            return ResourceContent("text/markdown", text=f"# {m.group(1)}\n")
        if (m := re.fullmatch(r"oa://projects/(p_\w+)/board/(\w+)", uri)) and m.group(
            1
        ) in self._projects:
            return ResourceContent("application/json", text=json.dumps({"part": m.group(2)}))
        if m := re.fullmatch(r"oa://results/(r_\w+)/items/(\w+)/arrays/(\w+)", uri):
            return ResourceContent("application/x-npy", blob=b"\x93NUMPY")
        raise ResourceNotFound(uri)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _err(
        code: str, message: str, path: str | None = None, hint: str | None = None, **details: Any
    ) -> JsonDict:
        return {
            "code": code,
            "message": message,
            "path": path,
            "hint": hint,
            "source": None,
            "details": details,
        }

    def _fail(self, spec: FakeSpec, errors: list[JsonDict]) -> JsonDict:
        return {
            "schema": f"oa.{spec.output_model.__name__}/1",
            "version": "0.1.0",
            "ok": False,
            "data": None,
            "warnings": [],
            "errors": sorted(errors, key=lambda e: (e["code"], e["path"] or "")),
        }

    def _inside_roots(self, path: Path, ctx: McpCallContext) -> bool:
        roots = [Path(r).resolve() for r in (ctx.roots or self.roots)]
        resolved = path.resolve()
        return any(resolved == r or r in resolved.parents for r in roots)

    # ------------------------------------------------------------------ handlers
    def _meta_describe(self, a: DescribeIn, ctx: McpCallContext) -> Any:
        return {"text": f"{len(SPECS)} commands", "commands": [s.name for s in SPECS]}

    def _board_open(self, a: OpenBoardIn, ctx: McpCallContext) -> Any:
        p = Path(a.path)
        if not self._inside_roots(p, ctx):
            return [
                self._err(
                    "PERMISSION_DENIED",
                    "Path is outside the allowed roots.",
                    "/path",
                    "Start oa-mcp with --root covering this path.",
                )
            ]
        board = p / "board.kicad_pcb" if p.is_dir() else p
        if not board.is_file():
            return [self._err("NOT_FOUND", f"No board at {p.name}.", "/path", "Check the path.")]
        text = board.read_text(encoding="utf-8")
        if board.suffix == ".kicad_pcb":
            layers = [{"id": n, "name": n} for n in re.findall(r'\(\d+ "([^"]+\.Cu)" ', text)]
        else:
            doc = json.loads(text)
            layers = [
                {"id": layer["id"], "name": layer["id"]}
                for layer in doc["stackup"]["layers"]
                if layer.get("kind") == "conductor"
            ]
        pid = "p_" + hashlib.sha1(str(board.resolve()).encode()).hexdigest()[:12]
        summary = {
            "layer_count": len(layers),
            "copper_layers": layers,
            "total_thickness_m": None,
            "diagnostics": {"counts": {"error": 0, "warning": 0, "info": 0}},
            "text": f"{board.name}: {len(layers)} copper layers.",
        }
        handle = {
            "project_id": pid,
            "name": board.stem,
            "project_path": str(board.parent),
            "board_path": str(board),
            "ephemeral": board.suffix != ".kicad_pro",
            "board_hash": hashlib.sha256(text.encode()).hexdigest()[:16],
            "summary": summary,
            "text": summary["text"],
        }
        self._projects[pid] = handle
        return handle

    def _project(self, pid: str) -> JsonDict | list[JsonDict]:
        if pid not in self._projects:
            close = sorted(self._projects)
            return [
                self._err(
                    "NOT_FOUND",
                    f"Unknown project {pid}.",
                    "/project_id",
                    "Call open_board first.",
                    candidates=close,
                )
            ]
        return self._projects[pid]

    def _board_summary(self, a: ProjectIn, ctx: McpCallContext) -> Any:
        p = self._project(a.project_id)
        return p if isinstance(p, list) else p["summary"]

    def _board_nets(self, a: ListNetsIn, ctx: McpCallContext) -> Any:
        p = self._project(a.project_id)
        if isinstance(p, list):
            return p
        pad = "x" * self.inflate_bytes
        return {"text": "1 net", "items": [{"name": "SIG", "pad": pad}], "total": 1}

    def _board_net(self, a: GetNetIn, ctx: McpCallContext) -> Any:
        p = self._project(a.project_id)
        if isinstance(p, list):
            return p
        if a.net not in ("SIG", "GND"):
            return [
                self._err(
                    "NOT_FOUND",
                    f"Unknown net {a.net}.",
                    "/net",
                    "Use list_nets.",
                    candidates=["SIG", "GND"],
                )
            ]
        return {"text": a.net}

    def _board_stackup(self, a: StackupIn, ctx: McpCallContext) -> Any:
        p = self._project(a.project_id)
        return p if isinstance(p, list) else {"text": "stackup"}

    def _impedance_xs(self, a: XsIn, ctx: McpCallContext) -> Any:
        return {"text": "z0 50 ohm", "z0_ohm": 50.0}

    def _impedance_profile(self, a: ProfileIn, ctx: McpCallContext) -> Any:
        return {"text": "profile"}

    def _erc_run(self, a: RunErcIn, ctx: McpCallContext) -> Any:
        p = self._project(a.project_id)
        if isinstance(p, list):
            return p
        if ctx.progress is not None:
            ctx.progress(0.5, "halfway")
        rid = "r_" + hashlib.sha1(a.project_id.encode()).hexdigest()[:10]
        counts = {"error": 0, "warning": 0, "info": 0, "waived": 0}
        for v in _VIOLATIONS:
            counts[v["severity"]] += 1
        text = (
            f"ERC: {counts['error']} errors, {counts['warning']} warnings, {counts['info']} info."
        )
        summary = {
            "result_id": rid,
            "analysis": "erc",
            "status": "warning",
            "text": text,
            "diagnostics": counts,
        }
        return {
            "result_id": rid,
            "counts": counts,
            "by_rule": [{"rule_id": v["rule_id"], "count": 1} for v in _VIOLATIONS],
            "summary": summary,
        }

    def _violations(self) -> list[dict[str, Any]]:
        out = [{**v, "fingerprint": _fp(v["rule_id"], v["nets"])} for v in _VIOLATIONS]
        return sorted(out, key=lambda v: (-_SEV[v["severity"]], v["rule_id"], v["fingerprint"]))

    def _results_violations(self, a: ListViolationsIn, ctx: McpCallContext) -> Any:
        rows = self._violations()
        if a.severity_min:
            rows = [v for v in rows if _SEV[v["severity"]] >= _SEV[a.severity_min]]
        if a.rule_id:
            rows = [v for v in rows if fnmatch.fnmatchcase(v["rule_id"], a.rule_id)]
        page = rows[a.offset : a.offset + a.limit]
        end = a.offset + len(page)
        return {
            "items": page,
            "offset": a.offset,
            "limit": a.limit,
            "total": len(rows),
            "next_offset": end if end < len(rows) else None,
            "sort": "severity desc, rule_id, fingerprint",
        }

    def _results_explain(self, a: ExplainIn, ctx: McpCallContext) -> Any:
        known = [v["fingerprint"] for v in self._violations()]
        if a.fingerprint not in known:
            return [
                self._err(
                    "NOT_FOUND",
                    "Unknown fingerprint.",
                    "/fingerprint",
                    "Use list_violations.",
                    candidates=known[:3],
                )
            ]
        return {"text": "explained"}

    def _analysis_run(self, a: RunAnalysisIn, ctx: McpCallContext) -> Any:
        return {"text": "estimate", "valid": True, "dry_run": a.dry_run}

    def _jobs_status(self, a: JobStatusIn, ctx: McpCallContext) -> Any:
        return {"text": "done"}

    def _jobs_cancel(self, a: JobIn, ctx: McpCallContext) -> Any:
        return {"text": "cancelled"}

    def _results_get(self, a: GetResultIn, ctx: McpCallContext) -> Any:
        return {"text": "manifest"}

    def _results_summary(self, a: ResultIn, ctx: McpCallContext) -> Any:
        counts = {"error": 2, "warning": 2, "info": 1, "waived": 0}
        return {
            "result_id": a.result_id,
            "analysis": "erc",
            "status": "warning",
            "text": "ERC: 2 errors, 2 warnings, 1 info.",
            "diagnostics": counts,
        }

    def _results_export(self, a: ExportIn, ctx: McpCallContext) -> Any:
        return {"text": "exported"}

    def _token(self, pid: str) -> str:
        return hmac.new(self._secret, pid.encode(), "sha256").hexdigest()

    def _propose_rules(self, a: ProposeIn, ctx: McpCallContext) -> Any:
        p = self._project(a.project_id)
        if isinstance(p, list):
            return p
        pid = "prop_" + secrets.token_hex(6)
        diff = '+(rule "hs_clearance" (constraint clearance (min 0.2mm)))\n'
        self._proposals[pid] = {"used": False}
        return {
            "proposal_id": pid,
            "files": [{"path": "board.kicad_dru", "unified_diff": diff}],
            "confirm_token": self._token(pid),
            "text": "1 file changed (dry run)",
        }

    def _propose_apply(self, a: ApplyIn, ctx: McpCallContext) -> Any:
        if not ctx.allow_writes:
            return [
                self._err("PERMISSION_DENIED", "Writes are disabled.", None, "Use --allow-writes.")
            ]
        rec = self._proposals.get(a.proposal_id)
        if rec is None:
            return [self._err("NOT_FOUND", "Unknown proposal.", "/proposal_id")]
        if rec["used"] or not hmac.compare_digest(a.confirm_token, self._token(a.proposal_id)):
            return [
                self._err(
                    "CONFIRMATION_REQUIRED", "Token invalid or already used.", "/confirm_token"
                )
            ]
        rec["used"] = True
        self.applied.append(a.proposal_id)
        return {"text": "applied"}


def _issues(exc: ValidationError) -> list[JsonDict]:
    out: list[JsonDict] = []
    for e in exc.errors():
        path = "/" + "/".join(str(x) for x in e["loc"])
        hint = (
            "remove the field or check its spelling; see the tool's input schema"
            if e["type"] == "extra_forbidden"
            else "fix the value"
        )
        out.append(
            {
                "code": "SETUP_INVALID",
                "message": str(e["msg"]),
                "path": path,
                "hint": hint,
                "source": None,
                "details": {"type": e["type"]},
            }
        )
    return out


def create_service(*, roots: Sequence[str] = (), executor: str = "inline") -> FakeOaService:
    """Factory matching the ``--service-factory`` contract."""
    return FakeOaService(roots, executor)
