// SPDX-License-Identifier: GPL-3.0-or-later
import type { ArcSeg, FlatPoints, PadShape, PathSeg, Polygon, Vec2 } from '../types/board';
import { M_TO_MM } from './units';

export type Ring = [number, number][];

/** Flat metres [x0,y0,...] -> ring in mm. */
export function flatToRingMm(flat: FlatPoints): Ring {
  const r: Ring = [];
  for (let i = 0; i + 1 < flat.length; i += 2) r.push([flat[i]! * M_TO_MM, flat[i + 1]! * M_TO_MM]);
  return r;
}

/** deck.gl polygon form: exterior first, then holes. */
export function polygonToDeck(p: Polygon): Ring[] {
  return [flatToRingMm(p.exterior), ...(p.holes ?? []).map(flatToRingMm)];
}

/** Shoelace area (signed, CCW positive). */
export function ringArea(r: Ring): number {
  let a = 0;
  for (let i = 0; i < r.length; i++) {
    const [x0, y0] = r[i]!;
    const [x1, y1] = r[(i + 1) % r.length]!;
    a += x0 * y1 - x1 * y0;
  }
  return a / 2;
}

/** Area of a deck polygon (exterior minus holes), in the ring units squared. */
export function polygonArea(rings: Ring[]): number {
  return rings.reduce((s, r, i) => s + (i === 0 ? Math.abs(ringArea(r)) : -Math.abs(ringArea(r))), 0);
}

export function circleRing(radius: number, n = 32): Ring {
  const r: Ring = [];
  for (let i = 0; i < n; i++) {
    const t = (2 * Math.PI * i) / n;
    r.push([radius * Math.cos(t), radius * Math.sin(t)]);
  }
  return r;
}

function rectRing(w: number, h: number): Ring {
  return [
    [w / 2, h / 2],
    [-w / 2, h / 2],
    [-w / 2, -h / 2],
    [w / 2, -h / 2],
  ];
}

function roundedRect(w: number, h: number, rad: number, perCorner = 6): Ring {
  const r = Math.min(rad, w / 2, h / 2);
  const hw = w / 2;
  const hh = h / 2;
  const corners: [number, number, number][] = [
    [hw - r, hh - r, 0],
    [-hw + r, hh - r, Math.PI / 2],
    [-hw + r, -hh + r, Math.PI],
    [hw - r, -hh + r, (3 * Math.PI) / 2],
  ];
  const out: Ring = [];
  for (const [cx, cy, a0] of corners) {
    for (let i = 0; i <= perCorner; i++) {
      const t = a0 + ((Math.PI / 2) * i) / perCorner;
      out.push([cx + r * Math.cos(t), cy + r * Math.sin(t)]);
    }
  }
  return out;
}

/** Pad shape outline(s) in the pad-local frame, metres in -> rings in mm out. */
export function padShapeRings(shape: PadShape): Ring[] {
  const s = M_TO_MM;
  switch (shape.kind) {
    case 'circle':
      return [circleRing((shape.diameter / 2) * s)];
    case 'rect':
      return [rectRing(shape.width * s, shape.height * s)];
    case 'roundrect':
      return [roundedRect(shape.width * s, shape.height * s, shape.corner_radius * s)];
    case 'obround':
      return [roundedRect(shape.width * s, shape.height * s, (Math.min(shape.width, shape.height) / 2) * s, 8)];
    case 'chamfered_rect':
    case 'trapezoid':
      // Rendered as bounding rectangle at this stage.
      return [rectRing(shape.width * s, shape.height * s)];
    case 'polygon':
      return shape.polygons.flatMap((p) => polygonToDeck(p));
  }
}

export function transformRing(r: Ring, pos: Vec2, rot: number): Ring {
  const c = Math.cos(rot);
  const s = Math.sin(rot);
  const px = pos[0] * M_TO_MM;
  const py = pos[1] * M_TO_MM;
  return r.map(([x, y]) => [px + x * c - y * s, py + x * s + y * c]);
}

/** Tessellate an arc to points (metres in, metres out), including endpoints. */
export function arcPoints(a: ArcSeg, maxStep = Math.PI / 36): Vec2[] {
  const [cx, cy] = a.center;
  const a0 = Math.atan2(a.start[1] - cy, a.start[0] - cx);
  const a1 = Math.atan2(a.end[1] - cy, a.end[0] - cx);
  let sweep = a1 - a0;
  if (a.ccw && sweep <= 0) sweep += 2 * Math.PI;
  if (!a.ccw && sweep >= 0) sweep -= 2 * Math.PI;
  const r = Math.hypot(a.start[0] - cx, a.start[1] - cy);
  const n = Math.max(2, Math.ceil(Math.abs(sweep) / maxStep));
  const pts: Vec2[] = [];
  for (let i = 0; i <= n; i++) {
    const t = a0 + (sweep * i) / n;
    pts.push([cx + r * Math.cos(t), cy + r * Math.sin(t)]);
  }
  pts[0] = a.start;
  pts[n] = a.end;
  return pts;
}

/** Centreline path -> polyline in mm. */
export function pathToPolylineMm(path: PathSeg[]): Ring {
  const out: Ring = [];
  for (const seg of path) {
    const pts = seg.kind === 'line' ? [seg.start, seg.end] : arcPoints(seg);
    pts.forEach((p, i) => {
      if (out.length > 0 && i === 0) return; // contiguous: skip repeated start
      out.push([p[0] * M_TO_MM, p[1] * M_TO_MM]);
    });
  }
  return out;
}

export function polylineLength(pts: Ring): number {
  let l = 0;
  for (let i = 1; i < pts.length; i++) l += Math.hypot(pts[i]![0] - pts[i - 1]![0], pts[i]![1] - pts[i - 1]![1]);
  return l;
}
