// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Builds the UI-level `ResultBundle` from the REST pieces: the stored manifest (arrays are
 * `{"$array": ...}` refs), `.npy` array downloads, the paged violations and (non-inline) tables.
 * Array names follow oa_service.results_view._walk_arrays (`quantities[0].Z0`, `pieces[0].mesh.nodes`).
 */
import type {
  AccuracyClass, FieldGrid, NetworkResult, ProfileResult, Provenance, QualityReport, ResultBundle, ResultItem,
  TableResult, ValidityFlag, Violation,
} from '../../api/types';
import type { Npy } from './npy';
import { rasterizeTriMesh } from './rasterize';

export interface ResultSource {
  manifest(rid: string): Promise<RawManifest>;
  array(rid: string, item: string, name: string): Promise<Npy>;
  violations(rid: string, item: string): Promise<Violation[]>;
  table(rid: string, item: string): Promise<{ columns: string[]; rows: unknown[][] }>;
}

type Obj = Record<string, unknown>;
export interface RawManifest extends Obj {
  id: string;
  status: ResultBundle['status'];
  summary: string;
  items: { id: string; kind: string; title: string; data: Obj }[];
}

const str = (x: unknown): string | null => (typeof x === 'string' ? x : null);
const num = (x: unknown): number | null => (typeof x === 'number' ? x : null);
const arr = (x: unknown): unknown[] => (Array.isArray(x) ? x : []);
const obj = (x: unknown): Obj => (x && typeof x === 'object' ? (x as Obj) : {});

function flags(d: Obj): ValidityFlag[] {
  return arr(d.validity_flags).map((f) => {
    const o = obj(f);
    return { code: str(o.code) ?? '', ok: o.ok !== false, message: str(o.message) ?? '' };
  });
}

function provenance(p: unknown): Provenance | null {
  const o = obj(p);
  if (!str(o.analysis)) return null;
  return {
    analysis: str(o.analysis)!, analysis_version: str(o.analysis_version) ?? '', board_hash: str(o.board_hash) ?? '',
    setup_hash: str(o.setup_hash) ?? '', physics_hash: str(o.physics_hash), inputs_hash: str(o.inputs_hash),
    solver: str(o.solver), solver_version: str(o.solver_version), config: obj(o.config),
  };
}

export async function buildBundle(src: ResultSource, rid: string): Promise<ResultBundle> {
  const m = await src.manifest(rid);
  const items: ResultItem[] = [];
  for (const it of m.items) items.push(await buildItem(src, rid, it));
  return {
    id: m.id,
    status: m.status,
    summary: m.summary,
    accuracy_class: (str(m.accuracy_class) as AccuracyClass | null) ?? null,
    provenance: provenance(m.provenance),
    diagnostics: arr(m.diagnostics).map((d) => {
      const o = obj(d);
      return { severity: str(o.severity) ?? 'info', code: str(o.code) ?? '', message: str(o.message) ?? '' };
    }),
    items,
  };
}

