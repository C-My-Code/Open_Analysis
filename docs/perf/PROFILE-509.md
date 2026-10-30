<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-509 profile and results (Windows, 1 process, CARGO_BUILD_JOBS=4; 50k and Jetson not run)

Boards: `cm5_minima`, synthetic 10k tracks (`tools/bench/gen_synth_board.py --tracks 10000`) for
the ReferenceMap, `erc_synthetic.many_tracks_board(1000, 10)` for ERC (all rules, IM-002 on every
net, `BoardGeometry.build` included).

## Decision: make_valid(method="structure")
**Switched**, behind an area guard. Evidence (`packages/oa-ingest-kicad/tests/test_make_valid_equivalence.py`
and an ad-hoc run over 13 demos, the 11 WP-102 boards and the 10k synthetic board):

| Check | Result |
|---|---|
| Zone-fill point set (symmetric difference per zone and layer) | exactly 0 on all boards (limit 1 nm^2) |
| Polygon count, hole count (islands, holes) | identical on all boards |
| Fill area per layer/net | differs by up to 4e-17 m^2 (about 40 nm^2): shoelace rounding from the changed start vertex, not geometry |
| Connectivity (`islands()` item sets per net) | identical on all boards |
| `content_hash()` | changes on boards with fractured fills (ring start vertex) |

One synthetic ring (two holes joined by overlapping collinear bridges) loses its holes under
"structure" alone. `fix_fractured` therefore accepts the structure result only when its area
equals the ring's shoelace area, and falls back to `linework` otherwise. No fallback occurs on
any WP-102 board or demo. No committed golden contains a `content_hash` of such a board, so no
`--update-golden` regeneration was needed (nothing to list). cm5 repair: 0.53 s -> 0.04 s.

## Results (same machine)
| Item | Before WP-509 | After | Target |
|---|---|---|---|
| cm5 read | 0.75 s | 0.23 s | - |
| ReferenceMap, synthetic 10k (incl. about 0.4 s one-off scipy import) | 1.55 s | about 1.3 s | 50k <= 3 s (extrapolates to about 5 s) |
| ERC 10k, all rules, IM-002 everywhere | 13.1 s (measured the same way) | 6.2 s | <= 3 s |
| Jetson 88 MB read | 9.9 s (before WP-508b) | not measured (heavy token) | <= 2.5 s |

## What changed
- Reader: `make_valid` structure (cm5 repair 0.53 -> 0.04 s); `validate_board` was 44 % of the
  remaining read (numpy O(n^2) ring test): native `ring_self_intersects` / `all_points_inside`
  in `oa-native` with the same f64 expressions (identical booleans, tested against the oracle).
- ERC: `sample_net` computes poses, cut lines, STRtree queries and intervals per segment in bulk;
  samples with an identical signature share one canonical `CrossSection` (only provenance
  differs); IM-002 hashes shared sections once; own/partner nets are not clipped; adjacent
  reference layers are memoized. The Python canonicalization (`model_copy` x 12 per sample) is
  gone from the hot path for repeated signatures.
- ReferenceMap: memoized candidate layers; single-span fast path when the nearest candidate
  fully covers the footprint (the same RefInfo the general path builds).

## Equivalence
- ReferenceMap JSON hash and the full ERC violation set (hash of the dumped violations): equal
  on the 11 WP-102 boards (default intent, and a 40 ohm target on every net so IM-002 reports),
  cm5 / pic_programmer / interf_u (ReferenceMap), and 100-net synthetic boards.
- `sample_net` sections: equal to 1 fm and `key()` equal on diff_pair, combined, pic_programmer,
  interf_u and cm5 (15 nets each). A few coordinates differ in the last bit: the cut-interval
  projection is a vectorised dot product, the old code used a BLAS gemv with fused multiply-add.
- Package tests (geometry, erc, ingest-kicad, native, model, impedance) pass: 546 passed.

## Remaining profile (why the targets are not met)
- ERC 10k (6.2 s): cut-vs-copper `shapely.intersection` (1.6 s, GEOS overlay, about 20 us per
  pair), refpath `net_runs` (2.7 s cum., includes the ReferenceMap and the topology build),
  edge rules (1.9 s cum., 49k buffers), crosstalk (1.4 s). All general Python/GEOS; none is a
  tight numeric loop. A native clipping kernel must reproduce GEOS' closed-set semantics
  (boundary overlaps, vertex touches) exactly to keep ERC output identical; see
  `docs/change-requests/WP-509-native-clipping.md`.
- ReferenceMap (1.3 s per 10k): footprint `buffer` 0.3 s, `_bulk_cover` 0.5 s, `all_pieces`
  / T-junction cuts 0.3 s (`segs.py`), per-piece span assembly 0.3 s. A hash-identical
  speed-up of the footprint geometry needs the same Rust clipping kernel.
- BEM assembly (`bem/kernels.py`) and the profile-sampling kernels were not touched: neither is
  in the 10k-ERC or ReferenceMap profile.
