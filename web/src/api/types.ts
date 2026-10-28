// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Hand-written API DTOs following docs/specs/interfaces.md section 7 and 4.1.
 * SCHEMA SWITCH POINT: replace with types generated from `web/src/api/openapi.json` (WP-207) via
 * openapi-typescript; `ApiClient` below is the only seam the app depends on.
 */
import type { Board, Diagnostic, LayerId, NetId, Vec2 } from '../types/board';

export interface ProjectSummary {
  id: string;
  name: string;
  board_hash: string;
  diagnostics: Diagnostic[];
}

export interface NetInfo {
  id: NetId;
  kind: string;
  net_class: string | null;
  n_items: number;
  length: number; // m
  layers: LayerId[];
}

export type Severity = 'info' | 'warning' | 'error';

export interface Violation {
  rule_id: string;
  severity: Severity;
  message: string;
  nets: NetId[];
  layers: LayerId[];
  items: string[];
  location: Vec2; // metres, board frame
  measured?: number | null;
  limit?: number | null;
  unit?: string | null;
  fix_hint?: string | null;
  fingerprint: string;
}

export interface ViolationSet {
  kind: 'violations';
  rules_run: string[];
  violations: Violation[];
}

/** RasterGrid2D + one scalar field (interfaces.md 4.1 FieldPiece, simplified to one field). */
export interface FieldResult {
  kind: 'field';
  layer: LayerId;
  name: string;
  unit: string;
  origin: Vec2; // metres
  dx: number;
  dy: number;
  nx: number;
  ny: number;
  values: number[]; // row-major, row 0 = lowest y
  clim_hint?: [number, number];
}

export interface ProfileResult {
  kind: 'profile';
  net: NetId | [NetId, NetId];
  s: number[]; // m
  name: string;
  unit: string;
  values: number[];
  target?: number;
  tolerance?: number;
}

export type ResultItem = ViolationSet | FieldResult | ProfileResult;

export interface ResultBundle {
  id: string;
  status: 'ok' | 'warning' | 'failed_quality' | 'error';
  summary: string;
  items: ResultItem[];
}

export interface JobStatus {
  id: string;
  state: 'queued' | 'running' | 'done' | 'failed';
  analysis: string;
  result_id: string | null;
}

export interface AnalysisInfo {
  kind: string;
  title: string;
}

/** The only surface the UI uses to reach a backend. Implemented by `mockClient` and `httpClient`. */
export interface ApiClient {
  listProjects(): Promise<ProjectSummary[]>;
  openProject(path: string): Promise<ProjectSummary>;
  getBoard(pid: string): Promise<Board>;
  listNets(pid: string, query?: string): Promise<NetInfo[]>;
  listAnalyses(): Promise<AnalysisInfo[]>;
  createJob(pid: string, analysis: string): Promise<JobStatus>;
  getResult(rid: string): Promise<ResultBundle>;
}
