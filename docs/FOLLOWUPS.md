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
