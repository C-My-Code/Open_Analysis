# Work Breakdown for Parallel Implementation

Status: v1, 2026-10-27 (AI integration added per ADR-0007; M1P added 2026-10-29 per ADR-0009/0010; M2 EMC WPs 420-426 and WP-506 Part B added 2026-10-29 per ADR-0011). This file covers M0, M1 and M1P (performance and accuracy, between the M1 features and the M1 release) in full detail and sketches M2 at the end. Contracts: [specs/board-model.md](specs/board-model.md), [specs/interfaces.md](specs/interfaces.md), [specs/ai-integration.md](specs/ai-integration.md). Architecture: [ARCHITECTURE.md](ARCHITECTURE.md). Gates: [ROADMAP.md](ROADMAP.md).

## Rules of engagement (every agent reads this first)

1. **Owned directories are exclusive.** Within a wave, no two WPs own overlapping paths. Write only inside your owned paths. Reading is unrestricted.
2. **Shared root files.**
   - `pyproject.toml` (root), `uv.lock` and `.github/workflows/ci.yml` belong to WP-000 and, after it, to the integrator.
   - You add dependencies only to *your package's* `pyproject.toml`. The integrator re-runs `uv lock` at merge. Never commit `uv.lock` changes from a WP branch.
   - Extra CI for your area goes in your own workflow file `.github/workflows/<wp-id>.yml`.
3. **Contracts are frozen.** If a contract in `docs/specs/` blocks you:
   - Write `docs/change-requests/<WP-ID>-<slug>.md` describing the problem, the proposed change and the impact. That file is always yours.
   - Implement a local adapter, and do not edit the spec.
   - The architecture owner decides, and if needed issues an ADR.
4. **Use the test harness.** Use `oa_testkit` fixtures and `oa_testkit.harness.run_inline()` instead of depending on unfinished packages. Wave-1 analyses must not need `oa_jobs` to test.
5. **Golden data.** Each WP owns `tests/golden/<suite>/` for the suites named in its acceptance criteria. Every case has a `case.toml` with `source` (citation or "analytic: <formula>"), `tolerance`, `inputs` and `expected`. Regenerate only with `--update-golden`, and list regenerated files in the PR description.
6. **Definition of done.**
   - All acceptance tests pass on Windows and Linux. macOS passes for the pure-Python packages.
   - `ruff`, `mypy` (strict where required) and `lint-imports` are clean.
   - A package README states the public API, the contracts implemented and the golden suites.
   - Every new source file has an SPDX header.
   - There are no `TODO`s in public APIs.
7. **AI and agent contracts** (ADR-0007, [specs/ai-integration.md](specs/ai-integration.md); §13 there is a binding addendum to interfaces.md):
   - Every public model field has a `description` with its unit in `[...]` (ai-integration §7.1).
   - Errors are `ErrorInfo{code, message, path, hint}` (§6). Never raise bare exceptions across a package boundary.
   - Every `ResultItem` you produce sets `accuracy` (§4.1). Every payload's `summary()` stays within the size caps (§4.2).
   - Every violation sets `remediation` and `measured`/`limit`/`unit` for threshold rules (§5).
   - User-level operations are `oa_service` commands. CLI, REST and MCP code contains no logic of its own.
   - Read-only by default. Only `write` commands touch user files, through propose → apply (§10.3).
   - No network and no LLM calls outside `oa-assistant`. Import-linter and a socket guard enforce this.
   - Output ordering is deterministic (§3.3, §3.5).
8. **Sizes** (one agent, focused):
   - S ≤ 2 days.
   - M 3-5 days.
   - L 6-10 days.
   - XL > 10 days (split it if possible).

8. **Test helper modules need unique names.** Tests run under `--import-mode=importlib`, with each package's `tests/` directory on `sys.path`. A non-test module in `tests/` therefore shares one global namespace with every other package's test helpers. Name helpers with a package prefix, such as `geometry_helpers.py`, and never `from conftest import ...`.

9. **Keep iteration cheap.**
   - While iterating, run only your own package's tests with `uv run pytest packages/<pkg> -q`, plus `ruff`/`mypy` on your paths.
   - Run the full CI command set once, before reporting done: `ruff check`, `ruff format --check`, `mypy`, `lint-imports`, and `pytest -m "not external and not kicad"`.
   - Read only the spec sections your brief names. Look up other sections only when you hit a specific question.

10. **Respect machine limits.** These are now enforced in code by the root `conftest.py`: each pytest run is capped at 8 GB of memory (`OA_TEST_MEM_GB`), and `slow` tests are skipped unless `OA_RUN_SLOW=1`. Only the agent the integrator names may set `OA_RUN_SLOW=1`. The dev machine has 8 CPUs, and an earlier run of parallel agents froze it.
    - Thread caps are set by the root `conftest.py`: numerical libraries and openEMS get 2 threads each. Shell commands outside pytest must `export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 OA_NUM_THREADS=2`.
    - Only the agent the integrator names may run `slow` or `external` tests, such as 3D meshing, openEMS or large solves. Everyone else uses `-m "not external and not kicad and not slow"`.
    - Use oa_jobs budgets (`max_runtime_s` and `max_memory_gb`, 8 GB by default) for solver runs.
    - Before reporting done, check that no stray python, openEMS or gmsh processes from your worktree are still running, and end your own if any are.

11. **One heavy/GPU token (M1P onward).** The reference machine (Ryzen 9 9950X3D 16C/32T, 47 GB, RTX 4080 SUPER 16 GB) has frozen under uncapped load.
    - At most **3 agents** run at once, and at most **one** of them holds the token at a time. The integrator names the holder, and the holder releases the token when its block ends.
    - Only the token holder may:
      - use the GPU at all (`OA_RUN_GPU=1`, `gpu` marker, `OA_DEVICE=cuda`);
      - run `slow`/`external` tests, Palace, openEMS or oa-fdtd beyond unit size;
      - build Palace;
      - run the `bench` profile of [BENCHMARKS.md](BENCHMARKS.md) §1.
    - Everyone else runs on `OA_DEVICE=cpu` with rule 10's caps and queues heavy runs for their next token block.
    - Token blocks last at most 2 hours, except the WP-511 gate run.
    - Rust builds (`cargo`, `maturin`) use `-j 2` (`CARGO_BUILD_JOBS=2`).
    - The rule 10 caps (2 threads, 8 GB per pytest run, oa_jobs budgets) stay in force for everyone, the token holder included, outside the `bench` profile.

---

## Dependency graph

```mermaid
flowchart LR
  WP000[WP-000 Scaffold + model + contracts + fixtures]
  subgraph W1[Wave 1]
    WP101[101 KiCad reader]
    WP102[102 Synthetic KiCad 10 boards]
    WP103[103 IPC-2581 reader + KiCad bridge]
    WP104[104 Geometry + ReferenceMap]
    WP105[105 Impedance closed-form + BEM]
    WP106[106 Physics + via model + S-param gate]
    WP107[107 Jobs, runners, result IO]
    WP108[108 openEMS adapter]
    WP109[109 Mesh 2D]
    WP110[110 Web shell + board viewer]
  end
  subgraph W2[Wave 2]
    WP201[201 ERC/EMI engine + M1 rules]
    WP202[202 Impedance FEM reference + R f]
    WP203[203 Impedance profile analysis]
    WP204[204 DC IR drop]
    WP205[205 EM flow: region to EMProblem to S-params]
    WP206[206 3D OCC mesher]
    WP207[207 Server + CLI]
    WP208[208 Ingest cross-validation]
    WP209[209 Web setup/jobs/results views]
    WP210[210 MCP server + agent docs]
  end
  subgraph W3[Wave 3]
    WP301[301 Palace adapter]
    WP302[302 Validation suite + validation page]
    WP303[303 M1 end-to-end + release]
    WP304[304 Performance benchmarks]
  end
  WP000 --> W1
  WP104 --> WP201 & WP203 & WP204 & WP205
  WP105 --> WP201 & WP202 & WP203
  WP106 --> WP201 & WP202 & WP204 & WP205
  WP109 --> WP202 & WP204 & WP206
  WP107 --> WP205 & WP207 & WP210
  WP207 -. oa_service protocol + FakeService .-> WP210
  WP108 --> WP205
  WP101 & WP102 & WP103 --> WP208
  WP110 --> WP209
  WP207 --> WP209
  WP205 & WP206 --> WP301
  WP201 & WP202 & WP203 & WP204 & WP205 & WP208 --> WP302
  WP301 --> WP302
  WP207 & WP209 & WP210 & WP302 --> WP303
  WP101 & WP104 & WP201 & WP204 --> WP304
```

Critical path: WP-000 → WP-104 → WP-205 → WP-301 → WP-302 → WP-303.

---

## Wave 0 (one agent)

