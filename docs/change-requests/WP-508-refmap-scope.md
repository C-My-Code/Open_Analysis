<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-508: ReferenceMap kernels need a split

## Problem
`oa_geometry.refmap` builds its spatial index and footprint coverage from shapely/GEOS
(`union_all`, `STRtree`, intersection of buffered footprints with plane copper). A native
version that is tolerance-exact needs polygon union and clipping in Rust (for example the
`geo` crate with its own robustness behaviour), which is a separate piece of work from the
parser and not a hot loop that can be lifted out on its own.

## Proposed change
Ship WP-508 with the parser (done). Move the ReferenceMap kernels to a follow-up WP-508b:
profile `_profile` / `_bulk_cover` on the 50k-track board first, then port the per-piece
coverage loop and keep GEOS for the union. Acceptance for B-ING-01 "RefMap 50k <= 3 s" stays
with that follow-up and the gate run.

## Impact
B-ING-01 runtime gate for ReferenceMap is not met by WP-508 as merged. The Jetson 88 MB parse
is ready for the gate run. The workflow file is `.github/workflows/wp-508-wheels.yml` (the
TASKS owned path), not `wp-508.yml`.
