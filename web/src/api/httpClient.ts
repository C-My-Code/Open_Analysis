// SPDX-License-Identifier: GPL-3.0-or-later
import type { Board } from '../types/board';
import type {
  AnalysisInfo, ApiClient, DryRun, JobRequest, JobStatus, NetInfo, ProjectSummary, Violation,
} from './types';
import { buildBundle, type RawManifest, type ResultSource } from '../features/results/adapt';
import { parseNpy } from '../features/results/npy';
import type { components } from './generated/schema';

type ViolationPage = components['schemas']['ViolationPage'];

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public hint?: string | null) {
    super(`${code}: ${message}`);
  }
}

/**
 * Real client for `oa serve` (WP-207 REST API, `web/src/api/openapi.json`). The bearer token comes
 * from the URL fragment `#token=...` printed by `oa serve`. Job progress is polled (`getJob`); the
 * `/events` WebSocket is not used yet.
 */
export function createHttpClient(base = '/api/v1', token = '', fetchImpl: typeof fetch = (...a) => fetch(...a)): ApiClient {
  const raw = async (path: string, init?: RequestInit): Promise<Response> => {
    const res = await fetchImpl(base + path, {
      ...init,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
    if (!res.ok) {
      let e: { code?: string; message?: string; hint?: string | null } = {};
      try {
        e = ((await res.json()) as { error?: typeof e }).error ?? {};
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, e.code ?? 'FAILED', e.message ?? res.statusText, e.hint);
    }
    return res;
  };
  const call = async <T>(path: string, init?: RequestInit): Promise<T> => (await raw(path, init)).json() as Promise<T>;
  const post = <T>(path: string, body: unknown) => call<T>(path, { method: 'POST', body: JSON.stringify(body) });

  const source: ResultSource = {
    manifest: (rid) => call<RawManifest>(`/results/${rid}`),
    array: async (rid, item, name) =>
      parseNpy(await (await raw(`/results/${rid}/items/${encodeURIComponent(item)}/arrays/${encodeURIComponent(name)}`)).arrayBuffer()),
    violations: async (rid, item) => {
      const out: Violation[] = [];
      for (let offset: number | null = 0; offset !== null; ) {
        const q: string = `item_id=${encodeURIComponent(item)}&fields=full&include_waived=true&limit=500&offset=${offset}`;
        const page: ViolationPage = await call<ViolationPage>(`/results/${rid}/violations?${q}`);
        out.push(...(page.items as Violation[]));
        offset = page.next_offset ?? null;
      }
      return out;
    },
    table: async (rid, item) => {
      const { tableFromIPC } = await import('apache-arrow');
      const t = tableFromIPC(new Uint8Array(await (await raw(`/results/${rid}/items/${encodeURIComponent(item)}/table?limit=2000`)).arrayBuffer()));
      const columns = t.schema.fields.map((f) => f.name);
      const rows = t.toArray().map((r) => columns.map((c) => (r as Record<string, unknown>)[c]));
      return { columns, rows };
    },
  };

  const jobBody = (r: JobRequest) => ({ analysis: r.analysis, config: r.config ?? {}, budget: r.budget ?? null, wait_s: 2 });

  return {
    listProjects: () => call<ProjectSummary[]>('/projects'),
    openProject: async (path) => {
      const known = (await call<ProjectSummary[]>('/projects')).find((p) => p.id === path || p.name === path);
      return known ?? post<ProjectSummary>('/projects/open', { path });
    },
    getBoard: (pid) => call<Board>(`/projects/${pid}/board/full`),
    listNets: async (pid, q = '') => {
      const nets = await call<NetInfo[]>(`/projects/${pid}/board/nets?limit=500`);
      return nets.filter((n) => n.id.toLowerCase().includes(q.toLowerCase()));
    },
    listAnalyses: () => call<AnalysisInfo[]>('/analyses'),
    dryRun: async (pid, r) => {
      const env = await post<{ ok: boolean; data: DryRun | null; errors: { message: string; path?: string | null; code?: string }[] }>(
        '/commands/analysis.run', { args: { project_id: pid, analysis: r.analysis, config: r.config ?? {}, budget: r.budget ?? null, dry_run: true } },
      );
      if (!env.data) return { valid: false, diagnostics: env.errors, estimate: null };
      return { valid: env.data.valid, diagnostics: env.data.diagnostics ?? [], estimate: env.data.estimate ?? null };
    },
    createJob: (pid, r) => post<JobStatus>(`/projects/${pid}/jobs`, jobBody(r)),
    getJob: (jid) => call<JobStatus>(`/jobs/${jid}`),
    cancelJob: (jid) => post<JobStatus>(`/jobs/${jid}/cancel`, {}),
    getResult: (rid) => buildBundle(source, rid),
  };
}
