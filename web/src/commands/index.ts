// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Typed command/action layer. EVERY user action goes through `dispatch(command)`:
 * UI components never write the store directly. The same commands are what an AI agent will
 * issue over the API later (`commandCatalog` is its machine-readable description, and
 * `window.oa` exposes dispatch/catalog/snapshot in the browser).
 *
 * Targets use stable ids: net names, refdes, item ids, rule ids, layer ids (see `Ref`).
 */
import type { ApiClient, Budget, JobStatus, ResultBundle, Severity } from '../api/types';
import { isTerminal } from '../api/types';
import { finiteRange } from '../features/results/rasterize';
import { mockClient } from '../mock/mockClient';
import { buildIndex } from '../geometry/boardIndex';
import { buildRenderModel } from '../geometry/render';
import type { Units } from '../geometry/units';
import type { Overlay, MarkerPoint } from '../overlays/types';
import { initialState, saveSettings, useStore, type AppState, type MainTab, type Ref, type Theme, type Tool, type ViewState } from '../state/store';
import { fitBounds, refBounds, refExists } from '../state/refs';

export type Command =
  | { type: 'project.list' }
  | { type: 'project.open'; path: string }
  | { type: 'layer.setVisible'; layer: string; visible: boolean }
  | { type: 'layer.setColor'; layer: string; color: string }
  | { type: 'layer.move'; layer: string; direction: 'up' | 'down' }
  | { type: 'view.setShow'; outline?: boolean; drills?: boolean }
  | { type: 'net.highlight'; nets: string[]; mode?: 'set' | 'add' | 'toggle' }
  | { type: 'net.clearHighlight' }
  | { type: 'net.search'; query: string }
  | { type: 'select.set'; refs: Ref[] }
  | { type: 'select.clear' }
  | { type: 'view.fit' }
  | { type: 'view.zoomTo'; ref: Ref }
  | { type: 'view.set'; viewState: ViewState }
  | { type: 'view.setViewport'; w: number; h: number }
  | { type: 'tab.set'; tab: MainTab }
  | { type: 'tool.set'; tool: Tool }
  | { type: 'measure.addPoint'; point: [number, number] }
  | { type: 'measure.clear' }
  | { type: 'units.set'; units: Units }
  | { type: 'theme.set'; theme: Theme }
  | { type: 'overlay.add'; overlay: Overlay }
  | { type: 'overlay.remove'; id: string }
  | { type: 'view.zoomToPoint'; point: [number, number]; spanMm?: number }
  | { type: 'analysis.list' }
  | { type: 'budget.set'; budget: Budget }
  | { type: 'setup.set'; analysis: string; config: Record<string, unknown> }
  | { type: 'setup.waive'; fingerprint: string; reason: string }
  | { type: 'analysis.dryRun'; analysis: string; config?: Record<string, unknown>; budget?: Budget | null }
  | { type: 'analysis.run'; analysis: string; config?: Record<string, unknown>; budget?: Budget | null }
  | { type: 'job.refresh'; jobId: string }
  | { type: 'job.cancel'; jobId: string }
  | { type: 'result.show'; resultId: string }
  | { type: 'settings.setAssistantEnabled'; enabled: boolean }
  | { type: 'assistant.setOpen'; open: boolean };

export type CommandType = Command['type'];

export interface CommandResult {
  ok: boolean;
  data?: unknown;
  error?: { code: 'NOT_FOUND' | 'INVALID' | 'FAILED'; message: string };
}

class CommandError extends Error {
  constructor(public code: 'NOT_FOUND' | 'INVALID' | 'FAILED', message: string) {
    super(message);
  }
}

let client: ApiClient = mockClient;
export function setApiClient(c: ApiClient): void {
  client = c;
}

const set = (p: Partial<AppState>) => useStore.setState(p);
const get = () => useStore.getState();

