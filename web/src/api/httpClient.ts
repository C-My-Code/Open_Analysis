// SPDX-License-Identifier: GPL-3.0-or-later
import type { Board } from '../types/board';
import type { AnalysisInfo, ApiClient, JobStatus, NetInfo, ProjectSummary, ResultBundle } from './types';

/**
 * Real client for the WP-207 server (interfaces.md section 7). Not exercised by tests yet: the
 * server does not exist. Select with `?api=http` (token from the URL fragment `#token=...`).
 * Geometry currently comes from the whole Board JSON; WP-207 may later add the Arrow endpoints,
 * at which point only `getBoard` consumers in `state/` need to change.
 */
export function createHttpClient(base = '/api/v1', token = ''): ApiClient {
  const call = async <T>(path: string, init?: RequestInit): Promise<T> => {
    const res = await fetch(base + path, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    return (await res.json()) as T;
  };
  return {
    listProjects: () => call<ProjectSummary[]>('/projects'),
    openProject: (path) => call<ProjectSummary>('/projects/open', { method: 'POST', body: JSON.stringify({ path }) }),
    getBoard: (pid) => call<Board>(`/projects/${pid}/board/full`),
    listNets: (pid, q = '') => call<NetInfo[]>(`/projects/${pid}/board/nets?query=${encodeURIComponent(q)}`),
    listAnalyses: () => call<AnalysisInfo[]>('/analyses'),
    createJob: (pid, analysis) => call<JobStatus>(`/projects/${pid}/jobs`, { method: 'POST', body: JSON.stringify({ setup: analysis }) }),
    getResult: (rid) => call<ResultBundle>(`/results/${rid}`),
  };
}
