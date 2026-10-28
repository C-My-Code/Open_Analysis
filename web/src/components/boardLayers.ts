// SPDX-License-Identifier: GPL-3.0-or-later
import { BitmapLayer, PathLayer, ScatterplotLayer, SolidPolygonLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import type { Ring } from '../geometry/shapes';
import type { RenderModel, PathItem, PolyItem } from '../geometry/render';
import type { LayerStyle, Theme } from '../state/store';
import type { HeatmapOverlay, Overlay } from '../overlays/types';
import { heatmapRgba } from '../overlays/colormap';

type RGBA = [number, number, number, number];

export function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export interface LayerInputs {
  render: RenderModel;
  layerOrder: string[];
  layerStyle: Record<string, LayerStyle>;
  activeNets: string[];
  selectedItems: string[];
  selectedRule: string | null;
  showOutline: boolean;
  showDrills: boolean;
  overlays: Overlay[];
  measure: [number, number][];
  theme: Theme;
}

const SEVERITY_COLOR: Record<string, RGBA> = {
  error: [240, 64, 64, 255],
  warning: [245, 165, 36, 255],
  info: [62, 155, 240, 255],
};

const canvasCache = new WeakMap<HeatmapOverlay, HTMLCanvasElement>();
function heatmapCanvas(h: HeatmapOverlay): HTMLCanvasElement {
  let c = canvasCache.get(h);
  if (!c) {
    c = document.createElement('canvas');
    c.width = h.nx;
    c.height = h.ny;
    const data = heatmapRgba(h.values, h.nx, h.ny, h.clim);
    c.getContext('2d')!.putImageData(new ImageData(data as Uint8ClampedArray<ArrayBuffer>, h.nx, h.ny), 0, 0);
    canvasCache.set(h, c);
  }
  return c;
}

/** Builds the deck.gl layer stack. Pure function of its inputs (no store access). */
export function buildDeckLayers(inp: LayerInputs): Layer[] {
  const { render, activeNets, selectedItems } = inp;
  const hasActive = activeNets.length > 0;
  const active = new Set(activeNets);
  const sel = new Set(selectedItems);
  const alphaFor = (net: string | null, id: string, base: number): number => {
    if (sel.has(id)) return 255;
    if (!hasActive) return base;
    return net && active.has(net) ? 255 : 40;
  };
  const trig = [activeNets.join('|'), selectedItems.join('|')];
  const dark = inp.theme === 'dark';
  const layers: Layer[] = [];

  if (inp.showOutline) {
    layers.push(
      new PathLayer<Ring>({
        id: 'outline',
        data: render.outline,
        getPath: (r) => [...r, r[0]!],
        getColor: dark ? [220, 220, 120, 255] : [90, 90, 20, 255],
        getWidth: 1.5,
        widthUnits: 'pixels',
      }),
    );
  }

  for (const lid of inp.layerOrder) {
    const st = inp.layerStyle[lid];
    const g = render.layers[lid];
    if (!st?.visible || !g) continue;
    const rgb = hexToRgb(st.color);
    const color = (net: string | null, id: string, base: number): RGBA => [...rgb, alphaFor(net, id, base)];
    const fills = g.polys.filter((p) => p.kind === 'zone' || p.kind === 'shape');
    const pads = g.polys.filter((p) => p.kind === 'pad' || p.kind === 'via');
    layers.push(
      new SolidPolygonLayer<PolyItem>({
        id: `fill-${lid}`, data: fills, pickable: true, getPolygon: (d) => d.polygon as never,
        getFillColor: (d) => color(d.net, d.id, 90),
        updateTriggers: { getFillColor: [st.color, ...trig] },
      }),
      new PathLayer<PathItem>({
        id: `trk-${lid}`, data: g.paths, pickable: true, getPath: (d) => d.path, getWidth: (d) => d.width,
        widthUnits: 'common', capRounded: true, jointRounded: true, widthMinPixels: 1,
        getColor: (d) => color(d.net, d.id, 230),
        updateTriggers: { getColor: [st.color, ...trig] },
      }),
      new SolidPolygonLayer<PolyItem>({
        id: `pad-${lid}`, data: pads, pickable: true, getPolygon: (d) => d.polygon as never,
        getFillColor: (d) => color(d.net, d.id, 255),
        updateTriggers: { getFillColor: [st.color, ...trig] },
      }),
    );
    // Selection outline.
    const selPaths = g.paths.filter((p) => sel.has(p.id));
    const selPolys = [...fills, ...pads].filter((p) => sel.has(p.id));
    if (selPaths.length + selPolys.length > 0) {
      layers.push(
        new PathLayer<Ring>({
          id: `sel-${lid}`,
          data: [...selPaths.map((p) => p.path), ...selPolys.flatMap((p) => p.polygon.map((r) => [...r, r[0]!]))],
          getPath: (d) => d, getColor: [255, 255, 255, 255], getWidth: 2, widthUnits: 'pixels',
        }),
      );
    }
  }

  if (inp.showDrills) {
    layers.push(
      new ScatterplotLayer({
        id: 'drills', data: render.drills, getPosition: (d) => d.position, getRadius: (d) => d.radius,
        radiusUnits: 'common', getFillColor: dark ? [16, 18, 22, 255] : [250, 250, 250, 255],
      }),
    );
  }

  for (const o of inp.overlays) {
    if (o.kind === 'heatmap') {
      if (!inp.layerStyle[o.layer]?.visible) continue;
      const b: [number, number, number, number] = [
        o.origin[0] * 1e3, o.origin[1] * 1e3, (o.origin[0] + o.nx * o.dx) * 1e3, (o.origin[1] + o.ny * o.dy) * 1e3,
      ];
      layers.push(
        new BitmapLayer({
          id: `ov-${o.id}`, bounds: b, image: heatmapCanvas(o) as never, opacity: o.opacity ?? 0.8,
          textureParameters: { magFilter: 'nearest', minFilter: 'nearest' },
        }),
      );
    } else if (o.kind === 'markers') {
      layers.push(
        new ScatterplotLayer({
          id: `ov-${o.id}`, data: o.markers, pickable: true,
          getPosition: (d) => [d.location[0] * 1e3, d.location[1] * 1e3],
          getRadius: (d) => (inp.selectedRule && d.rule_id === inp.selectedRule ? 12 : 8),
          radiusUnits: 'pixels', stroked: true, lineWidthUnits: 'pixels', getLineWidth: 2,
          getLineColor: [255, 255, 255, 255],
          getFillColor: (d) => SEVERITY_COLOR[d.severity] ?? SEVERITY_COLOR.info!,
          updateTriggers: { getRadius: [inp.selectedRule] },
        }),
      );
    } else {
      layers.push(
        new PathLayer({
          id: `ov-${o.id}`, data: o.lines, getPath: (d) => d.points.map((p: [number, number]) => [p[0] * 1e3, p[1] * 1e3]),
          getColor: (d) => [...(d.color ?? [255, 80, 200]), 255] as RGBA, getWidth: 2, widthUnits: 'pixels',
        }),
      );
    }
  }

  if (inp.measure.length > 0) {
    layers.push(
      new PathLayer({ id: 'measure-line', data: inp.measure.length === 2 ? [inp.measure] : [], getPath: (d) => d, getColor: [255, 255, 255, 255], getWidth: 2, widthUnits: 'pixels' }),
      new ScatterplotLayer({ id: 'measure-pts', data: inp.measure, getPosition: (d) => d, getRadius: 5, radiusUnits: 'pixels', getFillColor: [255, 255, 255, 255], stroked: true, getLineColor: [0, 0, 0, 255], lineWidthUnits: 'pixels', getLineWidth: 1 }),
    );
  }
  return layers;
}
