# ERC

[Back to the summary](index.md). Generated 2026-10-29T13:13:28+00:00 from commit `0cc834dc47ea` (worktree-agent-a556a5506fa1604ff) on Windows 11, Python 3.13.14; full run 456 s.

## erc

| Case | Source | Class | Indep. | Quantity | Expected | Got | Error | Tolerance | Status |
|---|---|---|---|---|---|---|---|---|---|
| `erc.combined`<br>`tests/boards/kicad10/combined`<br>ERC on WP-102 board 'combined' | tests/boards/kicad10/combined/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 15 | 15 | +0.0000 % | exact | pass |
| `erc.cpwg`<br>`tests/boards/kicad10/cpwg`<br>ERC on WP-102 board 'cpwg' | tests/boards/kicad10/cpwg/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 1 | 1 | +0.0000 % | exact | pass |
| `erc.diff_pair`<br>`tests/boards/kicad10/diff_pair`<br>ERC on WP-102 board 'diff_pair' | tests/boards/kicad10/diff_pair/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 3 | 3 | +0.0000 % | exact | pass |
| `erc.microstrip`<br>`tests/boards/kicad10/microstrip`<br>ERC on WP-102 board 'microstrip' | tests/boards/kicad10/microstrip/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 2 | 2 | +0.0000 % | exact | pass |
| `erc.microstrip_v9`<br>`tests/boards/kicad10/microstrip_v9`<br>ERC on WP-102 board 'microstrip_v9' | tests/boards/kicad10/microstrip_v9/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 2 | 2 | +0.0000 % | exact | pass |
| `erc.power_plane`<br>`tests/boards/kicad10/power_plane`<br>ERC on WP-102 board 'power_plane' | tests/boards/kicad10/power_plane/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 1 | 1 | +0.0000 % | exact | pass |
| `erc.power_strip`<br>`tests/boards/kicad10/power_strip`<br>ERC on WP-102 board 'power_strip' | tests/boards/kicad10/power_strip/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 0 | 0 | +0 | exact | pass |
| `erc.power_two_layer_vias`<br>`tests/boards/kicad10/power_two_layer_vias`<br>ERC on WP-102 board 'power_two_layer_vias' | tests/boards/kicad10/power_two_layer_vias/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 0 | 0 | +0 | exact | pass |
| `erc.split_plane`<br>`tests/boards/kicad10/split_plane`<br>ERC on WP-102 board 'split_plane' | tests/boards/kicad10/split_plane/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 6 | 6 | +0.0000 % | exact | pass |
| `erc.stripline`<br>`tests/boards/kicad10/stripline`<br>ERC on WP-102 board 'stripline' | tests/boards/kicad10/stripline/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 0 | 0 | +0 | exact | pass |
| `erc.via_transition`<br>`tests/boards/kicad10/via_transition`<br>ERC on WP-102 board 'via_transition' | tests/boards/kicad10/via_transition/expected.yaml (constructed by tools/boardgen, independent of the rule engine) | analytic | yes | expected violations matched | 2 | 2 | +0.0000 % | exact | pass |
| `erc.im002_geometry_vs_target`<br>`tests/boards/kicad10`<br>IM-002 hits on WP-102 nets whose geometry misses its own impedance target | docs/change-requests/WP-201-expected-impedance.md; docs/FOLLOWUPS.md (ERC/EMI) | analytic | yes | unlisted IM-002 violations | 0 | 14 | +14 | exact | known gap |
