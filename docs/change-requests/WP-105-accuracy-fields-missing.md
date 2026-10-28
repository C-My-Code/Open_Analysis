# WP-105: `Accuracy` / `ResultItem.accuracy` are specified but not implemented in oa_results

Raised by: WP-105 (impedance). Status: proposal, local adapter in place.

## Problem

ai-integration.md section 4.1 and the addendum A4 require `ResultItem.accuracy: Accuracy | None`
with `accuracy_class in {closed_form, 2d_field, ...}`, `tolerance_rel`, `tolerance_basis`,
`validity`, `f_min/f_max`, `converged`, `assumptions`. The merged `oa_results` has none of these.
It has `ResultContainer.accuracy_class` with a different literal set
(`reference | validated | screening | estimate | unverified`) and `validity_flags`.

## Local adapter (WP-105)

- `RLGCResultSet` / `ScalarSet` carry `accuracy_class = "screening"` (closed form) or
  `"validated"` (BEM).
- A `ValidityFlag(code="accuracy", ok=<validity == in_range>, message="accuracy_class=2d_field;
  method=...; validity=...; tolerance_basis=golden:impedance_bem ...")` holds the section 4.1
  vocabulary, so consumers can already parse it.
- The quasi-static `f_max` is a `ValidityFlag(code="quasi_static_fmax")` on the ImpedanceResult.

## Proposed change

Add `oa_results.accuracy.Accuracy` and `ResultItem.accuracy` exactly as in section 4.1 and map
the container `accuracy_class` onto it (or deprecate it). WP-105 will then set
`Accuracy(accuracy_class="closed_form"|"2d_field", method=model_name, tolerance_rel=..., ...)`
and drop the flag encoding. Impact: additive for oa_results; WP-105 `analysis.py` reduce().
