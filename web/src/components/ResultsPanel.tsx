// SPDX-License-Identifier: GPL-3.0-or-later
import { dispatch } from '../commands';
import { useStore } from '../state/store';
import { ProfilePlot } from './ProfilePlot';

export function ResultsPanel() {
  const board = useStore((s) => s.board);
  const rid = useStore((s) => s.activeResultId);
  const bundle = useStore((s) => (s.activeResultId ? s.results[s.activeResultId] : undefined));
  const selection = useStore((s) => s.selection);
  return (
    <section aria-label="Results">
      <h2>Analysis</h2>
      <div className="btns">
        {['erc', 'pi.dc'].map((a) => (
          <button key={a} disabled={!board} onClick={() => void dispatch({ type: 'analysis.run', analysis: a })}>
            Run {a}
          </button>
        ))}
      </div>
      {bundle && (
        <div data-testid="results">
          <p>{rid}: <strong>{bundle.status}</strong> - {bundle.summary}</p>
          {bundle.items.map((it, n) =>
            it.kind === 'violations' ? (
              <table className="grid" key={n}>
                <thead><tr><th>Rule</th><th>Sev</th><th>Message</th></tr></thead>
                <tbody>
                  {it.violations.map((v) => (
                    <tr
                      key={v.fingerprint}
                      className={selection.some((r) => r.kind === 'rule' && r.id === v.rule_id) ? 'sel' : ''}
                      onClick={() => void dispatch({ type: 'select.set', refs: [{ kind: 'rule', id: v.rule_id }] })}
                    >
                      <td>{v.rule_id}</td><td>{v.severity}</td><td>{v.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : it.kind === 'profile' ? (
              <ProfilePlot key={n} p={it} />
            ) : null,
          )}
        </div>
      )}
    </section>
  );
}
