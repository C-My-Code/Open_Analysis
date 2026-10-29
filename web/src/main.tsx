// SPDX-License-Identifier: GPL-3.0-or-later
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { commandCatalog, dispatch, setApiClient, snapshot } from './commands';
import { createHttpClient } from './api/httpClient';
import './styles.css';
import './features/features.css';

// `oa serve` prints a URL with #token=...; that (or ?api=http) selects the real API, otherwise the fixture mock.
const token = /[#&]token=([^&]+)/.exec(window.location.hash)?.[1];
const mode = new URLSearchParams(window.location.search).get('api');
if (mode !== 'mock' && (token || mode === 'http')) setApiClient(createHttpClient('/api/v1', token ? decodeURIComponent(token) : ''));

// Agent/automation surface: the same commands the UI uses, addressed by stable ids.
(window as unknown as { oa: unknown }).oa = { dispatch, commandCatalog, snapshot };

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
