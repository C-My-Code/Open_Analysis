# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed inputs and outputs of the command catalog (ai-integration.md sections 2.4, 4, 8.3).

Every field carries a ``description`` with its unit in ``[...]`` (rule 7). Outputs are SI floats.
Input models end in ``In``; a command's output model is the type named in its ``CommandSpec``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, model_validator

from oa_jobs.models import Budget, PlanEstimate
from oa_model._base import OAModel
from oa_model.common import Diagnostic, Frequency, Length
from oa_model.xsection import CrossSection
from oa_service.types import Page, PageRequest

Severity = Literal["info", "warning", "error"]
JobKind = Literal["queued", "planning", "running", "reducing", "succeeded", "failed", "cancelled"]

# ---------------------------------------------------------------- shared small types


class DiagnosticDigest(OAModel):
    """Counts of board or ingest diagnostics by severity plus the worst few."""

    counts: dict[str, int] = Field(description="Number of diagnostics per severity [1].")
    top: tuple[Diagnostic, ...] = Field(description="Up to 20 diagnostics, errors first.")


class ArrayRef(OAModel):
    """Pointer to a large array that is not inlined (never more than 1000 elements inline)."""

    name: str = Field(description="Array name within the result item.")
    shape: tuple[int, ...] = Field(description="Array shape [1].")
    dtype: str = Field(description="NumPy dtype string, for example 'float64'.")
    unit: str | None = Field(default=None, description="SI unit of the values, if known.")
    uri: str = Field(
        description="MCP resource URI or REST path serving the array (application/x-npy)."
    )


# ---------------------------------------------------------------- projects and board


class CopperLayerInfo(OAModel):
    """One copper layer of the stackup."""

    id: str = Field(description="Layer id 'L1'..'Ln' (top to bottom).")
    name: str = Field(description="Source or user name, for example 'F.Cu'.")
    z_m: float = Field(description="Bottom surface height above the stack bottom [m].")
    thickness_m: float = Field(description="Copper thickness [m].")


class NetClassInfo(OAModel):
    """A net class with its member count and impedance targets."""

    name: str = Field(description="Net class name.")
    n_nets: int = Field(description="Number of member nets [1].")
    target_z_se: float | None = Field(
        default=None, description="Target single-ended impedance [ohm]."
    )
    target_z_diff: float | None = Field(
        default=None, description="Target differential impedance [ohm]."
    )


class CountBy(OAModel):
    """A total with a breakdown by kind."""

    total: int = Field(description="Total count [1].")
    by_kind: dict[str, int] = Field(description="Count per kind, keys sorted [1].")


class ZoneCounts(OAModel):
    """Zone totals."""

    count: int = Field(description="Number of zones [1].")
    unfilled: int = Field(description="Zones whose fill_state is not 'filled' [1].")


class IntentBrief(OAModel):
    """Resolved design intent in one glance."""

    high_speed_nets: int = Field(description="Number of nets flagged high-speed [1].")
    rails: tuple[str, ...] = Field(description="Power rail net names, sorted.")
    ground_nets: tuple[str, ...] = Field(description="Ground net names, sorted.")


class BoardMetaView(OAModel):
    """Where the board came from."""

    name: str = Field(description="Board name.")
    source_tool: str = Field(description="Source tool: kicad, ipc2581, altium, synthetic ...")
    source_file: str | None = Field(default=None, description="Source file name (not a path).")
    board_hash: str = Field(description="Content hash of the board without physics, 'sha256:...'.")
    physics_applied: bool = Field(description="Whether a physics sidecar was applied.")


class BoardSummary(OAModel):
    """Compact board description (size, stackup, counts, intent). Read it before listing nets."""

    meta: BoardMetaView = Field(description="Provenance of the board.")
    size_m: tuple[float, float] = Field(description="Outline bounding box width and height [m].")
    layer_count: int = Field(description="Number of copper layers [1].")
    copper_layers: tuple[CopperLayerInfo, ...] = Field(description="Copper layers, top first.")
    total_thickness_m: float = Field(description="Overall stack height [m].")
    nets: CountBy = Field(description="Nets by kind (signal, power, ground, unknown).")
    net_classes: tuple[NetClassInfo, ...] = Field(description="Net classes sorted by name.")
    diff_pairs: int = Field(description="Number of differential pairs [1].")
    components: int = Field(description="Number of components [1].")
    vias: CountBy = Field(description="Vias by kind (through, blind, buried, micro).")
    zones: ZoneCounts = Field(description="Zone totals.")
    intent: IntentBrief = Field(description="Resolved design intent.")
    diagnostics: DiagnosticDigest = Field(description="Ingest and validation diagnostics.")


