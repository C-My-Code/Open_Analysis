// @vitest-environment jsdom
// SPDX-License-Identifier: GPL-3.0-or-later
import { createElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { dispatch, setApiClient } from '../../src/commands';
import { mockClient } from '../../src/mock/mockClient';
import { initialState, useStore } from '../../src/state/store';
import type { JobStatus, ResultBundle } from '../../src/api/types';
import { SetupPanel } from '../../src/features/setup/SetupPanel';
import { JobsPanel } from '../../src/features/jobs/JobsPanel';
import { ResultsPanel } from '../../src/features/results/ResultsPanel';

// Plotly needs a real canvas/WebGL environment; the plots are covered by the Playwright smoke test.
vi.mock('../../src/features/common/PlotlyChart', () => ({
  PlotlyChart: ({ testId }: { testId: string }) => createElement('div', { 'data-testid': testId }),
}));

beforeEach(async () => {
  setApiClient(mockClient);
  useStore.setState(initialState(), true);
  await dispatch({ type: 'project.list' });
  await dispatch({ type: 'project.open', path: 'sample_board' });
});
afterEach(cleanup);

async function pickAnalysis(kind: string) {
  render(<SetupPanel />);
  await waitFor(() => expect(screen.getByLabelText('Analysis')).toBeTruthy());
  await waitFor(() => expect(within(screen.getByLabelText('Analysis')).getAllByRole('option').length).toBe(4));
  fireEvent.change(screen.getByLabelText('Analysis'), { target: { value: kind } });
}

describe('setup forms generated from the config schemas', () => {
  it.each([
    ['erc', /Waivers/],
    ['impedance.profile', /Nets/],
    ['impedance.xsection', /Method/],
    ['pi.dc', /Loads/],
  ])('renders the %s form', async (kind, label) => {
    await pickAnalysis(kind);
    const form = screen.getByTestId('setup-form');
    expect(within(form).getAllByText(label).length).toBeGreaterThan(0);
  });

  it('writes edits into the in-memory setup draft', async () => {
    await pickAnalysis('impedance.xsection');
    const method = screen.getByLabelText(/Method/) as HTMLSelectElement;
    const bem = within(method).getByText('bem') as HTMLOptionElement; // RJSF option values are indices
    fireEvent.change(method, { target: { value: bem.value } });
    await waitFor(() => expect(useStore.getState().setupDrafts['impedance.xsection']?.method).toBe('bem'));
  });

  it('dry-runs and shows the estimate; runs and shows the job and result', async () => {
    await pickAnalysis('erc');
    fireEvent.click(screen.getByText('Dry run'));
    await waitFor(() => expect(screen.getByTestId('dry-run').textContent).toContain('valid'));
    fireEvent.click(screen.getByText('Run erc'));
    await waitFor(() => expect(useStore.getState().jobs[0]?.state).toBe('succeeded'));
    expect(useStore.getState().activeResultId).toBe('res_erc_1');
  });

  it('passes the budget with a run', async () => {
    const spy = vi.spyOn(mockClient, 'createJob');
    await pickAnalysis('erc');
    fireEvent.change(screen.getByLabelText('Max runtime'), { target: { value: '30' } });
    fireEvent.click(screen.getByText('Run erc'));
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]![1].budget).toMatchObject({ max_runtime_s: 30 });
    spy.mockRestore();
  });
});

