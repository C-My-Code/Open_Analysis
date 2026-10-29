// SPDX-License-Identifier: GPL-3.0-or-later
import { useMemo, useState } from 'react';
import { dispatch } from '../../commands';
import type { ProfileEvent, ProfileResult } from '../../api/types';
import { PlotlyChart, type PlotClick } from '../common/PlotlyChart';

/** Linear interpolation of y(s) for placing event markers on the curve. */
export function interp(s: number[], y: number[], at: number): number {
  if (s.length === 0) return NaN;
  if (at <= s[0]!) return y[0]!;
  for (let i = 1; i < s.length; i++) {
    if (at <= s[i]!) {
      const f = (at - s[i - 1]!) / (s[i]! - s[i - 1]! || 1);
      return y[i - 1]! + f * (y[i]! - y[i - 1]!);
    }
  }
  return y[y.length - 1]!;
}

/** Cross-probe: highlight (and zoom to) the board object behind a profile event, else the net. */
export async function probeEvent(p: ProfileResult, e: ProfileEvent): Promise<void> {
  if (e.item) {
    const r = await dispatch({ type: 'select.set', refs: [{ kind: 'item', id: e.item }] });
    if (r.ok) { await dispatch({ type: 'view.zoomTo', ref: { kind: 'item', id: e.item } }); return; }
  }
  const nets = Array.isArray(p.net) ? p.net : [p.net];
  const r = await dispatch({ type: 'net.highlight', nets, mode: 'set' });
  if (r.ok) await dispatch({ type: 'view.zoomTo', ref: { kind: 'net', id: nets[0]! } });
}

export function ProfileView({ p }: { p: ProfileResult }) {
  const [qi, setQi] = useState(0);
  const q = p.quantities[qi] ?? p.quantities[0];
  const mm = useMemo(() => p.s.map((v) => v * 1e3), [p.s]);
  const data = useMemo(() => {
    if (!q) return [];
    const d: Record<string, unknown>[] = [{ x: mm, y: q.values, type: 'scatter', mode: 'lines', name: q.name }];
    if (p.events.length) {
      d.push({
        x: p.events.map((e) => e.s * 1e3), y: p.events.map((e) => interp(p.s, q.values, e.s)), type: 'scatter', mode: 'markers',
        name: 'discontinuities', text: p.events.map((e) => `${e.kind}${e.message ? `: ${e.message}` : ''}`), hoverinfo: 'text+x',
        marker: { size: 11, symbol: 'diamond', color: '#f5a524', line: { width: 1, color: '#000' } },
      });
    }
    return d;
  }, [p, q, mm]);
  const layout = useMemo(() => {
    const shapes: Record<string, unknown>[] = [];
    if (q && p.target != null && q.name.toLowerCase().startsWith('z')) {
      const tol = p.tolerance ?? 0;
      shapes.push({ type: 'rect', xref: 'paper', x0: 0, x1: 1, y0: p.target * (1 - tol), y1: p.target * (1 + tol), fillcolor: 'rgba(48,164,108,0.18)', line: { width: 0 } });
    }
    return { shapes, xaxis: { title: { text: 's (mm)' } }, yaxis: { title: { text: q ? `${q.name} (${q.unit})` : '' } } };
  }, [p, q]);
  const onClick = (c: PlotClick) => {
    const e = c.curve === 1 ? p.events[c.point] : undefined;
    if (e) void probeEvent(p, e);
  };
  return (
    <div data-testid="profile-view">
      <h3>{Array.isArray(p.net) ? p.net.join(' / ') : p.net}</h3>
      {p.quantities.length > 1 && (
        <select aria-label="Profile quantity" value={qi} onChange={(e) => setQi(Number(e.target.value))}>
          {p.quantities.map((x, i) => <option key={x.name} value={i}>{x.name} ({x.unit || '-'})</option>)}
        </select>
      )}
      <PlotlyChart data={data} layout={layout} testId="profile-plot" onPointClick={onClick} />
      {p.events.length > 0 && (
        <ul className="rows" aria-label="Discontinuities">
          {p.events.map((e, i) => (
            <li key={i}>
              <button className="link" onClick={() => void probeEvent(p, e)}>
                {(e.s * 1e3).toFixed(2)} mm {e.kind}
              </button>
              <span className="muted">{e.message}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