class ProjectHandle(OAModel):
    """An opened project. `project_id` is stable across restarts and keys every other command."""

    project_id: str = Field(description="'p_' + first 12 hex of sha1(absolute project path).")
    name: str = Field(description="Project name.")
    project_path: str | None = Field(
        default=None, description="Absolute path of oa-project.yaml; null for an ephemeral project."
    )
    board_path: str = Field(description="Absolute path of the board file.")
    ephemeral: bool = Field(
        description="True when a bare board file was opened: nothing is written next to it."
    )
    board_hash: str = Field(description="Content hash of the board, 'sha256:...'.")
    physics_hash: str | None = Field(default=None, description="Hash of the physics sidecar.")
    source_tool: str = Field(description="Source tool of the board.")
    diagnostics: DiagnosticDigest = Field(description="Ingest diagnostics.")
    summary: BoardSummary = Field(description="Board summary.")


class BoardOpenIn(OAModel):
    """Open a project directory, an `oa-project.yaml`, or a bare board file."""

    path: str = Field(
        description="Project directory, oa-project.yaml, or board file (.kicad_pcb, .xml, "
        ".PcbDoc, .oab, .oab.json)."
    )
    refill_zones: Literal["never", "if_unfilled"] = Field(
        default="never", description="Refill KiCad zones through kicad-cli before reading."
    )
    physics: str | None = Field(
        default=None, description="Physics sidecar path for a bare board file."
    )
    intent: str | None = Field(default=None, description="Intent file path for a bare board file.")


class ProjectIn(OAModel):
    """Address an opened project."""

    project_id: str = Field(description="Project id returned by board.open.")


class ProjectListIn(OAModel):
    """List recently opened projects (no arguments)."""


class ProjectList(OAModel):
    """Projects opened in this session or recently."""

    projects: tuple[ProjectHandle, ...] = Field(description="Projects sorted by project_id.")


class NetInfo(OAModel):
    """One net with its routed extent."""

    id: str = Field(description="Net id (equal to the net name).")
    name: str = Field(description="Net name.")
    kind: str = Field(description="signal, power, ground or unknown (intent overrides ingest).")
    net_class: str | None = Field(default=None, description="Net class name.")
    diff_partner: str | None = Field(default=None, description="Name of the differential partner.")
    n_items: int = Field(description="Tracks, vias, pads and zones on the net [1].")
    length_m: float = Field(description="Total routed track length [m].")
    layers: tuple[str, ...] = Field(description="Copper layer ids carrying this net, sorted.")
    via_count: int = Field(description="Vias on the net [1].")
    target_z: float | None = Field(
        default=None, description="Target impedance of the net (se or diff) [ohm]."
    )
    intent_tags: tuple[str, ...] = Field(
        description="Intent tags: 'high_speed', 'rail', 'ground', 'diff_pair'."
    )


class NetQuery(OAModel):
    """Filters for board.nets; every filter is optional and they are ANDed."""

    project_id: str = Field(description="Project id returned by board.open.")
    query: str | None = Field(
        default=None,
        description="Case-sensitive fnmatch glob on the net name, for example 'USB_*'.",
    )
    kind: Literal["signal", "power", "ground", "unknown"] | None = Field(
        default=None, description="Keep only nets of this kind."
    )
    net_class: str | None = Field(default=None, description="Keep only nets of this class.")
    layer: str | None = Field(default=None, description="Keep nets with copper on this layer id.")
    high_speed_only: bool = Field(
        default=False, description="Keep only nets flagged high-speed by the design intent."
    )
    page: PageRequest = Field(default_factory=PageRequest, description="Paging window.")


class NetRefIn(OAModel):
    """Address one net."""

    project_id: str = Field(description="Project id returned by board.open.")
    net: str = Field(description="Net name or id.")


class LayerRun(OAModel):
    """Routed copper of a net on one layer."""

    layer: str = Field(description="Copper layer id.")
    length_m: float = Field(description="Track length on the layer [m].")
    widths_m: tuple[float, ...] = Field(description="Up to 10 distinct track widths, sorted [m].")
    segments: int = Field(description="Number of track segments [1].")


class ViaBrief(OAModel):
    """A via on a net."""

    id: str = Field(description="Via item id.")
    kind: str = Field(description="through, blind, buried or micro.")
    span: tuple[str, str] = Field(description="Start and end copper layer ids.")
    drill_m: float | None = Field(default=None, description="Drill diameter [m].")
    stub_m: float | None = Field(default=None, description="Unused barrel length [m], if known.")


class PadBrief(OAModel):
    """A pad on a net."""

    refdes: str = Field(description="Owning component reference designator.")
    pin: str = Field(description="Pad number or name.")
    layer: str | None = Field(default=None, description="Copper layer id of the pad.")


class NetDetail(OAModel):
    """A net with per-layer extent, vias and pads. Lists are capped; `truncated` says so."""

    info: NetInfo = Field(description="The net summary.")
    per_layer: tuple[LayerRun, ...] = Field(description="Routed copper per layer.")
    vias: tuple[ViaBrief, ...] = Field(description="Up to 50 vias, sorted by id.")
    pads: tuple[PadBrief, ...] = Field(description="Up to 100 pads, sorted by refdes and pin.")
    intent: dict[str, JsonValue] = Field(
        description="Resolved intent: rise_time_s [s], target_z [ohm], tolerance [1]."
    )
    truncated: bool = Field(description="True when vias or pads were capped.")


