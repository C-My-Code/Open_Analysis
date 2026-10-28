// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Board Model v1 JSON types, hand-written from docs/specs/board-model.md.
 *
 * SCHEMA SWITCH POINT: once `schemas/board-model.v1.json` exists, generate these with
 * openapi-typescript / json-schema-to-typescript into `src/types/generated/board-model.ts`
 * and make this file a thin re-export (`export type { Board, ... } from './generated/board-model'`).
 * Every other module imports Board types from `src/types/board` only, so nothing else changes.
 *
 * JSON conventions: lengths in metres, angles in radians, Y up, `Points` as flat [x0,y0,x1,y1,...],
 * polygon exterior CCW and holes CW, rings not closed.
 */

export type Vec2 = [number, number];
export type FlatPoints = number[];
export type LayerId = string; // L1.., D1.., MT, MB
export type NetId = string;
export type ItemId = string;

export interface SourceRef {
  tool: string;
  uuid?: string | null;
  token?: string | null;
  name?: string | null;
}

export interface Diagnostic {
  severity: 'info' | 'warning' | 'error';
  code: string;
  message: string;
  item?: ItemId | null;
  location?: Vec2 | null;
  layer?: LayerId | null;
}

export interface Polygon {
  exterior: FlatPoints;
  holes?: FlatPoints[];
}

export interface LineSeg {
  kind: 'line';
  start: Vec2;
  end: Vec2;
}
export interface ArcSeg {
  kind: 'arc';
  start: Vec2;
  end: Vec2;
  center: Vec2;
  ccw: boolean;
}
export type PathSeg = LineSeg | ArcSeg;

export interface Material {
  kind: 'dielectric' | 'conductor';
  name: string;
  model?: { kind: string; dk?: number; df?: number; f_ref?: number };
  conductivity?: number;
}

export interface ConductorLayer {
  kind: 'conductor';
  id: LayerId;
  index: number;
  source_name?: string | null;
  role?: string;
  thickness: number;
  z_bottom: number;
  z_top: number;
  material: string;
}
export interface DielectricLayer {
  kind: 'dielectric';
  id: LayerId;
  index: number;
  source_name?: string | null;
  construction?: string;
  thickness: number;
  z_bottom: number;
  z_top: number;
  material: string;
}
export interface MaskLayer {
  kind: 'mask';
  id: 'MT' | 'MB';
  source_name?: string | null;
  thickness: number;
  z_bottom: number;
  z_top: number;
  material: string;
  present?: boolean;
}
export type StackLayer = ConductorLayer | DielectricLayer | MaskLayer;

export interface Stackup {
  layers: StackLayer[]; // physical order, top first
  materials: Material[];
  finish?: string | null;
  total_thickness: number;
  known?: boolean;
}

export interface Net {
  id: NetId;
  kind: 'signal' | 'power' | 'ground' | 'unknown';
  net_class?: string | null;
}
export interface NetClass {
  name: string;
  track_width?: number | null;
  target_z_diff?: number | null;
  target_z_se?: number | null;
}
export interface DiffPair {
  id: ItemId;
  p_net: NetId;
  n_net: NetId;
  origin: string;
  target_z_diff?: number | null;
}

export type PadShape =
  | { kind: 'circle'; diameter: number }
  | { kind: 'rect'; width: number; height: number }
  | { kind: 'roundrect'; width: number; height: number; corner_radius: number }
  | { kind: 'obround'; width: number; height: number }
  | { kind: 'chamfered_rect'; width: number; height: number; chamfer: number; corners: string[] }
  | { kind: 'trapezoid'; width: number; height: number; delta_x?: number; delta_y?: number }
  | { kind: 'polygon'; polygons: Polygon[] };

export interface Drill {
  kind: 'plated' | 'npth';
  shape?: 'round' | 'slot';
  diameter: number;
  slot_length?: number | null;
  offset?: Vec2;
}
export interface PadstackLayer {
  layer: LayerId;
  pad: PadShape | null;
  antipad?: PadShape | null;
}
export interface Padstack {
  id: ItemId;
  drill: Drill | null;
  layers: PadstackLayer[];
}

export interface Pad {
  id: ItemId;
  component: string | null;
  number: string;
  padstack: ItemId;
  position: Vec2;
  rotation: number;
  net: NetId | null;
  kind: 'smd' | 'through_hole' | 'npth' | 'connector_edge' | 'aperture';
  pin_function?: string | null;
  source?: SourceRef | null;
}

export interface Via {
  id: ItemId;
  padstack: ItemId;
  position: Vec2;
  net: NetId | null;
  kind: 'through' | 'blind' | 'buried' | 'micro';
  start_layer: LayerId;
  end_layer: LayerId;
  source?: SourceRef | null;
}

export interface Track {
  id: ItemId;
  net: NetId | null;
  layer: LayerId;
  width: number;
  path: PathSeg[];
  end_caps?: 'round' | 'square' | 'flat';
  source?: SourceRef | null;
}

export interface ZoneFill {
  layer: LayerId;
  polygons: Polygon[];
}
export interface Zone {
  id: ItemId;
  net: NetId | null;
  layers: LayerId[];
  priority?: number;
  outline: Polygon;
  fills?: ZoneFill[];
  fill_state?: 'filled' | 'unfilled' | 'stale_suspected' | 'refilled_by_tool';
  keepout?: Record<string, boolean> | null;
  source?: SourceRef | null;
}

export interface CopperShape {
  id: ItemId;
  net: NetId | null;
  layer: LayerId;
  polygon: Polygon;
}

export interface Component {
  refdes: string;
  value?: string | null;
  footprint?: string | null;
  side: 'top' | 'bottom';
  position: Vec2;
  rotation: number;
  pads: ItemId[];
  courtyard?: Polygon | null;
  dnp?: boolean;
}

export interface BoardMeta {
  name: string;
  source_tool: string;
  source_version?: string | null;
  source_file?: string | null;
  source_hash?: string | null;
  reader: string;
  arc_tolerance?: number;
  created_utc: string;
}

export interface BoardOutline {
  polygon: Polygon;
  edges?: PathSeg[];
}

export interface Board {
  schema_version: string;
  meta: BoardMeta;
  outline: BoardOutline;
  stackup: Stackup;
  nets: Net[];
  net_classes?: NetClass[];
  diff_pairs?: DiffPair[];
  padstacks?: Padstack[];
  pads?: Pad[];
  vias?: Via[];
  tracks?: Track[];
  zones?: Zone[];
  shapes?: CopperShape[];
  components?: Component[];
  diagnostics?: Diagnostic[];
}