describe('result viewers', () => {
  it('lists ERC violations with remediation, accuracy and provenance; markers go to the board', async () => {
    await dispatch({ type: 'analysis.run', analysis: 'erc' });
    render(<ResultsPanel />);
    expect(screen.getAllByTestId('violation-row')).toHaveLength(3);
    expect(screen.getByText(/Place a 100 nF capacitor/)).toBeTruthy();
    expect(screen.getAllByTestId('accuracy-badge')[0]!.textContent).toContain('screening');
    expect(screen.getByTestId('provenance').textContent).toContain('erc v1');
    const ov = useStore.getState().overlays;
    expect(ov[0]!.kind === 'markers' && ov[0]!.markers).toHaveLength(3);
    fireEvent.click(screen.getAllByTestId('violation-row')[1]!);
    expect(useStore.getState().selection).toEqual([{ kind: 'rule', id: 'PD-001' }]);
  });

  it('waives a violation in the setup draft only (dry run)', async () => {
    await dispatch({ type: 'analysis.run', analysis: 'erc' });
    render(<ResultsPanel />);
    fireEvent.click(screen.getAllByText('Waive (dry run)')[0]!);
    fireEvent.change(screen.getByLabelText('Waiver reason'), { target: { value: 'accepted' } });
    fireEvent.click(screen.getByText('Add waiver'));
    expect(useStore.getState().setupDrafts.erc?.waivers).toEqual([{ fingerprint: 'DP-001:trk:usb_dp,trk:usb_dn', reason: 'accepted' }]);
    expect(screen.getByTestId('waiver-note').textContent).toContain('nothing is saved');
    fireEvent.click(screen.getByText('Dry run with waivers'));
    await waitFor(() => expect(useStore.getState().dryRun?.analysis).toBe('erc'));
  });

  it('shows the profile with discontinuities that cross-probe to the board', async () => {
    await dispatch({ type: 'analysis.run', analysis: 'pi.dc' });
    render(<ResultsPanel />);
    expect(screen.getByTestId('profile-plot')).toBeTruthy();
    fireEvent.click(screen.getByText(/6.00 mm via/));
    await waitFor(() => expect(useStore.getState().selection).toEqual([{ kind: 'item', id: 'via:1v0_b' }]));
  });

  it('adds a heatmap overlay for a field result and toggles it', async () => {
    await dispatch({ type: 'analysis.run', analysis: 'pi.dc' });
    expect(useStore.getState().overlays.some((o) => o.kind === 'heatmap')).toBe(true);
    render(<ResultsPanel />);
    const box = within(screen.getByTestId('field-view')).getByRole('checkbox');
    fireEvent.click(box);
    expect(useStore.getState().overlays.some((o) => o.kind === 'heatmap')).toBe(false);
    fireEvent.click(box);
    expect(useStore.getState().overlays.some((o) => o.kind === 'heatmap')).toBe(true);
    expect(screen.getByTestId('field-range').textContent).toContain('0.95');
  });

  it('shows S-parameter plots, the quality badge and a prominent failed_quality banner', async () => {
    await dispatch({ type: 'analysis.run', analysis: 'pi.dc' }); // the mock result carries a passing network item
    render(<ResultsPanel />);
    expect(screen.getByTestId('sparam-plot')).toBeTruthy();
    expect(screen.getByTestId('quality-badge').textContent).toBe('pass');
    expect(screen.queryByTestId('failed-quality')).toBeNull();
    cleanup();
    const b = structuredClone(useStore.getState().results.res_pi_1!) as ResultBundle;
    b.status = 'failed_quality';
    const net = b.items.find((i) => i.kind === 'network')!;
    if (net.kind === 'network' && net.quality) { net.quality.verdict = 'fail'; net.quality.notes = ['passivity violated']; }
    useStore.setState({ results: { fq: b }, activeResultId: 'fq' });
    render(<ResultsPanel />);
    expect(screen.getByTestId('failed-quality').textContent).toContain('Quality gate failed');
    expect(screen.getByTestId('quality-badge').textContent).toBe('fail');
    expect(screen.getByTestId('quality-report').textContent).toContain('passivity violated');
  });

  it('shows the load table and scalars', async () => {
    await dispatch({ type: 'analysis.run', analysis: 'pi.dc' });
    render(<ResultsPanel />);
    expect(screen.getByTestId('table-view').textContent).toContain('U1');
  });
});

describe('jobs panel', () => {
  const running: JobStatus = {
    id: 'j1', state: 'running', analysis: 'pi.dc', setup: 'inline', progress: 0.4, tasks_total: 5, tasks_done: 2, tasks_cached: 0,
    tasks_failed: 0, message: 'meshing', result_id: null, created_utc: 't', updated_utc: 't',
  };

  it('shows progress and the budget, and cancels a running job', async () => {
    const cancel = vi.fn(async () => ({ ...running, state: 'cancelled' as const }));
    setApiClient({ ...mockClient, cancelJob: cancel, getJob: async () => running });
    useStore.setState({ jobs: [running], jobBudget: { allow_external: true, max_runtime_s: 60, max_memory_gb: 4 } });
    render(<JobsPanel />);
    expect(screen.getByTestId('budget').textContent).toContain('60 s');
    expect(screen.getByTestId('budget').textContent).toContain('4 GB');
    expect(screen.getByText('2/5 tasks - meshing')).toBeTruthy();
    fireEvent.click(screen.getByText('Cancel'));
    await waitFor(() => expect(useStore.getState().jobs[0]!.state).toBe('cancelled'));
    expect(cancel).toHaveBeenCalledWith('j1');
    expect(screen.queryByText('Cancel')).toBeNull();
  });

  it('job.refresh settles a job that finished: result is fetched and shown', async () => {
    setApiClient({ ...mockClient, getJob: async () => ({ ...running, state: 'succeeded', progress: 1, result_id: 'res_erc_1' }) });
    useStore.setState({ jobs: [running] });
    await dispatch({ type: 'job.refresh', jobId: 'j1' });
    expect(useStore.getState().jobs[0]!.state).toBe('succeeded');
    expect(useStore.getState().activeResultId).toBe('res_erc_1');
  });
});
