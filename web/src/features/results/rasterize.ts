// SPDX-License-Identifier: GPL-3.0-or-later
import type { FieldGrid } from '../../api/types';

/**
 * Rasterise a triangle-mesh scalar field onto a regular grid for the heatmap overlay.
 * `location` 'node': barycentric interpolation; 'cell': flat value per triangle. Cells that no
 * triangle covers stay NaN (drawn transparent). Resolution is capped at `maxSide` cells per axis.
 */
export function rasterizeTriMesh(
  nodes: Float64Array, // (N, 2) interleaved, metres
  tris: Float64Array, // (M, 3) interleaved node indices
  values: Float64Array,
  location: 'node' | 'cell',
  maxSide = 400,
): Pick<FieldGrid, 'origin' | 'dx' | 'dy' | 'nx' | 'ny' | 'values'> {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = 0; i < nodes.length; i += 2) {
    x0 = Math.min(x0, nodes[i]!); x1 = Math.max(x1, nodes[i]!);
    y0 = Math.min(y0, nodes[i + 1]!); y1 = Math.max(y1, nodes[i + 1]!);
  }
  if (!Number.isFinite(x0)) return { origin: [0, 0], dx: 1, dy: 1, nx: 1, ny: 1, values: [NaN] };
  const w = Math.max(x1 - x0, 1e-9), h = Math.max(y1 - y0, 1e-9);
  const d = Math.max(w, h) / maxSide;
  const nx = Math.max(1, Math.ceil(w / d)), ny = Math.max(1, Math.ceil(h / d));
  const grid = new Array<number>(nx * ny).fill(NaN);
  const nt = tris.length / 3;
  for (let t = 0; t < nt; t++) {
    const a = tris[3 * t]!, b = tris[3 * t + 1]!, c = tris[3 * t + 2]!;
    const ax = nodes[2 * a]!, ay = nodes[2 * a + 1]!;
    const bx = nodes[2 * b]!, by = nodes[2 * b + 1]!;
    const cx = nodes[2 * c]!, cy = nodes[2 * c + 1]!;
    const den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
    if (den === 0) continue;
    const i0 = Math.max(0, Math.floor((Math.min(ax, bx, cx) - x0) / d - 0.5));
    const i1 = Math.min(nx - 1, Math.ceil((Math.max(ax, bx, cx) - x0) / d - 0.5));
    const j0 = Math.max(0, Math.floor((Math.min(ay, by, cy) - y0) / d - 0.5));
    const j1 = Math.min(ny - 1, Math.ceil((Math.max(ay, by, cy) - y0) / d - 0.5));
    for (let j = j0; j <= j1; j++) {
      const py = y0 + (j + 0.5) * d;
      for (let i = i0; i <= i1; i++) {
        const px = x0 + (i + 0.5) * d;
        const l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / den;
        const l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / den;
        const l3 = 1 - l1 - l2;
        const e = -1e-9;
        if (l1 < e || l2 < e || l3 < e) continue;
        grid[j * nx + i] = location === 'node' ? l1 * values[a]! + l2 * values[b]! + l3 * values[c]! : values[t]!;
      }
    }
  }
  return { origin: [x0, y0], dx: d, dy: d, nx, ny, values: grid };
}

export function finiteRange(values: number[]): [number, number] {
  let lo = Infinity, hi = -Infinity;
  for (const v of values) {
    if (Number.isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  }
  return Number.isFinite(lo) ? [lo, hi] : [0, 1];
}
