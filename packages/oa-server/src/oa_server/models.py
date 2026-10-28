# SPDX-FileCopyrightText: 2026 Open_Analysis contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""REST-specific body shapes (interfaces.md section 7).

REST keeps raw typed bodies, as the generated TypeScript client expects; the catalog envelope is
for the CLI and MCP. Where interfaces.md section 7 and the catalog name a field differently, the
REST body follows section 7 (for example `NetInfo.length`) and adds the catalog fields.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue

from oa_jobs.models import Budget
from oa_model._base import OAModel
from oa_model.common import Diagnostic
from oa_service.errors import ErrorInfo


class ErrorBody(OAModel):
    """Every 4xx/5xx body: `error` is the primary problem, `errors` all of them."""

    error: ErrorInfo = Field(description="The first (primary) error.")
    errors: tuple[ErrorInfo, ...] = Field(default=(), description="All errors, sorted by code.")


class ToolStatus(OAModel):
    """One external tool as seen by `oa doctor`."""

    name: str = Field(description="Logical tool name, for example 'openems'.")
    available: bool = Field(description="True when the tool resolves.")
    version: str | None = Field(default=None, description="Tool version, if known.")


class Health(OAModel):
    """Server liveness and tool availability."""

    version: str = Field(description="Open_Analysis version.")
    tools: tuple[ToolStatus, ...] = Field(description="External tools, sorted by name.")


class ProjectSummary(OAModel):
    """An opened project (interfaces.md section 7) plus the catalog fields."""

    id: str = Field(description="Project id: 'p_' + sha1(project path)[:12].")
    name: str = Field(description="Project name.")
    board_hash: str = Field(description="Content hash of the board, 'sha256:...'.")
    diagnostics: tuple[Diagnostic, ...] = Field(description="Up to 20 ingest diagnostics.")
    ephemeral: bool = Field(description="True for a bare board file: nothing written beside it.")
    project_path: str | None = Field(default=None, description="oa-project.yaml path.")
    board_path: str = Field(description="Board file path.")
    source_tool: str = Field(description="Source tool of the board.")


class OpenBody(OAModel):
    """Open a project or board."""

    path: str = Field(description="Project directory, oa-project.yaml or board file.")
    refill_zones: Literal["never", "if_unfilled"] = Field(
        default="never", description="Refill zones before reading."
    )


class RestNetInfo(OAModel):
    """Net listing entry (interfaces.md section 7 field names plus the catalog fields)."""

    id: str = Field(description="Net id (the net name).")
    kind: str = Field(description="signal, power, ground or unknown.")
    net_class: str | None = Field(default=None, description="Net class name.")
    n_items: int = Field(description="Tracks, vias, pads and zones on the net [1].")
    length: float = Field(description="Routed track length [m].")
    layers: tuple[str, ...] = Field(description="Copper layer ids carrying the net.")
    via_count: int = Field(description="Vias on the net [1].")
    diff_partner: str | None = Field(default=None, description="Differential partner net.")
    target_z: float | None = Field(default=None, description="Impedance target [ohm].")
    intent_tags: tuple[str, ...] = Field(description="Intent tags.")


class RestJobStatus(OAModel):
    """`JobStatus` of interfaces.md section 4.4 plus `cancel_reason`."""

    id: str = Field(description="Job id (ULID).")
    state: Literal[
        "queued", "planning", "running", "reducing", "succeeded", "failed", "cancelled"
    ] = Field(description="Lifecycle state.")
    analysis: str = Field(description="Analysis kind.")
    setup: str = Field(description="Setup name.")
    progress: float = Field(description="Weighted progress [1], 0..1.")
    tasks_total: int = Field(description="Tasks in the plan [1].")
    tasks_done: int = Field(description="Tasks finished, including cached [1].")
    tasks_cached: int = Field(description="Tasks served from the cache [1].")
    tasks_failed: int = Field(description="Tasks that failed [1].")
    message: str = Field(description="Last status message.")
    result_id: str | None = Field(default=None, description="Result id once succeeded.")
    error: ErrorInfo | None = Field(default=None, description="Failure details, if failed.")
    cancel_reason: str | None = Field(default=None, description="Why the job was cancelled.")
    created_utc: str = Field(description="Creation time, ISO-8601 UTC.")
    updated_utc: str = Field(description="Last update time, ISO-8601 UTC.")


class JobBody(OAModel):
    """Start a job from a stored setup or an inline config."""

    setup: str | None = Field(default=None, description="Stored setup name.")
    analysis: str | None = Field(default=None, description="Analysis kind (with `config`).")
    config: dict[str, JsonValue] | None = Field(default=None, description="Inline config.")
    executor: Literal["inline", "process"] = Field(default="process", description="Task executor.")
    budget: Budget | None = Field(default=None, description="Per-job budget (clamped).")
    wait_s: float = Field(default=0.0, ge=0, le=3600, description="Wait this long [s] for the end.")
    dry_run: bool = Field(default=False, description="Estimate only.")


class XsBody(OAModel):
    """Body of POST /projects/{pid}/impedance/xs: the catalog input without `sample.project_id`."""

    sample: dict[str, JsonValue] | None = Field(default=None, description="Geometry sample.")
    xsection: dict[str, JsonValue] | None = Field(default=None, description="Inline oa-xsection/1.")
    quick: dict[str, JsonValue] | None = Field(default=None, description="Closed-form topology.")
    freqs: tuple[float | str, ...] = Field(default=(), description="Frequencies [Hz].")
    method: Literal["auto", "closed_form", "bem", "fem"] = Field(
        default="auto", description="Solver method."
    )


class CommandCall(OAModel):
    """Generic catalog call (POST /commands/{name}); the body is the command input."""

    args: dict[str, JsonValue] = Field(default_factory=dict, description="Command arguments.")
