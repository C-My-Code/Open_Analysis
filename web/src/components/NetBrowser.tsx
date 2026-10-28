// SPDX-License-Identifier: GPL-3.0-or-later
import { dispatch } from '../commands';
import { useStore } from '../state/store';
import { formatLengthMm } from '../geometry/units';

export function NetBrowser() {
  const board = useStore((s) => s.board);
  const index = useStore((s) => s.index);
  const query = useStore((s) => s.netQuery);
  const highlighted = useStore((s) => s.highlightedNets);
  const selection = useStore((s) => s.selection);
  const units = useStore((s) => s.settings.units);
  if (!board || !index) return <section aria-label="Nets"><h2>Nets</h2><p className="muted">No board</p></section>;
  const q = query.toLowerCase();
  const nets = board.nets.filter((n) => n.id.toLowerCase().includes(q));
  const comps = (board.components ?? []).filter((c) => c.refdes.toLowerCase().includes(q));
  const selected = (kind: string, id: string) => selection.some((r) => r.kind === kind && r.id === id);
  return (
    <section aria-label="Nets">
      <h2>Nets</h2>
      <input
        type="search"
        placeholder="Search nets / refdes"
        aria-label="Search nets"
        value={query}
        onChange={(e) => void dispatch({ type: 'net.search', query: e.target.value })}
      />
      <ul className="rows">
        {nets.map((n) => {
          const st = index.netStats.get(n.id);
          return (
            <li key={n.id} className={selected('net', n.id) ? 'sel' : ''}>
              <input
                type="checkbox"
                aria-label={`Highlight net ${n.id}`}
                checked={highlighted.includes(n.id)}
                onChange={() => void dispatch({ type: 'net.highlight', nets: [n.id], mode: 'toggle' })}
              />
              <button className="link grow" onClick={() => void dispatch({ type: 'select.set', refs: [{ kind: 'net', id: n.id }] })}>
                {n.id}
              </button>
              <small className="muted">{st ? formatLengthMm(st.length_mm, units) : ''}</small>
            </li>
          );
        })}
      </ul>
      {comps.length > 0 && (
        <>
          <h3>Components</h3>
          <ul className="rows">
            {comps.map((c) => (
              <li key={c.refdes} className={selected('refdes', c.refdes) ? 'sel' : ''}>
                <button className="link grow" onClick={() => void dispatch({ type: 'select.set', refs: [{ kind: 'refdes', id: c.refdes }] })}>
                  {c.refdes}
                </button>
                <small className="muted">{c.value}</small>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
