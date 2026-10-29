// SPDX-License-Identifier: GPL-3.0-or-later
/** MSW mock of the `oa serve` REST subset the UI uses (shapes copied from a real server run). */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import analyses from '../../src/fixtures/analyses.json';

export const BASE = 'http://oa.test/api/v1';

function npy(descr: string, shape: number[], values: number[] | BigInt64Array | Float64Array): ArrayBuffer {
  const dict = `{'descr': '${descr}', 'fortran_order': False, 'shape': (${shape.join(', ')}${shape.length === 1 ? ',' : ''}), }`;
  let header = dict;
  while ((10 + header.length + 1) % 64 !== 0) header += ' ';
  header += '\n';
  const itemsize = descr === '<c16' ? 16 : descr === '<f4' ? 4 : 8;
  const count = shape.reduce((a, b) => a * b, 1);
  const buf = new ArrayBuffer(10 + header.length + count * itemsize);
  const dv = new DataView(buf);
  new Uint8Array(buf).set([0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59, 1, 0]);
  dv.setUint16(8, header.length, true);
  new Uint8Array(buf).set(new TextEncoder().encode(header), 10);
  const off = 10 + header.length;
  const arr = Array.from(values as ArrayLike<number | bigint>);
  arr.forEach((v, i) => {
    if (descr === '<i8') dv.setBigInt64(off + i * 8, BigInt(v as number), true);
    else if (descr === '<f4') dv.setFloat32(off + i * 4, Number(v), true);
    else dv.setFloat64(off + i * 8, Number(v), true);
  });
  return buf;
}
export const npyF8 = (values: number[], shape = [values.length]) => npy('<f8', shape, values);
export const npyC16 = (interleaved: number[], shape: number[]) => npy('<c16', shape, interleaved);
export const npyI8 = (values: number[], shape: number[]) => npy('<i8', shape, values);

const prov = {
  analysis: 'x', analysis_version: '1', board_hash: 'sha256:abc', setup_hash: 'sha256:def', physics_hash: null,
  inputs_hash: null, solver: null, solver_version: null, config: {},
};
const flag = [{ code: 'accuracy', ok: true, message: 'screening' }];

const profileItem = {
  id: 'profile:MS_250', kind: 'profile', title: 'MS_250',
  data: {
    kind: 'profile', accuracy_class: 'screening', validity_flags: flag, net: 'MS_250', target: 50, tolerance: 0.1,
    s: { $array: 'a/s.npy', type: 'Floats' },
    quantities: [{ name: 'Z0', unit: 'ohm', location: 'node', values: { $array: 'a/z.npy', type: 'NDArray' } }],
    events: [{ kind: 'via', s: 0.002, item: 'via:1', message: 'layer change' }],
  },
};
const fieldItem = {
  id: 'field', kind: 'field', title: 'Rail field',
  data: {
    kind: 'field', accuracy_class: 'validated', validity_flags: [],
    pieces: [{
      net: '+1V0',
      mesh: { layer: 'L3', nodes: { $array: 'a/n.npy', type: 'Points' }, triangles: { $array: 'a/t.npy', type: 'NDArray' } },
      fields: [{ name: 'V', unit: 'V', location: 'node', clim_hint: null, log_scale: false, values: { $array: 'a/v.npy', type: 'NDArray' } }],
    }],
  },
};
const networkItem = {
  id: 'sparams', kind: 'network', title: 'S-parameters',
  data: {
    kind: 'network', accuracy_class: 'validated', validity_flags: [], port_names: ['P1', 'P2'],
    freqs: { $array: 'a/f.npy', type: 'Floats' }, s: { $array: 'a/sp.npy', type: 'NDArray' },
    quality: {
      verdict: 'fail', pqm: 90, rqm: 99, cqm: 70, max_singular_value: 1.2, reciprocity_max_err: 1e-3, dc: 'missing', f_min: 0, f_max: 2e9,
      n_freqs: 2, fixes_applied: [], notes: ['passivity violated'],
    },
  },
};
const tableItem = {
  id: 'deviation', kind: 'table', title: 'deviation',
  data: { kind: 'table', accuracy_class: 'screening', validity_flags: [], columns: [{ name: 'net', unit: null, dtype: 'str' }, { name: 'z_mean', unit: 'ohm', dtype: 'f8' }], inline: [['MS_250', 50.2]] },
};
const violItem = { id: 'violations', kind: 'violations', title: 'ERC/EMI violations', data: { kind: 'violations', accuracy_class: 'screening', validity_flags: flag, rules_run: ['RP-001'], violations: [] } };

const manifest = (id: string, status: string, items: unknown[]) => ({
  id, status, summary: `summary ${id}`, accuracy_class: null, diagnostics: [], provenance: { ...prov, analysis: id }, items,
});

