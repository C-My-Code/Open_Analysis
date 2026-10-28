// SPDX-License-Identifier: GPL-3.0-or-later
import { dispatch } from '../commands';
import { useStore } from '../state/store';

export function LayerPanel() {
  const order = useStore((s) => s.layerOrder);
  const style = useStore((s) => s.layerStyle);
  const showOutline = useStore((s) => s.showOutline);
  const showDrills = useStore((s) => s.showDrills);
  const overlays = useStore((s) => s.overlays);
  const top = [...order].reverse(); // list top layer first
  return (
    <section aria-label="Layers">
      <h2>Layers</h2>
      <ul className="rows">
        {top.map((id) => {
          const st = style[id];
          if (!st) return null;
          return (
            <li key={id}>
              <label className="row">
                <input
                  type="checkbox"
                  checked={st.visible}
                  aria-label={`Toggle layer ${id}`}
                  data-testid={`layer-toggle-${id}`}
                  onChange={(e) => void dispatch({ type: 'layer.setVisible', layer: id, visible: e.target.checked })}
                />
                <input
                  type="color"
                  value={st.color}
                  aria-label={`Colour of layer ${id}`}
                  onChange={(e) => void dispatch({ type: 'layer.setColor', layer: id, color: e.target.value })}
                />
                <span className="grow">{id}</span>
              </label>
              <span className="btns">
                <button aria-label={`Raise ${id}`} onClick={() => void dispatch({ type: 'layer.move', layer: id, direction: 'up' })}>↑</button>
                <button aria-label={`Lower ${id}`} onClick={() => void dispatch({ type: 'layer.move', layer: id, direction: 'down' })}>↓</button>
              </span>
            </li>
          );
        })}
        <li>
          <label className="row">
            <input type="checkbox" checked={showOutline} onChange={(e) => void dispatch({ type: 'view.setShow', outline: e.target.checked })} />
            <span>Outline</span>
          </label>
        </li>
        <li>
          <label className="row">
            <input type="checkbox" checked={showDrills} onChange={(e) => void dispatch({ type: 'view.setShow', drills: e.target.checked })} />
            <span>Drills</span>
          </label>
        </li>
      </ul>
      {overlays.length > 0 && (
        <>
          <h3>Overlays</h3>
          <ul className="rows">
            {overlays.map((o) => (
              <li key={o.id}>
                <span className="grow">{o.title} <small>({o.kind})</small></span>
                <button aria-label={`Remove overlay ${o.title}`} onClick={() => void dispatch({ type: 'overlay.remove', id: o.id })}>×</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
