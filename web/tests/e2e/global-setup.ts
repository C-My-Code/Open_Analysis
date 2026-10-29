// SPDX-License-Identifier: GPL-3.0-or-later
import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));

/**
 * Starts `oa serve <microstrip board>` on a free 127.0.0.1 port and publishes the URL (with the
 * #token fragment printed on stderr) as OA_E2E_URL. Env: OA_BIN (default `oa`), OA_E2E_BOARD.
 */
export default async function globalSetup(): Promise<() => Promise<void>> {
  const root = resolve(here, '../../..');
  const board = process.env.OA_E2E_BOARD ?? resolve(root, 'tests/boards/kicad10/microstrip/board.kicad_pcb');
  const bin = process.env.OA_BIN ?? 'oa';
  const child: ChildProcess = spawn(bin, ['serve', board, '--no-browser', '--host', '127.0.0.1', '--port', '0'], {
    env: {
      ...process.env,
      OA_WEB_DIST: resolve(here, '../../dist'),
      OMP_NUM_THREADS: '2', OPENBLAS_NUM_THREADS: '2', MKL_NUM_THREADS: '2', OA_NUM_THREADS: '2',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const url = await new Promise<string>((ok, fail) => {
    let buf = '';
    const t = setTimeout(() => fail(new Error(`oa serve printed no URL in 60 s:\n${buf}`)), 60_000);
    child.stderr!.on('data', (d: Buffer) => {
      buf += d.toString();
      const m = /(http:\/\/127\.0\.0\.1:\d+\/#token=\S+)/.exec(buf);
      if (m) { clearTimeout(t); ok(m[1]!); }
    });
    child.on('exit', (code) => fail(new Error(`oa serve exited (${code}):\n${buf}`)));
    child.on('error', fail);
  });
  process.env.OA_E2E_URL = url;
  return async () => {
    child.removeAllListeners('exit');
    // The Windows `oa.exe` launcher forks the real Python process (plus job workers): kill the whole tree.
    if (process.platform === 'win32' && child.pid) spawnSync('taskkill', ['/PID', String(child.pid), '/T', '/F']);
    else child.kill();
  };
}
