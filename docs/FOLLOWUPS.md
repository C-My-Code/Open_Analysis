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
