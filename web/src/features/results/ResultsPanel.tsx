// SPDX-License-Identifier: GPL-3.0-or-later
import { useStore } from '../../state/store';
import type { ResultItem } from '../../api/types';
import { AccuracyBadge, ProvenanceLine, ValidityFlags } from '../common/Badges';
import { FieldView } from './FieldView';
import { NetworkView } from './NetworkView';
import { ProfileView } from './ProfileView';
import { ViolationsView } from './ViolationsView';

const fmtCell = (v: unknown) => (typeof v === 'number' ? String(Number(v.toPrecision(5))) : v == null ? '' : String(v));

function ItemBody({ rid, item }: { rid: string; item: ResultItem }) {
  switch (item.kind) {
    case 'violations': return <ViolationsView set={item} />;
    case 'profile': return <ProfileView p={item} />;
    case 'field': return <FieldView rid={rid} item={item} />;
    case 'network': return <NetworkView n={item} />;
    case 'table':
      return (
        <table className="grid" data-testid="table-view">
          <thead><tr>{item.columns.map((c) => <th key={c.name}>{c.name}{c.unit ? ` [${c.unit}]` : ''}</th>)}</tr></thead>
          <tbody>
            {item.rows.slice(0, 200).map((r, i) => <tr key={i}>{r.map((c, k) => <td key={k}>{fmtCell(c)}</td>)}</tr>)}
          </tbody>
        </table>
      );
    case 'scalars':
      return (
        <table className="grid" data-testid="scalars-view">
          <tbody>
            {item.values.map((v) => (
              <tr key={v.name}><th>{v.name}</th><td>{fmtCell(v.value)} {v.unit}</td><td>{v.status ?? ''}</td></tr>
            ))}
          </tbody>
        </table>
      );
    default: return <p className="muted">{item.kind} item (download via export)</p>;
  }
}

/** Result viewer: bundle status, accuracy class and provenance, then one section per item. */
export function ResultsPanel() {
  const rid = useStore((s) => s.activeResultId);
  const bundle = useStore((s) => (s.activeResultId ? s.results[s.activeResultId] : undefined));
  if (!bundle || !rid) return null;
  return (
    <section aria-label="Result" data-testid="results">
      <h2>Result</h2>
      <p>
        <strong data-testid="result-status">{bundle.status}</strong> <AccuracyBadge value={bundle.accuracy_class} />
      </p>
      {bundle.status === 'failed_quality' && (
        <div className="banner bad" role="alert" data-testid="failed-quality">
          Quality gate failed: the numbers below must not be trusted. See the quality report in each network item.
        </div>
      )}
      <p>{bundle.summary}</p>
      <ProvenanceLine p={bundle.provenance} />
      {bundle.diagnostics.length > 0 && (
        <ul className="flags">{bundle.diagnostics.map((d, i) => <li key={i}>{d.severity} {d.code}: {d.message}</li>)}</ul>
      )}
      {bundle.items.map((item) => (
        <section key={item.id} className="item" data-testid={`item-${item.id}`}>
          <h3>{item.title} <AccuracyBadge value={item.accuracy_class} /></h3>
          <ValidityFlags flags={item.validity_flags} />
          <ItemBody rid={rid} item={item} />
        </section>
      ))}
    </section>
  );
}