const LAYER_COLORS = ['#e5484d', '#3e9bf0', '#30a46c', '#f5a524', '#8e4ec6', '#12a594'];

function requireBoard(): AppState {
  const s = get();
  if (!s.board) throw new CommandError('INVALID', 'No project is open');
  return s;
}
function requireLayer(id: string): void {
  if (!get().layerStyle[id]) throw new CommandError('NOT_FOUND', `Unknown layer ${id}`);
}
function requireRef(r: Ref): void {
  if (!refExists(get(), r)) throw new CommandError('NOT_FOUND', `Unknown ${r.kind} "${r.id}"`);
}

function showResult(rid: string, bundle: ResultBundle): void {
  const overlays = get().overlays.filter((o) => !o.id.startsWith(`${rid}:`));
  bundle.items.forEach((item) => {
    if (item.kind === 'violations') {
      const markers: MarkerPoint[] = item.violations.map((v) => ({
        id: v.fingerprint, location: v.location_m as [number, number], severity: v.severity as Severity,
        label: `${v.rule_id}: ${v.message}`, rule_id: v.rule_id, nets: v.nets, items: v.items,
      }));
      overlays.push({ kind: 'markers', id: `${rid}:${item.id}`, title: 'Violations', markers });
    } else if (item.kind === 'field') {
      item.fields.forEach((f, n) => {
        overlays.push({
          kind: 'heatmap', id: `${rid}:${item.id}:${n}`, title: `${f.name}${f.layer ? ` (${f.layer})` : ''}`, layer: f.layer ?? 'L1',
          origin: f.origin, dx: f.dx, dy: f.dy, nx: f.nx, ny: f.ny, values: f.values, unit: f.unit,
          clim: f.clim_hint ?? finiteRange(f.values), opacity: 0.8,
        });
      });
    }
  });
  set({ results: { ...get().results, [rid]: bundle }, activeResultId: rid, overlays });
}

function upsertJob(job: JobStatus): void {
  set({ jobs: [...get().jobs.filter((j) => j.id !== job.id), job] });
}

/** Terminal job: show its result when it succeeded. */
async function settleJob(job: JobStatus): Promise<void> {
  upsertJob(job);
  set({ status: `Job ${job.analysis}: ${job.state}` });
  if (job.state === 'succeeded' && job.result_id) showResult(job.result_id, await client.getResult(job.result_id));
}

const configFor = (analysis: string, config?: Record<string, unknown>) => config ?? get().setupDrafts[analysis] ?? {};

type Handlers = { [K in CommandType]: (c: Extract<Command, { type: K }>) => unknown | Promise<unknown> };

