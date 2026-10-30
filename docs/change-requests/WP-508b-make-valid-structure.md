<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-508b: allow `make_valid(method="structure")` for fractured zone fills

## Problem
`fix_fractured` uses GEOS `make_valid` (linework), 44 % of the cm5_minima read and likely the
largest share of the Jetson parse. `method="structure"` takes 0.04 s instead of 0.53 s on cm5
with equal areas (to 1e-15) and polygon counts, but ring vertex order differs, so
`content_hash` changes.

## Proposed change
Accept `structure` and regenerate the affected content hashes/goldens in one reviewed step (or
normalise ring start vertex in `from_shapely`, which also changes hashes).

## Impact
All KiCad boards with filled zones get a new `content_hash`; golden regeneration needed. Not
applied in WP-508b because the equivalence rule forbids hash changes.
