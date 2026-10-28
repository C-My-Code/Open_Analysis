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
