// SPDX-License-Identifier: GPL-3.0-or-later
import sample from '../fixtures/sample_board.oab.json';
import analyses from '../fixtures/analyses.json';
import type { Board } from '../types/board';
import { buildIndex } from '../geometry/boardIndex';
import type {
  AnalysisInfo, ApiClient, FieldResult, JobStatus, NetworkResult, ProfileResult, ProjectSummary, ResultBundle, Violation,
} from '../api/types';

const PROJECTS: ProjectSummary[] = [
  {
    id: 'sample_board', name: 'sample_board', board_hash: 'sha256:fixture', diagnostics: [], ephemeral: true,
    project_path: null, board_path: 'sample_board.oab.json', source_tool: 'fixture',
  },
];

function boards(pid: string): Board {
  if (pid !== 'sample_board') throw new Error(`NOT_FOUND: project ${pid}`);
  return sample as unknown as Board;
}

const delay = <T>(v: T): Promise<T> => Promise.resolve(v);

const PROV = (analysis: string) => ({
  analysis, analysis_version: '1', board_hash: 'sha256:fixture', setup_hash: 'sha256:mock-setup',
  physics_hash: null, inputs_hash: null, solver: null, solver_version: null, config: {},
});

function ercResult(): ResultBundle {
  const v = (
    rule_id: string,
    severity: Violation['severity'],
    message: string,
    nets: string[],
    location_m: [number, number],
    items: string[],
    fix: string,
    extra: Partial<Violation> = {},
  ): Violation => ({
    rule_id, severity, message, nets, layers: ['L1'], items, location_m, fingerprint: `${rule_id}:${items.join(',')}`,
    waived: false, fix_hint: fix, remediation: { hint: fix, actions: [] }, ...extra,
  });
  return {
    id: 'res_erc_1',
    status: 'warning',
    summary: '3 violations',
    accuracy_class: 'screening',
    provenance: PROV('erc'),
    diagnostics: [],
    items: [
      {
        id: 'violations', title: 'ERC/EMI violations', kind: 'violations', accuracy_class: 'screening',
        validity_flags: [{ code: 'accuracy', ok: true, message: 'screening: geometry rules with documented default thresholds' }],
        rules_run: ['DP-001', 'PD-001', 'IM-001'],
        violations: [
          v('DP-001', 'warning', 'Diff pair skew exceeds limit', ['USB_DP', 'USB_DN'], [0.011, 0.015], ['trk:usb_dp', 'trk:usb_dn'],
            'Add serpentine tuning to the shorter leg.', { measured: 2.1e-3, limit: 1e-3, unit: 'm' }),
          v('PD-001', 'error', 'No decoupling capacitor near power pin', ['+1V0'], [0.0215, 0.0153], ['pad:U1.3'],
            'Place a 100 nF capacitor within 2 mm of the pin.'),
          v('IM-001', 'info', 'Impedance 52 ohm vs 50 ohm target', ['SIG_A'], [0.026, 0.0115], ['trk:sig_a'],
            'Narrow the trace by 0.02 mm.', { measured: 52, limit: 50, unit: 'ohm' }),
        ],
      },
    ],
  };
}

function piResult(): ResultBundle {
  const nx = 40, ny = 22, x0 = 0.018, y0 = 0.016, dx = 0.0005, dy = 0.0005;
  const values: number[] = [];
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    // IR-drop-like field: falls off from the via array at (0.0225, 0.019).
    const r = Math.hypot(x0 + i * dx - 0.0225, y0 + j * dy - 0.019);
    values.push(1.0 - 0.05 * (1 - Math.exp(-r / 0.006)));
  }
  const field: FieldResult = {
    id: 'field', title: 'Rail voltage', kind: 'field', accuracy_class: 'validated', validity_flags: [],
    fields: [{ name: 'V', unit: 'V', layer: 'L3', net: '+1V0', origin: [x0, y0], dx, dy, nx, ny, values, clim_hint: [0.95, 1.0], log_scale: false }],
  };
  const s = Array.from({ length: 30 }, (_, i) => (i * 0.0145) / 29);
  const profile: ProfileResult = {
    id: 'profile:USB', title: 'USB Zdiff', kind: 'profile', accuracy_class: 'screening', validity_flags: [],
    net: ['USB_DP', 'USB_DN'], s,
    quantities: [{ name: 'Zdiff', unit: 'ohm', values: s.map((x) => 90 + 4 * Math.sin(x * 400)) }],
    events: [{ kind: 'via', s: 0.006, item: 'via:1v0_b', message: 'layer change' }, { kind: 'neckdown', s: 0.011, item: 'trk:usb_dp', message: 'neck-down' }],
    target: 90, tolerance: 0.1,
  };
  return {
    id: 'res_pi_1', status: 'ok', summary: 'pi.dc and profile (mock)', accuracy_class: 'validated', provenance: PROV('pi.dc'),
    diagnostics: [], items: [field, profile, sparamsItem(), {
      id: 'loads', title: 'Load table', kind: 'table', accuracy_class: 'validated', validity_flags: [],
      columns: [{ name: 'load', unit: null }, { name: 'v_load', unit: 'V' }, { name: 'drop', unit: 'V' }],
      rows: [['U1', 0.9712, 0.0288]],
    }],
  };
}

