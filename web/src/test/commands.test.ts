// SPDX-License-Identifier: GPL-3.0-or-later
import { beforeEach, describe, expect, it } from 'vitest';
import { commandCatalog, dispatch, snapshot } from '../commands';
import { activeNets, initialState, useStore } from '../state/store';

beforeEach(async () => {
  useStore.setState(initialState(), true);
  await dispatch({ type: 'project.list' });
});

async function open() {
  const r = await dispatch({ type: 'project.open', path: 'sample_board' });
  expect(r.ok).toBe(true);
}

describe('command layer', () => {
  it('opens a board and lists 4 copper layers', async () => {
    await open();
    expect(snapshot().layers.map((l) => l.id)).toEqual(['L4', 'L3', 'L2', 'L1']);
  });

  it('rejects an unknown project with NOT_FOUND', async () => {
    const r = await dispatch({ type: 'project.open', path: 'nope' });
    expect(r.ok).toBe(false);
    expect(r.error?.code).toBe('NOT_FOUND');
  });

  it('toggles a layer and validates ids', async () => {
    await open();
    expect((await dispatch({ type: 'layer.setVisible', layer: 'L2', visible: false })).ok).toBe(true);
    expect(useStore.getState().layerStyle.L2!.visible).toBe(false);
    expect((await dispatch({ type: 'layer.setVisible', layer: 'L9', visible: false })).error?.code).toBe('NOT_FOUND');
    expect((await dispatch({ type: 'layer.setColor', layer: 'L1', color: 'red' })).error?.code).toBe('INVALID');
  });

  it('selecting the via array item shows net +1V0 (stable ids)', async () => {
    await open();
    await dispatch({ type: 'select.set', refs: [{ kind: 'item', id: 'via:1v0_b' }] });
    expect(activeNets(useStore.getState())).toEqual(['+1V0']);
  });

  it('selects by refdes and highlights by net name', async () => {
    await open();
    expect((await dispatch({ type: 'select.set', refs: [{ kind: 'refdes', id: 'R1' }] })).ok).toBe(true);
    expect(activeNets(useStore.getState())).toEqual(['SIG_A']);
    await dispatch({ type: 'net.highlight', nets: ['GND', 'USB_DP'], mode: 'set' });
    await dispatch({ type: 'net.highlight', nets: ['GND'], mode: 'toggle' });
    expect(useStore.getState().highlightedNets).toEqual(['USB_DP']);
    expect((await dispatch({ type: 'net.highlight', nets: ['NOPE'] })).error?.code).toBe('NOT_FOUND');
  });

  it('runs erc, creates a marker overlay and allows selecting a rule id', async () => {
    await open();
    expect((await dispatch({ type: 'select.set', refs: [{ kind: 'rule', id: 'PD-001' }] })).ok).toBe(false);
    const r = await dispatch({ type: 'analysis.run', analysis: 'erc' });
    expect(r.ok).toBe(true);
    const ov = useStore.getState().overlays;
    expect(ov).toHaveLength(1);
    expect(ov[0]!.kind === 'markers' && ov[0]!.markers.length).toBe(3);
    expect((await dispatch({ type: 'select.set', refs: [{ kind: 'rule', id: 'PD-001' }] })).ok).toBe(true);
    expect((await dispatch({ type: 'view.zoomTo', ref: { kind: 'rule', id: 'PD-001' } })).ok).toBe(true);
  });

  it('runs pi.dc and creates a heatmap overlay on L3', async () => {
    await open();
    await dispatch({ type: 'analysis.run', analysis: 'pi.dc' });
    const h = useStore.getState().overlays.find((o) => o.kind === 'heatmap');
    expect(h && h.kind === 'heatmap' && h.layer).toBe('L3');
  });

  it('measure keeps at most two points', async () => {
    await open();
    for (const p of [[0, 0], [3, 4], [9, 9]] as [number, number][]) await dispatch({ type: 'measure.addPoint', point: p });
    expect(useStore.getState().measure).toEqual([[9, 9]]);
  });

  it('assistant is hidden unless enabled in settings', async () => {
    expect((await dispatch({ type: 'assistant.setOpen', open: true })).ok).toBe(false);
    await dispatch({ type: 'settings.setAssistantEnabled', enabled: true });
    expect((await dispatch({ type: 'assistant.setOpen', open: true })).ok).toBe(true);
    await dispatch({ type: 'settings.setAssistantEnabled', enabled: false });
    expect(useStore.getState().assistantOpen).toBe(false);
  });

  it('describes every command in the catalog', () => {
    for (const spec of Object.values(commandCatalog)) expect(spec.description.length).toBeGreaterThan(3);
  });
});