async function buildItem(src: ResultSource, rid: string, it: RawManifest['items'][number]): Promise<ResultItem> {
  const d = it.data;
  const base = {
    id: it.id, title: it.title,
    accuracy_class: (str(d.accuracy_class) as AccuracyClass | null) ?? null,
    validity_flags: flags(d),
  };
  switch (it.kind) {
    case 'violations':
      return { ...base, kind: 'violations', rules_run: arr(d.rules_run).map(String), violations: await src.violations(rid, it.id) };
    case 'profile': {
      const s = (await src.array(rid, it.id, 's')).data;
      const quantities = await Promise.all(
        arr(d.quantities).map(async (q, i) => {
          const o = obj(q);
          const name = str(o.name) ?? `q${i}`;
          const v = await src.array(rid, it.id, `quantities[${i}].${name}`);
          return { name, unit: str(o.unit) ?? '', values: Array.from(v.data) };
        }),
      );
      const net = Array.isArray(d.net) ? ([String(d.net[0]), String(d.net[1])] as [string, string]) : String(d.net);
      const out: ProfileResult = {
        ...base, kind: 'profile', net, s: Array.from(s), quantities,
        events: arr(d.events).map((e) => {
          const o = obj(e);
          return { kind: str(o.kind) ?? 'other', s: num(o.s) ?? 0, item: str(o.item), message: str(o.message) ?? '' };
        }),
        target: num(d.target), tolerance: num(d.tolerance),
      };
      return out;
    }
    case 'field': {
      const fields: FieldGrid[] = [];
      for (const [pi, p] of arr(d.pieces).entries()) {
        const piece = obj(p);
        const mesh = obj(piece.mesh);
        const layer = str(mesh.layer);
        const net = str(piece.net);
        let nodes: Npy | null = null;
        let tris: Npy | null = null;
        const isRaster = typeof mesh.nx === 'number';
        if (!isRaster) {
          nodes = await src.array(rid, it.id, `pieces[${pi}].mesh.nodes`);
          tris = await src.array(rid, it.id, `pieces[${pi}].mesh.triangles`);
        }
        for (const [fi, f] of arr(piece.fields).entries()) {
          const fo = obj(f);
          const name = str(fo.name) ?? `f${fi}`;
          const v = await src.array(rid, it.id, `pieces[${pi}].fields[${fi}].${name}`);
          const [lo, hi] = arr(fo.clim_hint).length === 2 ? (fo.clim_hint as [number, number]) : [NaN, NaN];
          const common = {
            name, unit: str(fo.unit) ?? '', layer, net,
            clim_hint: Number.isFinite(lo) ? ([lo, hi] as [number, number]) : null, log_scale: fo.log_scale === true,
          };
          // Vector fields (n, k) are shown as their magnitude.
          const k = v.shape.length > 1 ? v.shape[1]! : 1;
          const scalar = k === 1 ? v.data : magnitude(v.data, k);
          if (isRaster) {
            const o = arr(mesh.origin);
            fields.push({
              ...common, origin: [Number(o[0]), Number(o[1])], dx: Number(mesh.dx), dy: Number(mesh.dy),
              nx: Number(mesh.nx), ny: Number(mesh.ny), values: Array.from(scalar),
            });
          } else {
            const g = rasterizeTriMesh(nodes!.data, tris!.data, scalar, fo.location === 'cell' ? 'cell' : 'node');
            fields.push({ ...common, ...g });
          }
        }
      }
      return { ...base, kind: 'field', fields };
    }
    case 'network': {
      const freqs = Array.from((await src.array(rid, it.id, 'freqs')).data);
      const s = await src.array(rid, it.id, 's');
      const s_re: number[] = [], s_im: number[] = [];
      for (let i = 0; i < s.data.length; i += 2) { s_re.push(s.data[i]!); s_im.push(s.data[i + 1]!); }
      const q = obj(d.quality);
      const quality: QualityReport | null = str(q.verdict)
        ? {
            verdict: q.verdict as QualityReport['verdict'], pqm: num(q.pqm) ?? NaN, rqm: num(q.rqm) ?? NaN, cqm: num(q.cqm) ?? NaN,
            max_singular_value: num(q.max_singular_value) ?? NaN, reciprocity_max_err: num(q.reciprocity_max_err) ?? NaN,
            dc: str(q.dc) ?? '', f_min: num(q.f_min) ?? 0, f_max: num(q.f_max) ?? 0, n_freqs: num(q.n_freqs) ?? freqs.length,
            fixes_applied: arr(q.fixes_applied).map(String), notes: arr(q.notes).map(String),
          }
        : null;
      const out: NetworkResult = { ...base, kind: 'network', freqs, port_names: arr(d.port_names).map(String), s_re, s_im, quality };
      return out;
    }
    case 'table': {
      const cols = arr(d.columns).map((c) => ({ name: str(obj(c).name) ?? '', unit: str(obj(c).unit) }));
      let rows = d.inline as unknown[][] | null | undefined;
      if (!Array.isArray(rows)) rows = (await src.table(rid, it.id)).rows;
      const out: TableResult = { ...base, kind: 'table', columns: cols, rows };
      return out;
    }
    case 'scalars':
      return {
        ...base, kind: 'scalars',
        values: arr(d.values).map((v) => {
          const o = obj(v);
          return { name: str(o.name) ?? '', value: num(o.value) ?? NaN, unit: str(o.unit) ?? '', limit: num(o.limit), status: str(o.status) };
        }),
      };
    default:
      return { ...base, kind: it.kind === 'artifact' ? 'artifact' : 'other' };
  }
}

function magnitude(data: Float64Array, k: number): Float64Array {
  const n = data.length / k;
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let s = 0;
    for (let c = 0; c < k; c++) s += data[i * k + c]! ** 2;
    out[i] = Math.sqrt(s);
  }
  return out;
}
