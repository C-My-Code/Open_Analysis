// SPDX-License-Identifier: GPL-3.0-or-later
import type { Board, ItemId, LayerId, NetId } from '../types/board';
import { M_TO_MM } from './units';
import {
  padShapeRings,
  pathToPolylineMm,
  polygonToDeck,
  transformRing,
  type Ring,
} from './shapes';

export type PolyKind = 'zone' | 'pad' | 'via' | 'shape';

export interface PolyItem {
  id: ItemId;
  kind: PolyKind;
  net: NetId | null;
  layer: LayerId;
  refdes?: string;
  polygon: Ring[]; // deck.gl form, mm
}
export interface PathItem {
  id: ItemId;
  kind: 'track';
  net: NetId | null;
  layer: LayerId;
  width: number; // mm
  path: Ring; // mm
}
export interface DrillItem {
  id: ItemId;
  net: NetId | null;
  position: [number, number]; // mm
  radius: number; // mm
}
export interface LayerGeometry {
  polys: PolyItem[];
  paths: PathItem[];
}
export interface RenderModel {
  outline: Ring[];
  layers: Record<LayerId, LayerGeometry>;
  drills: DrillItem[];
  /** [minx, miny, maxx, maxy] in mm */
  bbox: [number, number, number, number];
}

/** Board JSON -> deck.gl-ready geometry (mm, Y up). Mirrors what the Arrow endpoints will carry. */
export function buildRenderModel(board: Board): RenderModel {
  const layers: Record<LayerId, LayerGeometry> = {};
  const lg = (id: LayerId): LayerGeometry => (layers[id] ??= { polys: [], paths: [] });
  for (const l of board.stackup.layers) if (l.kind === 'conductor') lg(l.id);

  const padstacks = new Map((board.padstacks ?? []).map((p) => [p.id, p]));

  for (const z of board.zones ?? []) {
    if (z.keepout) continue;
    (z.fills ?? []).forEach((f) =>
      f.polygons.forEach((p) =>
        lg(f.layer).polys.push({ id: z.id, kind: 'zone', net: z.net, layer: f.layer, polygon: polygonToDeck(p) }),
      ),
    );
  }
  for (const s of board.shapes ?? []) {
    lg(s.layer).polys.push({ id: s.id, kind: 'shape', net: s.net, layer: s.layer, polygon: polygonToDeck(s.polygon) });
  }
  for (const t of board.tracks ?? []) {
    lg(t.layer).paths.push({
      id: t.id,
      kind: 'track',
      net: t.net,
      layer: t.layer,
      width: t.width * M_TO_MM,
      path: pathToPolylineMm(t.path),
    });
  }
  const drills: DrillItem[] = [];
  for (const p of board.pads ?? []) {
    const ps = padstacks.get(p.padstack);
    if (!ps) continue;
    for (const pl of ps.layers) {
      if (!pl.pad) continue;
      const rings = padShapeRings(pl.pad).map((r) => transformRing(r, p.position, p.rotation));
      lg(pl.layer).polys.push({ id: p.id, kind: 'pad', net: p.net, layer: pl.layer, refdes: p.component ?? undefined, polygon: rings });
    }
    if (ps.drill) {
      drills.push({ id: p.id, net: p.net, position: [p.position[0] * M_TO_MM, p.position[1] * M_TO_MM], radius: (ps.drill.diameter / 2) * M_TO_MM });
    }
  }
  const layerIndex = (id: LayerId) => Number(id.slice(1));
  for (const v of board.vias ?? []) {
    const ps = padstacks.get(v.padstack);
    if (!ps) continue;
    const a = layerIndex(v.start_layer);
    const b = layerIndex(v.end_layer);
    for (const pl of ps.layers) {
      const li = layerIndex(pl.layer);
      if (!pl.pad || li < a || li > b) continue;
      const rings = padShapeRings(pl.pad).map((r) => transformRing(r, v.position, 0));
      lg(pl.layer).polys.push({ id: v.id, kind: 'via', net: v.net, layer: pl.layer, polygon: rings });
    }
    if (ps.drill) {
      drills.push({ id: v.id, net: v.net, position: [v.position[0] * M_TO_MM, v.position[1] * M_TO_MM], radius: (ps.drill.diameter / 2) * M_TO_MM });
    }
  }

  const outline = polygonToDeck(board.outline.polygon);
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const [x, y] of outline[0] ?? []) {
    minx = Math.min(minx, x); maxx = Math.max(maxx, x);
    miny = Math.min(miny, y); maxy = Math.max(maxy, y);
  }
  if (!isFinite(minx)) { minx = miny = 0; maxx = maxy = 1; }
  return { outline, layers, drills, bbox: [minx, miny, maxx, maxy] };
}

export function ringsBounds(rings: Ring[]): [number, number, number, number] {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  for (const r of rings) for (const [x, y] of r) {
    minx = Math.min(minx, x); maxx = Math.max(maxx, x);
    miny = Math.min(miny, y); maxy = Math.max(maxy, y);
  }
  return [minx, miny, maxx, maxy];
}
