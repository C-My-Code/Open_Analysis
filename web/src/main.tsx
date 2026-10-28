// SPDX-License-Identifier: GPL-3.0-or-later
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { commandCatalog, dispatch, snapshot } from './commands';
import './styles.css';

// Agent/automation surface: the same commands the UI uses, addressed by stable ids.
(window as unknown as { oa: unknown }).oa = { dispatch, commandCatalog, snapshot };

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
