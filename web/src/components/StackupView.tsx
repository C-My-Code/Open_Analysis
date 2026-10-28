// SPDX-License-Identifier: GPL-3.0-or-later
import { useStore } from '../state/store';
import { formatLengthM } from '../geometry/units';

const KIND_COLOR: Record<string, string> = { conductor: '#c98a2b', dielectric: '#3f7a5a', mask: '#2d5fa8' };

export function StackupView() {
  const board = useStore((s) => s.board);
  const units = useStore((s) => s.settings.units);
  if (!board) return <p className="muted pad">No board</p>;
  const st = board.stackup;
  // Diagram heights are exaggerated (min 6 px) so thin copper stays visible.
  return (
    <div className="stackup pad">
      <svg width="140" height={st.layers.length * 26 + 4} role="img" aria-label="Stackup diagram">
        {st.layers.map((l, i) => (
          <rect key={l.id} x="4" y={i * 26 + 2} width="130" height="24" fill={KIND_COLOR[l.kind]} opacity={0.85} stroke="var(--border)" />
        ))}
        {st.layers.map((l, i) => (
          <text key={`t${l.id}`} x="12" y={i * 26 + 18} fill="#fff" fontSize="12">{l.id}</text>
        ))}
      </svg>
      <table className="grid">
        <thead>
          <tr><th>Layer</th><th>Name</th><th>Kind</th><th>Thickness</th><th>Material</th><th>Dk / Df</th></tr>
        </thead>
        <tbody>
          {st.layers.map((l) => {
            const m = st.materials.find((x) => x.name === l.material);
            return (
              <tr key={l.id}>
                <td>{l.id}</td>
                <td>{l.source_name ?? ''}</td>
                <td>{l.kind}</td>
                <td>{formatLengthM(l.thickness, units === 'mm' ? 'mm' : 'mil')}</td>
                <td>{l.material}</td>
                <td>{m?.model?.dk != null ? `${m.model.dk} / ${m.model.df}` : ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="muted">Total {formatLengthM(st.total_thickness, units)}{st.finish ? `, finish ${st.finish}` : ''}</p>
    </div>
  );
}