class StackLayerView(OAModel):
    """One layer of the stackup evaluated at a frequency."""

    id: str = Field(description="Layer id: L1.. copper, D1.. dielectric, MT/MB mask.")
    kind: Literal["conductor", "dielectric", "mask"] = Field(description="Layer kind.")
    name: str | None = Field(default=None, description="Source name.")
    z_bottom_m: float = Field(description="Bottom surface height [m].")
    z_top_m: float = Field(description="Top surface height [m].")
    thickness_m: float = Field(description="Thickness [m].")
    material: str = Field(description="Material name.")
    dk: float | None = Field(
        default=None, description="Relative permittivity at the frequency [1]."
    )
    df: float | None = Field(default=None, description="Loss tangent at the frequency [1].")
    conductivity_s_per_m: float | None = Field(
        default=None, description="Bulk conductivity of copper layers [S/m]."
    )
    roughness: str | None = Field(default=None, description="Roughness model name of the top face.")


class StackupView(OAModel):
    """The physical stackup evaluated at one frequency."""

    layers: tuple[StackLayerView, ...] = Field(description="Layers, top first.")
    total_thickness_m: float = Field(description="Overall stack height [m].")
    frequency_hz: float = Field(description="Frequency at which dk and df are evaluated [Hz].")
    physics_source: Literal["sidecar", "board", "default"] = Field(
        description="Where the material data came from."
    )
    warnings: tuple[str, ...] = Field(description="Caveats, for example a defaulted stackup.")


class StackupIn(OAModel):
    """Stackup at a frequency."""

    project_id: str = Field(description="Project id returned by board.open.")
    frequency: Frequency | None = Field(
        default=None, description="Evaluation frequency [Hz]; '1GHz' parses. Default 1 GHz."
    )


# ---------------------------------------------------------------- analyses, jobs, results


class AnalysisInfo(OAModel):
    """An installed analysis."""

    kind: str = Field(description="Analysis kind, for example 'erc'.")
    title: str = Field(description="Display title.")
    version: str = Field(description="Analysis version (part of cache keys).")
    config_schema: dict[str, JsonValue] = Field(description="JSON Schema of the setup `config`.")


class RuleInfo(OAModel):
    """An installed ERC or EMI rule."""

    id: str = Field(description="Stable rule id, for example 'RP-001'.")
    title: str = Field(description="Display title.")
    category: str = Field(description="Rule category.")
    aliases: tuple[str, ...] = Field(description="Alternative ids.")
    default_severity: Severity = Field(description="Severity used when the setup does not set one.")
    config_schema: dict[str, JsonValue] = Field(description="JSON Schema of the rule thresholds.")
    references: tuple[str, ...] = Field(description="Citations for the thresholds.")
    explanation: str = Field(description="Physics and rationale (empty if the rule has none).")


class JobBrief(OAModel):
    """Job state in agent-friendly form."""

    job_id: str = Field(description="Job id (ULID).")
    state: JobKind = Field(description="Lifecycle state.")
    analysis: str = Field(description="Analysis kind.")
    setup: str = Field(description="Setup name.")
    progress: float = Field(description="Weighted progress [1], 0..1.")
    message: str = Field(description="Last status message.")
    result_id: str | None = Field(default=None, description="Result id once succeeded.")
    tasks_total: int = Field(default=0, description="Tasks in the plan [1].")
    tasks_done: int = Field(default=0, description="Tasks finished, including cached [1].")
    tasks_cached: int = Field(default=0, description="Tasks served from the cache [1].")
    tasks_failed: int = Field(default=0, description="Tasks that failed [1].")
    error: dict[str, JsonValue] | None = Field(
        default=None, description="ErrorInfo of a failed job; null otherwise."
    )
    cancel_reason: str | None = Field(
        default=None, description="'user', 'budget:max_runtime' or 'budget:max_memory'."
    )
    created_utc: str = Field(description="Creation time, ISO-8601 UTC.")
    updated_utc: str = Field(description="Last update time, ISO-8601 UTC.")


class ProvenanceBrief(OAModel):
    """Who produced a result and from what."""

    analysis: str = Field(description="Analysis kind.")
    analysis_version: str = Field(description="Analysis version.")
    solver: str | None = Field(default=None, description="Solver name.")
    solver_version: str | None = Field(default=None, description="Solver version.")
    board_hash: str = Field(description="Hash of the raw board.")
    physics_hash: str | None = Field(default=None, description="Hash of the physics sidecar.")
    setup_hash: str = Field(description="Hash of the setup.")
    inputs_hash: str | None = Field(default=None, description="Hash of all inputs.")


