// SPDX-License-Identifier: GPL-3.0-or-later
import type { Board, Component, ItemId, Net, NetId, Pad, Track, Via, Zone, CopperShape } from '../types/board';
import { pathToPolylineMm, polylineLength } from './shapes';

export type BoardItem =
  | { kind: 'track'; obj: Track }
  | { kind: 'via'; obj: Via }
  | { kind: 'pad'; obj: Pad }
  | { kind: 'zone'; obj: Zone }
  | { kind: 'shape'; obj: CopperShape };

export interface NetStats {
  n_items: number;
  length_mm: number;
  layers: string[];
}

export interface BoardIndex {
  nets: Map<NetId, Net>;
  netStats: Map<NetId, NetStats>;
  items: Map<ItemId, BoardItem>;
  components: Map<string, Component>;
}

/** Lookup tables keyed by the stable ids used for selection and highlight (net name, refdes, item id). */
export function buildIndex(board: Board): BoardIndex {
  const nets = new Map(board.nets.map((n) => [n.id, n]));
  const items = new Map<ItemId, BoardItem>();
  const netStats = new Map<NetId, NetStats>();
  const stat = (n: NetId | null): NetStats | null => {
    if (!n) return null;
    let s = netStats.get(n);
    if (!s) netStats.set(n, (s = { n_items: 0, length_mm: 0, layers: [] }));
    return s;
  };
  const touch = (n: NetId | null, layers: string[]) => {
    const s = stat(n);
    if (!s) return;
    s.n_items++;
    for (const l of layers) if (!s.layers.includes(l)) s.layers.push(l);
  };
  for (const t of board.tracks ?? []) {
    items.set(t.id, { kind: 'track', obj: t });
    touch(t.net, [t.layer]);
    const s = stat(t.net);
    if (s) s.length_mm += polylineLength(pathToPolylineMm(t.path));
  }
  for (const v of board.vias ?? []) {
    items.set(v.id, { kind: 'via', obj: v });
    touch(v.net, [v.start_layer, v.end_layer]);
  }
  for (const p of board.pads ?? []) {
    items.set(p.id, { kind: 'pad', obj: p });
    touch(p.net, []);
  }
  for (const z of board.zones ?? []) {
    items.set(z.id, { kind: 'zone', obj: z });
    touch(z.net, z.layers);
  }
  for (const s of board.shapes ?? []) {
    items.set(s.id, { kind: 'shape', obj: s });
    touch(s.net, [s.layer]);
  }
  return { nets, netStats, items, components: new Map((board.components ?? []).map((c) => [c.refdes, c])) };
}

export function itemNet(it: BoardItem): NetId | null {
  return it.obj.net ?? null;
}
