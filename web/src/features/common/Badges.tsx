// SPDX-License-Identifier: GPL-3.0-or-later
import type { AccuracyClass, Provenance, ValidityFlag } from '../../api/types';

const ACCURACY_HELP: Record<AccuracyClass, string> = {
  reference: 'Reference solver quality',
  validated: 'Validated against references within a stated tolerance',
  screening: 'Screening quality: rule-of-thumb or closed-form accuracy',
  estimate: 'Estimate only',
  unverified: 'Not verified',
};

/** accuracy_class chip: shown next to every result item (ai-integration section 4.1). */
export function AccuracyBadge({ value }: { value: AccuracyClass | null }) {
  const v = value ?? 'unverified';
  return (
    <span className={`badge acc-${v}`} title={ACCURACY_HELP[v]} data-testid="accuracy-badge">
      accuracy: {v}
    </span>
  );
}

export function ValidityFlags({ flags }: { flags: ValidityFlag[] }) {
  const shown = flags.filter((f) => !f.ok || f.code !== 'accuracy');
  if (shown.length === 0) return null;
  return (
    <ul className="flags">
      {shown.map((f) => <li key={f.code + f.message} className={f.ok ? '' : 'bad'}>{f.ok ? 'ok' : 'warn'} {f.code}: {f.message}</li>)}
    </ul>
  );
}

const short = (h: string | null | undefined) => (h ? h.replace('sha256:', '').slice(0, 10) : '-');

/** Provenance summary: analysis/version, solver, and the board/setup hashes that identify the run. */
export function ProvenanceLine({ p }: { p: Provenance | null }) {
  if (!p) return null;
  return (
    <details className="prov" data-testid="provenance">
      <summary>
        Provenance: {p.analysis} v{p.analysis_version}{p.solver ? `, ${p.solver} ${p.solver_version ?? ''}` : ''}
      </summary>
      <table className="kv">
        <tbody>
          <tr><th>board</th><td>{short(p.board_hash)}</td></tr>
          <tr><th>setup</th><td>{short(p.setup_hash)}</td></tr>
          <tr><th>inputs</th><td>{short(p.inputs_hash)}</td></tr>
          <tr><th>physics</th><td>{short(p.physics_hash)}</td></tr>
        </tbody>
      </table>
    </details>
  );
}
