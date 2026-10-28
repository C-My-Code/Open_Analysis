// SPDX-License-Identifier: GPL-3.0-or-later
import { describe, expect, it } from 'vitest';
import sample from '../fixtures/sample_board.oab.json';
import type { Board } from '../types/board';
import { buildRenderModel } from '../geometry/render';
import { buildIndex } from '../geometry/boardIndex';
import { arcPoints, circleRing, polygonArea, polygonToDeck, pathToPolylineMm, polylineLength, ringArea } from '../geometry/shapes';
import { formatLengthMm, metresToDisplay } from '../geometry/units';
import { heatmapRgba, viridis } from '../overlays/colormap';

const board = sample as unknown as Board;

describe('units', () => {
  it('converts metres to display units', () => {
    expect(metresToDisplay(0.001, 'mm')).toBeCloseTo(1);
    expect(metresToDisplay(25.4e-6, 'mil')).toBeCloseTo(1);
    expect(formatLengthMm(25.4, 'mil')).toBe('1000.0 mil');
  });
});

describe('geometry', () => {
  it('computes zone area with a hole (mm^2)', () => {
    const z = board.zones!.find((x) => x.id === 'zone:gnd_l2')!;
    const area = polygonArea(polygonToDeck(z.fills![0]!.polygons[0]!));
    expect(area).toBeCloseTo(38 * 28 - 4 * 3, 6);
  });

  it('keeps exterior CCW (positive area) and Y up', () => {
    const r = polygonToDeck(board.outline.polygon)[0]!;
    expect(ringArea(r)).toBeGreaterThan(0);
    const top = Math.max(...r.map((p) => p[1]));
    expect(top).toBeCloseTo(30); // 0.03 m -> 30 mm, +Y
  });

  it('tessellates circles with small area error', () => {
    const r = 0.5;
    expect(Math.abs(ringArea(circleRing(r, 32)) - Math.PI * r * r) / (Math.PI * r * r)).toBeLessThan(0.01);
  });

  it('tessellates a clockwise quarter arc to the analytic length', () => {
    const seg = board.tracks!.find((t) => t.id === 'trk:sig_a')!.path[1]!;
    if (seg.kind !== 'arc') throw new Error('fixture');
    const pts = arcPoints(seg).map((p) => [p[0] * 1e3, p[1] * 1e3] as [number, number]);
    expect(polylineLength(pts)).toBeCloseTo((Math.PI / 2) * 2, 2);
  });

  it('builds a track polyline without duplicate joints', () => {
    const t = board.tracks!.find((x) => x.id === 'trk:sig_a')!;
    const pl = pathToPolylineMm(t.path);
    for (let i = 1; i < pl.length; i++) expect(pl[i]).not.toEqual(pl[i - 1]);
  });

  it('builds the render model with layers, pads and via barrels', () => {
    const m = buildRenderModel(board);
    expect(Object.keys(m.layers).sort()).toEqual(['L1', 'L2', 'L3', 'L4']);
    expect(m.layers.L1!.paths.length).toBe(6);
    expect(m.layers.L2!.polys.filter((p) => p.kind === 'zone').length).toBe(1);
    expect(m.layers.L3!.polys.filter((p) => p.kind === 'via').length).toBe(4);
    expect(m.drills.length).toBe(4);
    expect(m.bbox).toEqual([0, 0, 40, 30]);
  });

  it('indexes nets by stable ids', () => {
    const idx = buildIndex(board);
    expect(idx.items.get('via:1v0_b')?.obj.net).toBe('+1V0');
    expect(idx.netStats.get('USB_DP')!.length_mm).toBeCloseTo(14.5, 6);
    expect(idx.components.get('U1')!.pads.length).toBe(5);
  });
});

describe('colormap', () => {
  it('clamps and maps endpoints', () => {
    expect(viridis(-1)).toEqual([68, 1, 84]);
    expect(viridis(2)).toEqual([253, 231, 37]);
  });
  it('flips rows so lowest y is at the bottom of the image', () => {
    const rgba = heatmapRgba([0, 1], 1, 2, [0, 1]); // row0 (low y)=0, row1=1
    expect(Array.from(rgba.slice(0, 3))).toEqual(viridis(1)); // image top = high y
    expect(Array.from(rgba.slice(4, 7))).toEqual(viridis(0));
  });
});
