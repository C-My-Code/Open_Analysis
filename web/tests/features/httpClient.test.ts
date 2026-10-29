// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import { http, HttpResponse } from 'msw';
import { createHttpClient, ApiError } from '../../src/api/httpClient';
import { parseNpy } from '../../src/features/results/npy';
import { finiteRange, rasterizeTriMesh } from '../../src/features/results/rasterize';
import { BASE, npyC16, npyF8, npyI8, server } from './msw';

const client = () => createHttpClient(BASE, 'tok');

describe('npy + rasterize', () => {
  it('parses f8, i8 and complex arrays', () => {
    expect(Array.from(parseNpy(npyF8([1, 2, 3])).data)).toEqual([1, 2, 3]);
    const i = parseNpy(npyI8([0, 1, 2, 1, 3, 2], [2, 3]));
    expect(i.shape).toEqual([2, 3]);
    expect(Array.from(i.data)).toEqual([0, 1, 2, 1, 3, 2]);
    const c = parseNpy(npyC16([1, 2, 3, 4], [2]));
    expect(c.complex).toBe(true);
    expect(Array.from(c.data)).toEqual([1, 2, 3, 4]);
  });

  it('interpolates node values over triangles and leaves uncovered cells NaN', () => {
    const nodes = new Float64Array([0, 0, 1, 0, 0, 1]); // one triangle, lower-left half of the unit square
    const g = rasterizeTriMesh(nodes, new Float64Array([0, 1, 2]), new Float64Array([1, 0.9, 0.9]), 'node', 10);
    const at = (i: number, j: number) => g.values[j * g.nx + i]!;
    expect(at(0, 0)).toBeCloseTo(1 - 0.1 * 0.1, 1); // near the origin
    expect(Number.isNaN(at(g.nx - 1, g.ny - 1))).toBe(true); // far corner is outside the triangle
  });
});

describe('http client against the MSW server', () => {
  it('lists analyses with config schemas and projects', async () => {
    const a = await client().listAnalyses();
    expect(a.map((x) => x.kind).sort()).toEqual(['erc', 'impedance.profile', 'impedance.xsection', 'pi.dc']);
    expect((await client().listProjects())[0]!.id).toBe('p1');
  });

  it('builds a profile bundle: arrays, events, inline table, accuracy and provenance', async () => {
    const b = await client().getResult('r_profile');
    expect(b.status).toBe('ok');
    expect(b.provenance?.analysis).toBe('r_profile');
    const p = b.items[0]!;
    expect(p.kind).toBe('profile');
    if (p.kind !== 'profile') return;
    expect(p.accuracy_class).toBe('screening');
    expect(p.s).toEqual([0, 0.0025, 0.005]);
    expect(p.quantities[0]).toMatchObject({ name: 'Z0', unit: 'ohm', values: [49, 50, 51] });
    expect(p.events).toEqual([{ kind: 'via', s: 0.002, item: 'via:1', message: 'layer change' }]);
    expect(b.items[1]).toMatchObject({ kind: 'table', rows: [['MS_250', 50.2]] });
  });

  it('rasterises a TriMesh2D field piece to a grid', async () => {
    const b = await client().getResult('r_field');
    const f = b.items[0]!;
    if (f.kind !== 'field') throw new Error('expected field');
    expect(f.fields).toHaveLength(1);
    const g = f.fields[0]!;
    expect(g).toMatchObject({ name: 'V', unit: 'V', layer: 'L3', net: '+1V0' });
    const finite = g.values.filter(Number.isFinite);
    const [lo, hi] = finiteRange(g.values);
    expect(finite.length).toBeGreaterThan(g.values.length * 0.9);
    expect(lo).toBeGreaterThanOrEqual(0.8 - 1e-9);
    expect(hi).toBeLessThanOrEqual(1 + 1e-9);
  });

  it('decodes complex S-parameters and the quality report of a failed_quality result', async () => {
    const b = await client().getResult('r_net');
    expect(b.status).toBe('failed_quality');
    const n = b.items[0]!;
    if (n.kind !== 'network') throw new Error('expected network');
    expect(n.freqs).toEqual([1e9, 2e9]);
    expect(n.s_re.length).toBe(8);
    expect(n.s_im[5]).toBe(0.5);
    expect(n.quality?.verdict).toBe('fail');
    expect(n.quality?.notes).toEqual(['passivity violated']);
  });

  it('pages violations (fields=full) and keeps remediation and waived', async () => {
    const b = await client().getResult('r_erc');
    const v = b.items[0]!;
    if (v.kind !== 'violations') throw new Error('expected violations');
    expect(v.violations.map((x) => x.fingerprint)).toEqual(['fp0', 'fp1', 'fp2']);
    expect(v.violations[2]!.waived).toBe(true);
    expect((v.violations[0]!.remediation as { hint: string }).hint).toBe('Move the track away.');
  });

  it('creates a job (state "succeeded") and maps API errors to ApiError', async () => {
    const j = await client().createJob('p1', { analysis: 'erc', config: {} });
    expect(j.state).toBe('succeeded');
    expect(j.result_id).toBe('r_erc');
    const err = await client().createJob('p1', { analysis: 'erc', budget: { allow_external: true, max_runtime_s: 1 } }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe('BUDGET_EXCEEDED');
    expect((err as ApiError).status).toBe(409);
  });

  it('dry-runs through the command endpoint', async () => {
    const d = await client().dryRun('p1', { analysis: 'erc' });
    expect(d.valid).toBe(true);
    expect(d.estimate?.tasks).toBe(21);
  });

  it('sends the bearer token', async () => {
    let auth = '';
    server.use(http.get(`${BASE}/projects`, ({ request }) => { auth = request.headers.get('authorization') ?? ''; return HttpResponse.json([]); }));
    await client().listProjects();
    expect(auth).toBe('Bearer tok');
  });
});
