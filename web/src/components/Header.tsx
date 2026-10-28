// SPDX-License-Identifier: GPL-3.0-or-later
import { useState } from 'react';
import { dispatch } from '../commands';
import { useStore, type MainTab } from '../state/store';

const TABS: [MainTab, string][] = [['board', '2D'], ['3d', '3D'], ['stackup', 'Stackup']];

export function Header() {
  const projects = useStore((s) => s.projects);
  const projectId = useStore((s) => s.projectId);
  const tab = useStore((s) => s.mainTab);
  const tool = useStore((s) => s.tool);
  const settings = useStore((s) => s.settings);
  const [menu, setMenu] = useState(false);
  return (
    <header className="header">
      <strong>Open Analysis</strong>
      <label>
        Project{' '}
        <select
          aria-label="Project"
          value={projectId ?? ''}
          onChange={(e) => e.target.value && void dispatch({ type: 'project.open', path: e.target.value })}
        >
          <option value="">Open...</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
      </label>
      <nav className="tabs" aria-label="Views">
        {TABS.map(([t, label]) => (
          <button key={t} aria-pressed={tab === t} onClick={() => void dispatch({ type: 'tab.set', tab: t })}>{label}</button>
        ))}
      </nav>
      <span className="btns">
        <button aria-pressed={tool === 'measure'} onClick={() => void dispatch({ type: 'tool.set', tool: tool === 'measure' ? 'pan' : 'measure' })}>Measure</button>
        <button onClick={() => void dispatch({ type: 'view.fit' })}>Fit</button>
        <button onClick={() => void dispatch({ type: 'units.set', units: settings.units === 'mm' ? 'mil' : 'mm' })}>Units: {settings.units}</button>
        <button onClick={() => void dispatch({ type: 'theme.set', theme: settings.theme === 'dark' ? 'light' : 'dark' })}>
          Theme: {settings.theme}
        </button>
      </span>
      <span className="grow" />
      <span className="menu">
        <button aria-expanded={menu} onClick={() => setMenu(!menu)}>Settings</button>
        {menu && (
          <div className="popover" role="dialog" aria-label="Settings">
            <label>
              <input
                type="checkbox"
                checked={settings.assistantEnabled}
                onChange={(e) => void dispatch({ type: 'settings.setAssistantEnabled', enabled: e.target.checked })}
              />{' '}
              Enable Assistant panel
            </label>
          </div>
        )}
      </span>
    </header>
  );
}
