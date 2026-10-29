// SPDX-License-Identifier: GPL-3.0-or-later
import { ringsBounds } from '../geometry/render';
import type { AppState, Ref, ViewState } from './store';
import type { ViolationSet } from '../api/types';

type Bounds = [number, number, number, number];

export function allViolations(s: AppState) {
  return Object.values(s.results).flatMap((r) =>
    r.items.filter((i): i is ViolationSet => i.kind === 'violations').flatMap((i) => i.violations),
  );
}

export function refExists(s: AppState, r: Ref): boolean {
  switch (r.kind) {
    case 'net': return !!s.index?.nets.has(r.id);
    case 'refdes': return !!s.index?.components.has(r.id);
    case 'item': return !!s.index?.items.has(r.id);
    case 'layer': return !!s.layerStyle[r.id];
    case 'rule': return allViolations(s).some((v) => v.rule_id === r.id);
  }
}

function union(bs: Bounds[]): Bounds | null {
  if (bs.length === 0) return null;
  return bs.reduce<Bounds>(
    (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])],
    [Infinity, Infinity, -Infinity, -Infinity],
  );
}

/** Bounds in mm of whatever a reference points at. */
export function refBounds(s: AppState, r: Ref): Bounds | null {
  if (!s.render) return null;
  const bs: Bounds[] = [];
  const match = (id: string, net: string | null) =>
    r.kind === 'item' ? id === r.id : r.kind === 'net' ? net === r.id : false;
  if (r.kind === 'item' || r.kind === 'net') {
    for (const lg of Object.values(s.render.layers)) {
      for (const p of lg.polys) if (match(p.id, p.net)) bs.push(ringsBounds(p.polygon));
      for (const p of lg.paths) if (match(p.id, p.net)) bs.push(ringsBounds([p.path]));
    }
  } else if (r.kind === 'refdes') {
    const c = s.index?.components.get(r.id);
    const ids = new Set(c?.pads ?? []);
    for (const lg of Object.values(s.render.layers)) for (const p of lg.polys) if (ids.has(p.id)) bs.push(ringsBounds(p.polygon));
  } else if (r.kind === 'rule') {
    for (const v of allViolations(s)) if (v.rule_id === r.id) bs.push([v.location_m[0]! * 1e3, v.location_m[1]! * 1e3, v.location_m[0]! * 1e3, v.location_m[1]! * 1e3]);
  }
  return union(bs);
}

export function fitBounds(b: Bounds, viewport: { w: number; h: number }, pad = 0.85, minSpanMm = 2): ViewState {
  const w = Math.max(b[2] - b[0], minSpanMm);
  const h = Math.max(b[3] - b[1], minSpanMm);
  const scale = Math.min(viewport.w / w, viewport.h / h) * pad; // px per mm
  return { target: [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 0], zoom: Math.log2(scale) };
}
