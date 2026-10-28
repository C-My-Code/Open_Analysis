// SPDX-License-Identifier: GPL-3.0-or-later
import type { LayerId, Vec2 } from '../types/board';
import type { Severity } from '../api/types';

/** Overlay API payloads. All coordinates are board-frame metres (converted to mm at render). */
export interface MarkerPoint {
  id: string;
  location: Vec2;
  severity: Severity;
  label: string;
  rule_id?: string;
  nets?: string[];
  items?: string[];
}
export interface MarkersOverlay {
  kind: 'markers';
  id: string;
  title: string;
  markers: MarkerPoint[];
}
export interface HeatmapOverlay {
  kind: 'heatmap';
  id: string;
  title: string;
  layer: LayerId;
  origin: Vec2;
  dx: number;
  dy: number;
  nx: number;
  ny: number;
  values: number[]; // row-major, row 0 = lowest y
  unit: string;
  clim: [number, number];
  opacity?: number;
}
export interface PolylinesOverlay {
  kind: 'polylines';
  id: string;
  title: string;
  lines: { points: Vec2[]; color?: [number, number, number] }[];
}
export type Overlay = MarkersOverlay | HeatmapOverlay | PolylinesOverlay;
