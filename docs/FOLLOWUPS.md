# Integration follow-ups

The integrator tracks known gaps found while merging work packages here. Each item names the WP expected to close it. Remove an item once it is fixed.

## Validation and accuracy (feeds WP-302)
- [ ] WP-106: the cannonball-Huray tile area (36 r²) is an unverified placeholder that may overestimate K. Check it against Simonovich (DesignCon 2015).
- [ ] WP-106: the Huray golden case checks the equation with typical values. Replace it with a digitised published curve.
- [ ] WP-106: CQM (causality metric) is implementation-defined and weakly sensitive. Check the IEEE P370 reference code's license, and calibrate CQM against it if allowed.
- [ ] WP-000C: the testkit's exact zero-thickness cross-section cases use t = 1e-4·ref with rtol 1e-2, which is too loose. WP-105 adds a tight thin-strip suite.
- [ ] WP-000C: the 7 Hammerstad-Jensen fixture rows are self-computed. They need an independent check.

## Web UI (feeds WP-209)
- [ ] WP-110: never run in a real browser. Smoke-test it after WP-207.
- [ ] WP-110: not done: Arrow decoding, MSW, Playwright, the 200k-polygon benchmark, the OpenAPI client, the offline check. It used npm instead of pnpm.
- [ ] WP-110: assumes `GET /projects/{pid}/board/full` and simplified ViolationSet and field shapes. Reconcile these with WP-207.

## Tooling
- [ ] The CI `schema-compat` job is a placeholder, and the cross-OS `content_hash` artifact comparison isn't wired up yet.
- [ ] The CI has never run, because there is no GitHub remote yet.

## Ingest (feeds WP-208)
- [ ] WP-101: per-net copper-area checks against expected.yaml are not done yet. Footprint-owned zones, padstack mode syntax and `die_delay` units are only lightly tested. Copper text is skipped.
- [ ] WP-103: KiCad's IPC-2581 export loses zone outline, priority and hatch; it has no backdrill diameter and no stub length; its materials carry Dk/Df only and always a 10 µm mask. Micro vias read as blind.
- [ ] WP-103: CR: `ReadOptions` is defined twice (`oa_model.setup` and `oa_framework.ingest`). `Backdrill` has no MAX_STUB_LENGTH field.
- [ ] WP-103: Altium and other-vendor IPC-2581 samples are env-gated only (license). Find CC-licensed samples.

## Geometry and mesh
- [ ] WP-104: backdrill depth and stub conventions need confirming against WP-101's reader (KiCad: the end layer is itself drilled).
- [ ] WP-104: `physics_temp` has its own ρ(T) formula. Switch to `oa_physics` (WP-106 is now merged).
- [ ] WP-104: reference_map takes 51 s at 50k tracks. Profile it and consider the Rust/PyO3 path (ADR-0002).
- [ ] WP-104: testkit builders lack the variants the acceptance text expects (a backdrilled via, a VDD via-array switch).
- [ ] WP-109: the mesh2d golden signatures are Windows-recorded and skip on gmsh version mismatch. Check them on Linux CI.
- [ ] WP-109: the default `simplify` is 0, not 1–5 µm. Gaps below 1 µm close and are reported.

## Jobs
- [ ] WP-107: CR: Budget, PlanEstimate, cancel_reason and the Provenance extensions are missing from the frozen contracts. Job priority doesn't reorder dispatch. Docker is untested.

## Impedance (WP-105)
- [ ] The embedded-microstrip exponent: no primary source was reached. The default is 1.55, flagged approximate; 2 scores about the same against BEM.
- [ ] The Cannonball-Huray geometry comes from secondary sources only (also listed under WP-106).
- [ ] There is no closed form for broadside-coupled lines (BEM only), and no dispersion for coupled lines or CPW.
- [ ] The free-space CPW golden is 0.22% off from the 20× domain truncation. Revisit this when the WP-202 FEM is in.
- [ ] CRs: accuracy fields missing from the contracts; testkit thin-strip tolerance.

## openEMS (WP-108, feeds WP-205)
- [ ] Stripline wave-port runs stall at about −33 dB energy and give non-passive S.
- [ ] CPW, coax and coupled ports are not supported, and every port must be excited.
- [ ] The single-pole Debye fit has about 11% tanδ error over 0.5–10 GHz. Use a multi-pole fit or note the band limit.
- [ ] `est_time_s` is pessimistic (316 s estimated vs 21 s actual).
- [ ] The far field is behind a flag and untested. The MSL notch tutorial reproduction is not done.
- [ ] CR: EMSolveOutput vs NetworkResult quality requirement.

