# WP-201: expected.yaml under-specifies IM-002 (and a few notes for the contract owners)

## expected.yaml vs the solver (WP-102 boards)

IM-002 on `combined` and `diff_pair` is listed only for `MS_250` and `MS_500`. With the net-class
targets (`50ohm`, `90ohm`, `100ohm`, parsed from the class name or `target_z_*`) and the 2D BEM
(`method="bem"`, accuracy class `validated`) the engine also reports:

| Net(s) | Layer | BEM | Target +-10 % |
|---|---|---|---|
| CPW_A, CPW_B (CPWG, S 0.3, G 0.15, h 0.2) | L1 | Z0 55.3 ohm | 45..55 |
| DP_P/DP_N | L1 | Zdiff 139 ohm | 90..110 |
| DP_P/DP_N | L3 (stripline) | Zdiff 69 ohm | 90..110 |
| USB_D+/USB_D- | L1 | Zdiff 126 ohm | 81..99 |

(The pair sections near the launches add shorter runs.) These are real solver results, not
rule bugs, so the rule is not tuned to hide them. The tests list them as `EXTRA_IM002` and assert
everything else equals `expected.yaml`. Either the board generator's dimensions or the expected
list should be corrected (WP-102 owner). The standalone `microstrip` boards have no net class, so
the 50 ohm target for IM-002 there must come from an intent (the tests pass one).

## Other contract notes

- `RunContext` (interfaces.md 3.3) has no `intent`, but rules need it. The erc analysis therefore
  ships the non-default entries of the resolved intent in each task payload.
- `oa_results` has no `Accuracy` model / `ResultItem.accuracy` (A4); the erc result uses
  `ViolationSet.accuracy_class="screening"` plus a `ValidityFlag(code="accuracy")`, as WP-105 does.
- Two `Waiver` models exist (`oa_model.setup.Waiver`, `oa_results.violations.Waiver`) with the
  same fields; the engine takes the former and converts to the latter for `Violation.waived`.
- docs/research/07 E5 quotes 7.5 mm (air) and 3.6 mm (eps_r 4.3) for lambda/20 at 1 GHz; the
  formula gives 15 mm and 7.2 mm. The implementation follows the formula.
- Rule `Config.nets` (a `NetSelector`) narrows `RuleContext.nets_in_scope()`; interfaces.md 3.5 does
  not say how the context learns the selector, so `ErcContext` takes it as a constructor argument.