const arrays: Record<string, () => ArrayBuffer> = {
  's.npy': () => npyF8([0, 0.0025, 0.005]),
  'quantities[0].Z0': () => npyF8([49, 50, 51]),
  's': () => npyF8([0, 0.0025, 0.005]),
  'pieces[0].mesh.nodes': () => npyF8([0, 0, 1, 0, 0, 1, 1, 1], [4, 2]),
  'pieces[0].mesh.triangles': () => npyI8([0, 1, 2, 1, 3, 2], [2, 3]),
  'pieces[0].fields[0].V': () => npyF8([1, 0.9, 0.9, 0.8]),
  'freqs': () => npyF8([1e9, 2e9]),
  // S[f][i][j] for f in {1,2} GHz: S11=0.1, S21 = 0.9 and 0.5j
  'sparams:s': () => npyC16([0.1, 0, 0.9, 0, 0.9, 0, 0.1, 0, 0.1, 0, 0, 0.5, 0, 0.5, 0.1, 0], [2, 2, 2]),
};

const results: Record<string, unknown> = {
  r_profile: manifest('r_profile', 'ok', [profileItem, tableItem]),
  r_field: manifest('r_field', 'ok', [fieldItem]),
  r_net: manifest('r_net', 'failed_quality', [networkItem]),
  r_erc: manifest('r_erc', 'warning', [violItem]),
};

const violation = (i: number) => ({
  fingerprint: `fp${i}`, rule_id: 'RP-001', severity: 'warning', message: `v${i}`, nets: ['N'], layers: ['L1'], items: ['trk:1'],
  location_m: [0.001 * i, 0.002], kicad_mm: null, geometry_bbox_m: null, measured: 1.5, limit: 1, unit: 'mm',
  fix_hint: 'move it', remediation: { hint: 'Move the track away.', actions: [] }, waived: i === 2,
});

export const calls: string[] = [];

export const handlers = [
  http.get(`${BASE}/projects`, () => HttpResponse.json([{ id: 'p1', name: 'board', board_hash: 'sha256:1', diagnostics: [], ephemeral: true, project_path: null, board_path: 'b', source_tool: 'kicad' }])),
  http.get(`${BASE}/analyses`, () => HttpResponse.json(analyses)),
  http.get(`${BASE}/results/:rid`, ({ params }) => {
    const m = results[params.rid as string];
    return m ? HttpResponse.json(m) : HttpResponse.json({ error: { code: 'NOT_FOUND', message: 'no such result', hint: null } }, { status: 404 });
  }),
  http.get(`${BASE}/results/:rid/items/:item/arrays/:name`, ({ params }) => {
    const name = decodeURIComponent(params.name as string);
    const key = params.item === 'sparams' && name === 's' ? 'sparams:s' : name;
    const make = arrays[key];
    calls.push(`array ${key}`);
    return make ? new HttpResponse(make(), { headers: { 'content-type': 'application/x-npy' } }) : HttpResponse.json({ error: { code: 'NOT_FOUND', message: name } }, { status: 404 });
  }),
  http.get(`${BASE}/results/:rid/violations`, ({ request }) => {
    const u = new URL(request.url);
    const offset = Number(u.searchParams.get('offset') ?? 0);
    const limit = Number(u.searchParams.get('limit') ?? 50);
    const total = 3;
    const items = [0, 1, 2].slice(offset, offset + Math.min(limit, 2)).map(violation);
    const next = offset + items.length;
    return HttpResponse.json({ items, offset, limit, total, next_offset: next < total ? next : null, sort: 'severity' });
  }),
  http.post(`${BASE}/projects/:pid/jobs`, async ({ request }) => {
    const body = (await request.json()) as { analysis: string; budget?: { max_runtime_s?: number } };
    calls.push(`job ${body.analysis}`);
    if (body.budget?.max_runtime_s === 1) {
      return HttpResponse.json({ error: { code: 'BUDGET_EXCEEDED', message: 'estimate exceeds max_runtime_s', hint: 'raise the budget', details: {} } }, { status: 409 });
    }
    return HttpResponse.json({
      id: 'j1', state: 'succeeded', analysis: body.analysis, setup: 'inline', progress: 1, tasks_total: 2, tasks_done: 2, tasks_cached: 0,
      tasks_failed: 0, message: 'done', result_id: 'r_erc', error: null, cancel_reason: null, created_utc: 't', updated_utc: 't',
    });
  }),
  http.post(`${BASE}/commands/analysis.run`, () =>
    HttpResponse.json({ ok: true, data: { dry_run: true, valid: true, diagnostics: [], estimate: { tasks: 21, est_seconds: 21, within_budget: true }, job: null }, errors: [] })),
];

export const server = setupServer(...handlers);
