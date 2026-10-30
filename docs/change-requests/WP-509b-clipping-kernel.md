<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-509b: native clipping kernels, canonical float output, owned-path notes

## Problem
WP-509 left the 10k ERC (6.2 s vs 3 s) and the 50k ReferenceMap (about 5 s vs 3 s) bound by GEOS
clipping (`docs/change-requests/WP-509-native-clipping.md`).

## Decision: purpose-built kernels, not a general overlay
`i_overlay`, `geo`/`geo-clipper` and a clipper2 port are general polygon overlays. They snap
coordinates to an integer grid and do not promise GEOS' closed-set results on boundary overlaps
and vertex touches. The hot operations here are not general overlays: a straight segment against a
polygon with holes (ERC cut intervals) and an oriented rectangle against a polygon with holes
(ReferenceMap footprint coverage and edge distance). Both are built in `oa-native` with f64
orientation tests and a half-open crossing rule (`crates/oa-native/src/clip.rs`, `rect.rs`);
the GEOS code stays as oracle and fallback (`OA_NATIVE_CLIP=0`, or no compiled module).
Library timings were not benchmarked: the purpose-built kernels need no polygon construction at
all, which is where GEOS spends its time.

## Contract change: canonical floats (coordinator decision)
- `sampling._u_intervals` rounds cut-interval ends to 1 fm in both paths.
- `RefInfo.edge_distance` is rounded to 1 fm and `RefInfo.coverage` to 12 significant digits by
  field validators, in both paths.
With this the native and GEOS ReferenceMap JSON are byte-identical on the synthetic 10k board.
Residual risk: a value within about 1e-18 of a rounding boundary can still straddle it (about
0.3 % of arbitrary values, none of the values seen on the test boards).
No committed golden contains these fields, so no golden was regenerated.

## Impact
Public models are unchanged except for the quantisation of two fields. ERC violation sets are
identical on the WP-102 boards and demos compared (see the PR notes); numeric values can differ
at 1e-12 relative where the impedance solver amplifies a last-bit input difference.

## Paths touched outside the WP-509b owned list
- `crates/oa-native/src/{clip,rect,lib}.rs`, `packages/oa-native/tests/test_native_clip.py`;
- `packages/oa-geometry/src/oa_geometry/{_native,sampling,refmap,models}.py`;
- `packages/oa-erc/src/oa_erc/rules/crosstalk.py` (bounding-box query instead of a round buffer);
- `tools/bench/gen_synth_board.py` (`--mix`: known ERC violation mix).

## Remaining gap
ERC 10k is bound by per-sample Python and pydantic work (`_sections`, crosstalk and edge
buffers/distances, `net_runs`), not by clipping. The next step is batching cross-section
construction on validated data and native crosstalk/edge distance kernels.
