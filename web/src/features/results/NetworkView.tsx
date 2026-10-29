// SPDX-License-Identifier: GPL-3.0-or-later
import { useMemo, useState } from 'react';
import type { NetworkResult } from '../../api/types';
import { PlotlyChart } from '../common/PlotlyChart';

/** S_ij(f) as dB and unwrapped phase in degrees (index = i*P + j). */
export function sparam(n: NetworkResult, i: number, j: number): { f: number[]; db: number[]; phase: number[] } {
  const P = n.port_names.length;
  const f = n.freqs.map((x) => x / 1e9);
  const db: number[] = [];
  const phase: number[] = [];
  let prev = 0, off = 0;
  n.freqs.forEach((_, k) => {
    const re = n.s_re[k * P * P + i * P + j]!, im = n.s_im[k * P * P + i * P + j]!;
    db.push(20 * Math.log10(Math.max(Math.hypot(re, im), 1e-12)));
    let a = (Math.atan2(im, re) * 180) / Math.PI + off;
    if (k > 0) {
      while (a - prev > 180) { a -= 360; off -= 360; }
      while (a - prev < -180) { a += 360; off += 360; }
    }
    prev = a;
    phase.push(a);
  });
  return { f, db, phase };
}

const VERDICT: Record<string, string> = { pass: 'pass', warn: 'warn', fail: 'fail' };

export function NetworkView({ n }: { n: NetworkResult }) {
  const P = n.port_names.length;
  const [ij, setIj] = useState<[number, number]>([P > 1 ? 1 : 0, 0]);
  const sp = useMemo(() => sparam(n, ij[0], ij[1]), [n, ij]);
  const name = `S(${n.port_names[ij[0]]},${n.port_names[ij[1]]})`;
  const data = useMemo(
    () => [
      { x: sp.f, y: sp.db, type: 'scatter', mode: 'lines', name: `${name} dB` },
      { x: sp.f, y: sp.phase, type: 'scatter', mode: 'lines', name: `${name} phase`, yaxis: 'y2', line: { dash: 'dot' } },
    ],
    [sp, name],
  );
  const layout = useMemo(
    () => ({
      xaxis: { title: { text: 'f (GHz)' } }, yaxis: { title: { text: 'dB' } },
      yaxis2: { title: { text: 'phase (deg)' }, overlaying: 'y', side: 'right', showgrid: false },
    }),
    [],
  );
  const q = n.quality;
  return (
    <div data-testid="network-view">
      {q && (
        <p>
          Quality: <span className={`badge q-${q.verdict}`} data-testid="quality-badge">{VERDICT[q.verdict]}</span>
          {' '}<span className="muted">PQM {q.pqm.toFixed(1)}, RQM {q.rqm.toFixed(1)}, CQM {q.cqm.toFixed(1)}</span>
        </p>
      )}
      {q && q.verdict !== 'pass' && (
        <ul className="flags bad" data-testid="quality-report">
          <li>max singular value {q.max_singular_value.toPrecision(5)}, reciprocity error {q.reciprocity_max_err.toExponential(2)}, DC {q.dc}</li>
          {q.fixes_applied.map((x) => <li key={x}>fix applied: {x}</li>)}
          {q.notes.map((x) => <li key={x}>{x}</li>)}
        </ul>
      )}
      <label>
        Parameter{' '}
        <select
          aria-label="S-parameter"
          value={`${ij[0]},${ij[1]}`}
          onChange={(e) => setIj(e.target.value.split(',').map(Number) as [number, number])}
        >
          {n.port_names.flatMap((a, i) => n.port_names.map((b, j) => <option key={`${i},${j}`} value={`${i},${j}`}>S({a},{b})</option>))}
        </select>
      </label>
      <PlotlyChart data={data} layout={layout} testId="sparam-plot" height={240} />
    </div>
  );
}
