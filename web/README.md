# oa-web: Open Analysis web UI (WP-110)

React 18 + TypeScript + Vite, deck.gl (2D board), three.js (3D stub), Plotly (lazy, plots).
Package manager: **npm** (pnpm was not available in the dev environment; `package-lock.json` is committed).

```
npm ci
npm run dev        # http://localhost:5173, uses the mock API
npm run typecheck && npm run lint && npm test && npm run build   # build output: web/dist
```

## Architecture

- `src/types/board.ts`: hand-written Board Model v1 types (docs/specs/board-model.md).
  **Schema switch point:** when `schemas/board-model.v1.json` exists, generate into `src/types/generated/`
  and re-export from this file. Nothing else imports generated types directly.
- `src/api/types.ts` (`ApiClient` interface + DTOs) is the only backend seam. `src/mock/mockClient.ts` is the typed
  fixture client (no network); `src/api/httpClient.ts` targets the WP-207 REST API. **Switch point:** generate
  DTOs from `web/src/api/openapi.json` when WP-207 lands, then call `setApiClient(createHttpClient(...))`.
- `src/commands/index.ts`: **every user action is a typed command** (`dispatch({type: 'layer.setVisible', ...})`).
  Components only read the store and call `dispatch`; only command handlers write it. `commandCatalog` describes
  all commands for agents, `snapshot()` returns agent-readable state, and both are exposed as `window.oa`.
- Selection/highlight use stable ids (`Ref`: net name, refdes, item id, rule id, layer id).
- Overlays: `dispatch({type: 'overlay.add', overlay})` with `markers | heatmap | polylines` (metres, board frame).
  `analysis.run` / `result.show` turn result bundles into overlays.
- Assistant: `AssistantPanel` is a placeholder slot (no LLM calls), hidden unless "Enable Assistant panel" is set.
- Units: Board JSON is metres, Y up; the view uses mm world units (`M_TO_MM`), mm/mil is display-only.

## WP-209: setup forms, jobs, result viewers

- `src/api/generated/schema.d.ts` is generated from `src/api/openapi.json` (`npm run gen:api`); `npm run build` runs
  the freshness gate `npm run gen:api:check`. Wire DTOs in `src/api/types.ts` alias the generated types. The result
  manifest is an untyped object in OpenAPI, so `src/features/results/adapt.ts` builds the UI-level `ResultBundle`
  (arrays come from the `.npy` endpoint, violations from the paged `/violations` endpoint, TriMesh fields are
  rasterised for the heatmap overlay).
- `src/features/setup`: RJSF forms generated from the `/analyses` config schemas (impedance.xsection,
  impedance.profile, erc, pi.dc), a NetSelector datalist widget, an "Edit as JSON" fallback, dry run and budget.
  Waivers added from the violation list go into the in-memory ERC setup only (dry run); nothing is written.
- `src/features/jobs`: jobs panel (progress, cancel, budget). Jobs are polled with `job.refresh`; the `/events` WebSocket is not used yet.
- `src/features/results`: profile (Plotly, discontinuity markers cross-probe to board items), violations (board
  markers, remediation, waivers), field heatmap overlay with legend, S-parameters (dB/phase, quality badge, failed_quality
  banner), tables, scalars. accuracy_class and provenance are shown on every bundle and item.
- Client selection (`src/main.tsx`): a `#token=` fragment (as printed by `oa serve`) or `?api=http` uses the real API,
  `?api=mock` or neither uses the fixture mock.
- Tests: `npm test` (Vitest; MSW mock server in `tests/features/msw.ts`, jsdom component tests).
  `npm run build && npm run test:e2e` runs the Playwright smoke test (one Chromium, one worker) against a real
  `oa serve` on 127.0.0.1 with `tests/boards/kicad10/microstrip`; set `OA_BIN` if `oa` is not on PATH.
  First time: `npx playwright install chromium`.

## Not done yet

Arrow geometry decoding (the server has `/board/layers/{layer}/geometry|tracks` and `/board/drills`, but the UI still
loads `/board/full`), the WebSocket job feed, TDR and Smith views, 3D region preview, port picker, export buttons,
Lighthouse/200k-polygon benchmarks, and the offline-network build check. Arrow IPC is decoded only for non-inline tables.