## MCP (WP-210), held unmerged until WP-207 lands
- [ ] Reconcile `OaServiceLike` with the real oa-service: schema_ids, read_resource, the progress hook, the service factory name.
- [ ] Not done: `oa_service.proposals`, MCP elicitation for apply_proposal, the `tests/agents/` M1 workflow test, CLI parity, the socket guard, `llms-full.txt`.

## Impedance profile (WP-203)
- [ ] ProfileEvent.kind lacks no_reference, width_change and the pair causes; they map to `other` (CR WP-203-profile-event-kinds).
- [ ] Summary stats are lost on a JSON round trip, because the subclass reverts to ProfileResult.
- [ ] The tuning profile is only a JSON artifact (`oa-tuning-profile/1`); the KiCad format is unspecified.
- [ ] Via delay is not included.

## DC IR drop (WP-204)
- [x] (WP-211: 10.5 s total, see CR WP-211-pi-dc-timing) PERF: the agent relaxed its own 3e5-unknown test from total <20 s to solve <20 s / total <60 s, because gmsh meshing takes 12-20 s. Check this against the WP-204 acceptance criterion in TASKS.md; it needs a mesher speed-up (WP-211) or a spec decision.
- [x] (fixed WP-211) BUG in oa_mesh: hole-mode contact ring nodes are wrong (CR WP-204-mesh-hole-contacts). WP-204 uses embedded mode instead. Needs a fix in WP-109's package (WP-211).
- [ ] CR WP-204-accuracy-class: there is no `2d_sheet` accuracy class or ResultItem.accuracy in oa_results.
- [ ] The KiPI/padne/kicad-irdrop cross-checks were not run.
- [ ] Multi-rail trees and series elements are not done.
- [ ] Pads are ideal equipotentials, drawn as holes in field plots.

## Service/CLI (WP-207)
- [ ] Not implemented: `oa pi dc`, `oa em`, `oa cache gc`, `oa mcp`.
- [ ] impedance.xs/profile are untested against the real WP-105/203; ERC is tested only via stubs (see WP-201 integration note below).
- [ ] The structure3d endpoint returns 424 until WP-206 registers a service.
- [ ] WP-209 must adapt JobStatus 'done'->'succeeded' and the result/violation shapes (CR WP-207-api-reconciliation).
- [ ] The CLI uses click, not typer (CR).
- [ ] The full suite hung twice; root cause unknown at merge time.

## ERC/EMI (WP-201)
- [ ] The test boards miss their own impedance targets per BEM: CPW_A/B are 55.3 ohm vs 50, DP Zdiff 139 ohm vs 100, USB_D 126 ohm vs 90. Either redesign the WP-102 board geometry to hit the targets or list the IM-002 hits in expected.yaml (CR WP-201-expected-impedance).
- [ ] Microstrip boards have no net class, so IM-002 fires only with an explicit 50 ohm intent.
- [ ] Single-violation and negative fixtures exist only for IM-002, RP-003 and RF-001. The other rules need them.
- [ ] Gaps: no broadside XT-001; ED-003 covers the board outline only; no per-pin PD-001 coverage mode; EM-011 skips the "fed by a high-speed via" condition; stripline gaps are judged only when no side is referenced.
- [ ] PERF: 10k tracks with IM-002 on every net take 34.9 s (target for ADR-0008 native kernels).
- [ ] CRs: WP-201-remediation-model (oa_results Remediation shape) and WP-201-violationset-summary (the 2 KiB summary fits only 2 entries).
- [ ] INTEGRATION: the WP-207 service/CLI tests register a stub `erc` analysis that now collides with the real WP-201 `erc`; four tests fail (see merge notes).

## 3D mesher (WP-206)
- [x] (WP-211: min SICN 0.333) QUALITY: the agent relaxed the solid-copper min-SICN bound to 0.01 (worst 0.017 for solid 35 um copper with pads; sheets and fixtures are above 0.18). Poor tets will hurt Palace accuracy. WP-211 should improve this or default to sheet copper.
- [ ] PERF: the performance test takes 42 s (solid) and 61 s (both variants) under load; the via region takes about 15 s standalone.
- [ ] Palace details marked [verify] need checking against Palace 0.18. Interior-sheet BCs are untested in Palace (WP-301).
- [ ] CR WP-206-port-surfaces-and-signatures: wave ports are coplanar face sets, extra section-9 parameters, ViaBody.pads are ignored, and backdrill is a void only when clearance is set.

