// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useState } from 'react';
import Form from '@rjsf/core';
import validator from '@rjsf/validator-ajv8';
import type { RJSFSchema, WidgetProps } from '@rjsf/utils';
import { dispatch } from '../../commands';
import { useStore } from '../../state/store';
import { pruneConfig, SETUP_KINDS, simplifyCached, uiSchemaFor } from './schemaUtils';

/** Net-name input with the open board's nets as suggestions (NetSelector widget). */
function NetWidget(p: WidgetProps) {
  const board = useStore((s) => s.board);
  const nets = board?.nets.map((n) => n.id) ?? [];
  const list = `nets-${p.id}`;
  return (
    <>
      <input
        id={p.id} list={list} value={(p.value as string | undefined) ?? ''} disabled={p.disabled || p.readonly}
        required={p.required} onChange={(e) => p.onChange(e.target.value === '' ? undefined : e.target.value)}
      />
      <datalist id={list}>{nets.map((n) => <option key={n} value={n} />)}</datalist>
    </>
  );
}
const widgets = { netName: NetWidget };

/** Setup editor: a form generated from the analysis config schema, dry run, budget and run. */
export function SetupPanel() {
  const analyses = useStore((s) => s.analyses);
  const hasBoard = useStore((s) => !!s.board);
  const drafts = useStore((s) => s.setupDrafts);
  const dry = useStore((s) => s.dryRun);
  const budget = useStore((s) => s.jobBudget);
  const [kind, setKind] = useState<string>(SETUP_KINDS[0]);
  const [raw, setRaw] = useState<string | null>(null);
  const [rawErr, setRawErr] = useState('');

  useEffect(() => { void dispatch({ type: 'analysis.list' }); }, []);

  const offered = analyses.filter((a) => (SETUP_KINDS as readonly string[]).includes(a.kind));
  const configSchema = offered.find((a) => a.kind === kind)?.config_schema;
  const schema = configSchema ? (simplifyCached(configSchema) as RJSFSchema) : null;
  const uiSchema = uiSchemaFor(kind);
  const draft = drafts[kind] ?? {};
  const cfg = () => pruneConfig(draft) as Record<string, unknown>;

  const setBudget = (key: 'max_runtime_s' | 'max_memory_gb', v: string) =>
    void dispatch({ type: 'budget.set', budget: { ...budget, [key]: v === '' || Number(v) <= 0 ? null : Number(v) } });

  return (
    <section aria-label="Setup">
      <h2>Setup</h2>
      <label>
        Analysis{' '}
        <select aria-label="Analysis" value={kind} onChange={(e) => { setKind(e.target.value); setRaw(null); }}>
          {offered.map((a) => <option key={a.kind} value={a.kind}>{a.title} ({a.kind})</option>)}
        </select>
      </label>
      {schema && raw === null && (
        <div className="setup-form" data-testid="setup-form">
          <Form
            schema={schema}
            uiSchema={uiSchema}
            validator={validator}
            formData={draft}
            widgets={widgets}
            showErrorList={false}
            liveOmit
            omitExtraData
            tagName="div"
            onChange={(e) => {
              // RJSF re-emits onChange when it fills schema defaults; only store real changes.
              const next = (e.formData ?? {}) as Record<string, unknown>;
              if (JSON.stringify(next) !== JSON.stringify(draft)) void dispatch({ type: 'setup.set', analysis: kind, config: next });
            }}
          >
            <></>
          </Form>
        </div>
      )}
      {raw !== null && (
        <div>
          <textarea aria-label="Setup JSON" rows={10} value={raw} onChange={(e) => setRaw(e.target.value)} />
          <button
            onClick={() => {
              try {
                void dispatch({ type: 'setup.set', analysis: kind, config: JSON.parse(raw) as Record<string, unknown> });
                setRawErr(''); setRaw(null);
              } catch (e) { setRawErr(String(e)); }
            }}
          >Apply JSON</button>
          {rawErr && <p className="bad">{rawErr}</p>}
        </div>
      )}
      <button className="link" onClick={() => setRaw(raw === null ? JSON.stringify(draft, null, 2) : null)}>
        {raw === null ? 'Edit as JSON' : 'Back to form'}
      </button>
      <fieldset className="budget">
        <legend>Budget</legend>
        <label>Max runtime [s] <input aria-label="Max runtime" type="number" min="0" value={budget.max_runtime_s ?? ''} onChange={(e) => setBudget('max_runtime_s', e.target.value)} /></label>
        <label>Max memory [GB] <input aria-label="Max memory" type="number" min="0" value={budget.max_memory_gb ?? ''} onChange={(e) => setBudget('max_memory_gb', e.target.value)} /></label>
      </fieldset>
      <div className="btns">
        <button disabled={!hasBoard} onClick={() => void dispatch({ type: 'analysis.dryRun', analysis: kind, config: cfg(), budget })}>Dry run</button>
        <button disabled={!hasBoard} onClick={() => void dispatch({ type: 'analysis.run', analysis: kind, config: cfg(), budget })}>Run {kind}</button>
      </div>
      {dry && (
        <div data-testid="dry-run" className={dry.valid ? '' : 'bad'}>
          <strong>Dry run {dry.analysis}: {dry.valid ? 'valid' : 'invalid'}</strong>
          {dry.estimate && (
            <p>
              {dry.estimate.tasks ?? '?'} task(s), about {dry.estimate.est_seconds ?? '?'} s, peak {dry.estimate.est_peak_memory_gb ?? '?'} GB
              {dry.estimate.within_budget === false ? ' - EXCEEDS BUDGET' : ''}
            </p>
          )}
          <ul className="flags">
            {dry.diagnostics.map((d, i) => <li key={i} className="bad">{d.path ? `${d.path}: ` : ''}{d.message}</li>)}
            {dry.estimate?.reasons?.map((r) => <li key={r} className="bad">{r}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}
