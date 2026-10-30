<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-508b profile (cProfile, Windows, 1 process; heavy cases not run)

Boards: `cm5_minima`, synthetic 10k tracks (`tools/bench/gen_synth_board.py --tracks 10000`) for
the ReferenceMap, and `erc_synthetic.many_tracks_board(1000, 10)` with IM-002 on every net for ERC.

## Reader (cm5_minima, 0.95 s)
1. `shapely.make_valid` in `fix_fractured` (10 fractured zone fills, 0.51 s, 44 %): GEOS-bound.
2. `validate_board` / `_check_polygon` (0.28 s): O(n^2) numpy self-intersection test per ring.
3. `union_all` of zone fills (0.07 s); S-expression parse is 0.01 s (native).

## ReferenceMap (synthetic 10k, 2.06 s = 0.2 ms/piece)
1. `_profile` called for 14k (piece, layer) pairs although most have no foreign copper under the
   footprint (GND via pads make a near layer a candidate): per-piece `_rects`, STRtree query,
   `shapely.intersection`.
2. `_bulk_cover` only covered the nearest candidate; a miss there sent every piece through
   `_profile` for the next layer even when it is one big plane.
3. Per-piece Python in `_side_intervals` / `_candidates` (60k calls).

## ERC 10k (IM-002 everywhere, 20.2 s)
1. `CrossSection.key()` called 3x per sample in `IM002._runs` (91k calls): `canonicalize`
   (`model_copy` x ~12) + `canon` recursion (8.2 M calls, 8 s self time).
2. `cross_section_at` canonicalised twice per sample (symmetry probe + result).
3. `shapely.intersection` in `_intervals` (174k) and `buffer` (78k) in crosstalk/refpath.

## Changes and results (same machine)
| Item | Before | After |
|---|---|---|
| cm5 read | 0.95 s | 0.75 s |
| ReferenceMap, synthetic 10k | 2.06 s | 1.55 s |
| ERC 10k, IM-002 everywhere | 20.2 s | 14.5 s |

- `fix_fractured_many`: zone fills `make_valid` on 2 threads (GIL released); results identical.
- `_bulk_empty` + `_bulk_cover` for every candidate layer, footprints built with the same
  arithmetic as `_profile`.
- `IM-002`: key once per sample; `cross_section_at`: one canonicalize; `canon`: exact-type fast path.

Equivalence (checked against the pre-change commit): `content_hash` of cm5 and of every WP-102
board, `ReferenceMap` JSON hash and the full violation set of all WP-102 boards plus the synthetic
board are identical. Package tests (geometry, ingest-kicad, erc, model) pass.

## Not done / next
- `make_valid(method="structure")` is 13x faster on cm5 but changes ring start vertices (hash
  change): see `docs/change-requests/WP-508b-make-valid-structure.md`.
- ERC 10k is still 14.5 s vs 3 s: remaining cost is `CrossSection` construction/`key()` per sample
  (pydantic) and per-sample shapely intersections; needs a native sampler or section dedup before
  construction. ReferenceMap at 50k extrapolates to about 8 s on the synthetic board; remaining
  cost is per-piece Python in `_compute_spans`. Jetson parse not profiled (heavy token).
- No Rust kernels were added: no hot spot was tokenizer-to-model bound.