function sparamsItem(): NetworkResult {
  const freqs = Array.from({ length: 41 }, (_, i) => i * 1e8);
  const s_re: number[] = [], s_im: number[] = [];
  for (const f of freqs) {
    // Matched-ish 2-port: |S11| rises, |S21| rolls off with frequency.
    const g = 0.02 + f / 4e10, t = Math.sqrt(1 - g * g) * Math.exp(-f / 3e10);
    const ph = -2 * Math.PI * f * 3e-10;
    s_re.push(g, t * Math.cos(ph), t * Math.cos(ph), g);
    s_im.push(0, t * Math.sin(ph), t * Math.sin(ph), 0);
  }
  return {
    id: 'sparams', title: 'S-parameters', kind: 'network', accuracy_class: 'validated', validity_flags: [],
    freqs, port_names: ['P1', 'P2'], s_re, s_im,
    quality: { verdict: 'pass', pqm: 99.9, rqm: 99.9, cqm: 92, max_singular_value: 0.9999, reciprocity_max_err: 1e-9, dc: 'extrapolated', f_min: 0, f_max: 4e9, n_freqs: 41, fixes_applied: [], notes: [] },
  };
}

const results: Record<string, ResultBundle> = { res_erc_1: ercResult(), res_pi_1: piResult() };
const resultOf: Record<string, string> = { erc: 'res_erc_1', 'pi.dc': 'res_pi_1', 'impedance.profile': 'res_pi_1' };
const jobsById = new Map<string, JobStatus>();

const mkJob = (analysis: string, rid: string | null, state: JobStatus['state']): JobStatus => ({
  id: `job_${analysis}`, state, analysis, setup: 'inline', progress: state === 'succeeded' ? 1 : 0, tasks_total: 1,
  tasks_done: state === 'succeeded' ? 1 : 0, tasks_cached: 0, tasks_failed: 0, message: '', result_id: rid,
  created_utc: '2026-01-01T00:00:00Z', updated_utc: '2026-01-01T00:00:00Z',
});

/** Typed fixture client: stands in for the WP-207 server (no network). */
export const mockClient: ApiClient = {
  listProjects: () => delay(PROJECTS),
  openProject: async (path) => {
    const name = path.split(/[\\/]/).pop()?.replace(/\.[^.]*$/, '') ?? path;
    const p = PROJECTS.find((x) => x.id === name || x.name === name);
    if (!p) throw new Error(`NOT_FOUND: ${path}`);
    return p;
  },
  getBoard: async (pid) => boards(pid),
  listNets: async (pid, query = '') => {
    const b = boards(pid);
    const idx = buildIndex(b);
    return b.nets
      .filter((n) => n.id.toLowerCase().includes(query.toLowerCase()))
      .map((n) => {
        const s = idx.netStats.get(n.id);
        return {
          id: n.id, kind: n.kind, net_class: n.net_class ?? null, n_items: s?.n_items ?? 0, length: (s?.length_mm ?? 0) * 1e-3,
          layers: s?.layers ?? [], via_count: 0, intent_tags: [],
        };
      });
  },
  listAnalyses: async () => (analyses as unknown as AnalysisInfo[]),
  dryRun: async (_pid, req) =>
    resultOf[req.analysis]
      ? { valid: true, diagnostics: [], estimate: { tasks: 3, est_seconds: 4, est_peak_memory_gb: 0.5, within_budget: true, reasons: [], warnings: [] } }
      : { valid: false, diagnostics: [{ message: `unknown analysis ${req.analysis}` }], estimate: null },
  createJob: async (_pid, req) => {
    const rid = resultOf[req.analysis];
    if (!rid) throw new Error(`SETUP_INVALID: unknown analysis ${req.analysis}`);
    const j = mkJob(req.analysis, rid, 'succeeded');
    jobsById.set(j.id, j);
    return j;
  },
  getJob: async (jid) => {
    const j = jobsById.get(jid);
    if (!j) throw new Error(`NOT_FOUND: job ${jid}`);
    return j;
  },
  cancelJob: async (jid) => {
    const j = jobsById.get(jid);
    if (!j) throw new Error(`NOT_FOUND: job ${jid}`);
    const c = { ...j, state: 'cancelled' as const };
    jobsById.set(jid, c);
    return c;
  },
  getResult: async (rid) => {
    const r = results[rid];
    if (!r) throw new Error(`NOT_FOUND: result ${rid}`);
    return r;
  },
};
