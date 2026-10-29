// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { dispatch } from '../../commands';
import type { Violation, ViolationSet } from '../../api/types';
import { useStore } from '../../state/store';

const fmt = (v: number | null | undefined, unit: string | null | undefined) =>
  v == null ? '' : `${Number(v.toPrecision(4))}${unit ? ` ${unit}` : ''}`;

function remediationText(v: Violation): string {
  const r = v.remediation as { hint?: string } | null | undefined;
  return r?.hint ?? v.fix_hint ?? '';
}

export function ViolationsView({ set }: { set: ViolationSet }) {
  const selection = useStore((s) => s.selection);
  const draftWaivers = useStore((s) => s.setupDrafts.erc?.waivers) as { fingerprint?: string }[] | undefined;
  const waivers = draftWaivers ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const counts = { error: 0, warning: 0, info: 0 } as Record<string, number>;
  for (const v of set.violations) counts[v.severity] = (counts[v.severity] ?? 0) + 1;
  const waivedLocal = new Set(waivers.map((w) => w.fingerprint));

  const show = (v: Violation) => {
    void dispatch({ type: 'select.set', refs: [{ kind: 'rule', id: v.rule_id }] });
    void dispatch({ type: 'view.zoomToPoint', point: [v.location_m[0]!, v.location_m[1]!] });
  };
  return (
    <div data-testid="violations-view">
      <p data-testid="violation-count">
        {set.violations.length} violation(s): {counts.error} error, {counts.warning} warning, {counts.info} info
        {' '}<span className="muted">({set.rules_run.length} rules run)</span>
      </p>
      {set.violations.length > 0 && (
        <table className="grid">
          <thead><tr><th>Rule</th><th>Sev</th><th>Message</th><th>Measured / limit</th></tr></thead>
          <tbody>
            {set.violations.map((v) => {
              const waived = v.waived || waivedLocal.has(v.fingerprint);
              return [
                <tr
                  key={v.fingerprint}
                  data-testid="violation-row"
                  className={`${selection.some((r) => r.kind === 'rule' && r.id === v.rule_id) ? 'sel' : ''}${waived ? ' waived' : ''}`}
                  onClick={() => show(v)}
                >
                  <td>{v.rule_id}</td><td>{v.severity}</td>
                  <td>{v.message}{waived ? ' (waived)' : ''}</td>
                  <td>{fmt(v.measured, v.unit)}{v.limit != null ? ` / ${fmt(v.limit, v.unit)}` : ''}</td>
                </tr>,
                <tr key={`${v.fingerprint}:d`} className="detail">
                  <td colSpan={4}>
                    {remediationText(v) && <div data-testid="remediation"><strong>Fix:</strong> {remediationText(v)}</div>}
                    {!waived && open !== v.fingerprint && (
                      <button onClick={() => { setOpen(v.fingerprint); setReason(''); }}>Waive (dry run)</button>
                    )}
                    {open === v.fingerprint && (
                      <span className="btns">
                        <input aria-label="Waiver reason" value={reason} placeholder="reason" onChange={(e) => setReason(e.target.value)} />
                        <button
                          disabled={!reason.trim()}
                          onClick={() => { void dispatch({ type: 'setup.waive', fingerprint: v.fingerprint, reason: reason.trim() }); setOpen(null); }}
                        >Add waiver</button>
                        <button onClick={() => setOpen(null)}>Cancel</button>
                      </span>
                    )}
                  </td>
                </tr>,
              ];
            })}
          </tbody>
        </table>
      )}
      {waivers.length > 0 && (
        <p data-testid="waiver-note">
          {waivers.length} waiver(s) in the ERC setup draft (in memory only; nothing is saved to the project).{' '}
          <button onClick={() => void dispatch({ type: 'analysis.dryRun', analysis: 'erc' })}>Dry run with waivers</button>
        </p>
      )}
    </div>
  );
}
