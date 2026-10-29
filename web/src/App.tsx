// SPDX-License-Identifier: GPL-3.0-or-later
import { lazy, Suspense, useEffect } from 'react';
import { dispatch } from './commands';
import { useStore } from './state/store';
import { Header } from './components/Header';
import { LayerPanel } from './components/LayerPanel';
import { NetBrowser } from './components/NetBrowser';
import { Inspector } from './components/Inspector';
import { ResultsPanel } from './features/results/ResultsPanel';
import { SetupPanel } from './features/setup/SetupPanel';
import { JobsPanel } from './features/jobs/JobsPanel';
import { BoardView } from './components/BoardView';
import { StackupView } from './components/StackupView';
import { AssistantPanel } from './components/AssistantPanel';

const Viewport3D = lazy(() => import('./components/Viewport3D').then((m) => ({ default: m.Viewport3D })));

export function App() {
  const tab = useStore((s) => s.mainTab);
  const theme = useStore((s) => s.settings.theme);
  const status = useStore((s) => s.status);
  const loaded = useStore((s) => !!s.board);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    void dispatch({ type: 'project.list' }).then(() => {
      const first = useStore.getState().projects[0];
      return dispatch({ type: 'project.open', path: first?.id ?? 'sample_board' });
    });
  }, []);

  return (
    <div className="app">
      <Header />
      <div className="body">
        <div className="side left">
          <LayerPanel />
          <NetBrowser />
        </div>
        <main className="main">
          {!loaded && <p className="muted pad">Open a project to begin.</p>}
          {loaded && tab === 'board' && <BoardView />}
          {loaded && tab === '3d' && (
            <Suspense fallback={<p className="muted pad">Loading 3D...</p>}>
              <Viewport3D />
            </Suspense>
          )}
          {loaded && tab === 'stackup' && <StackupView />}
        </main>
        <div className="side right" data-testid="right-panel">
          <Inspector />
          <SetupPanel />
          <JobsPanel />
          <ResultsPanel />
        </div>
        <AssistantPanel />
      </div>
      <footer className="status" role="status">{status}</footer>
    </div>
  );
}
