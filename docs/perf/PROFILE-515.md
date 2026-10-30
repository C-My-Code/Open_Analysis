<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-515 profile and results (Windows, CPU only: OA_DEVICE=cpu; GPU timings deferred)

## Findings
- The "remediation" cost on the `--mix` board is IM-002: 60 nets, 383 distinct BEM sections
  (neighbour widths differ, so almost nothing is shared), each a ~1000-panel dense solve.
  Violation building itself is negligible. The cost is per-section BEM: influence assembly
  (numpy, ~140 ms), LU, meshing (`build_problem`, ~21 ms, GIL-bound).
- scipy LU does not run in parallel across Python threads (4 workers = 1 worker), so
  `solve_many` threads give 1.2-1.5x only; BLAS threads inside one solve do help.
- ERC 10k: per-segment `_sections` calls (10k calls, ~3 samples each), per-pair shapely calls in
  XT-001, per-segment distances in ED-002, pydantic copies/validation, networkx import and graph.

## Changes
- `oa_geometry.sampling`: one batched `_sections_multi` per net (one copper query per layer);
  `_fastcopy.replace`, `model_construct` for computed models; kernel lookup cached.
- XT-001 and ED-002 vectorised (same GEOS calls on arrays, same order); topology uses an own
  Dijkstra instead of networkx and bbox queries instead of buffers; per-layer ref cache in `net_runs`.
- `oa-native` `bem_influence` (threaded Rust, same f64 expressions; `OA_NATIVE_BEM=0` = numpy
  oracle) for the BEM P/D matrices.
- BEM batches: BLAS limit set once, device-pool release once per batch, small GPU-auto batches
  stay on the host. `OA_IMPEDANCE_BLAS_THREADS` (default 1) sets BLAS threads per solve.

## Results (CPU)
| Item | Before | After | Target |
|---|---|---|---|
| ERC 10k, cold process | 4.5 s | 3.0 s | 3 s |
| ERC mix board (144 violations) | 74 s | 40 s | 5 s |
| 383 mix sections x 50 f (B-IMP-04 proxy) | 80 s (about 210 s per 1000) | 47 s with `OA_IMPEDANCE_BLAS_THREADS=8` (about 120 s per 1000) | 20 s |
| B-IMP-04 GPU | deferred | deferred | 5 s |

## Equivalence
Violation sets (floats at 9 significant digits) identical to the pre-change code on the 11 WP-102
boards (default and 40 ohm intents), mix, synthetic 10k, cm5_minima (all rules except IM-002).
Package tests pass (impedance, geometry, erc, native, model, service). GPU tests not run.