class ItemSummary(OAModel):
    """Size-bounded summary of one result item (at most 2 KiB)."""

    item_id: str = Field(description="Item id within the bundle.")
    kind: str = Field(description="Item kind: violations, profile, scalars, network, ...")
    title: str = Field(description="Item title.")
    accuracy_class: str | None = Field(
        default=None,
        description="reference, validated, screening, estimate or unverified; null if not stated.",
    )
    digest: str = Field(description="One or two plain-text sentences.")
    stats: dict[str, JsonValue] = Field(description="Small statistics; lists are capped.")
    validity_flags: tuple[dict[str, JsonValue], ...] = Field(description="Validity statements.")
    truncated: bool = Field(default=False, description="True when statistics were cut to fit.")


class BundleSummary(OAModel):
    """Size-bounded summary of a result bundle (at most 8 KiB). Read this before fetching items."""

    result_id: str = Field(description="Result id (ULID).")
    analysis: str = Field(description="Analysis kind.")
    status: Literal["ok", "warning", "failed_quality", "error"] = Field(description="Verdict.")
    text: str = Field(description="At most 1500 characters; equals ResultBundle.summary.")
    items: tuple[ItemSummary, ...] = Field(description="Up to 20 item summaries, bundle order.")
    n_items: int = Field(description="Total items in the bundle [1].")
    diagnostics: dict[str, int] = Field(description="Diagnostic counts by severity [1].")
    top_diagnostics: tuple[Diagnostic, ...] = Field(description="Up to 10, errors first.")
    provenance: ProvenanceBrief = Field(description="Provenance.")
    more: str | None = Field(default=None, description="How to get the remaining items.")


class JobHandle(OAModel):
    """Returned by long-running commands: poll `jobs.status`, or read `summary` when done."""

    job_id: str = Field(description="Job id (ULID).")
    state: JobKind = Field(description="Lifecycle state.")
    progress: float = Field(description="Weighted progress [1], 0..1.")
    estimate: PlanEstimate | None = Field(default=None, description="Pre-flight estimate.")
    result_id: str | None = Field(default=None, description="Result id once succeeded.")
    summary: BundleSummary | None = Field(
        default=None, description="Result summary when the job finished within wait_s."
    )
    status: JobBrief | None = Field(default=None, description="Current job status.")


class RunDryRun(OAModel):
    """Output of a dry run: validity and the resource estimate, no job started."""

    valid: bool = Field(description="True when the setup validates and the plan was built.")
    diagnostics: tuple[Diagnostic, ...] = Field(description="Setup diagnostics.")
    estimate: PlanEstimate | None = Field(default=None, description="Pre-flight estimate.")


class RunResult(OAModel):
    """Output of every run command: a dry-run estimate, or the started (maybe finished) job."""

    dry_run: bool = Field(description="True when only an estimate was made and no job started.")
    valid: bool = Field(description="True when the setup validates and a plan was built.")
    diagnostics: tuple[Diagnostic, ...] = Field(description="Setup diagnostics.")
    estimate: PlanEstimate | None = Field(default=None, description="Pre-flight estimate.")
    job: JobHandle | None = Field(
        default=None, description="The job; includes the summary when it finished within wait_s."
    )


class RunAnalysisIn(OAModel):
    """Run any registered analysis from a stored setup or an inline config."""

    project_id: str = Field(description="Project id returned by board.open.")
    analysis: str | None = Field(
        default=None, description="Analysis kind; required with `config`, ignored with `setup`."
    )
    setup: str | None = Field(
        default=None, description="Name of a stored setup (oa/setups/NAME.yaml)."
    )
    config: dict[str, JsonValue] | None = Field(
        default=None, description="Inline analysis config (validated against the analysis Config)."
    )
    ports: tuple[dict[str, JsonValue], ...] = Field(
        default=(), description="Inline ports for EM and PI analyses (oa-setup/1 `ports`)."
    )
    dry_run: bool = Field(default=False, description="Estimate only; start no job.")
    budget: Budget | None = Field(
        default=None, description="Per-call budget, clamped to the ceiling."
    )
    wait_s: float = Field(
        default=0.0,
        ge=0,
        le=3600,
        description="Wait up to this long for the job to finish [s]; 0 returns a handle at once.",
    )
    executor: Literal["inline", "process"] = Field(
        default="process", description="Where tasks run."
    )

    @model_validator(mode="after")
    def _one_source(self) -> RunAnalysisIn:
        if self.setup is None and (self.config is None or self.analysis is None):
            raise ValueError("give either `setup`, or both `analysis` and `config`")
        if self.setup is not None and self.config is not None:
            raise ValueError("give `setup` or `config`, not both")
        return self


