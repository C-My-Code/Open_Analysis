// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * View/session state. Components READ this store; only command handlers (src/commands) WRITE it,
 * so every user action is available to an AI agent through the same typed command layer.
 */
import { create } from 'zustand';
import type { Board, LayerId } from '../types/board';
import type { BoardIndex } from '../geometry/boardIndex';
import type { RenderModel } from '../geometry/render';
import type { Units } from '../geometry/units';
import type { Overlay } from '../overlays/types';
import type { AnalysisInfo, Budget, DryRun, JobStatus, ProjectSummary, ResultBundle } from '../api/types';

/** Stable, human/agent-addressable reference. `id` is a net name, refdes, item id, rule id or layer id. */
export type Ref =
  | { kind: 'net'; id: string }
  | { kind: 'refdes'; id: string }
  | { kind: 'item'; id: string }
  | { kind: 'rule'; id: string }
  | { kind: 'layer'; id: string };

export type Tool = 'pan' | 'measure';
export type Theme = 'light' | 'dark';
export type MainTab = 'board' | '3d' | 'stackup';

export interface LayerStyle {
  visible: boolean;
  color: string; // #rrggbb
}

export interface ViewState {
  target: [number, number, number]; // mm
  zoom: number;
}

export interface Settings {
  theme: Theme;
  units: Units;
  assistantEnabled: boolean;
}

export interface AppState {
  projects: ProjectSummary[];
  projectId: string | null;
  board: Board | null;
  index: BoardIndex | null;
  render: RenderModel | null;
  layerStyle: Record<LayerId, LayerStyle>;
  /** Draw order, bottom-most first. */
  layerOrder: LayerId[];
  showOutline: boolean;
  showDrills: boolean;
  highlightedNets: string[];
  selection: Ref[];
  tool: Tool;
  measure: [number, number][]; // mm, 0..2 points
  overlays: Overlay[];
  results: Record<string, ResultBundle>;
  activeResultId: string | null;
  jobs: JobStatus[];
  analyses: AnalysisInfo[];
  /** In-memory setup (config) drafts per analysis kind; sent with `analysis.run`. */
  setupDrafts: Record<string, Record<string, unknown>>;
  dryRun: (DryRun & { analysis: string }) | null;
  jobBudget: Budget;
  viewState: ViewState;
  viewport: { w: number; h: number };
  netQuery: string;
  mainTab: MainTab;
  assistantOpen: boolean;
  status: string;
  settings: Settings;
}

const SETTINGS_KEY = 'oa.settings.v1';
export const DEFAULT_SETTINGS: Settings = { theme: 'dark', units: 'mm', assistantEnabled: false };

export function loadSettings(): Settings {
  try {
    const raw = globalThis.localStorage?.getItem(SETTINGS_KEY);
    if (raw) return { ...DEFAULT_SETTINGS, ...(JSON.parse(raw) as Partial<Settings>) };
  } catch {
    /* storage unavailable: fall back to defaults */
  }
  return DEFAULT_SETTINGS;
}

export function saveSettings(s: Settings): void {
  try {
    globalThis.localStorage?.setItem(SETTINGS_KEY, JSON.stringify(s));
  } catch {
    /* ignore */
  }
}

export const initialState = (): AppState => ({
  projects: [],
  projectId: null,
  board: null,
  index: null,
  render: null,
  layerStyle: {},
  layerOrder: [],
  showOutline: true,
  showDrills: true,
  highlightedNets: [],
  selection: [],
  tool: 'pan',
  measure: [],
  overlays: [],
  results: {},
  activeResultId: null,
  jobs: [],
  analyses: [],
  setupDrafts: {},
  dryRun: null,
  jobBudget: { allow_external: true },
  viewState: { target: [0, 0, 0], zoom: 4 },
  viewport: { w: 800, h: 600 },
  netQuery: '',
  mainTab: 'board',
  assistantOpen: false,
  status: 'No project open',
  settings: loadSettings(),
});

export const useStore = create<AppState>(() => initialState());

/** Nets that are currently emphasised: explicit highlights plus the nets of the selection. */
export function activeNets(s: AppState): string[] {
  const out = new Set(s.highlightedNets);
  for (const r of s.selection) {
    if (r.kind === 'net') out.add(r.id);
    else if (r.kind === 'item') {
      const it = s.index?.items.get(r.id);
      if (it?.obj.net) out.add(it.obj.net);
    } else if (r.kind === 'refdes') {
      const c = s.index?.components.get(r.id);
      for (const pid of c?.pads ?? []) {
        const p = s.index?.items.get(pid);
        if (p?.obj.net) out.add(p.obj.net);
      }
    }
  }
  return [...out];
}
