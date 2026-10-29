// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * API DTOs. Wire types come from `./generated/schema.d.ts`, generated from `openapi.json` by
 * `npm run gen:api` (freshness gate: `npm run gen:api:check`, run by `npm run build`).
 * The result manifest (`GET /results/{rid}`) is an untyped object in the OpenAPI document, so the
 * UI-level result model below is hand-written and built by `features/results/adapt.ts`.
 * `ApiClient` is the only seam the app depends on (mock client for dev/tests, http client for `oa serve`).
 */
import type { components } from './generated/schema';
import type { Board } from '../types/board';

type S = components['schemas'];

export type ProjectSummary = S['ProjectSummary'];
export type NetInfo = S['RestNetInfo'];
export type AnalysisInfo = S['AnalysisInfo'];
export type Budget = S['Budget'];
export type ProvenanceBrief = S['ProvenanceBrief'];
/** Violation as served by `GET /results/{rid}/violations?fields=full` (WP-207: `location_m`, metres). */
export type Violation = S['ViolationView'];
/** `JobState` of the server; `succeeded` (not `done`) per CR WP-207-api-reconciliation. */
export type JobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'cached';
export type JobStatus = Omit<S['RestJobStatus'], 'state'> & { state: JobState };

export type Severity = 'info' | 'warning' | 'error';
export type AccuracyClass = 'reference' | 'validated' | 'screening' | 'estimate' | 'unverified';

export const isTerminal = (s: JobState): boolean => s !== 'queued' && s !== 'running';

export interface ValidityFlag {
  code: string;
  ok: boolean;
  message: string;
}

export interface Provenance {
  analysis: string;
  analysis_version: string;
  board_hash: string;
  setup_hash: string;
  physics_hash?: string | null;
  inputs_hash?: string | null;
  solver?: string | null;
  solver_version?: string | null;
  config?: Record<string, unknown>;
}

interface ItemBase {
  id: string;
  title: string;
  /** Always shown next to the item (ai-integration section 4.1). */
  accuracy_class: AccuracyClass | null;
  validity_flags: ValidityFlag[];
}

export interface ViolationSet extends ItemBase {
  kind: 'violations';
  rules_run: string[];
  violations: Violation[];
}

/** One scalar field on a regular grid (TriMesh2D pieces are rasterised in the adapter). */
export interface FieldGrid {
  name: string;
  unit: string;
  layer: string | null;
  net: string | null;
  origin: [number, number]; // metres
  dx: number;
  dy: number;
  nx: number;
  ny: number;
  values: number[]; // row-major, row 0 = lowest y, NaN outside the mesh
  clim_hint: [number, number] | null;
  log_scale: boolean;
}

export interface FieldResult extends ItemBase {
  kind: 'field';
  fields: FieldGrid[];
}

export interface ProfileEvent {
  kind: string;
  s: number; // m
  item: string | null;
  message: string;
}

export interface ProfileQuantity {
  name: string;
  unit: string;
  values: number[];
}

export interface ProfileResult extends ItemBase {
  kind: 'profile';
  net: string | [string, string];
  s: number[]; // m
  quantities: ProfileQuantity[];
  events: ProfileEvent[];
  target: number | null;
  tolerance: number | null;
}

export interface QualityReport {
  verdict: 'pass' | 'warn' | 'fail';
  pqm: number;
  rqm: number;
  cqm: number;
  max_singular_value: number;
  reciprocity_max_err: number;
  dc: string;
  f_min: number;
  f_max: number;
  n_freqs: number;
  fixes_applied: string[];
  notes: string[];
}

export interface NetworkResult extends ItemBase {
  kind: 'network';
  freqs: number[]; // Hz
  port_names: string[];
  /** S[f][i][j] flattened as f*P*P + i*P + j. */
  s_re: number[];
  s_im: number[];
  quality: QualityReport | null;
}

export interface TableResult extends ItemBase {
  kind: 'table';
  columns: { name: string; unit: string | null }[];
  rows: unknown[][];
}

export interface ScalarsResult extends ItemBase {
  kind: 'scalars';
  values: { name: string; value: number; unit: string; limit: number | null; status: string | null }[];
}

export interface OtherResult extends ItemBase {
  kind: 'artifact' | 'other';
}

export type ResultItem = ViolationSet | FieldResult | ProfileResult | NetworkResult | TableResult | ScalarsResult | OtherResult;

export interface ResultBundle {
  id: string;
  status: 'ok' | 'warning' | 'failed_quality' | 'error';
  summary: string;
  accuracy_class: AccuracyClass | null;
  provenance: Provenance | null;
  diagnostics: { severity: string; code: string; message: string }[];
  items: ResultItem[];
}

export interface JobRequest {
  analysis: string;
  config?: Record<string, unknown>;
  budget?: Budget | null;
}

/** Result of a dry run (`analysis.run` with `dry_run`): validity plus the PlanEstimate. */
export interface DryRun {
  valid: boolean;
  diagnostics: { severity?: string; code?: string; message: string; path?: string | null }[];
  estimate: {
    tasks?: number;
    est_seconds?: number;
    est_peak_memory_gb?: number;
    cpus?: number;
    external?: boolean;
    within_budget?: boolean;
    reasons?: string[];
    warnings?: string[];
  } | null;
}

/** The only surface the UI uses to reach a backend. Implemented by `mockClient` and `httpClient`. */
export interface ApiClient {
  listProjects(): Promise<ProjectSummary[]>;
  openProject(path: string): Promise<ProjectSummary>;
  getBoard(pid: string): Promise<Board>;
  listNets(pid: string, query?: string): Promise<NetInfo[]>;
  listAnalyses(): Promise<AnalysisInfo[]>;
  dryRun(pid: string, req: JobRequest): Promise<DryRun>;
  createJob(pid: string, req: JobRequest): Promise<JobStatus>;
  getJob(jid: string): Promise<JobStatus>;
  cancelJob(jid: string): Promise<JobStatus>;
  getResult(rid: string): Promise<ResultBundle>;
}