class ErcRunIn(OAModel):
    """Run the electrical rule check."""

    project_id: str = Field(description="Project id returned by board.open.")
    setup: str | None = Field(default=None, description="Stored setup name (analysis 'erc').")
    rules: dict[str, JsonValue] | None = Field(
        default=None,
        description="Per-rule overrides {RULE_ID: {enabled, severity, ...thresholds}}.",
    )
    nets: dict[str, JsonValue] | None = Field(
        default=None, description="NetSelector limiting the nets checked (names, net_classes, ...)."
    )
    dry_run: bool = Field(default=False, description="Estimate only; start no job.")
    budget: Budget | None = Field(
        default=None, description="Per-call budget, clamped to the ceiling."
    )
    wait_s: float = Field(default=20.0, ge=0, le=3600, description="Wait for the result [s].")
    executor: Literal["inline", "process"] = Field(
        default="process", description="Where tasks run."
    )


class ProfileRunIn(OAModel):
    """Impedance profile along nets."""

    project_id: str = Field(description="Project id returned by board.open.")
    nets: dict[str, JsonValue] = Field(
        description="NetSelector: {names: [globs], net_classes: [...], kinds: [...]}."
    )
    method: Literal["auto", "closed_form", "bem", "fem"] | None = Field(
        default=None, description="Impedance method; null lets the analysis choose."
    )
    ds: Length | None = Field(
        default=None, description="Sampling step along the net [m]; '0.5mm' parses."
    )
    tolerance: float | None = Field(
        default=None, gt=0, lt=1, description="Impedance tolerance as a fraction [1]."
    )
    dry_run: bool = Field(default=False, description="Estimate only; start no job.")
    budget: Budget | None = Field(
        default=None, description="Per-call budget, clamped to the ceiling."
    )
    wait_s: float = Field(default=20.0, ge=0, le=3600, description="Wait for the result [s].")
    executor: Literal["inline", "process"] = Field(
        default="process", description="Where tasks run."
    )


class JobIn(OAModel):
    """Address one job."""

    job_id: str = Field(description="Job id (ULID).")


class JobStatusIn(OAModel):
    """Job status, optionally waiting for a change."""

    job_id: str = Field(description="Job id (ULID).")
    wait_s: float = Field(
        default=0.0, ge=0, le=60, description="Block up to this long for the job to finish [s]."
    )
    events_after_seq: int | None = Field(
        default=None, ge=0, description="Also return job events with seq greater than this."
    )


class JobEventView(OAModel):
    """One job event."""

    seq: int = Field(description="Monotonic event number [1].")
    type: str = Field(description="Event type: state, progress, log, task_finished, ...")
    task_id: str | None = Field(default=None, description="Task the event belongs to.")
    data: dict[str, JsonValue] = Field(description="Event payload.")
    utc: str = Field(description="Event time, ISO-8601 UTC.")


class JobStatusOut(OAModel):
    """Job status plus recent events and the summary of a finished job."""

    status: JobBrief = Field(description="Current job status.")
    error: dict[str, JsonValue] | None = Field(
        default=None, description="Failure ErrorInfo, if failed."
    )
    events: tuple[JobEventView, ...] = Field(description="Up to 50 events after the given seq.")
    budget_used: dict[str, float] = Field(
        description="Live budget usage: elapsed_s [s], peak_memory_gb [GB]."
    )
    summary: BundleSummary | None = Field(default=None, description="Summary when succeeded.")


class JobsListIn(OAModel):
    """List jobs of a project, newest first."""

    project_id: str = Field(description="Project id returned by board.open.")
    state: JobKind | None = Field(default=None, description="Keep only jobs in this state.")
    page: PageRequest = Field(default_factory=PageRequest, description="Paging window.")


class ResultIn(OAModel):
    """Address one result bundle."""

    result_id: str = Field(description="Result id (ULID) from a finished job.")


class ViolationQuery(OAModel):
    """Filters for results.violations; ANDed."""

    result_id: str = Field(description="Result id (ULID) of an analysis with a violations item.")
    item_id: str | None = Field(
        default=None, description="Violations item id; required when the bundle has several."
    )
    severity_min: Severity = Field(default="info", description="Lowest severity to include.")
    rule_id: str | None = Field(default=None, description="fnmatch glob on the rule id, 'IM-*'.")
    net: str | None = Field(default=None, description="fnmatch glob on the net names.")
    layer: str | None = Field(default=None, description="Layer id.")
    include_waived: bool = Field(default=False, description="Include waived violations.")
    fields: Literal["brief", "full"] = Field(default="brief", description="Detail level.")
    page: PageRequest = Field(default_factory=PageRequest, description="Paging window.")


