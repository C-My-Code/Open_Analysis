// SPDX-License-Identifier: GPL-3.0-or-later
import sample from '../fixtures/sample_board.oab.json';
import type { Board } from '../types/board';
import { buildIndex } from '../geometry/boardIndex';
import type { ApiClient, FieldResult, ProfileResult, ResultBundle, Violation } from '../api/types';

const PROJECTS = [
  { id: 'sample_board', name: 'sample_board', board_hash: 'sha256:fixture', diagnostics: [] },
];

function boards(pid: string): Board {
  if (pid !== 'sample_board') throw new Error(`NOT_FOUND: project ${pid}`);
  return sample as unknown as Board;
}

const delay = <T>(v: T): Promise<T> => Promise.resolve(v);

function ercResult(): ResultBundle {
  const v = (
    rule_id: string,
    severity: Violation['severity'],
    message: string,
    nets: string[],
    location: [number, number],
    items: string[],
  ): Violation => ({
    rule_id,
    severity,
    message,
    nets,
    layers: ['L1'],
    items,
    location,
    fingerprint: `${rule_id}:${items.join(',')}`,
  });
  return {
    id: 'res_erc_1',
    status: 'warning',
    summary: '3 violations',
    items: [
      {
        kind: 'violations',
        rules_run: ['DP-001', 'PD-001', 'IM-001'],
        violations: [
          v('DP-001', 'warning', 'Diff pair skew exceeds limit', ['USB_DP', 'USB_DN'], [0.011, 0.015], ['trk:usb_dp', 'trk:usb_dn']),
          v('PD-001', 'error', 'No decoupling capacitor near power pin', ['+1V0'], [0.0215, 0.0153], ['pad:U1.3']),
          v('IM-001', 'info', 'Impedance 52 ohm vs 50 ohm target', ['SIG_A'], [0.026, 0.0115], ['trk:sig_a']),
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
    kind: 'field', layer: 'L3', name: 'V', unit: 'V', origin: [x0, y0], dx, dy, nx, ny, values, clim_hint: [0.95, 1.0],
  };
  const s = Array.from({ length: 30 }, (_, i) => (i * 0.0145) / 29);
  const profile: ProfileResult = {
    kind: 'profile', net: ['USB_DP', 'USB_DN'], s, name: 'Zdiff', unit: 'ohm',
    values: s.map((x) => 90 + 4 * Math.sin(x * 400)), target: 90, tolerance: 0.1,
  };
  return { id: 'res_pi_1', status: 'ok', summary: 'pi.dc and profile (mock)', items: [field, profile] };
}

const results: Record<string, ResultBundle> = { res_erc_1: ercResult(), res_pi_1: piResult() };
const jobs: Record<string, string> = { erc: 'res_erc_1', 'pi.dc': 'res_pi_1' };

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
        return { id: n.id, kind: n.kind, net_class: n.net_class ?? null, n_items: s?.n_items ?? 0, length: (s?.length_mm ?? 0) * 1e-3, layers: s?.layers ?? [] };
      });
  },
  listAnalyses: async () => [
    { kind: 'erc', title: 'ERC / EMI rules' },
    { kind: 'pi.dc', title: 'DC IR drop' },
  ],
  createJob: async (_pid, analysis) => {
    const rid = jobs[analysis];
    if (!rid) throw new Error(`SETUP_INVALID: unknown analysis ${analysis}`);
    return { id: `job_${analysis}`, state: 'done', analysis, result_id: rid };
  },
  getResult: async (rid) => {
    const r = results[rid];
    if (!r) throw new Error(`NOT_FOUND: result ${rid}`);
    return r;
  },
};