const handlers: Handlers = {
  'project.list': async () => {
    const projects = await client.listProjects();
    set({ projects });
    return projects;
  },
  'project.open': async ({ path }) => {
    const summary = await client.openProject(path);
    const board = await client.getBoard(summary.id);
    const index = buildIndex(board);
    const render = buildRenderModel(board);
    const copper = board.stackup.layers.filter((l) => l.kind === 'conductor').map((l) => l.id);
    const layerStyle = Object.fromEntries(copper.map((id, i) => [id, { visible: true, color: LAYER_COLORS[i % LAYER_COLORS.length]! }]));
    const prev = get();
    set({
      ...initialState(),
      settings: prev.settings,
      assistantOpen: prev.assistantOpen,
      projects: prev.projects,
      analyses: prev.analyses,
      jobBudget: prev.jobBudget,
      viewport: prev.viewport,
      projectId: summary.id,
      board, index, render, layerStyle,
      layerOrder: [...copper].reverse(), // bottom drawn first, L1 on top
      viewState: fitBounds(render.bbox, prev.viewport),
      status: `Opened ${summary.name}`,
    });
    return summary;
  },
  'layer.setVisible': ({ layer, visible }) => {
    requireLayer(layer);
    const s = get().layerStyle;
    set({ layerStyle: { ...s, [layer]: { ...s[layer]!, visible } } });
  },
  'layer.setColor': ({ layer, color }) => {
    requireLayer(layer);
    if (!/^#[0-9a-fA-F]{6}$/.test(color)) throw new CommandError('INVALID', 'color must be #rrggbb');
    const s = get().layerStyle;
    set({ layerStyle: { ...s, [layer]: { ...s[layer]!, color } } });
  },
  'layer.move': ({ layer, direction }) => {
    requireLayer(layer);
    const order = [...get().layerOrder];
    const i = order.indexOf(layer);
    const j = direction === 'up' ? i + 1 : i - 1; // "up" = drawn later = on top
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j]!, order[i]!];
    set({ layerOrder: order });
  },
  'view.setShow': ({ outline, drills }) => {
    set({ showOutline: outline ?? get().showOutline, showDrills: drills ?? get().showDrills });
  },
  'net.highlight': ({ nets, mode = 'set' }) => {
    requireBoard();
    nets.forEach((n) => requireRef({ kind: 'net', id: n }));
    const cur = get().highlightedNets;
    let next: string[];
    if (mode === 'set') next = [...new Set(nets)];
    else if (mode === 'add') next = [...new Set([...cur, ...nets])];
    else next = nets.reduce((a, n) => (a.includes(n) ? a.filter((x) => x !== n) : [...a, n]), cur);
    set({ highlightedNets: next });
    return next;
  },
  'net.clearHighlight': () => set({ highlightedNets: [] }),
  'net.search': ({ query }) => set({ netQuery: query }),
  'select.set': ({ refs }) => {
    requireBoard();
    refs.forEach(requireRef);
    set({ selection: refs });
  },
  'select.clear': () => set({ selection: [] }),
  'view.fit': () => {
    const s = requireBoard();
    set({ viewState: fitBounds(s.render!.bbox, s.viewport) });
  },
  'view.zoomTo': ({ ref }) => {
    const s = requireBoard();
    requireRef(ref);
    const b = refBounds(s, ref);
    if (!b) throw new CommandError('NOT_FOUND', `Nothing to show for ${ref.kind} "${ref.id}"`);
    set({ viewState: fitBounds(b, s.viewport, 0.6, 3) });
  },
  'view.set': ({ viewState }) => set({ viewState }),
  'view.setViewport': ({ w, h }) => set({ viewport: { w, h } }),
  'tab.set': ({ tab }) => set({ mainTab: tab }),
  'tool.set': ({ tool }) => set({ tool, measure: tool === 'measure' ? get().measure : [] }),
  'measure.addPoint': ({ point }) => {
    const m = get().measure;
    set({ measure: m.length >= 2 ? [point] : [...m, point] });
  },
  'measure.clear': () => set({ measure: [] }),
  'units.set': ({ units }) => {
    const settings = { ...get().settings, units };
    saveSettings(settings);
    set({ settings });
  },
  'theme.set': ({ theme }) => {
    const settings = { ...get().settings, theme };
    saveSettings(settings);
    set({ settings });
  },
  'overlay.add': ({ overlay }) => {
    set({ overlays: [...get().overlays.filter((o) => o.id !== overlay.id), overlay] });
  },
  'overlay.remove': ({ id }) => {
    set({ overlays: get().overlays.filter((o) => o.id !== id) });
  },
  'view.zoomToPoint': ({ point, spanMm = 4 }) => {
    const s = requireBoard();
    const [x, y] = [point[0] * 1e3, point[1] * 1e3];
    set({ viewState: fitBounds([x, y, x, y], s.viewport, 0.6, spanMm) });
  },
  'analysis.list': async () => {
    const analyses = await client.listAnalyses();
    set({ analyses });
    return analyses;
  },
  'budget.set': ({ budget }) => set({ jobBudget: budget }),
  'setup.set': ({ analysis, config }) => {
    set({ setupDrafts: { ...get().setupDrafts, [analysis]: config } });
  },
  'setup.waive': ({ fingerprint, reason }) => {
    // Dry-run only: the waiver is added to the in-memory ERC setup; nothing is written to disk.
    const draft = get().setupDrafts.erc ?? {};
    const waivers = [...((draft.waivers as unknown[] | undefined) ?? []), { fingerprint, reason }];
    set({ setupDrafts: { ...get().setupDrafts, erc: { ...draft, waivers } } });
    return waivers.length;
  },
  'analysis.dryRun': async ({ analysis, config, budget }) => {
    const s = requireBoard();
    const dry = await client.dryRun(s.projectId!, { analysis, config: configFor(analysis, config), budget });
    set({ dryRun: { analysis, ...dry } });
    return dry;
  },
  'analysis.run': async ({ analysis, config, budget }) => {
    const s = requireBoard();
    const job = await client.createJob(s.projectId!, { analysis, config: configFor(analysis, config), budget });
    if (isTerminal(job.state)) await settleJob(job);
    else upsertJob(job);
    return job;
  },
  'job.refresh': async ({ jobId }) => {
    const job = await client.getJob(jobId);
    const prev = get().jobs.find((j) => j.id === jobId);
    if (isTerminal(job.state) && (!prev || !isTerminal(prev.state))) await settleJob(job);
    else upsertJob(job);
    return job;
  },
  'job.cancel': async ({ jobId }) => {
    const job = await client.cancelJob(jobId);
    upsertJob(job);
    return job;
  },
  'result.show': async ({ resultId }) => {
    requireBoard();
    showResult(resultId, get().results[resultId] ?? (await client.getResult(resultId)));
  },
  'settings.setAssistantEnabled': ({ enabled }) => {
    const settings = { ...get().settings, assistantEnabled: enabled };
    saveSettings(settings);
    set({ settings, assistantOpen: enabled ? get().assistantOpen : false });
  },
  'assistant.setOpen': ({ open }) => {
    if (open && !get().settings.assistantEnabled) throw new CommandError('INVALID', 'Assistant is disabled in settings');
    set({ assistantOpen: open });
  },
};