## FEM reference (WP-202)
- [ ] ACCURACY: on narrow high-er microstrips the BEM eps_eff is 0.57-0.59% low against the FEM, which sits nearer HJ. The agent loosened the FEM-vs-BEM eps_eff tolerance to 0.7%; the spec says 0.5%. Investigate the BEM dielectric-interface panelling (WP-105).
- [ ] The 50-structure FEM-vs-BEM sweep never completed. The slow tests weren't re-run after the crash.
- [ ] CR WP-202-fem-lazy-registration: `method="fem"` registers only on import, so `methods.get` needs a lazy import. (The WP-105 test test_fem_unavailable_until_registered now clears the registry itself, because FEM tests import oa_impedance.fem in the same process.)
- [ ] No element-wise adaptivity (it re-meshes globally). Zero-thickness conductors have no DC R. Quasi-static only.
- [ ] PERF: 1-20 s per static solve; 5-25 s per eddy frequency (a target for ADR-0008).
- [ ] Timing assertions in the default suite are flaky under load (test_bem_median_under_50ms and similar). Move them to the `slow`/benchmark marker or make them relative.

## EM flow (WP-205)
- [ ] PORTABILITY: the agent says its test files don't run on Linux/macOS. Make them skip cleanly or become portable; CI must stay green.
- [ ] TOOLING: the openEMS install lives only in the finished WP-108 worktree (`.claude/worktrees/agent-a42ff817b09dd0eb5/.oa/tools/openems`). Move or install it into the main checkout's `.oa/tools/` before that worktree is cleaned up.
- [ ] Workarounds in oa_em/problem.py: MUR instead of PML for wave ports (PML plateaus at -31 dB); the sweep starts at 0.2*f_max; the gate runs with `require_dc="none"`; `est_time_s` is scaled by 0.1.
- [ ] Validated only 1-10 GHz (microstrip) and 4-20 GHz (via); the full 0-40 GHz band is not validated. The stripline wave-port stall is unresolved. The differential path has no solve.
- [ ] Z0 matches the BEM thin-strip value within 0.4%, because openEMS uses sheet copper; it is 3.8% off the 35 um BEM value. Add thick-copper modelling or document this.
- [ ] CRs: WP-205-setup-ports-and-adapter-entry-point, WP-205-openems-adapter-findings.

## Web results (WP-209)
- [ ] Arrow board-geometry decoding isn't done; the UI loads /board/full.
- [ ] Not done: TDR, Smith chart, 3D preview, port picker, export buttons.
- [ ] pi.dc and network views are tested only on synthetic data, not a live run. Jobs poll instead of using the WebSocket feed.
- [ ] CR WP-209-api-typing: untyped result manifest, implicit array names, job state enum.
- [ ] PROCESS: agents must not leave `oa serve` running; start servers only through test fixtures that tear them down.

## MCP / agents (WP-210)
- [ ] The seven published `schemas/*.v1.json` documents and `oa://projects/{pid}/physics` aren't served as MCP resources yet.
- [ ] The kicad-cli DRC acceptance check for applied proposals isn't done.
- [ ] Schema-lint ratchet: a baseline of about 580 schema fields without descriptions, in other WPs' schemas. Burn it down.
- [ ] BundleSummary is 8854 B with spaced JSON; it fits the cap only when compact. Enforce compact output or trim it.
- [ ] oa-service has no progress hook; MCP progress polls running jobs instead.

## Validation (WP-302)
- [ ] Empty [verify] slots: Polar SI9000, Wadell tables, Bryant-Weiss, and a TNT-MMTL/atlc cross-check. Pozar Example 3.6 is [verify]. Fill these only from checked sources.
- [ ] Pending: Palace (4 slots, WP-301), the openEMS tutorial reproductions, and the kicad-irdrop/padne DC IR oracle.
- [ ] Only 100 of the 248 passes have an independent reference. The rest are self-computed or in-repo cross-solver. Raise the independent share.
- [ ] Add mkdocs nav and ROADMAP links to docs/validation (CR WP-302).

## Palace (WP-301), superseded by M1P P1
- [ ] ACCURACY: the microstrip IL is 1.24 dB vs about 0.75 dB (openEMS 0.74, BEM RLGC 0.78) from a p1 coarse mesh, SuperLU and a 6 GB cap. Not acceptable; fix in P1 (p2/p3, AMR, iterative solvers, surface-impedance copper).
- [ ] FREEZE #4 cause: an uncapped Palace SuperLU run of about 300k DOF in WSL. Every Palace run now goes through the `oa-palace-run` wrapper under systemd-run MemoryMax=6G.
- [ ] The stripline and via Palace acceptance cases aren't run. AMR and the adaptive sweep are untested. There's no CI test for the Windows WSL runner. Wave-port z0 isn't honoured. Dielectrics are single-frequency; roughness is ignored.
- [ ] The install is built from source in WSL (~/opt/palace, v0.18.1); docker/palace/install-wsl.sh reproduces it. The Spack Dockerfile is untested.

