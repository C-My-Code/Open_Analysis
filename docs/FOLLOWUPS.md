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
