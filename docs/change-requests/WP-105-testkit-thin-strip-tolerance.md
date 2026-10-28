# WP-105: oa_testkit "zero-thickness" strips (t = 1e-4 ref, rtol 1e-2) are too loose for a field solver

Raised by: WP-105 (impedance). Status: proposal. oa_testkit is not modified.

## Problem

`oa_testkit.xsections` models the exact zero-thickness cases (Cohn stripline, edge-coupled
stripline, CPW) as strips of thickness `THIN = 1e-4 * ref` with `rtol = 1e-2`. The thickness
itself changes Z0 by up to about 1e-3, and 1 % hides real solver errors (the BEM here is at
0.03-0.25 % on the same cases).

## What WP-105 did instead

`packages/oa-impedance/tests/_xs.py` builds true thin strips (`t = 1e-6 * ref`) and the golden
suite `tests/golden/impedance_bem/` compares the BEM with the exact formulas at rtol <= 3e-3
(coax 1e-3). The BEM collapses such strips to zero-thickness ribbons internally.

## Proposal

Add to `oa_testkit.xsections` a `THIN_TRUE = 1e-6` option (or a second set of cases) and tighten
`rtol` of the analytic cases to 3e-3. Impact: testkit fixtures and any solver test using them.