## M1P decisions (user, 2026-10-29)
- Approved installs: rustup (user profile); CuPy + nvmath/cuDSS in the project venv (Windows CUDA).
- NOT approved: a CUDA toolkit in WSL. Palace's GPU build (WP-502 CUDA part) is blocked; Palace stays CPU-only.
- Heavy-job budget: 8 threads and 16 GB (WSL Palace runs: systemd-run MemoryMax=12G). Only one heavy or GPU job at a time.
- No AMD GPU is available, so the ROCm path is CI-built and unit-tested only, marked "untested on hardware".

## GPU backend (WP-503)
- [ ] `ErrorInfo` in oa-accel is a local copy (CR WP-503-errorinfo-layer); switch to the shared one when the layer placement is settled.
- [ ] The pinned-memory limit isn't enforced.
- [ ] ROCm is untested on hardware (no AMD GPU).
- [ ] No CuPy is installed in the venv, so the GPU tests skip here ("no CuPy device available"). Install the `cuda12` or `cuda13` extra to run them. The three extras are declared as conflicting in the root `[tool.uv]`.

## Native parser (WP-508)
- [ ] The full KiCadReader is barely faster with the native parser (1.07 s vs 1.02 s) because parsing is a small share of the time. The Jetson target needs whole-reader profiling.
- [ ] The ReferenceMap isn't native (CR WP-508-refmap-scope, proposed WP-508b).
- [ ] The wheel CI (`wp-508-wheels.yml`) hasn't run yet.

## Palace accuracy (WP-501, partial)
- Root cause of 1.24 dB: the first-order ABC was too close (1 mm / 2.5 mm), costing about 0.25 dB. Padded to 5/6 mm it gives 0.949 dB vs BEM 0.845 dB.
- The remaining 0.10 dB is about 0.07 dB from Palace's one-sided finite-thickness copper impedance and 0.03 dB from the dielectric model (DS fixed at 5 GHz vs BEM constant 4.3). The reference isn't apples-to-apples; WP-510 owns the fix.
- No p3/AMR convergence and nothing above 10 GHz. The B-EM references under tests/benchmarks/references/em/ don't exist yet. Roughness isn't implemented. LossTan=0 hangs the wave-port mode solve.
- PERF: the converged run took 597 s and 7.8 GB on CPU. The p2 run on the full 228k-tet mesh exceeded 12 GB.

## Profiling (WP-508b)
- cm5 read 0.95 to 0.75 s; RefMap 10k 2.06 to 1.55 s (50k extrapolates to about 8 s vs a 3 s target); ERC 10k 20.2 to 14.5 s (target 3 s).
- CR WP-508b-make-valid-structure: 13x faster on the 44% hot spot, but it changes hashes. WP-509 decides after area/topology equivalence tests.
- The Jetson parse isn't profiled. docs/perf/PROFILE-508b.md and tools/bench/gen_synth_board.py were added.

## Compiled core part 2 (WP-509, partial)
- make_valid structure adopted (area-guarded; evidence in docs/perf/PROFILE-509.md). No `--update-golden` needed: no committed golden holds a content_hash of a fractured-fill board.
- cm5 read 0.75 to 0.23 s; ERC 10k 13.1 to 6.2 s (target 3 s); RefMap 10k about 1.3 s (50k extrapolates to about 5 s vs 3 s). Jetson not measured.
- [ ] Native clipping kernel for ERC cut intervals and ReferenceMap (CR WP-509-native-clipping).
- [ ] BEM assembly and profile-sampling kernels (B-IMP-01/02 gates) not started.

## GPU FDTD core (WP-505)
- Port |S21| drift above 3 GHz: fixed in WP-506 (semi-implicit port voltage, `cos(w dt / 2)` in `run.sparams`); abs(S21) - 1 below 4e-5 to 20 GHz. `tests/golden/fdtd/tem_line/case.toml` still carries the old comment (WP-505 path).
- WP-506 Part A gaps: no Dey-Mittra conformal PEC; static quasi-TEM mode ports with a local 2-D solver instead of `oa_impedance`; B-EM-03 (40 GHz differential via) not run; low-frequency S11 from the port S reaches -26 dB (B-EM-01 asks below -29). See `packages/oa-fdtd/README.md`.
- CPML is validated at normal incidence only.
- ROCm is untested on hardware.
- Measured: 6.0 Gcells/s f32 and 3.2 f64 on 256^3 with CPML (target >= 3).