class ViolationBrief(OAModel):
    """One violation in the form an agent triages from."""

    fingerprint: str = Field(description="Stable violation hash.")
    rule_id: str = Field(description="Stable rule id.")
    severity: Severity = Field(description="Severity.")
    message: str = Field(description="One sentence.")
    nets: tuple[str, ...] = Field(description="Nets involved.")
    layers: tuple[str, ...] = Field(description="Layers involved.")
    location_m: tuple[float, float] = Field(
        description="Marker point in the board frame, Y up [m]."
    )
    kicad_mm: tuple[float, float] | None = Field(
        default=None, description="Marker point in KiCad's frame, Y down [mm]; null if unknown."
    )
    measured: float | None = Field(default=None, description="Measured value in `unit`.")
    limit: float | None = Field(default=None, description="Limit in `unit`.")
    unit: str | None = Field(default=None, description="SI unit of measured and limit.")
    fix_hint: str | None = Field(default=None, description="One-sentence remedy.")


class ViolationView(ViolationBrief):
    """A violation with its item references and remediation."""

    items: tuple[str, ...] = Field(description="Board item ids involved.")
    geometry_bbox_m: tuple[float, float, float, float] | None = Field(
        default=None, description="Highlight bounding box (x0, y0, x1, y1) [m]."
    )
    remediation: dict[str, JsonValue] | None = Field(
        default=None, description="Structured remediation."
    )
    waived: bool = Field(description="True when a waiver applies.")


class ViolationPage(Page[ViolationBrief | ViolationView]):
    """A page of violations; items are ViolationView when fields='full'."""


class ExplainIn(OAModel):
    """Explain one violation."""

    result_id: str = Field(description="Result id (ULID).")
    fingerprint: str = Field(description="Violation fingerprint from results.violations.")
    item_id: str | None = Field(default=None, description="Violations item id, if several.")


class RuleExplain(OAModel):
    """Rule metadata for an explanation."""

    id: str = Field(description="Rule id.")
    title: str = Field(description="Rule title.")
    category: str = Field(description="Rule category.")
    aliases: tuple[str, ...] = Field(description="Alternative ids.")
    default_severity: str = Field(description="Default severity.")
    references: tuple[str, ...] = Field(description="Citations.")
    explanation: str = Field(description="Physics and rationale.")


class ViolationExplanation(OAModel):
    """Deterministic explanation of a violation: facts, margin, context and a waiver snippet."""

    violation: ViolationView = Field(description="The violation.")
    rule: RuleExplain | None = Field(
        description="Rule metadata; null if the rule is not installed."
    )
    margin: float | None = Field(default=None, description="measured - limit in `unit`.")
    margin_rel: float | None = Field(default=None, description="margin / limit [1].")
    nearby_items: tuple[str, ...] = Field(description="Up to 20 item ids near the marker.")
    related: tuple[str, ...] = Field(
        description="Up to 20 fingerprints of violations on the same nets or items."
    )
    remediation: dict[str, JsonValue] | None = Field(
        default=None, description="Structured remediation."
    )
    waiver_yaml: str = Field(description="Ready-to-paste waiver for oa/setups/*.yaml `waivers`.")


class ResultItemBrief(OAModel):
    """Manifest entry of a result item."""

    id: str = Field(description="Item id.")
    kind: str = Field(description="Item kind.")
    title: str = Field(description="Item title.")
    accuracy_class: str | None = Field(default=None, description="Accuracy class, if stated.")


class ResultManifest(OAModel):
    """Bundle manifest."""

    id: str = Field(description="Result id.")
    status: str = Field(description="Verdict.")
    analysis: str = Field(description="Analysis kind.")
    summary: str = Field(description="Bundle digest text.")
    items: tuple[ResultItemBrief, ...] = Field(description="Items in bundle order.")


class ResultGetIn(OAModel):
    """Fetch a result manifest, or one item rendered compactly."""

    result_id: str = Field(description="Result id (ULID).")
    item_id: str | None = Field(default=None, description="Item to render; omit for the manifest.")
    select: tuple[str, ...] = Field(
        default=(), description="Array or column names to include; empty selects all."
    )
    max_points: int = Field(
        default=201, ge=2, le=1000, description="Decimate 1-D series to this many points [1]."
    )
    page: PageRequest = Field(default_factory=PageRequest, description="Row window for tables.")


class ResultItemView(OAModel):
    """One item rendered compactly: summary, decimated series and ArrayRefs."""

    item: ResultItemBrief = Field(description="Item identity.")
    summary: ItemSummary = Field(description="Item summary.")
    series: dict[str, list[float]] = Field(
        description="Decimated 1-D numeric arrays (at most max_points each), SI units."
    )
    arrays: tuple[ArrayRef, ...] = Field(description="Arrays too large to inline.")
    rows: Page[list[JsonValue]] | None = Field(default=None, description="Table rows window.")
    columns: tuple[str, ...] = Field(default=(), description="Table column names.")
    decimated_from: int | None = Field(
        default=None, description="Original length when decimated [1]."
    )


class ResultGetOut(OAModel):
    """Either the manifest or one rendered item."""

    manifest: ResultManifest = Field(description="Bundle manifest.")
    item: ResultItemView | None = Field(default=None, description="The requested item, if any.")


