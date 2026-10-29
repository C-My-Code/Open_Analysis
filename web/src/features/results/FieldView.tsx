// SPDX-License-Identifier: GPL-3.0-or-later
import { dispatch } from '../../commands';
import type { FieldResult } from '../../api/types';
import { viridis } from '../../overlays/colormap';
import { useStore } from '../../state/store';
import { finiteRange } from './rasterize';

const gradient = `linear-gradient(to right, ${[0, 0.25, 0.5, 0.75, 1].map((t) => `rgb(${viridis(t).join(',')})`).join(',')})`;

/** Voltage / current-density heatmaps: drawn on the board as overlays; this panel is the legend and toggle. */
export function FieldView({ rid, item }: { rid: string; item: FieldResult }) {
  const overlays = useStore((s) => s.overlays);
  return (
    <div data-testid="field-view">
      {item.fields.map((f, n) => {
        const id = `${rid}:${item.id}:${n}`;
        const on = overlays.some((o) => o.id === id);
        const [lo, hi] = f.clim_hint ?? finiteRange(f.values);
        return (
          <div key={id} className="field">
            <label>
              <input
                type="checkbox"
                checked={on}
                onChange={(e) => {
                  if (!e.target.checked) { void dispatch({ type: 'overlay.remove', id }); return; }
                  void dispatch({
                    type: 'overlay.add',
                    overlay: {
                      kind: 'heatmap', id, title: `${f.name}${f.layer ? ` (${f.layer})` : ''}`, layer: f.layer ?? 'L1', origin: f.origin,
                      dx: f.dx, dy: f.dy, nx: f.nx, ny: f.ny, values: f.values, unit: f.unit, clim: [lo, hi], opacity: 0.8,
                    },
                  });
                }}
              />{' '}
              {f.name} [{f.unit}]{f.layer ? ` on ${f.layer}` : ''}{f.net ? `, net ${f.net}` : ''}
            </label>
            <div className="colorbar" style={{ background: gradient }} aria-hidden="true" />
            <div className="cbar-labels" data-testid="field-range"><span>{Number(lo.toPrecision(5))}</span><span>{Number(hi.toPrecision(5))} {f.unit}</span></div>
          </div>
        );
      })}
    </div>
  );
}
