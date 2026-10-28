// SPDX-License-Identifier: GPL-3.0-or-later
import { dispatch } from '../commands';
import { useStore, type Ref } from '../state/store';
import { allViolations } from '../state/refs';
import { formatLengthM } from '../geometry/units';

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <tr>
      <th>{k}</th>
      <td>{v}</td>
    </tr>
  );
}

function Detail({ r }: { r: Ref }) {
  const state = useStore();
  const { index, settings } = state;
  const u = settings.units;
  const zoom = (
    <button onClick={() => void dispatch({ type: 'view.zoomTo', ref: r })}>Zoom to</button>
  );
  if (r.kind === 'item') {
    const it = index?.items.get(r.id);
    if (!it) return null;
    const o = it.obj;
    return (
      <table className="kv" data-testid="inspector">
        <tbody>
          <KV k="Type" v={it.kind} />
          <KV k="Id" v={o.id} />
          <KV k="Net" v={o.net ?? '(none)'} />
          {it.kind === 'track' && <KV k="Layer" v={it.obj.layer} />}
          {it.kind === 'track' && <KV k="Width" v={formatLengthM(it.obj.width, u)} />}
          {it.kind === 'via' && <KV k="Span" v={`${it.obj.start_layer} - ${it.obj.end_layer}`} />}
          {it.kind === 'pad' && <KV k="Pin" v={`${it.obj.component ?? ''}.${it.obj.number}`} />}
          {it.kind === 'zone' && <KV k="Layers" v={it.obj.layers.join(', ')} />}
          <KV k="" v={zoom} />
        </tbody>
      </table>
    );
  }
  if (r.kind === 'net') {
    const n = index?.nets.get(r.id);
    const st = index?.netStats.get(r.id);
    if (!n) return null;
    return (
      <table className="kv" data-testid="inspector">
        <tbody>
          <KV k="Net" v={n.id} />
          <KV k="Kind" v={n.kind} />
          <KV k="Class" v={n.net_class ?? ''} />
          <KV k="Items" v={st?.n_items ?? 0} />
          <KV k="Length" v={formatLengthM((st?.length_mm ?? 0) * 1e-3, u)} />
          <KV k="Layers" v={st?.layers.join(', ') ?? ''} />
          <KV k="" v={zoom} />
        </tbody>
      </table>
    );
  }
  if (r.kind === 'refdes') {
    const c = index?.components.get(r.id);
    if (!c) return null;
    return (
      <table className="kv" data-testid="inspector">
        <tbody>
          <KV k="Refdes" v={c.refdes} />
          <KV k="Value" v={c.value ?? ''} />
          <KV k="Footprint" v={c.footprint ?? ''} />
          <KV k="Side" v={c.side} />
          <KV k="Pads" v={c.pads.length} />
          <KV k="" v={zoom} />
        </tbody>
      </table>
    );
  }
  if (r.kind === 'rule') {
    const vs = allViolations(state).filter((v) => v.rule_id === r.id);
    return (
      <table className="kv" data-testid="inspector">
        <tbody>
          <KV k="Rule" v={r.id} />
          <KV k="Count" v={vs.length} />
          {vs.map((v) => <KV key={v.fingerprint} k={v.severity} v={v.message} />)}
          <KV k="" v={zoom} />
        </tbody>
      </table>
    );
  }
  return <KV k="Layer" v={r.id} />;
}

export function Inspector() {
  const selection = useStore((s) => s.selection);
  return (
    <section aria-label="Inspector">
      <h2>Inspector</h2>
      {selection.length === 0 && <p className="muted">Nothing selected</p>}
      {selection.map((r) => <Detail key={`${r.kind}:${r.id}`} r={r} />)}
    </section>
  );
}