ExportFormat = Literal["html", "sarif", "touchstone", "csv", "vtu", "json", "md"]


class ExportIn(OAModel):
    """Export a result to a file under the project's .oa/exports/."""

    result_id: str = Field(description="Result id (ULID).")
    format: ExportFormat = Field(description="Output format.")
    item_id: str | None = Field(default=None, description="Item to export; default the first fit.")
    out_path: str | None = Field(
        default=None, description="Destination inside an allowed root; default .oa/exports/<rid>/."
    )
    overwrite: bool = Field(default=False, description="Replace an existing file.")


class ExportInfo(OAModel):
    """A written export."""

    path: str = Field(description="Absolute path of the file (first file for multi-file formats).")
    media_type: str = Field(description="IANA media type.")
    size: int = Field(description="Size [B].")
    sha256: str = Field(description="SHA-256 of the file.")


# ---------------------------------------------------------------- impedance


class QuickXs(OAModel):
    """Closed-form cross-section described by a topology and a few dimensions."""

    topology: Literal[
        "microstrip",
        "embedded_microstrip",
        "stripline",
        "offset_stripline",
        "cpw",
        "cpwg",
        "edge_coupled_microstrip",
        "edge_coupled_stripline",
    ] = Field(description="Cross-section topology.")
    w: Length = Field(description="Trace width [m]; '5mil' parses.")
    h: Length = Field(description="Dielectric height to the reference [m].")
    t: Length = Field(description="Trace thickness [m].")
    er: float = Field(gt=0, description="Relative permittivity [1].")
    df: float = Field(default=0.0, ge=0, description="Loss tangent [1].")
    s: Length | None = Field(
        default=None, description="Edge-to-edge spacing for coupled lines [m]."
    )
    g: Length | None = Field(default=None, description="Coplanar gap [m].")
    h2: Length | None = Field(default=None, description="Second dielectric height [m].")
    mask_t: Length | None = Field(default=None, description="Solder mask thickness [m].")


class GeometrySample(OAModel):
    """A cross-section sampled from the board geometry."""

    project_id: str = Field(description="Project id returned by board.open.")
    net: str = Field(description="Net name.")
    at_m: tuple[float, float] | None = Field(default=None, description="Sample point (x, y) [m].")
    s_m: float | None = Field(default=None, ge=0, description="Arc length along the net [m].")
    layer: str | None = Field(default=None, description="Layer id when the net is on several.")


