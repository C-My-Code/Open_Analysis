# WP-209: API typing gaps found while building the web UI

Status: proposed by WP-209. No spec or Python change was made; each item has a local adapter in `web/src/features/results/adapt.ts` or `web/src/api/httpClient.ts`.

1. **`GET /results/{rid}` is untyped** (`additionalProperties: true`). The UI hand-parses the `oa-result/1` manifest. Proposal: publish `ResultBundleManifest` and the per-kind data models (profile, field, network, table, scalars, violations) in OpenAPI, with `$array` refs typed.
2. **Array names are implicit.** The names for `/results/{rid}/items/{item}/arrays/{name}` (`quantities[0].Z0`, `pieces[0].mesh.nodes`) are derived from `results_view._walk_arrays`. Proposal: let the manifest carry the name next to each `$array` ref.
3. **`RestJobStatus.state` is a plain string.** The UI assumes `queued|running|succeeded|failed|cancelled|cached`. Proposal: make it an enum in the schema. The `/events` WebSocket frames (`JobEvent`) are not in OpenAPI either, so the UI polls `GET /jobs/{jid}`.
4. **`ProfileEvent` has no board location.** Only `s` and an optional `item`; the UI cross-probes through the item (else the net). Proposal: add `location_m` to profile events so every discontinuity can be marked on the board.
5. **Tables are Arrow-only on the server** (`/items/{item}/table`). Inline tables are in the manifest, so the UI decodes Arrow (`apache-arrow`, lazy) only for non-inline ones. A `?format=json` option would remove that dependency from small views.
6. **Dry run goes through `POST /commands/analysis.run`**, not `POST /projects/{pid}/jobs` (`dry_run` is rejected there). Proposal: accept `dry_run` on the jobs endpoint, or add `POST /projects/{pid}/jobs:dry-run`.
7. **Budget is not echoed in `RestJobStatus`.** The jobs panel shows the budget the UI sent. Proposal: include the effective `Budget` and the `PlanEstimate` in the job status.
8. **No `Waiver` write path in REST besides `setups.put`.** The UI keeps waivers in the in-memory ERC setup (dry run) as the task requires; persisting them needs `allow_writes` and `confirm`.
