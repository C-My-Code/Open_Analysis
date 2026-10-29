// SPDX-License-Identifier: GPL-3.0-or-later
const STOPS: [number, number, number][] = [
  [68, 1, 84],
  [59, 82, 139],
  [33, 145, 140],
  [94, 201, 98],
  [253, 231, 37],
]; // viridis, 5 stops

/** Map t in [0,1] to RGB (clamped). */
export function viridis(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(x));
  const f = x - i;
  const a = STOPS[i]!;
  const b = STOPS[i + 1]!;
  return [0, 1, 2].map((k) => Math.round(a[k]! + (b[k]! - a[k]!) * f)) as [number, number, number];
}

/** Rasterise a grid (row 0 = lowest y) to RGBA bytes with row 0 at the TOP (image convention). */
export function heatmapRgba(values: number[], nx: number, ny: number, clim: [number, number]): Uint8ClampedArray {
  const out = new Uint8ClampedArray(nx * ny * 4);
  const span = clim[1] - clim[0] || 1;
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      const v = values[j * nx + i]!;
      const o = ((ny - 1 - j) * nx + i) * 4;
      if (!Number.isFinite(v)) continue; // outside the mesh: transparent
      const [r, g, b] = viridis((v - clim[0]) / span);
      out[o] = r; out[o + 1] = g; out[o + 2] = b; out[o + 3] = 255;
    }
  }
  return out;
}
