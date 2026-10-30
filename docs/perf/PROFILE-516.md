<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-516 profile and results: impedance screening (Windows, CPU only: OA_DEVICE=cpu, 2 threads)

Board: `tools/bench/gen_synth_board.py --tracks 1500 --mix` (60 nets, 144 violations), 50 ohm +-10 %
on `N*`; the 11 WP-102 boards; synthetic 10k; cm5_minima. Baseline: the code of `main` at 707be6e
(WP-515 merged).

## Findings
- IM-002 solved 383 distinct sections by BEM on the mix board. **357 of them hold a neighbouring
  signal conductor.** The BEM result of such a section has an RLGC matrix of order 2 or more, so
  `params.z0` is `None` (Zdiff exists for order 2 only). IM-002 never compares a `None`, so the
  BEM answer for those 357 sections was thrown away: 29 s of the 35 s of BEM.
- The 26 sections with a verdict all sit within 8 h of a plane edge (the reference plane of the mix
  board is split under the trace), where the closed forms are off by up to 3.4 % (calibration:
  plane edge at 1 h: 8.6 %). They have no screening bound and stay BEM. On this board the
  screening step decides nothing; the saving comes from not solving sections without a verdict.
- The frequency path of `solve_bem` already is "one static solve plus the analytic loss model"
  for frequency-independent materials (`n_solves == 1`, 7 ms of 130 ms per section for 50
  frequencies). The B-IMP-04 proxy time is the static per-section cost (meshing 20 ms, influence
  18 ms, LU, modal). Dispersive materials need up to six anchors; there is no exact
  one-solve form (THEORY.md section 2.7).

## Changes
- `oa_impedance.screening`: calibrated relative error bound of the static closed forms against the
  BEM, per family and regime; `closed_form_result` adds a `z_rel_bound` validity flag. No bound
  for CPW, thick coupled pairs, plane edges nearer than 8 h, extra conductors, skewed trapezoids,
  non-static frequencies.
- IM-002: skip sections without a verdict (exact); closed-form screening with the bound, BEM only
  where the band overlaps the error interval; the worst member of every failing run is made
  exact, so the output equals the all-BEM output (`exact_values=True`). Config `screen`,
  `exact_values`. Evidence keeps `accuracy_class`.

## Results (CPU, 2 threads, same machine, other agents running)
| Item | Before | After | Target |
|---|---|---|---|
| ERC mix board, all rules (144 violations) | 46.8 s | 1.2 s | 5 s |
| IM-002 alone on the mix board | about 35 s (383 BEM sections) | 0.6 to 1.0 s (26 BEM sections) | |
| Sections sent to BEM, mix board | 383 of 383 | 26 of 383 (6.8 %); 0 saved by the closed form | |
| Sections sent to BEM, width sweep, tol 30 % / 40 % (`erc_synthetic.width_sweep_board`) | 26 | 10 / 4 | |
| 383 mix sections x 50 f (B-IMP-04 proxy), `solve_many`, 4 BLAS threads | 47 s at 8 threads (WP-515) | 57 s at 4 threads, unchanged code path | 20 s |

The B-IMP-04 proxy is a throughput test of the BEM itself (every section, every frequency); no
decision depends on it, so screening cannot shorten it and it is not met by this WP. Merging
near-identical sections by quantised geometry was not implemented: after the verdict skip the mix
board has 26 sections left, all near a plane edge, where a quantised result cannot replace the
section without moving the reported impedance (the equivalence requirement), and the 383 proxy
sections differ in neighbour widths well above any quantum that stays far below the BEM error.

## Equivalence
- Baseline run of `main` against the new code, violation sets (floats at 9 significant digits)
  hashed with the WP-515 `equiv.py`: identical on the 11 WP-102 boards (default intent and 40 ohm
  on every net), mix, synthetic 10k and cm5_minima (all rules).
- `packages/oa-erc/tests/test_im002_screening.py` compares the new path with the all-BEM oracle
  (`screen=False` and the verdict skip switched off) byte for byte: the 11 WP-102 boards at four
  targets, a 30-width sweep at five target/tolerance pairs, and (slow) the mix board.
  `_matrix_order` is checked against the BEM result on every section of a mix board.
- `packages/oa-impedance/tests/test_screening.py` re-checks the bounds on a hold-out grid (errors
  at most 0.75 of the bound).

Package tests (impedance, erc): 273 passed, 36 skipped (perf, slow, GPU); with `OA_RUN_SLOW=1` the
two slow tests of `test_im002_screening.py` pass too.

## Open items
- A section with a neighbouring signal gets no IM-002 verdict at all (this is the behaviour of
  `main`, kept). Whether IM-002 should judge such sections (for example by the first conductor's
  Zc diagonal) is a decision for the rule owner.
- B-IMP-04 needs a faster BEM per section (GPU, or an H-matrix path), not screening.
