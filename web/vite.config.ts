// SPDX-License-Identifier: GPL-3.0-or-later
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Relative base so `oa serve` can host web/dist from any mount point.
  base: './',
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 2500 },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'tests/features/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/features/setup.ts'],
  },
});
