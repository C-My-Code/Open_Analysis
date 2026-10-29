// SPDX-License-Identifier: GPL-3.0-or-later
import { defineConfig } from '@playwright/test';

/**
 * Smoke test against the real `oa serve` on 127.0.0.1 (see tests/e2e/global-setup.ts).
 * One Chromium, one worker: the dev machine froze once under parallel load.
 * Needs `npm run build` first (the server hosts web/dist) and `oa` on PATH or OA_BIN.
 */
export default defineConfig({
  testDir: 'tests/e2e',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 90_000,
  reporter: [['list']],
  globalSetup: './tests/e2e/global-setup.ts',
  use: { browserName: 'chromium', headless: true, trace: 'off' },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