class XsImpedanceIn(OAModel):
    """Impedance of one cross-section: exactly one of `sample`, `xsection`, `quick`."""

    sample: GeometrySample | None = Field(default=None, description="Sample the board geometry.")
    xsection: CrossSection | None = Field(default=None, description="Inline oa-xsection/1.")
    quick: QuickXs | None = Field(default=None, description="Closed-form topology.")
    freqs: tuple[Frequency, ...] = Field(
        default=(), max_length=50, description="Frequencies [Hz]; empty uses the solver default."
    )
    method: Literal["auto", "closed_form", "bem", "fem"] = Field(
        default="auto", description="Solver method."
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> XsImpedanceIn:
        n = sum(x is not None for x in (self.sample, self.xsection, self.quick))
        if n != 1:
            raise ValueError("give exactly one of `sample`, `xsection`, `quick`")
        return self


class XsImpedance(OAModel):
    """Impedance results for one cross-section at several frequencies."""

    xs_key: str = Field(description="Content hash of the cross-section.")
    method: str = Field(description="Method actually used.")
    model_name: str = Field(description="Model or solver name.")
    freqs_hz: tuple[float, ...] = Field(description="Frequencies [Hz].")
    z0_ohm: tuple[float, ...] | None = Field(default=None, description="Single-ended Z0 [ohm].")
    zdiff_ohm: tuple[float, ...] | None = Field(default=None, description="Differential Z [ohm].")
    zcomm_ohm: tuple[float, ...] | None = Field(default=None, description="Common-mode Z [ohm].")
    zodd_ohm: tuple[float, ...] | None = Field(default=None, description="Odd-mode Z [ohm].")
    zeven_ohm: tuple[float, ...] | None = Field(default=None, description="Even-mode Z [ohm].")
    eps_eff: tuple[float, ...] = Field(description="Effective permittivity [1].")
    delay_s_per_m: tuple[float, ...] = Field(description="Propagation delay [s/m].")
    loss_db_per_m: tuple[float, ...] = Field(description="Attenuation [dB/m].")
    accuracy_class: str | None = Field(default=None, description="Accuracy class, if stated.")
    notes: tuple[str, ...] = Field(description="Caveats from the solver.")


# ---------------------------------------------------------------- setups, meta


class SetupSummary(OAModel):
    """A stored setup."""

    name: str = Field(description="Setup name (file stem).")
    analysis: str = Field(description="Analysis kind.")
    description: str = Field(description="Free text.")


class SetupsListIn(ProjectIn):
    """List stored setups."""


class SetupList(OAModel):
    """Stored setups of a project, sorted by name."""

    setups: tuple[SetupSummary, ...] = Field(description="Setups sorted by name.")


class SetupRefIn(OAModel):
    """Address a stored setup."""

    project_id: str = Field(description="Project id returned by board.open.")
    name: str = Field(description="Setup name.")


class SetupValidation(OAModel):
    """Result of validating a setup against its analysis."""

    name: str = Field(description="Setup name.")
    valid: bool = Field(description="True when no error diagnostics.")
    diagnostics: tuple[Diagnostic, ...] = Field(description="Diagnostics from the analysis.")


class SetupPutIn(OAModel):
    """Create or replace a stored setup. A write: needs `confirm` and allow_writes."""

    project_id: str = Field(description="Project id returned by board.open.")
    name: str = Field(description="Setup name (file stem).")
    analysis: str = Field(description="Analysis kind.")
    config: dict[str, JsonValue] = Field(description="Analysis config.")
    description: str = Field(default="", description="Free text.")
    ports: tuple[dict[str, JsonValue], ...] = Field(default=(), description="Ports.")
    confirm: bool = Field(
        default=False, description="Must be true to write; otherwise CONFIRMATION_REQUIRED."
    )


class SetupPutOut(OAModel):
    """A written setup file."""

    name: str = Field(description="Setup name.")
    path: str = Field(description="Absolute path written.")
    sha256: str = Field(description="SHA-256 of the written file.")
    backup: str | None = Field(default=None, description="Backup of the replaced file.")


class DoctorIn(OAModel):
    """Environment report (no arguments)."""


class DoctorOut(OAModel):
    """`oa doctor`: platform, external tools, KiCad and gmsh versions, optional packages."""

    report: dict[str, JsonValue] = Field(
        description="Environment report; keys documented in `oa doctor`."
    )


class ProjectInitIn(OAModel):
    """Scaffold a project directory. A write: needs `confirm`; never overwrites files."""

    dir: str = Field(description="Directory to create or use.")
    board: str | None = Field(
        default=None, description="Board file name inside `dir`; default the only board found."
    )
    name: str | None = Field(default=None, description="Project name; default the directory name.")
    confirm: bool = Field(default=False, description="Must be true to write files.")


class ProjectInitOut(OAModel):
    """Files created by project.init."""

    project_path: str = Field(description="Absolute path of oa-project.yaml.")
    created: tuple[str, ...] = Field(description="Absolute paths of the files created.")


class DescribeIn(OAModel):
    """Introspection: the catalog, one command, the analyses or the rules."""

    command: str | None = Field(
        default=None, description="Command name (for example 'erc.run'); omit to list all."
    )
    analyses: bool = Field(default=False, description="Also list installed analyses.")
    rules: bool = Field(default=False, description="Also list installed rules.")


class CommandSpecView(OAModel):
    """A catalog entry as served by describe (mirror of CommandSpec plus exit codes)."""

    name: str = Field(description="Dotted command name.")
    mcp_tool: str | None = Field(description="MCP tool name, or null if not exposed.")
    cli: tuple[str, ...] = Field(description="CLI path.")
    title: str = Field(description="Short title.")
    description: str = Field(description="What, when, cost, side effects, units.")
    side_effect: Literal["read", "compute", "write"] = Field(description="Side-effect class.")
    idempotent: bool = Field(description="Same input gives same output.")
    long_running: bool = Field(description="May return a JobHandle.")
    input_model: str = Field(description="Input schema id.")
    output_model: str = Field(description="Output schema id.")
    examples: tuple[dict[str, JsonValue], ...] = Field(description="Valid inputs (tested).")


class Description(OAModel):
    """Catalog description."""

    commands: tuple[CommandSpecView, ...] = Field(description="Commands sorted by name.")
    analyses: tuple[AnalysisInfo, ...] | None = Field(
        default=None, description="Installed analyses."
    )
    rules: tuple[RuleInfo, ...] | None = Field(default=None, description="Installed rules.")
    exit_codes: dict[str, str] = Field(description="CLI exit code -> meaning.")
    error_codes: dict[str, int] = Field(description="Error code -> CLI exit code.")


class SchemaIn(OAModel):
    """Fetch a schema by id, or list them."""

    id: str | None = Field(default=None, description="Schema id, for example 'oa.board_summary/1'.")


class SchemaEntry(OAModel):
    """One published schema."""

    id: str = Field(description="Schema id.")
    title: str = Field(description="Title.")
    file: str = Field(description="Relative file under schemas/.")


class SchemaOut(OAModel):
    """A schema document or the schema list."""

    entries: tuple[SchemaEntry, ...] = Field(description="Listing (when no id was given).")
    schema_doc: dict[str, JsonValue] | None = Field(
        default=None, description="The JSON Schema when an id was given."
    )
