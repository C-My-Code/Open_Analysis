// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useMemo, useRef } from 'react';
import DeckGL from '@deck.gl/react';
import { OrthographicView } from '@deck.gl/core';
import { useShallow } from 'zustand/react/shallow';
import { dispatch } from '../commands';
import { activeNets, useStore } from '../state/store';
import { buildDeckLayers } from './boardLayers';
import { formatLengthMm } from '../geometry/units';

// Y up: flipY=false so board coordinates render as seen from the top of the board.
const VIEW = new OrthographicView({ id: 'board', flipY: false });

export function BoardView() {
  const ref = useRef<HTMLDivElement>(null);
  const s = useStore(
    useShallow((st) => ({
      render: st.render, layerOrder: st.layerOrder, layerStyle: st.layerStyle, showOutline: st.showOutline,
      showDrills: st.showDrills, overlays: st.overlays, measure: st.measure, theme: st.settings.theme,
      units: st.settings.units, viewState: st.viewState, tool: st.tool, selection: st.selection,
      highlighted: st.highlightedNets, index: st.index,
    })),
  );
  const nets = useStore((st) => activeNets(st).join('\u0000'));

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      if (e) void dispatch({ type: 'view.setViewport', w: e.contentRect.width, h: e.contentRect.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const layers = useMemo(() => {
    if (!s.render) return [];
    const selectedItems = s.selection.flatMap((r) => {
      if (r.kind === 'item') return [r.id];
      if (r.kind === 'refdes') return s.index?.components.get(r.id)?.pads ?? [];
      return [];
    });
    const rule = s.selection.find((r) => r.kind === 'rule');
    return buildDeckLayers({
      render: s.render, layerOrder: s.layerOrder, layerStyle: s.layerStyle, activeNets: nets ? nets.split('\u0000') : [],
      selectedItems, selectedRule: rule?.id ?? null, showOutline: s.showOutline, showDrills: s.showDrills,
      overlays: s.overlays, measure: s.measure, theme: s.theme,
    });
  }, [s.render, s.layerOrder, s.layerStyle, s.showOutline, s.showDrills, s.overlays, s.measure, s.theme, s.selection, s.index, nets]);

  const measureText = useMemo(() => {
    if (s.measure.length !== 2) return s.tool === 'measure' ? 'Click two points to measure' : null;
    const [a, b] = s.measure as [[number, number], [number, number]];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    return `d ${formatLengthMm(Math.hypot(dx, dy), s.units)}  dx ${formatLengthMm(dx, s.units)}  dy ${formatLengthMm(dy, s.units)}`;
  }, [s.measure, s.tool, s.units]);

  return (
    <div ref={ref} className="board-view" data-testid="board-view">
      <DeckGL
        views={VIEW}
        viewState={s.viewState}
        controller={{ doubleClickZoom: false, scrollZoom: { smooth: true } }}
        layers={layers}
        getCursor={({ isDragging }) => (isDragging ? 'grabbing' : s.tool === 'measure' ? 'crosshair' : 'grab')}
        onViewStateChange={({ viewState }) => {
          const v = viewState as unknown as { target: [number, number, number]; zoom: number };
          void dispatch({ type: 'view.set', viewState: { target: [v.target[0], v.target[1], 0], zoom: v.zoom } });
        }}
        onClick={(info) => {
          if (s.tool === 'measure') {
            if (info.coordinate) void dispatch({ type: 'measure.addPoint', point: [info.coordinate[0]!, info.coordinate[1]!] });
            return;
          }
          const o = info.object as { id?: string; rule_id?: string } | null;
          if (o?.rule_id) void dispatch({ type: 'select.set', refs: [{ kind: 'rule', id: o.rule_id }] });
          else if (o?.id) void dispatch({ type: 'select.set', refs: [{ kind: 'item', id: o.id }] });
          else void dispatch({ type: 'select.clear' });
        }}
        getTooltip={({ object }) => {
          const o = object as { id?: string; net?: string | null; label?: string } | null;
          if (!o) return null;
          return o.label ?? (o.id ? `${o.id}${o.net ? ` (${o.net})` : ''}` : null);
        }}
      />
      {measureText && <div className="hud">{measureText}</div>}
    </div>
  );
}
