// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef } from 'react';
import { useStore } from '../../state/store';

export interface PlotClick {
  curve: number;
  point: number;
}

interface Props {
  data: Record<string, unknown>[];
  layout?: Record<string, unknown>;
  height?: number;
  testId: string;
  onPointClick?: (c: PlotClick) => void;
}

type PlotDiv = HTMLDivElement & { on?: (ev: string, cb: (e: { points: { curveNumber: number; pointNumber: number }[] }) => void) => void; removeAllListeners?: (ev: string) => void };

/** Plotly chart (lazy-loaded, own chunk) that follows the app theme. */
export function PlotlyChart({ data, layout, height = 200, testId, onPointClick }: Props) {
  const el = useRef<PlotDiv>(null);
  const theme = useStore((s) => s.settings.theme);
  const click = useRef(onPointClick);
  useEffect(() => { click.current = onPointClick; });
  useEffect(() => {
    let alive = true;
    const node = el.current;
    void import('plotly.js-basic-dist-min').then((mod) => {
      if (!alive || !node) return;
      const Plotly = (mod as unknown as { default: typeof import('plotly.js-basic-dist-min') }).default ?? mod;
      const fg = theme === 'dark' ? '#d8dce2' : '#1c2128';
      const grid = theme === 'dark' ? '#2d343d' : '#d0d7de';
      const ax = (a?: unknown) => ({ gridcolor: grid, zerolinecolor: grid, ...(a as object) });
      const l = (layout ?? {}) as { xaxis?: unknown; yaxis?: unknown; yaxis2?: unknown };
      void Plotly.react(
        node,
        data,
        {
          margin: { l: 52, r: 8, t: 8, b: 36 }, height, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
          font: { color: fg, size: 11 }, showlegend: data.length > 1, legend: { orientation: 'h', y: -0.3 },
          ...layout, xaxis: ax(l.xaxis), yaxis: ax(l.yaxis), ...(l.yaxis2 ? { yaxis2: ax(l.yaxis2) } : {}),
        },
        { displayModeBar: false, responsive: true },
      ).then(() => {
        node.removeAllListeners?.('plotly_click');
        node.on?.('plotly_click', (e) => {
          const p = e.points[0];
          if (p) click.current?.({ curve: p.curveNumber, point: p.pointNumber });
        });
      });
    });
    return () => { alive = false; };
  }, [data, layout, height, theme]);
  return <div ref={el} data-testid={testId} />;
}
