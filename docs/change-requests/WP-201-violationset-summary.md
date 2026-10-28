# WP-201: `ViolationSet.summary()` cannot list the worst 5 within 2 KiB

## Problem

WP-201 acceptance: the `ViolationSet` summary of `combined` lists counts by rule and the worst 5
violations within 2 KiB. `ViolationSet._stats` puts `Violation.summary()` (about 450 bytes each:
digest, location, nets, layers, items, fingerprint, remediation text) into `top`, and
`ResultContainer.summary()` halves the list cap until it fits, so `summary(max_chars=2048,
max_items=5)` on `combined` returns 2 entries.

## Local adapter (implemented)

`oa_erc.summary.compact_summary(vset, top=5, max_bytes=2048)` returns counts, `by_rule` and the
five worst unwaived violations (rule, severity, nets, layers, location in mm, measured, limit,
unit, 70-character fix, fingerprint); it shortens hints and digits before dropping entries.

## Proposed change

Make `ViolationSet._stats` use that compact per-violation form for `top` (full records stay
available through `list_violations`/`get_result`).

## Impact

`oa_results` only; MCP `result_summary` output gets smaller.