export async function dispatch(command: Command): Promise<CommandResult> {
  const h = handlers[command.type] as ((c: Command) => unknown) | undefined;
  if (!h) return { ok: false, error: { code: 'INVALID', message: `Unknown command ${(command as { type: string }).type}` } };
  try {
    return { ok: true, data: await h(command) };
  } catch (e) {
    if (e instanceof CommandError) return { ok: false, error: { code: e.code, message: e.message } };
    const message = e instanceof Error ? e.message : String(e);
    set({ status: `Error: ${message}` });
    return { ok: false, error: { code: message.startsWith('NOT_FOUND') ? 'NOT_FOUND' : 'FAILED', message } };
  }
}

export interface CommandSpec {
  description: string;
  params: Record<string, string>;
}

/** Machine-readable command list for agents. Exhaustive over `Command['type']` (compile-time). */
export const commandCatalog: Record<CommandType, CommandSpec> = {
  'project.list': { description: 'List recently opened projects', params: {} },
  'project.open': { description: 'Open a project/board by path or project name', params: { path: 'string' } },
  'layer.setVisible': { description: 'Show or hide a copper layer', params: { layer: 'layer id e.g. L2', visible: 'boolean' } },
  'layer.setColor': { description: 'Set a layer colour', params: { layer: 'layer id', color: '#rrggbb' } },
  'layer.move': { description: 'Move a layer up/down in draw order', params: { layer: 'layer id', direction: 'up|down' } },
  'view.setShow': { description: 'Toggle board outline / drill display', params: { outline: 'boolean?', drills: 'boolean?' } },
  'net.highlight': { description: 'Highlight nets by name', params: { nets: 'string[] net names', mode: 'set|add|toggle (default set)' } },
  'net.clearHighlight': { description: 'Clear net highlighting', params: {} },
  'net.search': { description: 'Filter the net browser', params: { query: 'string' } },
  'select.set': { description: 'Select objects by stable id', params: { refs: '{kind: net|refdes|item|rule|layer, id}[]' } },
  'select.clear': { description: 'Clear the selection', params: {} },
  'view.fit': { description: 'Fit the board in the viewport', params: {} },
  'view.zoomTo': { description: 'Zoom to a net, refdes, item or rule', params: { ref: '{kind, id}' } },
  'view.set': { description: 'Set camera', params: { viewState: '{target:[x_mm,y_mm,0], zoom}' } },
  'view.setViewport': { description: 'Report viewport pixel size (UI internal)', params: { w: 'number', h: 'number' } },
  'tab.set': { description: 'Switch the main view', params: { tab: 'board|3d|stackup' } },
  'tool.set': { description: 'Choose the pointer tool', params: { tool: 'pan|measure' } },
  'measure.addPoint': { description: 'Add a measure point (mm, board frame)', params: { point: '[x_mm, y_mm]' } },
  'measure.clear': { description: 'Clear the measurement', params: {} },
  'units.set': { description: 'Display units', params: { units: 'mm|mil' } },
  'theme.set': { description: 'Colour theme', params: { theme: 'light|dark' } },
  'overlay.add': { description: 'Add a markers/heatmap/polylines overlay (metres)', params: { overlay: 'Overlay' } },
  'overlay.remove': { description: 'Remove an overlay by id', params: { id: 'string' } },
  'view.zoomToPoint': { description: 'Zoom to a board point (metres)', params: { point: '[x_m, y_m]', spanMm: 'number?' } },
  'analysis.list': { description: 'List analyses with their config schemas', params: {} },
  'budget.set': { description: 'Set the job budget (max_runtime_s, max_memory_gb) used by dry runs and runs', params: { budget: '{max_runtime_s?, max_memory_gb?}' } },
  'setup.set': { description: 'Set the in-memory setup (config) draft of an analysis', params: { analysis: 'kind', config: 'object per config schema' } },
  'setup.waive': { description: 'Add a waiver to the ERC setup draft (dry-run only, nothing is written)', params: { fingerprint: 'string', reason: 'string' } },
  'analysis.dryRun': { description: 'Validate a setup and estimate cost/budget without running', params: { analysis: 'kind', config: 'object?', budget: 'Budget?' } },
  'analysis.run': { description: 'Run an analysis on the open board (job); its result is shown when it succeeds', params: { analysis: 'kind e.g. erc, pi.dc', config: 'object? (default: setup draft)', budget: '{max_runtime_s?, max_memory_gb?}?' } },
  'job.refresh': { description: 'Poll one job', params: { jobId: 'string' } },
  'job.cancel': { description: 'Cancel a queued or running job', params: { jobId: 'string' } },
  'result.show': { description: 'Show a result bundle as overlays', params: { resultId: 'string' } },
  'settings.setAssistantEnabled': { description: 'Enable the Assistant panel slot', params: { enabled: 'boolean' } },
  'assistant.setOpen': { description: 'Expand or collapse the Assistant panel', params: { open: 'boolean' } },
};

/** JSON-serialisable state for agents; everything is addressed by stable ids. */
export function snapshot() {
  const s = get();
  return {
    project: s.projectId,
    layers: s.layerOrder.map((id) => ({ id, ...s.layerStyle[id] })),
    highlightedNets: s.highlightedNets,
    selection: s.selection,
    overlays: s.overlays.map((o) => ({ id: o.id, kind: o.kind, title: o.title })),
    activeResultId: s.activeResultId,
    tool: s.tool,
    units: s.settings.units,
    nets: s.board?.nets.map((n) => n.id) ?? [],
    components: s.board?.components?.map((c) => c.refdes) ?? [],
  };
}
