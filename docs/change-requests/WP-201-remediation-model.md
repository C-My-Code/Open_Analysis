# WP-201: `oa_results.violations.Remediation` does not match ai-integration section 5

## Problem

ai-integration.md section 5 and addendum A7 define `RemediationAction(kind, description, targets,
parameter, current_value, suggested_value, unit, applies_via, confidence)` and
`Remediation(summary, actions, evidence)`, with `Violation.fix_hint == remediation.summary`.
The merged `oa_results.violations` has `RemediationAction(item, field, value, note)` and
`Remediation(hint, actions)`: no `kind`, no `applies_via`, no `evidence`. WP-201's acceptance
(typed action, `applies_via` separating `board_edit` from `dru_rule`/`netclass`, impedance
`accuracy_class` in `evidence`) cannot be expressed in it. `Violation` has no validator that fills
`fix_hint` from the remediation either.

## Local adapter (implemented)

`oa_erc.remediation.TypedRemediation` is spec-shaped. `to_model()` encodes it into the merged
model: `hint = summary`; one `RemediationAction` per typed action with `field = kind`,
`note = description`, `value` = the whole typed action as a JSON object; `evidence` becomes a last
action with `field = "evidence"`. `from_model()` reverses it. Rules also set `fix_hint = summary`.

## Proposed change

Replace the two classes in `oa_results.violations` by the section 5 shapes (additive on
`Violation`; `Remediation.hint` kept as a property alias of `summary` for one release), and add the
`fix_hint` fill-in validator. `oa_erc` then drops `to_model`/`from_model`.

## Impact

`oa_results.io.sarif` reads `remediation.hint` (rename). Web viewer reads `remediation`. No other
package populates it yet.