## FDTD physics (WP-506A)
- S11 is -28.4 dB, just missing its target on B-EM-01.
- B-EM-03 (40 GHz differential via) wasn't run.
- Vias are staircased (no conformal PEC).
- The mode solver is local, not oa_impedance (CR WP-506-modal-solver).
- [x] Matrix-pencil tail extrapolation (WP-507) and `FDTD.UNSTABLE` (non-finite energy plus a configurable 20 dB late-time rise; WP-507 and WP-513 merged into one detector).
- PERF-01 PCB-like throughput isn't measured.
- Measured on B-EM-01: IL error 0.005 dB at 10 GHz and 0.038 dB at 20 GHz; runtime 1.6 s on GPU; 0.02 GiB VRAM.

## EMC decisions (user, 2026-10-29)
- Limit data: ship MIL-STD-461 and FCC Part 15 curves. For CISPR 32/25 and DO-160, ship citations and a template only; users enter the values.
- MIL-STD-461 scope, all in M2: RE102, CE102, RE101 and CE101.
- RE102 platforms: all of them (aircraft AF/Navy, ground Army/Navy/AF, ships and submarines, space).
- No user pre-compliance measurements and no commercial-tool licences for now. Calibrate only against published measured-vs-simulated cases, and keep the uncertainty bands conservative.
- 461G is verified from its PDF. 461H (17 Apr 2026) stays [verify] until the official text is obtained.
- [x] ARCHITECTURE.md lists EMI under M2 per ADR-0011 (section 6 table and diagram).

## CI (coordinator)
- [x] (FIXED 2026-10-30, run on 1cf846c: all 6 OS x Python combinations green; causes: wall-clock asserts, py3.13 HTTP 422 phrase in openapi, POSIX path in a WSL test, arm64 gmsh signature and divide warning) The CI `test` job had failed on every push, on all 6 OS × Python combinations, since at least 0cc834d. lint/types, schema, reuse, pip-licenses and the wp-508 wheel builds pass. Being reproduced in a clean clone with KiCad, openEMS and CuPy hidden. PROCESS FIX: the coordinator checks the CI result of every push, and a red `test` job blocks new merges.

## M1P integration round (coordinator, 2026-10-31)
- [ ] B-IMP-04 is still about 57 s CPU against a 20 s target (WP-516): it is raw BEM throughput, so it needs the GPU BEM path or a faster kernel. B-IMP-04 GPU (5 s) has not been measured.
- [ ] IM-002 gives no verdict for sections with a neighbouring signal (z0 is None for matrix order 2 or more). This is a rule-owner decision.
- [ ] cm5_minima ERC with its `.kicad_pro` net classes takes 88 s against 15 s without them (it took more than 5 min before WP-516). Profile the net-class path.
- [ ] B-EM-03 is converged against our own mesh study only (WP-517). It needs an independent reference: Palace (WP-501), or openEMS with real copper thickness (about 6 h per excitation).
- [ ] B-EM-03 SDD11 has not converged at 38-40 GHz (a null near -23 dB).
- [ ] The `test_fdtd_n_points[bem03]` GPU test takes 270 s against the 300 s pytest timeout. Give it its own timeout or shrink it.
- [ ] The `open-analysis` meta-package lives in `packaging/` because the root project shares the name. Rename the root project at release time (WP-303 final).
- [ ] Add `tests/e2e` to testpaths. It is not collected by CI yet; it takes about 2.3 min without slow tests.
- [ ] Linux has not been checked for the stdio fix (WP-518) beyond CI.
- [ ] On the Linux CI runner, the 50k-segment ReferenceMap took 196 s (build 11 s). Local 10k takes about 1.3 s, which extrapolates to about 5 s. Check whether oa_native is built and used on CI, or whether the fallback path has regressed. The benchmark's wall-clock assertion is now `perf`.
- [ ] Self-hosted nightly jobs (bench `nightly`, wp-503-gpu `cuda`) run only when the repository variables `OA_SELF_HOSTED_BENCH` / `OA_SELF_HOSTED_GPU` are `true`. There is no registered runner. A self-hosted runner on a public repo is a security decision for the owner.
