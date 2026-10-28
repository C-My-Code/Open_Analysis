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

## Not done yet (vs TASKS.md WP-110)

Arrow geometry decoding, MSW server, Playwright smoke test, Lighthouse/200k-polygon benchmarks,
OpenAPI client generation, and the offline-network build check.