### WP-000 Scaffold, frozen model, contracts, fixtures. Size: L (≈ 8-10 days)
**Owned:**
- `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `LICENSE`, `LICENSES/`, `REUSE.toml`;
- `packages/*/pyproject.toml` (skeletons for **all** packages in interfaces.md §0, including reserved names);
- `packages/oa-model/**`, `packages/oa-results/**` (types only; `src/oa_results/io/` is left as an empty package for WP-107);
- `packages/oa-framework/**`, `packages/oa-testkit/**`;
- `schemas/**`, `tests/fixtures/**`, `docs/adr/**` (already present), `mkdocs.yml`.

**Inputs:** this repository's docs.

**Deliverables:**
1. **Workspace.**
   - A uv workspace with every package skeleton (`src/<name>/__init__.py`, `py.typed`, `tests/`).
   - Expected third-party dependencies pre-declared per package (list in ARCHITECTURE.md §2 and §6) to minimize later lock churn.
   - Ruff, mypy and pytest config.
   - Import-linter `layers` + `independence` contracts exactly as interfaces.md §0.
   - REUSE/SPDX setup.
   - CI on {windows, ubuntu, macos} × {3.11, 3.13} with lint, types, tests, lint-imports, `reuse lint`, `pip-licenses` against an allow-list, and a `schema-compat` check.
2. **`oa_model`**, complete per board-model.md:
   - all models and `Points`/`Floats`/`NDArray`;
   - units and `parse_quantity`;
   - `validate_board` with every listed code;
   - `BoardIndex`;
   - `apply_physics`, `resolve_intent`;
   - setup schemas (ports, intent, `ProjectManifest`, `AnalysisSetup`);
   - `xsection` (CrossSection + `canonicalize` + `key`);
   - `em` (Structure3D, EMProblem and so on); `qs` and `thermal` reserved types;
   - load/dump `.oab.json` and `.oab`, migrations scaffold, newer-minor pruning;
   - the material library `data/materials.yaml` (≥ 8 entries with citations: generic FR-4, a 370HR-class laminate, Megtron-6-class, Rogers 4350B-class, PTFE, LPI solder mask, copper ED, copper rolled).
3. **`oa_results`** types (interfaces.md §1.1, §3.7, §4.1). **`oa_framework`**: protocols, `Registry` (entry points and `for_tests`), `Services`, the `JobService` protocol, `ExternalCommand`/`ExternalRunner` types, errors.
4. **`oa_testkit`.**
   - Builders: `microstrip_board`, `stripline_board`, `diff_pair_board`, `cpwg_board`, `split_plane_board`, `via_transition_board`, `power_plane_board`, each parameterized and each returning `Board`.
   - CrossSection fixtures: the HJ table cases, Cohn stripline cases, edge-coupled stripline, CPW S=1/G=0.5, coax ln(b/a)=1, wire over plane, two-wire line.
   - EMProblem fixtures: a 25 mm microstrip thru with 2 lumped ports, a 25 mm stripline thru with 2 wave ports, a single via transition with 2 wave ports.
   - `golden.assert_close_to_golden`, the `--update-golden` option, markers (`golden`, `external`, `slow`, `kicad`), and `harness.run_inline(analysis, board, setup, registry)` (plan → run_task → reduce with an in-memory RunContext).
5. `schemas/board-model.v1.json`, `oa-physics.v1.json`, `oa-intent.v1.json`, `oa-setup.v1.json`, `oa-xsection.v1.json`, `oa-emproblem.v1.json`, `oa-result.v1.json` plus `schemas/CHANGELOG.md`. The fixture dumps go in `tests/fixtures/model/*.oab.json`.

**Acceptance:**
- `uv sync && uv run pytest` passes on the CI matrix. `lint-imports` passes. A test proves a forbidden import (`oa_erc` → `oa_pi`) is caught, using a throwaway contract on a temp package.
- Round trip: for every builder, `load_board(dump_board(b)) == b`, and `content_hash` is identical across `.oab.json`/`.oab` and across Windows/Linux (a CI artifact comparison).
- `validate_board(strict=True)` passes for every builder. Each `MODEL.*` code has a negative test that triggers it.
- Hypothesis tests: `Polygon` orientation normalization helper, `Points` immutability, `parse_quantity` (≥ 30 examples, including `5mil` = 1.27e-4, `1oz` rejected for length, `25degC` = 298.15).
- `CrossSection.key()`: invariant under translation and provenance changes, changes under a 2 nm move, stable under 0.4 nm jitter.
- `apply_physics` on the microstrip builder with the example sidecar in board-model.md §11 gives the documented layers. `ConstantDkDf` is upgraded to DS.
- `harness.run_inline` runs a dummy analysis (in the testkit's tests) with 3 tasks and a reduce.
- Freeze declaration: `docs/specs/board-model.md` header changed to "FROZEN v1.0 (commit <sha>)".

**AI addendum (ADR-0007; ai-integration.md §13 items A1-A10, A13):**
- 000B:
  - `ErrorInfo`, plus `OAError.path/hint/details/info()`.
  - `SetupError`/`BoardValidationError.issues`, mapped from pydantic errors with JSON-Pointer paths.
  - `Diagnostic.path/hint`.
  - A field `description` with units on every `oa_model` field.
- 000A:
  - `Accuracy`, plus `ResultItem.accuracy`.
  - `ResultSummary`/`BundleSummary`, plus `summary()` on every payload type and `ResultBundle.digest()`.
  - The `Provenance` additions.
  - `Remediation`, plus `Violation.remediation`, with a validator filling `fix_hint`.
  - `Budget`, `PlanEstimate`, `AnalysisRequest.budget`, `JobStatus.cancel_reason`, `JobService.estimate`.
  - The `"surrogate"` method literal.
  - The new entry-point group names.
- Acceptance:
  - Every committed fixture bundle (one per payload kind) yields a `ResultSummary` ≤ 2 KiB and a `BundleSummary` ≤ 8 KiB.
  - `ResultBundle.summary == digest().text`.
  - A pydantic `extra_forbidden` error on a setup becomes an `ErrorInfo` with `path="/config/<field>"` and a hint.
  - The JSON Schemas in `schemas/` have a `description` on every property, checked by a temporary local test until WP-210's lint lands.

---

## Wave 1 (10 parallel WPs, all depend only on WP-000)

### WP-101 KiCad native reader. Size: L
**Owned:** `packages/oa-ingest-kicad/**`, `tests/golden/ingest_kicad/**`

**Inputs:**
- WP-000 and board-model.md.
- docs/research/01-ingest.md (whole file).
- KiCad demos at `D:\Program Files\KiCad\10.0\share\kicad\demos` (read-only, for development).
- Hand-written S-expression snippets in your own tests.

**Deliverables:**
- A streaming S-expression tokenizer, with point lists parsed straight into numpy. It tolerates unknown tokens, which it logs as `INGEST.UNKNOWN_TOKEN` info diagnostics.
- A semantic reader `KiCadReader` (`Reader` protocol, entry point `kicad`) for format versions 20221018 (v7) to 2026xxxx (v10), with the version support table in the README.
- Coverage:
  - layers and stackup, including `addsublayer` flattening and mask;
  - nets in both forms (v9 table and v10 name-only);
  - net classes and patterns from `.kicad_pro`;
  - diff-pair heuristic;
  - tracks and 3-point arcs → `ArcSeg`;
  - vias (through/blind/buried/micro, padstack modes, backdrill secondary/tertiary, protection flags);
  - footprints and pads (relative position, **absolute rotation**, back-side, every pad shape, custom pads unioned, drill offset, slots, die_length/die_delay);
  - zones (fills re-unioned from fractured polygons, keepouts, teardrops, priority, fill_state);
  - graphic copper;
  - outline chaining (1 µm tolerance; `gr_line/arc/circle/rect/poly/curve`, plus footprint Edge.Cuts).
- Unit conversion (mm → m, Y flip). Stable ids (`k:<uuid>`).
- A pcbnew verification note in the README on backdrill end-layer semantics (board-model.md §8), confirmed by running KiCad's bundled Python.

**Acceptance:**
- Every board in `share/kicad/demos` (≥ 10 boards, including `cm5_minima` and `jetson-agx-thor-baseboard`) loads with no `error` diagnostics, and `validate_board(strict=True)` passes.
- Golden `ingest_kicad`:
  - For 3 demo boards, the net count, via count by kind, pad count, track count, total track length per layer and stackup z-table match values extracted by a pcbnew script (committed under `tools/` inside your package, run once with KiCad Python, outputs committed). Counts must be exact, lengths ≤ 1e-9 relative, z ≤ 1 nm.
  - The per-layer copper area of zone fills vs pcbnew `GetFilledArea` is ≤ 0.1 %.
- Unit tests: pad rotation on a back-side rotated footprint, arc centre from 3 points (≤ 1 nm), fractured polygon → polygon with N holes, v9 and v10 net forms giving identical Boards apart from `meta`.
- Performance: `jetson-agx-thor-baseboard` (88 MB) loads in < 30 s and < 4 GB RAM on the reference machine. Record the measured time in the README.

### WP-102 Synthetic KiCad 10 test boards. Size: M
**Owned:** `tests/boards/kicad10/**`, `tools/boardgen/**`

**Inputs:** WP-000 (builders as a parameter reference), research 01 §1 (syntax), KiCad 10.0.5 at `D:\Program Files\KiCad\10.0`.

**Deliverables:**
- `tools/boardgen/` is a Python generator that writes `.kicad_pcb` + `.kicad_pro` text directly. Stackups are written in the file, because SWIG cannot set the stackup.
- It then uses `kicad-cli` and KiCad's bundled Python (`D:\Program Files\KiCad\10.0\bin\python.exe`) to:
  - refill zones (pcbnew `ZONE_FILLER`) and save;
  - run `kicad-cli pcb drc --format json` and commit the report;
  - export IPC-2581 rev C (`*.ipc2581.xml`) and ODB++ zip;
  - render PNGs (`kicad-cli pcb render` / svg) for docs.
- Every board directory has `board.kicad_pcb`, `board.kicad_pro`, `board.ipc2581.xml`, `drc.json`, `expected.yaml` (schema below) and `README.md` (intent and the variant's purpose).
- Boards. All are KiCad 10 format, FR-4-like Dk 4.3/Df 0.02 unless stated, 35 µm copper, and a known stackup.
  1. `microstrip`: 4 layers (1.6 mm), with L1 50 Ω microstrip traces (3 widths: 0.25/0.35/0.5 mm, h=0.2 mm to L2 GND), each 50 mm long. Edge launch pads. One variant `microstrip_v9` written in KiCad 9 syntax (net table), with identical copper.
  2. `stripline`: 6 layers. A symmetric stripline on L3 between L2/L4 GND, and an offset stripline on L4 (L3 empty above and L5 GND below).
  3. `diff_pair`: 100 Ω edge-coupled microstrip on L1 and stripline on L3. Includes a 3 mm uncoupled breakout (DP-001), a 0.4 mm intra-pair length mismatch (DP-002), a gap variation section (DP-003), and a layer change through a via pair with GND return vias.
  4. `cpwg`: L1 CPWG (S=0.3 mm, G=0.15 mm, h=0.2 mm) with a GND pour and via fences. One fence has its pitch at λ/10 at 10 GHz (pass) and one at 3× that (RF-001 fail).
  5. `split_plane`: 4 layers, with L2 GND split by a 1 mm slot and an L1 trace crossing it (RP-001). Also: a trace running 0.1 mm from the L2 plane edge (ED-001), a trace 0.3 mm from the board edge (ED-002), an L1 segment over a region with no L2 copper (RP-007), an antipad-cluster "swiss cheese" (RP-002), and a reference change from GND to +3V3 along a route (RP-003).
  6. `via_transition`: 8 layers (≈ 2.0 mm). An L1→L6 single-ended through via with an L6→L8 stub, the same via backdrilled from the bottom to L7, a differential via pair with 4 return vias, and a transition **without** a return via (RP-004).
  7. `power_plane`: 4 layers. L2 GND plane, L3 +1V0 plane 40 × 30 mm. A VRM footprint (`U5`, 3 output pins) at one end, and a load `U1` with 16 power pins + 16 GND pins on a 1 mm grid at the other, with via arrays. One decap at 1.5 mm from U1 (PD-001 pass) and one at 12 mm (fail).
     - Variant `power_strip`: an L1 copper strip 50 × 5 mm with full-width end pads, for the analytic R = Rs·L/W.
     - Variant `power_two_layer_vias`: two 20 × 20 mm squares on L1/L4 joined by N = 9 vias of known drill/plating.
  8. `combined`: every rule violation above on one board, plus net classes `50ohm`/`90ohm`/`100ohm` and an Altium-style explicit diff pair naming (`USB_D+`/`USB_D-`).
- `expected.yaml` schema (documented in `tests/boards/kicad10/README.md`):
  - `counts` (nets, vias by kind, pads, tracks, zones);
  - `stackup` (z_top/z_bottom per layer in metres);
  - `copper_area` (per layer per net, m², computed by pcbnew after refill);
  - `geometry` (named dimensions such as W, h, S, G, slot width and stub length);
  - `erc` (a list of `{rule_id, nets, layer, location_mm, tolerance_mm}` that MUST be reported, and `must_not_report: [rule_ids]`);
  - `dc` (analytic resistances where defined, with formula text);
  - `em` (suggested port definitions and frequency range for EM tests).

**Acceptance:**
- Every board opens in KiCad 10.0.5 (`kicad-cli pcb drc` exits 0).
- DRC JSON has zero errors except the ones listed in `expected.yaml: intended_drc`.
- `kicad-cli pcb export ipc2581` succeeds.
- Regeneration is deterministic: running `tools/boardgen/build_all.py` twice yields byte-identical `.kicad_pcb` files (fixed uuids).
- `expected.yaml` validates against its schema (a JSON Schema in the same directory).
- Expectations are traceable: each ERC expectation names the generator function that created the violating feature.

### WP-103 IPC-2581 reader and KiCad bridge (kicad-cli, Altium import, pcbnew helper). Size: L
**Owned:** `packages/oa-ingest-ipc2581/**`, `packages/oa-kicad-bridge/**`, `tests/golden/ingest_ipc2581/**`

**Inputs:** WP-000, research 01 §2.4, §4, §5. KiCad 10.0.5 locally.

**Deliverables:**
- `Ipc2581Reader` (entry point `ipc2581`), using lxml iterparse. It covers rev B and C:
  - units;
  - stackup (thickness, `DIELECTRIC_CONSTANT`, `LOSS_TANGENT`, material names);
  - padstacks, holes, back-drill spans (if present);
  - per-layer features (lines, arcs, polygons, contours with cutouts);
  - nets (`Set`s), components and pins;
  - outline.
  It converts to `Board`, with `meta.source_tool="ipc2581"`.
- `oa_kicad_bridge`:
  - `KiCadTools` resolves `kicad-cli` and KiCad Python from `tools.toml`, auto-detecting `D:\Program Files\KiCad\*\bin` on Windows and `/usr/bin` on Linux.
  - Functions: `upgrade(path)`, `drc(path) -> DrcReport`, `export_ipc2581(path, out)`, `export_odb(path, out, check_zones=True)`, `import_foreign(path, fmt="auto") -> Path` (`kicad-cli pcb import`, with the JSON report parsed into `Diagnostic`s), and `refill_zones(path, out)`, which runs a helper script with KiCad's Python through a subprocess, refills via `ZONE_FILLER` and saves to `out`.
  - `AltiumReader` (entry point `altium`, extension `.PcbDoc`) = `import_foreign` followed by the KiCad reader. The bridge sits at L2 next to `oa_ingest_kicad`, so it must not import it (or `oa_framework`). It resolves the `kicad` reader at runtime through `importlib.metadata.entry_points(group="open_analysis.ingest")`.
  - `ReadOptions.refill_zones` is honoured by a pre-step in the bridge.

**Acceptance:**
- IPC-2581 exports of 3 KiCad demo boards: counts, stackup thickness/Dk/Df and per-layer copper area match values recorded from pcbnew (≤ 0.5 % area, exact counts).
- ≥ 1 IPC-2581 file *not* produced by KiCad parses without errors: the Samtec/IEEE EPS TC-EDMS benchmark (if license permits download into the test cache; otherwise a documented manual test) or a public Altium/Allegro example.
- `refill_zones` on a board with a deliberately emptied zone produces `fill_state="refilled_by_tool"` and an area within 0.01 % of a GUI refill (recorded once).
- `import_foreign` round-trips a public Altium example board (e.g. from the Altium/KiCad importer QA corpus, if licensed) to a valid `Board`. Tests are skipped with marker `kicad` when KiCad is absent.
- No `pcbnew` import ever happens in the calling process (a test asserts `"pcbnew" not in sys.modules`).

### WP-104 Geometry service and ReferenceMap. Size: XL (split internally: copper/index → topology → reference map → sampling → regions)
**Owned:** `packages/oa-geometry/**`, `tests/golden/geometry/**`

**Inputs:**
- WP-000 (builders, CrossSection types, Structure3D types).
- interfaces.md §2.
- research 02 §5.3 (segment slicing and reference detection), 06 §3, 07 §3.1, 05 §3.1-3.2.

**Deliverables:** Everything in interfaces.md §2:
- copper unions (tracks buffered with round caps, arcs tessellated to `arc_tol`, pads from parametric shapes and rotation, via pads per padstack layer, zone fills, shapes);
- voids;
- `LayerIndex` STRtrees;
- `NetTopology` (junctions, stubs, pad stubs, via edges);
- islands;
- `ReferenceMap` (per-segment spans with coverage, edge distance, distance; via transitions with nearest return vias and stitching caps);
- `segments`, `cross_section_at` (trapezoid from etch, conformal mask regions via polygon offset, coplanar copper within window, aggressors, planes truncated to actual copper extent), `sample_net` (events: width change, ref change, layer change, neckdown, plane edge, no reference);
- `cut_region` → `Structure3D` + `boundary_crossings`;
- `resolve_terminal`;
- via stub length helper (`via_stub(via, used_layers) -> length`).

**Acceptance (golden `geometry`):**
- On `microstrip_board(w=0.35mm, h=0.2mm)`, `cross_section_at` gives a CrossSection with `topology_hint="microstrip"`, the trapezoid width within 1 nm and the plane at the correct z. The key is equal for all samples along the straight trace.
- On `stripline_board`, the symmetric section is classified `stripline` and the offset one `offset_stripline`. Distances to both planes are exact to 1 nm.
- On `cpwg_board`, coplanar grounds are included as `ground` conductors with gap G (1 nm). `diff_pair_board` gives 2 signal conductors in P, N order.
- `split_plane_board`: `ReferenceMap` spans show coverage < 1 exactly over the slot interval (± 10 µm). The RP-007 segment has `below=None`.
- `via_transition_board`: the stub length for the non-backdrilled via equals the z-distance from L6 to L8 barrel end (1 nm). The backdrilled via's stub equals the remaining L6→L7 portion.
- `power_plane_board`: `islands("+1V0") == 1`. Removing the via array (a builder parameter) yields 2 islands.
- `cut_region` on `via_transition_board` with nets = signal: every copper polygon inside the bbox is present, antipads are preserved as holes (count equals the builder's), and `boundary_crossings` lists exactly 2 trace crossings.
- Hypothesis: copper union area equals the sum of disjoint primitive areas (≤ 1e-9 relative) for random non-overlapping tracks; union is idempotent.
- Performance: `BoardGeometry.build` + `reference_map()` on a 10k-track synthetic board takes < 10 s.

### WP-105 Impedance: closed forms and 2D BEM, `impedance` service and `impedance.xsection` analysis. Size: XL
**Owned:** `packages/oa-impedance/**` (except `src/oa_impedance/fem/`, `src/oa_impedance/profile/`, `tests/fem/` and `tests/profile/`, which are reserved for WP-202/WP-203), `tests/golden/impedance_closedform/**`, `tests/golden/impedance_bem/**`

**Inputs:** WP-000 (CrossSection fixtures), research 02 (whole file). `oa_physics` is **not yet available**: use the `oa_physics` API from interfaces.md §10 through a thin local shim behind an interface, and swap to the real package when WP-106 merges. Keep the shim in `oa_impedance/_physics_compat.py`.

**Deliverables:**
- Closed forms (validity flags on every function):
  - HJ 1980 + thickness, Wheeler 1977, KJ 1982 dispersion, Jansen-Kirschning 1983 Z0(f);
  - embedded microstrip cover correction (flagged approximate);
  - Cohn stripline + Wheeler 1978 thickness, offset stripline (parallel approximation);
  - CPW and CPWG (Ghione-Naldi / Simons + Gupta thickness), using `scipy.special.ellipk` (m = k²) with Hilberg as a cross-check;
  - edge-coupled microstrip (KJ 1984 + 1985 errata), edge-coupled stripline (Cohn 1955 + Wadell t), broadside (Cohn 1960);
  - IPC-2141 legacy;
  - Wheeler-rule conductor loss.

  Porting KiCad `pcb_calculator/transline` or Qucs code is allowed (ADR-0001), with attribution.
- 2D BEM:
  - linear panels with corner grading and analytic log integrals;
  - free-charge plus polarization-charge formulation with dielectric interfaces truncated at `20·b` (configurable, reported in `notes`);
  - complex permittivity per frequency → C(f), G(f);
  - L from the vacuum C;
  - surface-impedance perturbation R(f) with roughness (via `oa_physics`);
  - modal and mixed-mode extraction (interfaces.md §1.1);
  - Numba optional (pure-numpy fallback).
- `ImpedanceService` (`solve`, `solve_many`, `quick`) registered as entry point `impedance`, with an in-memory LRU plus the job-cache hook.
- The method-registration hook `oa_impedance.methods.register(name, solver_fn)`, which WP-202 uses to plug in `fem` without editing WP-105 files. `"fem"` resolves lazily, and `method="fem"` raises `UnsupportedFeature` until WP-202 has registered it.
- `impedance.xsection` analysis.

**Acceptance:**
- Golden `impedance_closedform`, locked at 1e-6 relative:
  - the HJ table (report 02 §4.3, 7 rows: Z0 and eeff), Wheeler-77 values;
  - Cohn stripline W/b ∈ {0.1, 0.25, 0.5, 1.0, 2.0} er=1 → {194.23, 139.92, 100.43, 65.35, 38.58} Ω (to 0.01 Ω);
  - coupled stripline W/b=0.5, S/b ∈ {0.1, 0.25, 0.5} → (122.89, 69.87), (114.77, 83.52), (107.15, 93.22) Ω;
  - CPW S=1, G=0.5 infinite substrate → 120.57 Ω (er=1), 51.88 Ω (er=9.8);
  - IPC legacy at h=0.2 mm, t=35 µm, er=4.3, W=0.5 mm → 36.8 Ω, with Wheeler → 41.1 Ω.
- Closed form vs `skrf.media.MLine` (HJ + KJ) and `skrf.media.CPW` on 20 cases: ≤ 0.1 %.
- Golden `impedance_bem`:
  - coax ln(b/a)=1 → 59.958 Ω (≤ 0.1 %);
  - wire over plane and two-wire acosh formulas (≤ 0.5 %);
  - Cohn zero-thickness stripline cases via t → 0 (t = 1e-4·b plus extrapolation, ≤ 0.5 %);
  - CPW on a half-space eeff = (er+1)/2 (≤ 0.5 %);
  - homogeneous L·C = μ0ε0εr·I (1e-6);
  - C symmetric (1e-9), mirror symmetry C11 = C22 (1e-9);
  - BEM microstrip vs HJ inside the validity range ≤ 1 %.
- Panel convergence: the error halves at least every doubling, documented.
- Performance: the median BEM solve of a single microstrip plus a 2-conductor diff pair at 20 frequencies is < 50 ms (pytest-benchmark).
- `solve_many` dedups by key (a test with 100 identical sections calls the kernel once).
- Every `impedance.xsection` result item sets `accuracy`: `closed_form` with the formula validity, or `2d_field` with a `tolerance_basis` naming the golden suite. Out-of-range closed forms give `validity="extrapolated"`/`"invalid"` and the matching warning code.

### WP-106 Physics, analytic via model, and S-parameter quality gate. Size: L
**Owned:** `packages/oa-physics/**`, `packages/oa-network/**`, `tests/golden/physics/**`, `tests/golden/network_quality/**`

**Inputs:** WP-000, interfaces.md §4.5 and §10, research 02 §2, 03 §2.5, 07 §1.2, 04 §1.1.

**Deliverables:**
- `oa_physics`:
  - the functions in interfaces.md §10;
  - causal roughness (fitting K(f) − 1 with a sum of first-order sections);
  - temperature-dependent resistivity;
  - `oa_physics.via`: the analytic via estimators (Goldfarb-Pucel barrel L, Johnson pad C, via-pair loop L, barrel R with plating, stub resonance), registered as service `via_model`.
- `oa_network`:
  - `assess` (PQM/RQM/CQM per IEEE 370-2020 definitions as implemented in the IEEE 370 open-source reference code; cite the exact algorithm in the docstring), `make_dc`, `enforce_passivity` (scikit-rf VectorFitting), `gate`;
  - mixed-mode helpers (`se2gmm` port ordering);
  - renormalization;
  - reference-plane shift.

**Acceptance:**
- Golden `physics`:
  - DS fit round-trip (eps(f_ref) = Dk(1 − jDf) to 1e-9) and a Kramers-Kronig residual < 1e-3 over 1 kHz-100 GHz;
  - skin depth for Cu at 1 GHz = 2.09 µm (±0.5 %);
  - Hammerstad and Groisse K(f) saturating at 2;
  - Huray K vs a published curve (cite the source, ≤ 2 %);
  - copper Rs 1 oz at 20 °C = 0.49 mΩ/sq (±1 %);
  - via R for 0.30 mm drill, 25 µm plating, 1.6 mm → 1.27 mΩ (±1 %);
  - stub resonance 1.5 mm in Dk 4 → ≈ 25.0 GHz (formula lock);
  - via-pair L for h=1 mm, s=1 mm, r=0.15 mm → 0.76 nH (±1 %).
- Golden `network_quality`: committed synthetic Touchstone fixtures, each with an exact expected verdict:
  - a passive reciprocal line → pass;
  - a line with |S21| scaled by 1.05 → fail passivity;
  - a non-reciprocal fixture → fail RQM;
  - a non-causal (constant-Dk lossy) line → warn/fail CQM;
  - a line starting at 10 MHz → `dc=extrapolated`.
- `enforce_passivity` makes the 1.05 fixture pass with ≤ 0.2 dB change below 0.9 f_max.

### WP-107 Job system, external runners, result IO. Size: L
**Owned:** `packages/oa-jobs/**`, `packages/oa-results/src/oa_results/io/**`, `packages/oa-results/tests/io/**`

**Inputs:** WP-000, interfaces.md §4.

**Deliverables:**
- `oa_jobs`:
  - the SQLite (WAL) store;
  - `JobService` implementation;
  - `inline` and `process` worker executors (spawn pool);
  - the task DAG scheduler with resource-aware concurrency;
  - content-addressed cache + CAS + `gc`;
  - cancellation, including Windows job-object process-tree kill;
  - an event stream with `follow`;
  - the `RunContext` implementation;
  - `ExternalRunner`s: `local`, `wsl` (path translation `D:\x` ↔ `/mnt/d/x`, `wsl.exe -d <distro> --`), `docker` (bind-mount the job dir to `/work`, uid mapping on Linux);
  - tool resolution from `tools.toml`;
  - progress parsers `openems`, `palace`, `generic`;
  - `oa_jobs.doctor()` data for `oa doctor`.
- `oa_results.io`: `write_bundle`/`read_bundle`, Touchstone v1.1/v2.0 read/write (via scikit-rf; port names go in the v2.0 keyword section and in `! Port[n] = name` comment lines for v1.1), SARIF 2.1.0 (validated against the official schema in tests), CSV, Parquet, VTU (meshio).

**Acceptance:**
- A dummy analysis with 20 tasks (sleep and compute) runs on the process pool. Restarting the service mid-run resumes the remaining tasks. A second submit is 100 % cache hits.
- Cancel during a running external `python -c "import time; time.sleep(60)"` kills it in < 12 s on Windows and Linux.
- WSL path translation round-trips are tested. The WSL/Docker runners have `external` tests that run `uname -a` when available.
- Determinism: the same task payload in a different key order gives the same cache key.
- A Touchstone round trip of a 4-port with complex z0 is bit-exact for v2.0 and ≤ 1e-12 for v1.1. SARIF from a sample `ViolationSet` validates against the SARIF schema.
- 10k events stream with `after_seq` paging without loss.
- **Budgets** (ai-integration §10.4):
  - `JobService.estimate` sums the task `Resources`.
  - A dummy job whose estimate exceeds `AnalysisRequest.budget` is refused with `BUDGET_EXCEEDED` before any task starts.
  - A sleeping job with `max_runtime_s=5` is cancelled within 5-8 s with `cancel_reason="budget:max_runtime"`.
  - An external process allocating past `max_memory_gb` is killed on Windows (job-object limit) and Linux, with `cancel_reason="budget:max_memory"`.
- `Provenance.inputs_hash`, `oa_version`, `tool_versions` and `caller` are populated, and `inputs_hash` is identical for a fully cached re-run.

### WP-108 openEMS adapter. Size: L
**Owned:** `packages/oa-em-openems/**`, `tests/golden/em_openems/**`

**Inputs:**
- WP-000 (EMProblem fixtures, `SolverAdapter` protocol), interfaces.md §5.
- research 05 §1.2, §3, §6.
- openEMS v0.37.x, installed locally per `tools.toml`. Document the install steps in the README.

**Deliverables:**
- `OpenEMSAdapter`:
  - `capabilities` (wave ports as `tl_probe`; MSL/stripline/CPW port classes from v0.37);
  - `check`;
  - `prepare`, which generates a standalone Python script per excitation using CSXCAD/openEMS APIs:
    - rectilinear mesh generation (third-rule at metal edges, grading ≤ 1.3, ≥ 4 cells across dielectrics, ≥ 4-6 cells across via diameters, λ_min/20 max cell; presets);
    - polygons extruded per layer with priorities;
    - via cylinders;
    - Debye/DS dielectric fit (single-pole fit at band centre with a documented error estimate);
    - lumped RLC;
    - PML/Mur;
    - energy end-criterion;
    - optional nf2ff box;
  - `collect`: port V/I to S-matrix assembly across N runs, renormalization, port Z0 vs f.
- No openEMS import in-process: the script runs in the openEMS interpreter via `ctx.run_external`.

**Acceptance:**
- Snapshot tests: `prepare()` on the 3 EMProblem fixtures generates scripts and mesh-line summaries matching committed snapshots (mesh line count ± 0, script text normalized).
- `collect()` on committed sample raw outputs (produced once by a real run, a few MB, under `tests/golden/em_openems/raw/`) reproduces committed `.s2p` to 1e-9.
- External (nightly) tests:
  - The microstrip thru fixture's Z0 from port impedance is within 3 % of the HJ value for the same geometry, and |S11| < −20 dB to 10 GHz with matched renormalization.
  - Reproduce the openEMS MSL notch filter tutorial via a hand-built EMProblem, with the notch frequency within 1 % of the tutorial's own run.
- Mesh stats (cells, est memory) are reported in `PreparedRun.mesh_stats`.

### WP-109 Mesh 2D (gmsh). Size: M
**Owned:** `packages/oa-mesh/**` (except `src/oa_mesh/occ3d/` and `tests/occ3d/`, which are reserved for WP-206)

**Inputs:** WP-000, interfaces.md §9, research 02 §3.3, 04 §1.2.

**Deliverables:**
- `oa_mesh._session` (a process-local gmsh lifecycle, deterministic options).
- `mesh_cross_section`: P1/P2, corner refinement, outer box, region and boundary tags.
- `mesh_copper_sheets`: constrained mesh of polygons with holes, embedded via/pad contact rings as boundary or embedded curves, and size fields (distance to contacts, trace width/3, plane h_max) with a grading of 1.2-1.3. It returns `TriMesh2D` + `contacts`.
- Sliver cleanup (shapely `buffer(0)`, simplify 1-5 µm, configurable).
- Mesh quality report (min angle, aspect).

**Acceptance:**
- Cross-section meshes of all WP-000 CrossSection fixtures: every conductor boundary is tagged, no triangle has min angle < 15°, and the same mesh (node count and coordinates hash) results on Windows and Linux for a fixed gmsh version.
- Copper sheet: a 40 × 30 mm plane with 200 random holes meshes in < 5 s. Every contact ring has ≥ 16 nodes on it, and the ring nodes lie exactly on the circle (≤ 1 nm radial error).
- Mesh area equals the polygon area to 1e-9 relative.
- A test asserts gmsh is never initialized in the importing process when only types are imported.

### WP-110 Web UI shell and board viewer. Size: L
**Owned:** `web/**`, `.github/workflows/web.yml`

**Inputs:** WP-000 (`schemas/*.json`, `tests/fixtures/model/*.oab.json`), interfaces.md §7 (REST outline), ARCHITECTURE.md §7.

**Deliverables:**
- Vite + React 18 + TS + pnpm project.
- TS types generated from `schemas/board-model.v1.json`.
- A hand-written `web/src/api/openapi.draft.yaml` reflecting interfaces.md §7, from which the API client is generated. WP-207 later replaces it with the real `openapi.json`, so keep the generator script.
- An MSW mock server serving fixture boards, converting `.oab.json` into the Arrow layer format exactly as specified.
- App shell: project open dialog (mocked), layer panel (visibility, colour, z order), net search and highlight, item inspector, and a stackup table/diagram view.
- **Board view** in deck.gl (polygon and path layers from Arrow binary attributes, picking, net highlight, measuring tool, mm/mil toggle).
- An overlay API (`addOverlay({kind: "markers"|"heatmap"|"polylines", ...})`) used later by WP-209.
- Vitest unit tests and a Playwright smoke test. A production build goes into `web/dist`, with no external network requests (checked).

**Acceptance:**
- Playwright test: open `power_plane_board` (mock) → 4 layers listed → toggle L2 → click the via array → the inspector shows net `+1V0`.
- Render 200k polygons (synthetic) at ≥ 30 fps on the CI GPU-less runner, measured with a headless frame-time script, ≥ 20 fps with software GL as the floor.
- Arrow decoding and geometry: polygon count and areas match the fixture (≤ 1e-6 relative after float32).
- The bundle is under 5 MB gzipped. Lighthouse accessibility ≥ 90.

---

## Wave 2 (10 WPs; start each as soon as its listed dependencies merge)

### WP-201 ERC/EMI rule engine and M1 rules. Size: XL
**Owned:** `packages/oa-erc/**`, `tests/golden/erc/**`

**Deps:** WP-104 (geometry, ReferenceMap), WP-105 (`impedance` service), WP-106 (`via_model`), WP-102 (boards, for the integration part; unit tests use WP-000 builders).

**Deliverables:**
- The `erc` analysis (one task per rule, then reduce to a `ViolationSet` + summary table).
- Rule config schemas with defaults and citations.
- Waivers (fingerprint and rule/net glob, expiry).
- Severity overrides.
- The M1 rules in interfaces.md §3.6, with EMI aliases, using the thresholds in research 06 §3.2 and 07 §3.1.
- `fix_hint` text for every rule, plus a structured `remediation` (ai-integration §5) with at least one typed action. `applies_via` correctly distinguishes `board_edit` from `dru_rule`/`netclass`.
- An `explanation` class attribute for every rule.
- A rule authoring guide in the README, covering how a third-party package registers `open_analysis.rules`.

**Acceptance:**
- Golden `erc`: for each WP-000 builder variant with exactly one injected violation per rule, that rule reports exactly one violation (id, nets, layer) with location within 50 µm, and **no other rule reports anything**.
- Each rule also has a negative fixture that reports nothing.
- On WP-102 boards, the output equals `expected.yaml: erc`, with no `must_not_report` violations. This is run as an integration test marked `kicad` when it needs a fresh parse, otherwise from committed `.oab.json` dumps produced by WP-101.
- IM-002 on `microstrip` uses BEM values and flags the 0.5 mm trace (≈ 41 Ω) against a 50 Ω ± 10 % target.
- VI-001 reports a stub resonance within 1 % of c/(4·L·√Dk_eff).
- EM-011 cavity resonance on a 40 × 30 mm plane pair, Dk 4.3: f₁₀ = c/(2·0.04·√4.3) = 1.807 GHz (±0.5 %).
- SARIF output of the combined board validates.
- AI contract:
  - Every violation on `combined` has `remediation` with ≥ 1 action, and `fix_hint == remediation.summary`.
  - Every threshold rule sets `measured`, `limit` and `unit`.
  - IM-002 remediation carries the impedance `accuracy_class` in `evidence`.
  - The `ViolationSet` summary for `combined` lists the counts by rule and the worst 5, within 2 KiB.
  - Violation order is identical across two runs.
- Performance: all M1 rules on a 10k-track synthetic board in < 60 s.

### WP-202 Impedance FEM reference and R(f)/L_int(f) eddy-current solver. Size: L
**Owned:** `packages/oa-impedance/src/oa_impedance/fem/**`, `packages/oa-impedance/tests/fem/**`, `tests/golden/impedance_fem/**`

**Deps:** WP-105, WP-109, WP-106.

**Deliverables:**
- A scikit-fem electrostatic solve (P2, complex ε per region, outer box with a Kelvin transform or ≥ 20× box), giving C and G.
- A magneto-quasistatic A_z solve with per-conductor current constraints, giving R(f) and L(f) from DC through skin effect, with an impedance boundary condition above the δ < t/4 crossover.
- Adaptive refinement on energy error.
- Registration as `method="fem"` in the impedance service (via an internal registry hook that WP-105 exposes as `oa_impedance.methods.register`; WP-105 must provide this hook).

**Acceptance:**
- Golden `impedance_fem`:
  - analytic cases as for BEM: coax ≤ 0.1 %, wire over plane ≤ 0.5 %;
  - an isolated round wire R(f)/R_dc vs the Bessel-function solution ≤ 2 % from 1 kHz to 10 GHz;
  - Richardson-extrapolated convergence error < 0.1 % on the finest level;
  - **BEM vs FEM** on 50 generated PCB structures (the WP-105 structure generator): Z0 ≤ 0.5 %, Zdiff ≤ 1 %, eeff ≤ 0.5 %.
- FEM R(f) for stripline at 1, 5 and 10 GHz is within 5 % of the BEM perturbation R(f) where δ < t/4.
- FEM results set `accuracy` (`2d_field`, with `converged` from the refinement loop and `tolerance_basis` naming the golden suite).

### WP-203 Impedance profile analysis (`impedance.profile`). Size: M
**Owned:** `packages/oa-impedance/src/oa_impedance/profile/**`, `packages/oa-impedance/tests/profile/**`, `tests/golden/impedance_profile/**`

**Deps:** WP-104, WP-105.

**Deliverables:**
- The analysis that samples nets (`sample_net`), dedups sections by key (one task per unique key batch), solves through the service, and reduces to a `ProfileResult` per net with events, plus a summary table (min/max/mean Z, out-of-tolerance length, delay per layer).
- Export of field-solved per-layer delay as a KiCad 10 tuning-profile suggestion (a JSON file, not a KiCad edit).

**Acceptance:**
- On `microstrip_board`, the profile is flat within 0.1 Ω along the straight trace, and the unique-key count is 1 per width.
- On `diff_pair_board`, Zdiff drops are reported over the uncoupled breakout (event at the correct s ± 0.1 mm).
- On `split_plane_board`, a `ref_change` / `no_reference` event sits at the slot (± 50 µm).
- Delay per metre for stripline equals √(εr)/c within 0.5 %.
- On a 500-segment net with 3 unique sections, the service is called 3 times.
- Each `ProfileResult` item sets `accuracy`. Its `summary()` reports min/max/mean Z, target, out-of-tolerance length and the worst event (ai-integration §4.2), and stays within 2 KiB for a 100-net run.

### WP-204 DC IR drop (`pi.dc`). Size: XL
**Owned:** `packages/oa-pi/**`, `tests/golden/pi_dc/**`

**Deps:** WP-104, WP-109, WP-106.

**Deliverables:**
- Config per interfaces.md §6.
- Copper sheet extraction per (net, layer) with refuse-on-unfilled-zones.
- The P1 FEM assembler (vectorized COO).
- Via barrel segments between every layer touched, tied to all ring nodes.
- Thermal-relief spokes meshed as geometry.
- Pin-group loads (equipotential via 1 mΩ links or exact constraint), VRM Dirichlet with optional Thevenin R and remote sense (MNA extra unknown).
- Ground return solved together.
- Island detection.
- Solvers: SuperLU, with pypardiso/CHOLMOD if installed, and AMG-PCG above 1 M unknowns.
- Outputs: V, ΔV and |J| per layer (`FieldResult`), via current table, load table with PASS/FAIL, rail scalars (effective R, loss, worst neck).
- An in-repo **raster FVM reference solver** (`oa_pi.reference.raster`, ≈ 300 lines).
- Service `pdn` stub exposing `dc_solve(rail, T_field)` for M2 thermal coupling.

**Acceptance:**
- Golden `pi_dc` analytic:
  - strip R = Rs·L/W, with Rs from `oa_physics` at 20 °C, ≤ 0.1 %;
  - annulus Rs·ln(b/a)/(2π) ≤ 0.1 %;
  - two layers + N=9 vias: series/parallel formula ≤ 0.5 %.
- The point-contact disk shows a convergence order (documented), and ring contacts are mesh-independent within 0.5 % under 2× refinement.
- FEM vs raster on `power_plane_board`: load-pin ΔV within 1 % at raster h/4.
- On WP-102 `power_strip` and `power_two_layer_vias`, `expected.yaml: dc` is matched within 0.5 %.
- External oracle (nightly, `external`): the kicad-irdrop 4-layer test board (Apache-2.0, downloaded into the test cache) gives the effective R within 3 % of the published 3.04 mΩ.
- A power-plane board with 3×10⁵ unknowns solves in < 20 s.
- Items set `accuracy` (`2d_sheet`). The bundle summary states the worst load drop (value, unit, load, pass/fail against the limit) and the worst via current, without inlining field arrays.

### WP-205 EM flow: region → EMProblem → S-parameters (`em.sparams`). Size: L
**Owned:** `packages/oa-em/**`, `tests/golden/em_flow/**`

**Deps:** WP-104 (cut_region, resolve_terminal), WP-106 (gate), WP-107 (runners, used through RunContext; testable with `harness.run_inline` + a local runner shim), WP-108 (openEMS adapter, by entry point).

**Deliverables:**
- The `em.sparams` analysis:
  - region spec with auto margin;
  - port resolution (`TraceEndPort` → `EMWavePort` on the region face, with automatic port length ≥ 8 cells or 3-5 W from discontinuities; `LumpedPort` → `EMLumpedPort`; `CoaxPort`);
  - boundary selection (closed SI regions use PEC/absorbing side walls, open regions use air padding);
  - adapter selection and capability check;
  - task fan-out per excitation;
  - reduce with the quality gate, reference-plane de-embedding, mixed-mode pairs, Touchstone + JSON sidecar (region, port map, stackup hash, mesh stats, convergence, quality).
- A port-impedance sanity check against the `impedance` service (warn if > 10 % different).

**Acceptance:**
- From `microstrip_board`, the generated EMProblem matches a committed snapshot.
- From `via_transition_board` (single-ended and differential), the EMProblems have 2 and 4 ports respectively with correct face and conductors.
- Externally, on openEMS:
  - The microstrip thru Z0 is within 3 % of BEM.
  - IL matches an RLGC-cascade estimate within 0.2 dB at 10 GHz per 25 mm.
  - The bundle has quality `pass`.
  - On the via transition (0-40 GHz), the `.s4p` is produced with PQM ≥ 99 and RQM ≥ 99.
- A non-passive mock adapter output gives bundle status `failed_quality`.
- Network items set `accuracy` (`3d_full_wave`, with `quality` from the gate, `converged`, `f_min`/`f_max`). The summary reports the quality verdict, PQM/RQM and |S21|/|S11| at the 3 points of ai-integration §4.2.

### WP-206 3D OCC mesher (gmsh) for Structure3D. Size: L
**Owned:** `packages/oa-mesh/src/oa_mesh/occ3d/**`, `packages/oa-mesh/tests/occ3d/**`

**Deps:** WP-109 (session), WP-000 (Structure3D fixtures).

**Deliverables:**
- `build_occ_model`: dielectric slabs, copper as solids or zero-thickness sheets (threshold), via barrels (with plating or filled), antipads as holes, `BooleanFragments` for a conformal mesh, physical groups for materials, conductors, ports (faces) and outer boundaries.
- `write_msh` (v4.1) with size fields (λ/10 at f_max in each dielectric, edges w/4, via barrels ≥ 12 segments, ≥ 2 elements across thin dielectrics).
- Polygon healing for slivers (≤ 1 µm).
- Mesh stats (tets, est DOF for p=1/2).
- A GLB export of the model surfaces for the 3D UI preview.

**Acceptance:**
- All three EMProblem fixtures and `cut_region(via_transition_board)` mesh without gmsh errors.
- Physical group names are as specified. Every port face is a single surface. Volume per material equals the analytic slab volume minus conductors (≤ 1e-6 relative).
- Re-meshing is deterministic (same node count).
- Runtime < 60 s for the via region at the medium preset.

### WP-207 Server and CLI. Size: L
**Owned:** `packages/oa-server/**`, `packages/oa-cli/**`, `packages/oa-service/**` (except `src/oa_service/proposals/` and `tests/proposals/`, which are reserved for WP-210), `schemas/commands/**`, `schemas/oa-envelope.v1.json`, `schemas/oa-error.v1.json`, `web/src/api/openapi.json` (generated artifact only, coordinate the path with WP-209)

**Deps:** WP-107, WP-000. Analyses are discovered by entry points, so the CLI and server work with whatever analyses are installed.

**First deliverable (within the first 2 days, merged early):** the `oa_service` protocol, the `CommandSpec`/`CallContext`/`Envelope`/`Page` types, the catalog names from ai-integration §2.4, and `oa_service.testing.FakeService`. WP-210 and WP-110/WP-209 build against these in parallel.

**Deliverables:**
- FastAPI app per interfaces.md §7 (token auth, 127.0.0.1 bind, static web serving from the packaged `web/dist`, Arrow endpoints, GLB endpoint using WP-206 when installed, WS events).
- An OpenAPI export script.
- The `oa` CLI per interfaces.md §8, including `oa init`, `oa doctor` (tool resolution table, KiCad version, openEMS/Palace availability, gmsh version), and exit codes.
- Project manager (open, reload, file watching of board/sidecar/setups with debounce).
- **`oa-service`** (ai-integration §2-§4, §6, §9, §10):
  - the command catalog and dispatcher;
  - project sessions (stable `project_id`, ephemeral projects for bare board files);
  - roots and permission checks;
  - pagination, filtering and deterministic sorting;
  - result rendering for transports (decimation, `ArrayRef`s, size caps);
  - mapping errors to `ErrorInfo`;
  - budget clamping and pre-flight estimates.
- The CLI and REST become adapters over `oa_service`. Commands not hand-written in typer are generated from the catalog.
- The CLI AI conventions (ai-integration §9):
  - `--json` envelope on every command;
  - exit codes 0-7;
  - `--interactive`/`--yes`;
  - `--progress json`;
  - `oa describe`, `oa schema`, `oa board summary|nets|net|stackup`, `oa results summary|violations|explain`.
- REST additions from ai-integration §2.4 and A11/A17, including `GET /commands`, `GET /schemas/{id}` and the `{"error": ErrorInfo}` body.

**Acceptance:**
- An httpx test suite covers every endpoint with fixture projects built from `tests/fixtures/model` (`.oab.json` as board source).
- Arrow layer geometry for `power_plane_board` decodes to the same polygon areas as `oa_geometry` (≤ 1e-6).
- WS delivers ordered events for a 50-task dummy job.
- `oa erc --fail-on error` returns exit code 1 on `combined` and 0 on a clean board.
- `oa doctor` runs offline in < 3 s.
- The committed `openapi.json` is regenerated in CI with no diff.
- AI conventions:
  - For **every** catalog command, `--json` stdout parses as exactly one envelope that validates against `oa-envelope/1` and the command's output schema, and stderr carries all logs.
  - Two consecutive runs give byte-identical stdout once the volatile fields (ai-integration §3.5) are masked.
  - Exit codes match the §6 table for one test per code (2: bad setup, 3: missing tool, 5: failing gate fixture, 6: a budget breach, 7: a write without `--yes`). The `ok` flag is consistent with the exit code.
  - With stdin not a TTY, no command blocks on input (run under a closed stdin with a 10 s timeout).
  - `oa describe --json` lists every registered command, and every `examples` entry runs.
  - `oa schema --list` matches `schemas/commands/` and CI diffs the regeneration.
  - `list_nets`/`results.violations` pagination: concatenating pages equals the unpaged list, and `total` is correct.
  - REST 4xx bodies carry `ErrorInfo` with `path` for setup validation errors.

### WP-208 Ingest cross-validation (validation WP). Size: M
**Owned:** `tests/integration/ingest/**`, `tests/golden/ingest_cross/**`

**Deps:** WP-101, WP-102, WP-103.

**Deliverables:**
- A harness that, for each WP-102 board and 3 KiCad demo boards, reads the board natively and via its IPC-2581 export, then compares:
  - nets;
  - via count by kind and backdrills;
  - pads;
  - per-net per-layer copper area;
  - stackup z, Dk and Df;
  - outline area;
  - component count.
- The `expected.yaml` checks for counts, stackup and copper area against the native reader.
- A diff report as CSV.
- Also, round-trip `Board` → `.oab` → `Board` for every board.

**Acceptance:**
- The native reader matches `expected.yaml` exactly for counts, ≤ 1 nm for z, and ≤ 0.1 % for copper area.
- Native vs IPC-2581: counts exact (documented, justified exceptions allowed in `ingest_cross/exceptions.toml`), area ≤ 0.5 %, z ≤ 1 µm, Dk/Df exact.
- `microstrip` vs `microstrip_v9` yield equal Boards except `meta`.

### WP-209 Web: setup forms, jobs, result viewers. Size: L
**Owned:** `web/src/features/**`, `web/tests/features/**`

**Deps:** WP-110, WP-207 (real OpenAPI; may start on the draft spec).

**Deliverables:**
- Setup editor (RJSF forms from `/analyses` schemas, a port picker on the board view writing `Port` terminals, a NetSelector widget, waiver editing).
- Jobs panel (WS).
- Result viewers by kind:
  - violations (table + markers + zoom-to);
  - field (deck.gl heatmap per layer, clim, log toggle, hover probe);
  - profile (Plotly vs s, with board cross-probe);
  - network (dB/phase, Smith, mixed-mode toggle, quality badge with the report);
  - table (virtualized grid from Arrow);
  - scalars.
- 3D region preview (three.js GLB).
- Export buttons.

**Acceptance:**
- Playwright, against the real server + inline executor on fixture boards:
  - Run ERC on `combined` and see N markers equal to the expected count.
  - Run `pi.dc` on `power_plane_board` and see the heatmap and the load table.
  - Load a committed `.s4p` result and see the Smith chart and the quality badge `pass`.
- The UI shows `failed_quality` prominently, with the report.

### WP-210 MCP server and agent docs. Size: L
**Owned:** `packages/oa-mcp/**`, `packages/oa-service/src/oa_service/proposals/**`, `packages/oa-service/tests/proposals/**`, `tests/agents/**`, `docs/agents/**`, `llms.txt`, `CLAUDE.md`, `tools/agents/**`, `.github/workflows/wp-210.yml`

**Deps:**
- WP-107 (jobs, budgets).
- WP-207's `oa_service` protocol and `FakeService`, which is its first deliverable. **Runs in parallel with WP-207:** build against `FakeService`, and switch the integration tests to the real service when WP-207 merges.
- The analyses are runtime-discovered. Tests skip analyses that are not yet merged, and WP-303 makes them mandatory.

**Inputs:** [specs/ai-integration.md](specs/ai-integration.md) (all of §1-§10 and §13), ADR-0007, interfaces.md §3.7, §4 and §7.

**Deliverables:**
- **`oa-mcp`** (ai-integration §8):
  - a FastMCP server with stdio and streamable-HTTP transports (loopback, bearer token, Origin check);
  - root allow-list, `--allow-writes`, budget ceiling flags and config;
  - tools generated from the catalog, with annotations, `outputSchema`, `structuredContent` plus a text digest, `isError` envelopes, size caps, `wait_s` and progress notifications;
  - every tool in §8.3, every resource and resource template in §8.4, and every prompt in §8.5.
- **Proposals** (`oa_service.proposals`, ai-integration §10.3):
  - the catalog commands `propose.rules` and `propose.apply`;
  - a `.kicad_dru` reader/writer (round-trips untouched rules byte-for-byte);
  - a `.kicad_pro` `net_settings` editor that preserves key order;
  - remediation → `RuleChange` mapping, unified diffs, confirm tokens, base-hash check, atomic writes and backups.
- **Agent docs:** `llms.txt` (plus a generated `llms-full.txt`), `docs/agents/AGENTS.md` (the agent user guide) and the root `CLAUDE.md` (contributor coding agents), per ai-integration §7.3. The link and tool-name checker lives in `tools/agents/`.
- **The schema-description lint** `tests/agents/test_schema_descriptions.py` (§7.1).
- **The M1 agent workflow test** `tests/agents/test_m1_workflow.py`, used by the ROADMAP M1 exit criterion. It skips steps whose analysis is not installed and is made mandatory by WP-303.

**Acceptance:**
- **MCP smoke (stdio, official `mcp` client `ClientSession`):**
  1. `open_board` on the `microstrip` fixture (`tests/fixtures/model/microstrip.oab.json`, or WP-102's `microstrip` board when present).
  2. `board_summary` reports 4 layers.
  3. `run_erc` with `wait_s=60` completes.
  4. `result_summary` returns a `BundleSummary` ≤ 8 KiB with counts by severity.
  5. `list_violations` pages are consistent with the summary counts.

  The same flow passes over streamable HTTP with the token, and a request without the token or with a foreign `Origin` is rejected.
- **Parity.** For every tool, the `structuredContent.data` equals the `data` of the corresponding `oa ... --json` command on the same fixture, with the volatile fields masked. This proves MCP is a thin wrapper.
- **Schemas.** Every tool's input and output schema, and every file in `schemas/`, passes the description lint. `tools/list` order is stable across restarts.
- **Read-only.** After running every non-write tool against a copy of the `combined` project, a hash of every file outside `.oa/` is unchanged. An `open_board` of a bare `.kicad_pcb` writes nothing in its directory.
- **Proposals:**
  - `propose_rule_changes` from IM-001/DP-003 violations on `combined` returns a diff that KiCad accepts: `kicad-cli pcb drc` loads the patched `.kicad_dru` without rule-syntax errors (marker `kicad`).
  - `apply_proposal` fails with `PERMISSION_DENIED` without `--allow-writes`.
  - With `--allow-writes` it applies once. Reusing the token fails, and editing the file between propose and apply gives `STALE_BASE`.
  - A backup exists after apply.
- **Budgets.**
  - `run_analysis(dry_run=true)` returns an estimate and starts no job.
  - A job over the default budget is refused with `BUDGET_EXCEEDED` and the estimate.
  - A budget above the server ceiling is clamped, with a warning.
  - `cancel_job` cancels a running dummy job within 12 s.
- **Errors.** A setup with a misspelled field returns `isError=true`, an envelope with `SETUP_INVALID`, a JSON-Pointer `path` and a `hint`. An unknown net returns `NOT_FOUND` with close-match candidates.
- **Size.** No tool response for any fixture exceeds `max_response_bytes`. Truncation always comes with `RESULT_TRUNCATED` and a working `next_offset`.
- **No network.** The whole `tests/agents/` suite runs under pytest-socket (loopback only). Importing `oa_mcp` and `oa_service` does not load `anthropic`, `openai` or `requests`. The `httpx` that the `mcp` SDK depends on may load, but the socket guard proves it never connects off-host.
- **Docs.** The `llms.txt` links resolve, every tool name in `docs/agents/AGENTS.md` and `llms.txt` exists in the catalog, and `CLAUDE.md` is ≤ 200 lines.

### WP-211 Mesh performance and hole-contact fix. Size: M
**Owned:** `packages/oa-mesh/**` (starts after WP-206 merges, since WP-206 extends this package).
**Depends:** 109, 204, 206.
**Deliverables:**
1. Fix the hole-mode contact-ring bug (CR WP-204-mesh-hole-contacts).
2. Per-layer parallel meshing through a process pool. gmsh is global-state, so use one process per layer or region, with deterministic merge and unchanged content_hash semantics.
3. gmsh tuning: `General.NumThreads`, a 2D algorithm benchmark (Delaunay / Frontal-Delaunay / others) with a quality-vs-speed table, and HXT for 3D.
4. Size-field coarsening in plane interiors.
5. Feed WP-204's DC IR timing (3e5 unknowns) back to TASKS: either restore the total <20 s acceptance or record an ADR-backed change.

**Acceptance:**
- Benchmarks before and after on the power_plane board, plus a synthetic 8-layer board.
- All existing oa-mesh and pi.dc goldens are unchanged within tolerance.
- Mesh determinism holds across process counts.

---

## Wave 3

### WP-301 Palace adapter (and WSL/Docker execution). Size: L
**Owned:** `packages/oa-em-palace/**`, `tests/golden/em_palace/**`, `docker/palace/**`

**Deps:** WP-205, WP-206, WP-107.

**Deliverables:**
- `PalaceAdapter`:
  - `capabilities` (modal wave ports, lumped, absorbing BCs, broadband dielectric via frequency-dependent materials where supported, otherwise a per-band fit, documented);
  - `prepare`: mesh via WP-206, `config.json` (Driven, adaptive sweep option, AMR passes, wave/lumped ports, impedance BCs for rough copper where supported);
  - `collect`: `port-S.csv` → NetworkResult with port names, plus the convergence log.
- A Dockerfile pinning Palace 0.18.x (Spack). WSL install instructions.
- Hooks in reserved files for an MPI ranks option and a GPU flag.

**Acceptance:**
- Snapshot tests of `config.json` for the fixtures. `collect` on committed raw output.
- External (nightly, Linux container): the stripline thru Z0 is within 2 % of BEM. On the via transition, **openEMS vs Palace** gives |ΔS21| ≤ 0.5 dB to 20 GHz and ≤ 1 dB to 40 GHz, and |ΔS11| ≤ 3 dB where S11 > −30 dB. Quality `pass` for both.
- A Windows test with the `wsl` runner, if WSL is available on the runner, otherwise a documented manual check.

### WP-302 Validation suite and published validation page. Size: L
**Owned:** `tests/golden/crosssolver/**`, `tests/golden/published/**`, `tools/validation/**`, `docs/validation/**`

**Deps:** WP-201, WP-202, WP-203, WP-204, WP-205, WP-208, WP-301.

**Deliverables:**
- Published-reference cases:
  - ≥ 10 Polar SI9000 app-note or user-guide structures, cited (≤ 1 % SE, ≤ 2 % diff);
  - ≥ 20 Wadell worked examples;
  - Bryant-Weiss microstrip tables (≥ 10 points);
  - TNT-MMTL/atlc outputs generated offline, storing the outputs not the code, with the generation procedure documented.
- Cross-solver cases: BEM vs FEM (from WP-202) collected; openEMS vs Palace matrix; DC FEM vs raster vs kicad-irdrop/padne (external oracles, nightly).
- EM published: the openEMS MSL notch and simple patch tutorials regenerated from our pipeline.
- Generator `tools/validation/build_page.py` produces `docs/validation/*.md` tables and plots (error vs reference, pass/fail, tolerance, source) from the latest golden results. A CI job publishes it to the docs site.

**Acceptance:**
- Every ROADMAP M1 validation gate is represented by at least one case, with a link from the ROADMAP table to the case directory.
- The page builds in CI.
- Any gate failure fails the nightly workflow, and the summary lists the failing cases.

### WP-303 M1 end-to-end and release. Size: M
**Owned:** `tests/e2e/**`, `.github/workflows/release.yml`, `docs/user/**`, `packaging/**`

**Deps:** WP-207, WP-209, WP-210, WP-302, and **WP-511 (M1P gate)** for the release itself. The E2E scripts can be written before M1P closes; the release cannot ship until WP-511 passes.

**Deliverables:**
- E2E scripts. Starting from `.kicad_pcb` for every WP-102 board plus `cm5_minima`, they run `oa init`, then ingest, ERC (SARIF), impedance profile, DC IR (power boards), EM (via board, openEMS), and an HTML report.
- Wheels with the bundled `web/dist`.
- A `uv tool install open-analysis` meta-package (`packages/open-analysis` owned here, depending on all M1 packages).
- A GitHub Action example for `oa erc` in CI.
- A quick-start user guide (install, KiCad and openEMS/Palace setup, first analyses).

**Acceptance:**
- The E2E suite passes on Windows (openEMS local) and Linux (openEMS + Palace in Docker).
- A fresh-machine install following `docs/user/quickstart.md` works, verified by the CI job using a clean runner.
- Release dry-run artifacts are produced.
- **AI exit criterion (ROADMAP M1).** `tests/agents/test_m1_workflow.py` runs with **no skipped steps** on Windows and Linux. Over MCP stdio, an agent-style client performs:
  1. `open_board` (`combined`);
  2. `impedance_profile` (diff pairs);
  3. `run_erc`;
  4. `open_board` (`power_plane`);
  5. `run_analysis(pi.dc)`;
  6. `result_summary` and `list_violations`/`get_result` for each.

  Every summary states its accuracy class, and the results match the CLI E2E results.
- The meta-package exposes the `oa-mcp` console script.

### WP-304 Benchmark harness and reporting. Size: M (expanded for M1P; was S)
Defined in full in the M1P section below.

---

## M1P: performance and accuracy (between M1 features and the M1 release)

Milestone definition: [ROADMAP](ROADMAP.md) "M1P". Gates: [BENCHMARKS.md](BENCHMARKS.md). Design: [ADR-0008](adr/0008-compiled-core.md) (amended), [ADR-0009](adr/0009-gpu-acceleration.md), [ADR-0010](adr/0010-gpu-fdtd-engine.md). Rule 11 (one heavy/GPU token, at most 3 agents) applies to every WP here.

**Each M1P WP adds new packages and contracts.** For root-file changes, the WP writes a change request with the exact text, and the integrator applies it:
- workspace sources;
- import-linter `root_packages`, layers and forbidden contracts;
- `[tool.mypy]` paths;
- pytest markers such as `gpu`.

These change requests are `docs/change-requests/<WP-ID>-root-config.md`.

### Dependency graph and schedule

```mermaid
flowchart LR
  WP304[304 Benchmark harness]
  WP501[501 Palace accuracy + converged refs]
  WP502[502 Palace performance + CUDA build]
  WP503[503 oa-accel core]
  WP504[504 oa-accel integrations]
  WP505[505 oa-fdtd core engine]
  WP506[506 oa-fdtd physics + ports]
  WP507[507 oa-fdtd adapter + verification]
  WP508[508 oa-native crate, parser, ReferenceMap]
  WP509[509 oa-native ERC, BEM assembly, sampling]
  WP510[510 Accuracy closure 2D + openEMS]
  WP511[511 M1P gate run]
  WP304 --> WP501 & WP510 & WP504 & WP508
  WP501 --> WP502
  WP503 --> WP504 & WP505
  WP505 --> WP506 --> WP507
  WP505 -. grid API .-> WP507
  WP508 --> WP509
  WP501 -. converged EM refs .-> WP507
  WP502 & WP504 & WP507 & WP509 & WP510 --> WP511
  WP511 --> WP303[303 M1 release]
```

WP-304's dependency is soft: the downstream WPs need its case and result format (`oa-bench/1`) early, so WP-304 publishes that format in its first 2 days and the others start against it.

**Parallel lanes.** There are three lanes, so at most 3 agents run at once. One token is shared (rule 11).

| Lane | Order | Token use |
|---|---|---|
| 1, GPU | WP-503 (S) → WP-505 (L) → WP-506 (L) → WP-507 (M) → WP-511 (S) | GPU for kernel tests and the FDTD verification runs. Holds the token most of the time |
| 2, EM heavy | WP-501 (L) → WP-510 (L) → WP-502 (M) | Palace and openEMS runs, the Palace CUDA build. Token in scheduled blocks, overnight where possible |
| 3, CPU light | WP-304 (M) → WP-508 (L) → WP-509 (M) → WP-504 (M) | Light. WP-304's baseline run and WP-504's GPU measurements need token blocks |

- Can run in parallel at any time: one WP from each lane. The pairs that never collide on owned paths are 503/505/506 with 501/510/502 and with 304/508/509.
- WP-504 and WP-509 both touch `oa_impedance/bem/`, in different files (`solver.py` vs `kernels.py`). Lane 3 runs them in sequence, so they are never concurrent.
- Critical path: WP-503 → WP-505 → WP-506 → WP-507 → WP-511, about 24 working days at one focused agent each. Lanes 2 and 3 each take about 20 days, and token contention makes the real elapsed time longer. With the token serializing heavy work, plan for about 6 weeks.

---

### WP-304 Benchmark harness and reporting. Size: M
**Owned:**
- `tests/benchmarks/**`, except `tests/benchmarks/references/**` (owned by WP-501 and WP-510, see there);
- `tools/bench/**`;
- `tools/validation/**` (handed over from WP-302, which is merged);
- `docs/validation/benchmarks/**`;
- `.github/workflows/bench.yml`.

**Deps:** WP-101, WP-104, WP-201, WP-204 (all merged). Format first, so that other WPs can start (see above).

**Deliverables:**
- One case directory per BENCHMARKS.md id: `tests/benchmarks/cases/<ID>/case.toml`, containing:
  - the inputs (fixture or board, generator seed);
  - the reference (class, citation or `[verify]`, the path of the reference data under `tests/benchmarks/references/`);
  - the accuracy, runtime and memory targets per backend and solver, copied verbatim from BENCHMARKS.md §2.
- A runner, `tools/bench/run.py`:
  - selects cases (`--case`, `--group`), backends (`--device cpu|cuda|rocm`) and solvers;
  - applies the `bench` or `default` profile (BENCHMARKS.md §1), enforces the host memory cap and the CPU-load precheck, and runs cold + 3 warm repetitions;
  - records wall time, host peak memory (job object / cgroup), GPU peak and `AccelReport`;
  - compares accuracy against the reference with the case's metric (dB difference per frequency, % error, exact match), and writes `tests/benchmarks/results/<date>-<commit>-<host>.json` (schema `oa-bench/1`).
- Report generation: `tools/validation/build_page.py` extended to generate `docs/validation/benchmarks/index.md` and one page per group. These hold the target and measured values per backend, pass/fail, history plots (time and accuracy per commit) and a link from each case to its references. The summary page links it.
- Regression alert: more than 20 % slower, or an accuracy loss of more than 25 % of the tolerance, against the last passing run fails the nightly job.
- The pre-M1P baseline run, committed as `tests/benchmarks/BASELINE.md`, with py-spy flamegraphs for B-ING-01, B-ERC-01 and B-DC-02.

**Acceptance:**
- Every BENCHMARKS.md case id has a `case.toml`. Cases whose reference is not produced yet run and report `ref-pending`, and the runner never crashes on them.
- Runner unit tests use synthetic cases and pass on Windows and Linux without a GPU. `--device cuda` with no GPU present reports `skipped (no device)`, never `pass`.
- The baseline run is committed and reproduces the "Today" column of BENCHMARKS.md §2 within 20 %.
- The benchmark pages build in CI from a committed results file.

### WP-501 Palace accuracy, converged EM references, and the 1.24 dB discrepancy. Size: L (heavy)
**Owned:**
- `packages/oa-em-palace/**`;
- `docker/palace/**`;
- `tests/golden/em_palace/**`;
- `packages/oa-mesh/src/oa_mesh/occ3d/**`;
- `tests/benchmarks/references/em/**`.

**Deps:** WP-301 (merged), WP-304 (case format).

**Deliverables:**
- **Root cause and fix of the microstrip IL discrepancy.** At 10 GHz on 25 mm, Palace gives 1.24 dB against openEMS's 0.74 dB, and Pozar's closed form gives about 1.0 dB. Steps:
  1. Decompose IL into α_c, α_d and mismatch for Palace, openEMS and the 2D BEM/FEM RLGC, using γ and Z0 extracted from S.
  2. Make the dielectric model identical across all three (Djordjevic-Sarkar at each frequency; today Palace holds tan δ at its 5 GHz value).
  3. Check `Boundaries.Conductivity` with `Thickness` on interior zero-thickness sheets for double-sided loss, against thick copper with an impedance boundary on every face and against the FEM α_c.
  4. Check the wave-port `Offset` de-embedding and the modal normalization.
  5. Run p1 → p3 and AMR convergence.

  Write up the findings in `docs/change-requests/WP-501-palace-il-root-cause.md`.
- **Converged configuration:**
  - `Solver.Order` 2/3;
  - `Model.Refinement` AMR to max |ΔS| ≤ 0.002 (thru) or ≤ 0.005 (via, launch, filter);
  - curved (second-order) geometry for via barrels from `occ3d`;
  - per-band dielectrics: Palace 0.18 has no frequency-dependent materials, so the sweep is split into bands, each evaluated at its centre and stitched, with the band count set so the DS change inside a band is ≤ 0.2 % in ε and ≤ 2 % in tan δ;
  - surface-impedance copper with roughness: `Conductivity` with `Thickness` and an effective σ(f) per band from the `oa_physics` Hammerstad/Huray factor (documented as an approximation).
- **Iterative solvers as default:** AMS-preconditioned GMRES with tuned tolerances; a cuDSS/MUMPS direct solve only where the memory estimate fits. A memory estimator per configuration keeps runs inside the box (6 GB default, 12 GB for B-EM-03/04/06 under the token).
- **Converged reference data** for B-EM-01…06 in `tests/benchmarks/references/em/<ID>/`. Each holds the Touchstone, the convergence history and a `provenance.json` (Palace version, order, DOFs, ΔS history). It is regenerated only with `--update-golden`.
- `occ3d` changes as needed: `msh_version` argument (CR WP-301 §6), curved-element export, and size fields for p2/p3. The existing WP-206 tests must stay green.

**Acceptance:**
- B-EM-01 and B-EM-02(b): Palace within 0.05 dB IL and 1 dB |S11| of reference R1 (2D RLGC cascade, produced by WP-510), with R1 and R2 agreeing within 0.02 dB. B-EM-02(a): exact lossless within 0.01 dB.
- References exist for B-EM-03…06 with a converged ΔS history.
- The root-cause write-up names the cause or causes with numbers, and openEMS (after WP-510) agrees with Palace within 0.05 dB on B-EM-01.
- No Palace run exceeds its memory box (cgroup peak is recorded).

### WP-502 Palace performance and GPU build (CUDA in WSL2, documented HIP). Size: M (heavy, GPU)
**Owned:** the same paths as WP-501 (sequential after it), plus `docs/user/palace-gpu.md`.

**Deps:** WP-501.

**Deliverables:**
- An adaptive fast sweep (`Driven.AdaptiveTol`) as the default for ≥ 21 points, with the tolerance set from the B-EM accuracy gates.
- `docker/palace/install-wsl-cuda.sh` builds Palace 0.18.1 in WSL2 with:
  - `PALACE_WITH_CUDA=ON`, `CMAKE_CUDA_ARCHITECTURES=89`, `PALACE_WITH_MAGMA=ON`;
  - `PALACE_WITH_CUDSS=ON`, with `CUDSS_DIR` pointing at the cuDSS archive (not a wheel);
  - the CUDA toolkit from a user-space micromamba environment (no sudo; the Windows driver provides libcuda).

  The build runs under the same `systemd-run` memory/CPU scope at `make -j2`.
- The `gpu=True` path of `PalaceAdapter`: `Solver.Device = "GPU"`, the default backend, the GPU lease, and an MPI rank per GPU (1).
- `docs/user/palace-gpu.md`: the CUDA recipe as tested, and the HIP recipe (`PALACE_WITH_HIP=ON`, `CMAKE_HIP_ARCHITECTURES`, ROCm in WSL for the supported Radeon parts per ADR-0009 §7), marked "not tested on AMD hardware".

**Acceptance:**
- B-EM-01 Palace runtime: ≤ 300 s CPU and ≤ 90 s GPU, at the same accuracy as WP-501.
- B-EM-03 and B-EM-06: Palace runtime gates (GPU and CPU) met.
- GPU vs CPU Palace S-parameters agree within 1e-3 linear on B-EM-01.
- The build script runs from a clean WSL distro, as a documented manual check.

### WP-503 `oa-accel` core: device selection, memory budget, backend protocol. Size: S (GPU)
**Owned:** `packages/oa-accel/**`, `.github/workflows/wp-503-gpu.yml`, `docs/change-requests/WP-503-*.md`.

**Deps:** none (ADR-0009).

**Deliverables:**
- `oa_accel.select(policy)` implementing ADR-0009 §3: explicit argument → config → `OA_DEVICE` → `auto` with the crossover thresholds.
- `MemoryBudget`: `OA_GPU_MEM_GB`, a pool limit, a pinned-memory limit, `estimate_bytes` helpers, and an `ACCEL_OUT_OF_MEMORY` refusal.
- The `Backend` protocol (`dense_solve`, `sparse_solve` with CPU implementations, `raster_stencil`), `AccelReport`, and `fallback="cpu"|"error"`.
- Extras `cuda12`, `cuda13`, `rocm`. A lazy CuPy import, so that `import oa_accel` without CuPy costs < 50 ms.
- A `gpu` pytest marker, skipped unless `OA_RUN_GPU=1`.
- A CR with the root-config text: layer placement next to `oa_physics`, and the forbidden contract "only `oa_accel` and `oa_fdtd.engine` import `cupy`".

**Acceptance:**
- Without CuPy, every test passes on Windows, Linux and macOS, and `auto` selects the CPU.
- On the reference GPU:
  - `auto` selects `cuda` above the threshold and `cpu` below it;
  - the memory-limit refusal triggers before allocation;
  - a forced CUDA error falls back (`fallback="cpu"`) or returns `ErrorInfo` (`fallback="error"`).
- Dense and sparse CPU-vs-GPU agreement within ADR-0009 §5 on random SPD systems with fixed seeds.

### WP-504 `oa-accel` integrations: BEM dense, DC IR/thermal sparse, raster. Size: M (GPU)
**Owned:**
- `packages/oa-accel/src/oa_accel/solvers/**`;
- `packages/oa-impedance/src/oa_impedance/bem/solver.py`;
- `packages/oa-pi/src/oa_pi/dc/solvers.py`;
- `packages/oa-pi/src/oa_pi/reference/raster.py`.

**Deps:** WP-503, WP-304.

**Deliverables:**
- BEM: batched dense solves through `Backend.dense_solve` (cuSOLVER on NVIDIA) for `solve_many`, with sections grouped by size.
- DC IR: cuDSS via nvmath-python (SPD) and AMGX-PCG optional; CuPy CG with Jacobi as the portable path (ROCm).
- Raster FVM: a matrix-free CuPy stencil with CG.
- Thermal: the same sparse entry point, exposed for WP-404 (M2). No thermal solver here.
- The crossover thresholds measured on the reference machine and written into `oa_accel` defaults, citing the WP-304 results file.

**Acceptance:**
- B-IMP-04 and B-DC-02 meet their GPU runtime targets and memory ceilings. Their CPU accuracy is unchanged (existing golden suites green).
- GPU vs CPU within ADR-0009 §5 on B-IMP-01/02 and B-DC-01.
- The portable path that ROCm uses (CG + Jacobi, no cuDSS or AMGX) is tested on CUDA by forcing `method="cg"`, and smoke-tested on AMD hardware if available.

### WP-505 `oa-fdtd` core engine. Size: L (GPU)
**Owned:** `packages/oa-fdtd/**` (whole package until merged), `packages/oa-mesh/src/oa_mesh/rect/**`, `tests/golden/fdtd/**`, `docs/change-requests/WP-505-*.md`.

**Deps:** WP-503.

**Deliverables (ADR-0010 §2, §4, §7, §8):**
- `oa_mesh.rect`: the graded rectilinear mesher, ported from the `oa_em_openems.mesh` rules, with the copper-face lines.
- The averaged-material edge index, the Courant time step, and the float32/float64 field store.
- Engines:
  - the NumPy float64 reference engine;
  - CuPy `RawKernel` fused E and H updates with 2.5D z-marching, slab-group specialization, padded SoA layout and CUDA-graph step batching;
  - a `dtype=float64` GPU variant.
- PEC/PMC walls, CPML (CFS), lumped ports, Gaussian excitation, the on-device float64 running DFT, and the energy end criterion.
- The memory estimator wired to `oa_accel.MemoryBudget`, and multi-excitation batching of the field arrays.
- The `oa-fdtd run job.json` console script (an engine-level job format; no `oa_framework` import yet).

**Acceptance:**
- ADR-0010 §9 cases 1, 2, 3 and 7 pass on both engines. float32 GPU vs float64 is within ADR-0009 §5.
- B-PERF-01 plain-kernel throughput ≥ 3.0 Gcells/s.
- The NumPy engine runs every test without a GPU on Windows, Linux and macOS.

### WP-506 `oa-fdtd` physics and ports: SIBC copper, conformal, ADE, mode ports, de-embedding. Size: L (GPU)
**Owned:** `packages/oa-fdtd/src/oa_fdtd/{materials,subcell,ports,kernels}/**` and their tests, `tests/golden/fdtd_physics/**`.

**Deps:** WP-505.

**Deliverables (ADR-0010 §3, §5, §6):**
- Thick-copper SIBC: finite-slab `Zs(ω)` × roughness, pole fit, recursive convolution; plus the `sheet` and `pec` models.
- Dey-Mittra conformal PEC for via barrels and arcs, with area-fraction clipping.
- Djordjevic-Sarkar and table dielectrics as an automatic N-pole Debye fit, with ADE kernels.
- Quasi-TEM mode ports: templates from `oa_impedance` 2D FEM/BEM, N modes, TF/SF injection, modal V/I.
- Reference-plane shift from measured γ, and a hook for the `oa_network` 2x-thru.
- An optional matrix-pencil tail extrapolation.
- The `FDTD.UNSTABLE` late-time growth detector.

**Acceptance:**
- ADR-0010 §9 cases 4, 5 and 6 pass.
- The Debye fit meets the ε/tan δ error limits over 0.1-40 GHz for the physics library's FR-4 and low-loss laminates.
- B-PERF-01 PCB-like throughput ≥ 1.5 Gcells/s.

**Part B, M2 (EMC, [ADR-0011](adr/0011-emc-compliance-prediction.md) §5b). Added 2026-10-29.** It starts only after the WP-511 M1P gate and does not gate M1P, WP-507 or WP-511.
- **Owned (additional):** `packages/oa-fdtd/src/oa_fdtd/{nf2ff,probes,wires,templates}/**` and their tests, and `tests/golden/fdtd_emc/**`.
- **Deps:** WP-507 merged.

Deliverables:
- **NF2FF on the GPU (Huygens surface):**
  - a closed Huygens box with decimated surface sampling (≤ λ/10 at f_max);
  - a frequency-domain mode: on-device float64 DFT of the tangential E/H at the requested frequencies, giving far-field E_θ/E_φ, directivity, gain and radiated power;
  - a time-domain near-zone mode: the exact 1/R and 1/R² Kirchhoff/Stratton-Chu sum to a few observation points per time step, for many frequencies at finite distance (the 1 m MIL antenna);
  - free-space and PEC half-space (image) Green's functions;
  - memory accounted in the estimator.
- **Near-field sampling:** point E/H time series and DFT; plane (2D) sampling with decimation and aperture averaging (antenna apertures); field dumps for the viewer.
- **Thin wires over a ground plane:**
  - the Holland-Simpson thin-wire model (radius below the cell size), with lumped R/L/C loads and ports on segments, and junctions to PEC planes;
  - an insulated-wire correction;
  - a bundle as a common-mode thin wire coupled to an internal MTL (multiwire/MTLN hybrid; p.u.l. matrices supplied by the caller);
  - shield transfer impedance Z_t(f).
- **Huygens source injection** (equivalent-source replay): fields recorded on a box in one run are injected in another, on a different grid (ADR-0011 §5b.3 hybrid L2).
- **Large-domain bench template** `templates.bench(standard, params)`:
  - the MIL-STD-461G/H, CISPR 25 (component) and CISPR 32/FCC tabletop geometries;
  - a finite ground plane with bond straps, harness routing per ADR-0011 §2.3, LISN ports and antenna reference points;
  - graded coarse air with fine cells at the harness and EUT;
  - CFS-CPML settings for the low band;
  - a segment planner (S1-S4) with memory and runtime estimates against `oa_accel.MemoryBudget`, and spectral stitching with an overlap check.
- **Reciprocity mode:** Hertzian-dipole excitation at observation points, recording voltages and fields at source sites.

Acceptance:
- B-EMC-05 (thin wire) and B-EMC-06 (NF2FF and near-zone) pass.
- Huygens injection reproduces the direct run within 0.2 dB at the observation points on a dipole-in-box case.
- The bench template builds every listed standard. Its memory estimate lies within 0.8-1.25× of the measured peak (ADR-0009 §4).

**Size effect:** Part A stays L and is unchanged. Part B is **L (8-10 days)**: NF2FF M, thin wire and MTLN M-L, template and injection M. WP-506 as a whole is therefore XL across two milestones, which is why Part B is split out and scheduled after the M1P gate. To avoid reopening the M1P kernels later, Part A reserves the probe and Huygens hook points in the kernel step loop (no-op when unused, about +1 day inside Part A's L).

### WP-507 `oa-fdtd` adapter, integration and verification against openEMS. Size: M (GPU + heavy)
**Owned:** `packages/oa-fdtd/src/oa_fdtd/adapter/**`, `packages/oa-fdtd/pyproject.toml` (entry points), `packages/oa-fdtd/tests/verification/**`, `packages/oa-fdtd/README.md`, `tests/golden/fdtd_verify/**`.

**Deps:** WP-505 (start: grid API), WP-506 (acceptance), WP-501 (EM references).

**Deliverables:**
- `FdtdAdapter` (`SolverAdapter`, entry point `fdtd`): `capabilities`, `check`, `prepare` (`problem.json`, `grid.npz`, one `ExternalCommand` per batch, `Resources(gpu=True)`), and `collect` (power-wave S, Touchstone, sidecar, quality gate).
- An `oa em --solver fdtd` end-to-end test through `oa_testkit.harness`.
- Verification against openEMS on identical grids (ADR-0010 §9 cross-solver).
- The README: public API, models, verification table, known gaps.

**Acceptance:**
- The openEMS identical-grid check passes (|ΔS21| ≤ 0.01 dB, |ΔS11| ≤ 1 dB above −40 dB, to 20 GHz).
- **B-EM-01…05 accuracy and GPU runtime gates pass with oa-fdtd.** B-EM-06 is reported.
- The quality gate passes with PQM ≥ 99 on every B-EM case.

### WP-508 `oa-native` crate, KiCad S-expression parser, ReferenceMap. Size: L
**Owned:**
- `crates/oa-native/**` (whole crate until merged);
- `packages/oa-native/**`;
- `packages/oa-ingest-kicad/src/oa_ingest_kicad/sexpr.py`;
- `packages/oa-geometry/src/oa_geometry/refmap.py`;
- `.github/workflows/wp-508-wheels.yml`;
- `docs/change-requests/WP-508-*.md`.

**Deps:** WP-101, WP-104 (merged), WP-304. Needs the Rust toolchain installed (open question 6).

**Deliverables:**
- A PyO3/maturin crate with abi3 wheels for Windows, Linux and macOS.
- A native S-expression tokenizer/parser returning the same token tree as `sexpr.py`.
- Native ReferenceMap kernels: the spatial index and reference-plane queries.
- An `OA_NATIVE=0|1|auto` switch. The pure-Python implementations stay as the oracles.

**Acceptance:**
- B-ING-01 accuracy and runtime gates: native vs Python `content_hash()` identical on every test board.
- Wheel builds succeed for 3 OSes in CI.
- With no wheel installed, everything still passes on Python.

### WP-509 `oa-native`: ERC loops, BEM assembly, profile sampling. Size: M
**Owned:**
- `crates/oa-native/src/{erc,bem,sampling}/**` and `crates/oa-native/src/lib.rs` (after WP-508 merges);
- `packages/oa-erc/src/oa_erc/engine.py` and the new `packages/oa-erc/src/oa_erc/_native.py`;
- `packages/oa-impedance/src/oa_impedance/bem/kernels.py`;
- `packages/oa-geometry/src/oa_geometry/sampling.py`.

**Deps:** WP-508.

**Deliverables:**
- Native ERC inner loops for the rules dominating the 10k-track profile (IM-002 sampling, RP/ED/XT geometric queries) behind the existing engine interface.
- Native BEM matrix assembly (analytic log integrals).
- Native profile sampling.

**Acceptance:**
- B-ERC-01: exact match and runtime gates.
- B-IMP-01 and B-IMP-02 runtime gates, with BEM results identical to the Python assembly within 1e-12 relative.

### WP-510 Accuracy closure: 2D references, BEM interface, openEMS adapter. Size: L (heavy for openEMS runs)
**Owned:**
- `packages/oa-impedance/src/oa_impedance/bem/geometry.py`;
- `packages/oa-impedance/src/oa_impedance/fem/**`;
- `packages/oa-em-openems/**`;
- `tests/golden/em_openems/**`;
- `tests/golden/published/**`;
- `tests/benchmarks/references/xs/**`;
- `tests/benchmarks/references/dc/**`.

**Deps:** WP-304. Coordinate with WP-509, which owns `bem/kernels.py`.

**Deliverables:**
- BEM: fix the dielectric-interface panelling behind the 0.57-0.59 % εeff gap, and the CPW box-truncation error (0.22 %). Both are FOLLOWUPS items.
- Converged 2D references: FEM P2/P3, 3 levels, Richardson, for B-IMP-02/03 and the R1 RLGC references of B-EM-01/02. DC converged references for B-DC-01.
- openEMS adapter:
  - a multi-pole Debye fit (as ADR-0010 §5);
  - a thick-copper option (copper-face mesh lines; sheets on both faces);
  - the stripline stall fix (CFS-PML settings, or a documented Mur fallback reaching −50 dB);
  - the MSL notch tutorial reproduction (B-EM-06b).
- Published references: fill the Polar SI9000, Wadell and Bryant-Weiss slots, and the kicad-irdrop/padne values, **only from sources checked in this WP**. Anything else stays `[verify]`.

**Acceptance:**
- B-IMP-01, B-IMP-02 and B-IMP-03 accuracy gates pass.
- B-EM-01 and B-EM-02 pass with openEMS (accuracy and CPU runtime).
- The FOLLOWUPS items named above are closed.

### WP-511 M1P gate run and sign-off. Size: S (heavy; holds the token for the whole run)
**Owned:** `tests/benchmarks/results/**` and `docs/validation/benchmarks/**` (both after WP-304 merges), `docs/M1P-report.md`.

**Deps:** WP-502, WP-504, WP-507, WP-509, WP-510.

**Deliverables:**
- The full benchmark run on the reference machine under the `bench` profile, plus a CPU-only run (`OA_DEVICE=cpu`) for the accuracy gates.
- The generated benchmark pages, and a report listing every case's status, any gaps against BENCHMARKS.md, and the follow-ups.

**Acceptance:**
- Every ROADMAP M1P exit criterion holds. The report is signed off by the project owner, which unblocks WP-303's release.

---

## M2 sketch (to be detailed after M1; IDs reserved; WP-412 is M3)

| ID | Title | Owned (planned) | Depends |
|---|---|---|---|
| WP-401 | Linear SI channel engine (`oa-si`): MTL ABCD, node-equation cascade, TDR/step, skew/delay, tuning-profile export | `packages/oa-si/` | 104, 105, 106 |
| WP-402 | Crosstalk scan + XT-002/003, N-conductor sections | `packages/oa-si/src/oa_si/xtalk/` | 401, 105 |
| WP-403 | AC PDN lumped (`oa-pi/ac_lumped`), cap library, target Z | `packages/oa-pi/src/oa_pi/ac/` | 204, 106 |
| WP-404 | Thermal layered FVM + electro-thermal (`oa-thermal`) | `packages/oa-thermal/` | 204 (`pdn` service) |
| WP-405 | Quasi-static extraction (`oa-qs`): FasterCap, FastHenry2, Palace ES/MS adapters, π-SPICE export | `packages/oa-qs/` | 104, 206, 107 |
| WP-406 | **Superseded by WP-420 to WP-426 (ADR-0011).** Was: EMI estimates (`oa-emi`): DM/CM closed forms, limits, openEMS NF2FF post-processing. Its closed forms are WP-423; limits are WP-420; NF2FF moves into oa-fdtd (WP-506 Part B), and openEMS NF2FF stays as the cross-check in WP-426 | `packages/oa-emi/` | 401, 108 |
| WP-407 | Remaining P2 ERC/EMI rules | `packages/oa-erc/src/oa_erc/rules_p2/` | 201 |
| WP-408 | KiCad IPC plugin (thin) + cross-probe endpoints | `packages/oa-kicad-plugin/` | 207 |
| WP-409 | HTML report export | `web/src/report/`, `packages/oa-results/src/oa_results/io/html.py` | 209 |
| WP-410 | M2 validation (IPC-2152, JEDEC trends, Palace rings, dipole, SI cascade oracles) | `tests/golden/m2/**` | all above |
| WP-411 | In-app AI assistant (`oa-assistant`): provider protocol, Anthropic reference provider (`claude-opus-5-5` default, keyring BYO key, off by default), server router, web assistant panel, data preview and redaction, user doc on data sent (ai-integration §11) | `packages/oa-assistant/`, `web/src/features/assistant/`, `docs/user/assistant.md` | 207, 209, 210 |
| WP-412 | **(M3)** Surrogate dataset export (`oa-xsdataset/1`, `oa dataset export`/`sweep`) and impedance surrogate plugin (`oa-impedance-surrogate`, domain-gated, `accuracy_class="surrogate"`) (ai-integration §12) | `packages/oa-impedance-surrogate/`, `packages/oa-service/src/oa_service/dataset/` | 105, 202, 207 |
| WP-413 | **Superseded by WP-503/WP-504 (M1P, ADR-0009).** Was: optional GPU acceleration backend (`oa-accel`): array/sparse backend protocol with a CPU default; CUDA via CuPy first, ROCm via CuPy-ROCm through the same code; GPU paths for BEM dense solves, large sparse solves (DC IR, thermal, AC PDN) and raster solvers; Palace CUDA build in WSL2. Gated on WP-304 benchmarks; CPU fallback mandatory; ADR first | `packages/oa-accel/` | 105, 204, 301, 304 |
| WP-420 | EMC limit-line library and standards model (ADR-0011 §2) | `packages/oa-emi/src/oa_emi/limits/**` (+ see detail) | 000 |
| WP-421 | EMC noise-source extraction and culprit ranking (ADR-0011 §3) | `packages/oa-emi/src/oa_emi/sources/**` | 420, 401, 403 |
| WP-422 | CE102/CE101 conducted prediction (ADR-0011 §4) | `packages/oa-emi/src/oa_emi/conducted/**` | 420, 421, 403, 106 |
| WP-423 | RE102/RE101 screening estimator (ADR-0011 §5a) | `packages/oa-emi/src/oa_emi/radiated/**` | 420, 421 |
| WP-424 | Full-wave RE102 bench on oa-fdtd (ADR-0011 §5b) | `packages/oa-emi/src/oa_emi/bench/**` | 421, 506 Part B, 507 |
| WP-425 | EMC report, UI and MCP/CLI commands (ADR-0011 §6) | `packages/oa-service/src/oa_service/emc/**`, `web/src/features/emc/**`, `packages/oa-emi/src/oa_emi/report/**` | 422, 423, 424, 409, 210 |
| WP-426 | EMC validation (analytic, cross-solver, published measured) | `tests/golden/emc_validation/**`, `docs/validation/emc/**` | 420-425 |
| WP-414 | **Superseded by WP-508/WP-509 (M1P, ADR-0008 amendment).** Was: compiled core per ADR-0008: `oa-native` Rust crate (PyO3/maturin) for parser, geometry/ReferenceMap, BEM assembly, raster solvers, ERC loops, each with a Python reference oracle; mypyc for typed packages; wheel builds in CI for 3 OSes; native app bundle | `crates/oa-native/`, `packages/oa-native/` | 101, 104, 105, 201, 204, 304 |

---

## M2: EMC compliance prediction (ADR-0011). Detailed 2026-10-29

Design: [ADR-0011](adr/0011-emc-compliance-prediction.md). Benchmarks: [BENCHMARKS.md](BENCHMARKS.md) §5 (B-EMC-01…09). These WPs replace WP-406.

**Package ownership.** All of `oa-emi` is split by subpackage:
- `packages/oa-emi/pyproject.toml`, `src/oa_emi/__init__.py` and `src/oa_emi/model.py` (the shared payloads `EmcSource`, `EmcPrediction`, `UncertaintyBudget`, `LimitLine`) belong to **WP-420**. Later WPs request entry points or dependencies through a `docs/change-requests/WP-42x-*.md` that the integrator applies.
- `packages/oa-emi/README.md` belongs to **WP-425**. WP-420 to WP-424 each document their area in `packages/oa-emi/docs/<area>.md`, which the README links to.

**Layering.**
- `oa_emi` imports neither other analyses nor `oa_fdtd`. It reaches SI, PI, impedance and qs data through services, and oa-fdtd through its engine job format (ADR-0011 §5b.8).
- WP-420 adds an import-linter contract for this, through the integrator's root config.

**Schedule.**
- Start: WP-420 right after M1P. WP-421 and WP-423 can start once WP-420's `model.py` exists. WP-421 then needs WP-401's `si.net_currents` service.
- Critical path: WP-506 Part B → WP-424 → WP-426.
- GPU token: WP-424, WP-426 and WP-506 Part B need it for verification runs (rule 11).

### WP-420 EMC limit-line library and standards model. Size: M
**Owned:** `packages/oa-emi/src/oa_emi/limits/**`, `packages/oa-emi/src/oa_emi/data/limits/**`, `packages/oa-emi/src/oa_emi/model.py`, `packages/oa-emi/src/oa_emi/__init__.py`, `packages/oa-emi/pyproject.toml`, `packages/oa-emi/docs/limits.md`, `tests/golden/emc_limits/**`, `schemas/oa-limits-1.json`.

**Deps:** WP-000 (M2 start).

**Deliverables (ADR-0011 §2):**
- The `LimitLine` model and the `oa-limits/1` TOML schema: segments, interpolation, relaxations, detector, RBW table, distance, source citation with page and figure, `digitised` and `verify` flags, and a licence field.
- Distance scaling with a near-field refusal.
- Packaged data:
  - **MIL-STD-461G**: CE101-4, CE102 with voltage relaxations, RE101-1, RE102-1…4, Table II RBWs. Every breakpoint cites its figure and printed page.
  - **MIL-STD-461H**: its own ID. Values are copied from 461G and flagged `verify` until checked against the official 461H text, which this WP obtains from ASSIST; the CE101 20 kHz extension is included.
  - **FCC Part 15**: §15.107 (Class A/B, QP/average), §15.109 (3 m and 10 m), the §15.33(b)(1) scan-range rule and the §15.35 detectors.
- Schema-complete templates without values for CISPR 32, CISPR 25 and DO-160 Section 21 (ADR-0011 §2.4).
- The remaining figures CE101-1/2/3 and RE101-2, digitised and flagged.
- The `emc.limits` and `emc.limits.validate` command implementations (service functions; the adapters are WP-425).

**Acceptance:**
- Every breakpoint printed on a figure is encoded exactly (B-EMC-01).
- Digitised points are flagged and checked against a second independent digitisation within 0.5 dB.
- The FCC µV/m to dBµV/m conversions are exact to 0.01 dB.
- A user limit file round-trips, and invalid files give `EMC.LIMIT_FILE_INVALID` with a path.
- `LimitLine` fields carry descriptions with units.

### WP-421 EMC noise-source extraction and culprit ranking. Size: L
**Owned:** `packages/oa-emi/src/oa_emi/sources/**`, `packages/oa-emi/docs/sources.md`, `tests/golden/emc_sources/**`, `docs/change-requests/WP-421-*.md`.

**Deps:** WP-420 (model), WP-401 (needs the `si.net_currents(net, f)` service; file a change request on day 1 and use the I = V/|Z₀| fallback until it lands), WP-403 (`pdn` rails, cap library, plane-voltage map). WP-405 is optional (L_p,ret, C_sw).

**Deliverables (ADR-0011 §3):**
- Trapezoidal sources from the exact Fourier series, with clock frequency and duty from intent and edges from IBIS.
- PRBS/data densities and the spread-spectrum approximation.
- Switching-regulator DM and CM sources from the PI power tree plus user parameters. Datasheet defaults are proposed, never invented, and missing data gives `EMC.SOURCE_PARAM_MISSING`.
- Hockanson current- and voltage-driven CM sources at cable attachments.
- Board-intrinsic equivalent dipole sets: the TL-dipole sum and cavity edge currents.
- Geometry mapping and ERC cross-links (`culprit_id`).
- The receiver model (ADR-0011 §3.6).
- The ranking table.
- `emc.sources`.

**Acceptance:**
- B-EMC-02 passes.
- On every WP-102 board with a clock intent, the top culprit is a net carrying that clock, or a structure the ERC flags on it.
- Ranking order is deterministic.
- Every `EmcSource` carries `accuracy` and `assumptions`.

### WP-422 CE102/CE101 conducted prediction. Size: M
**Owned:** `packages/oa-emi/src/oa_emi/conducted/**`, `packages/oa-emi/docs/conducted.md`, `tests/golden/emc_conducted/**`, `docs/change-requests/WP-422-*.md`.

**Deps:** WP-420, WP-421, WP-403 (input-rail Z(f), cap library), WP-106 (`oa_network`).

**Deliverables (ADR-0011 §4):**
- LISN models: 461G Fig. 6, 50 µH/50 Ω, 5 µH [verify CISPR 25], and user networks.
- A power-lead MTL over the plane (p.u.l. from the `impedance` service).
- Filter import (Touchstone/SPICE) with parasitics.
- Nodal solve per frequency, with CM/DM decomposition.
- LISN-port voltage (CE102, commercial) and lead current (CE101).
- The filter-resonance and Middlebrook stability warnings.
- The `emc.conducted` analysis and command function.

**Acceptance:**
- B-EMC-03 passes: LISN impedance vs analytic ≤ 0.1 dB; a buck converter with filter vs an ngspice netlist ≤ 0.1 dB; runtime ≤ 2 s.
- Results carry `accuracy_class="estimate"` with the ADR-0011 §1.4 band.

### WP-423 RE102/RE101 screening estimator. Size: M
**Owned:** `packages/oa-emi/src/oa_emi/radiated/**`, `packages/oa-emi/docs/radiated.md`, `tests/golden/emc_screen/**`.

**Deps:** WP-420, WP-421.

**Deliverables (ADR-0011 §5a):**
- DM small-loop and TL-dipole-sum radiation with images.
- The CM cable-antenna model for the bench harness.
- RE101 Biot-Savart with loop-sensor flux averaging.
- Two-ray ground reflection and height scan for the 3 m and 10 m commercial sites (research 07 §3.4).
- The deliberate over-prediction bias, and `emc.screen`.

**Acceptance:**
- B-EMC-04 passes: closed forms implementation-locked to Ott/Paul; dipole fields vs analytic ≤ 0.1 dB; the TL-dipole sum vs oa-fdtd NF2FF on the microstrip-over-finite-plane case ≤ 3 dB DM; 1000 nets ≤ 30 s CPU.
- The screening prediction is ≥ the full-wave bench prediction minus 3 dB on every B-EMC-07 variant. This checks the bias.

### WP-424 Full-wave RE102 bench on oa-fdtd. Size: L (GPU)
**Owned:** `packages/oa-emi/src/oa_emi/bench/**`, `packages/oa-emi/docs/bench.md`, `tests/golden/emc_bench/**`, `docs/change-requests/WP-424-*.md`.

**Deps:** WP-421, WP-506 Part B, WP-507 (job path), WP-108 (openEMS, for the cross-check).

**Deliverables (ADR-0011 §5b):**
- Scene building from the board, enclosure description and harness description into the oa-fdtd bench template job.
- The L1/L2 board-representation choice, with the board-level run and Huygens recording for L2.
- Port bookkeeping for the bench network.
- Reciprocity-mode selection.
- Segment jobs through `oa_jobs` with the GPU lease.
- Stitching and the quasi-static low-frequency extension.
- Antenna observation models (point, aperture average, explicit rod).
- Frequency-domain attachment of the LISN and filter networks.
- `emc.radiated_bench` (item pair: `3d_full_wave` field solution plus the `estimate` prediction).
- `emc.whatif` evaluation from stored transfer functions.

**Acceptance:**
- B-EMC-07 passes:
  - oa-fdtd vs openEMS on the resolved-wire bench ≤ 0.5 dB;
  - the full MIL-STD-461 RE102 bench ≤ 45 min GPU and within 12 GB;
  - segment overlap ≤ 0.5 dB.
- `emc.whatif` reproduces a full re-run within 0.1 dB when only the source spectra change, in ≤ 5 s.

### WP-425 EMC report, UI and MCP/CLI commands. Size: M
**Owned:** `packages/oa-service/src/oa_service/emc/**`, `web/src/features/emc/**`, `packages/oa-emi/src/oa_emi/report/**`, `packages/oa-emi/README.md`, `docs/user/emc.md`, `docs/agents/emc.md`.

**Deps:** WP-422, WP-423, WP-424, WP-409 (HTML report), WP-210 (MCP), WP-207 (CLI/server).

**Deliverables (ADR-0011 §6):**
- The `CommandSpec`s for the seven `emc.*` commands, generated into the CLI, REST and MCP.
- `EmcPrediction.summary()` within the caps.
- Web views:
  - margin vs frequency with the limit and uncertainty band;
  - stacked source contributions;
  - a culprit table that cross-probes to the board viewer;
  - a setup editor for the harness, enclosure and converter parameters;
  - a provenance panel.
- An HTML report section with the fixed disclaimer.
- An agent guide.

**Acceptance:**
- The ai-integration contract tests pass for every `emc.*` command: envelope, ErrorInfo, deterministic order, summary caps.
- MCP end-to-end: an agent runs screen → conducted → bench → whatif on a WP-102 board through `oa_testkit.harness`.
- Every report shows the disclaimer, the limit source and its licence flag.

### WP-426 EMC validation. Size: L (GPU + heavy)
**Owned:** `tests/golden/emc_validation/**`, `docs/validation/emc/**`, `tests/benchmarks/cases/emc/**`.

**Deps:** WP-420 to WP-425.

**Deliverables:**
- B-EMC-08 published measured-vs-simulated cases, each built from the paper's stated geometry (**[verify]** each before it gates):
  - Hockanson et al., IEEE TEMC 38(4), 1996 (board with attached cable, current- and voltage-driven CM);
  - C. R. Paul, "A comparison of the contributions of common-mode and differential-mode currents in radiated emissions", IEEE TEMC 31(2), 1989;
  - Shim and Hubing, IEEE TEMC 47(4), 2005 (voltage-driven sources with attached cables).
- The IEEE Std 1597.1-2008 Feature Selective Validation (FSV) metric for curve comparison, implemented independently from the standard's definitions.
- The uncertainty-budget calibration that replaces the ADR-0011 §1.3 design values with measured statistics (the median and 90th percentile of |predicted − measured|).
- The B-EMC-09 end-to-end runs.
- Optional, owner-dependent (open question 13): our own pre-compliance measurements of a WP-102 board in a 461-style bench.

**Acceptance:**
- B-EMC-01…09 have status `pass`, or `[verify]` with the reason recorded.
- The published validation page shows predicted vs measured with FSV grades and the calibrated bands.
- The calibrated band is at least as wide as the observed 90th-percentile error.

---

## Open questions for the project owner

1. **Schema and fixture licensing.** Should `schemas/` and `tests/boards/` also be licensed CC-BY-4.0 so other tools can adopt the model? (ADR-0001, proposed refinement.)
2. **Reference test hardware.** Which machine is the performance reference (the WP-101, WP-304 targets)? Is a Linux runner with Docker available for nightly Palace?
3. **openEMS on Windows.** Which openEMS build and Python should be the default? `tools.toml` needs a canonical install recipe.
4. **Polar SI9000 and commercial reference data.** Can users with licences contribute results for the published-reference suite?
5. **Measured validation.** Is there a budget for the M5 VNA coupon program, or should we start one early?
6. **Rust toolchain (M1P, WP-508).** May we install `rustup` (user-level, stable toolchain, MSVC target on Windows) on the reference machine and in CI? WP-508/509 cannot start without it.
7. **GPU stack installs.** May we add `cupy-cuda12x` (about 1 GB of wheels), nvmath-python with cuDSS, and optionally `pyamgx` to the dev environment, and install a user-space CUDA toolkit and cuDSS archive inside WSL for the Palace GPU build (WP-502)?
8. **AMD hardware.** Is any AMD GPU (Linux or WSL-supported Radeon) available for the tier-2 smoke lane, or does AMD stay "documented, untested" for M1P?
9. **Benchmark profile.** BENCHMARKS.md §1 runs gate measurements at 8 threads and a 16 GB host cap, held by one agent with the token. Is that acceptable on the machine that froze, or should the gate use 4 threads and 12 GB (runtime targets would then be rescaled before WP-511)?
10. **Commercial comparisons.** Do you (or contacts) hold HFSS/SIwave/HyperLynx/SI9000/Sonnet licences that permit running the BENCHMARKS.md cases and publishing results? Without them, parity is claimed only against converged and published references.
11. **EMC limit licensing (ADR-0011 §2.4).** May we ship numeric CISPR 32, CISPR 25 and DO-160 Section 21 limit values as data, or do they stay user-entered templates? MIL-STD-461 (Distribution A) and FCC Part 15 (47 CFR) are shipped either way.
12. **Target standards and platforms.** Which MIL-STD-461 revision(s) and platform curves matter to you first: 461G or 461H; aircraft, ground, ship or submarine; internal or external? Do you also need CISPR 25 or DO-160 in M2, or only MIL and FCC/CISPR 32?
13. **Measured EMC data.** Can you get pre-compliance measurements (a LISN plus a rod, biconical or horn, or a lab slot) of one or two of our test boards in a 461-style bench? Published measured-vs-simulated cases cover only simple boards. Calibrating the uncertainty bands on real products needs our own data.
14. **Commercial EMC tools.** Do you or your contacts hold Ansys EMC Plus or CST Cable Studio licences that permit running B-EMC-07 and publishing the comparison?
