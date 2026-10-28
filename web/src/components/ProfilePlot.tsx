// SPDX-License-Identifier: GPL-3.0-or-later
import { useEffect, useRef } from 'react';
import type { ProfileResult } from '../api/types';
import { useStore } from '../state/store';

/** Plotly line plot of a profile result; Plotly is loaded lazily (own chunk). */
export function ProfilePlot({ p }: { p: ProfileResult }) {
  const el = useRef<HTMLDivElement>(null);
  const theme = useStore((s) => s.settings.theme);
  useEffect(() => {
    let alive = true;
    const node = el.current;
    void import('plotly.js-basic-dist-min').then((mod) => {
      if (!alive || !node) return;
      const Plotly = (mod as unknown as { default: typeof import('plotly.js-basic-dist-min') }).default ?? mod;
      const fg = theme === 'dark' ? '#d8dce2' : '#1c2128';
      void Plotly.react(
        node,
        [{ x: p.s.map((v) => v * 1e3), y: p.values, type: 'scatter', mode: 'lines', name: p.name }],
        {
          margin: { l: 48, r: 8, t: 8, b: 36 }, height: 180, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
          font: { color: fg, size: 11 }, xaxis: { title: { text: 's (mm)' } }, yaxis: { title: { text: `${p.name} (${p.unit})` } },
        },
        { displayModeBar: false, responsive: true },
      );
    });
    return () => { alive = false; };
  }, [p, theme]);
  return <div ref={el} data-testid="profile-plot" />;
}
