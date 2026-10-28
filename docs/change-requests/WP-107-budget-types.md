<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-107: budget and provenance types are missing from the frozen contracts

**Owner of the affected code:** WP-000 (`oa_framework.jobs`, `oa_results.bundle`).
**Raised by:** WP-107. **Status:** open.

ai-integration.md section 12 (A6, A9) lists additive contract changes that WP-000 was to land.
They are not in `main`:

| Item | Where it should be | What WP-107 did meanwhile |
|---|---|---|
| `Budget`, `PlanEstimate` | `oa_framework.jobs` | defined in `oa_jobs.models` |
| `AnalysisRequest.budget`, `caller` | `oa_framework.jobs` | `oa_jobs.JobRequest(AnalysisRequest)` adds `budget` and `caller` |
| `JobStatus.cancel_reason` | `oa_framework.jobs` | `oa_jobs.JobStatusEx(JobStatus)` adds `cancel_reason` and `budget_used` |
| `JobService.estimate(req) -> PlanEstimate` | `oa_framework.jobs` | implemented on `LocalJobService`, not in the protocol |
| `Provenance.oa_version`, `tool_versions`, `caller` | `oa_results.bundle` | cannot be stored: `Provenance` forbids extra fields. `LocalJobService.provenance_ext(job_id)` returns them from `results/<id>/meta.json` |

`Provenance.inputs_hash` exists and is populated (sha256 over the sorted task keys plus the board,
physics and setup hashes), so it is identical for a fully cached re-run.

## Requested change
Move `Budget` and `PlanEstimate` (incl. the `est_wall_seconds`, `reasons` and `warnings` fields
used by `oa_jobs`) into `oa_framework.jobs`, add the optional fields above, and add `estimate` to
the `JobService` protocol. `oa_jobs.models` then re-exports them and the subclasses go away. Add
the three `Provenance` fields; `oa_jobs` will populate them (`meta.json` already holds the values).

Compatibility: all additions are optional with defaults. `JobStatusEx` and `JobRequest` are
subclasses, so code written against the base types keeps working.
