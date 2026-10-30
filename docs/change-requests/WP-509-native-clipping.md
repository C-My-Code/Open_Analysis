<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-509: native clipping kernel and owned-path notes

## Problem
After the Python-level work (docs/perf/PROFILE-509.md) the 10k ERC (6.2 s vs 3 s) and the
ReferenceMap (about 5 s at 50k vs 3 s) are bound by GEOS polygon clipping and buffering called
once per sample or piece. A Rust `geo`/`i_overlay`/`clipper2` port was not built: ERC and
ReferenceMap outputs are compared bit for bit, and these engines do not promise GEOS' behaviour
on boundary overlaps and vertex touches (closed-set intersection), so a port needs its own
equivalence suite and probably a tolerance contract.

## Proposed change
A follow-up WP: (1) a cut-interval kernel (segment against polygon rings with holes, closed-set
semantics, exact orientation predicates) for `oa_geometry.sampling._u_intervals`; (2) native
footprint strips and coverage for `refmap._bulk_cover`; (3) let ERC/ReferenceMap goldens compare
floats within 1e-12 relative instead of by hash.

## Impact
None on current contracts. The gates ERC 10k <= 3 s and RefMap 50k <= 3 s stay open.

## Paths touched outside the WP-509 owned list
The brief asked for ReferenceMap, reader and ERC work, which needed edits beyond TASKS.md:
- `packages/oa-geometry/src/oa_geometry/refmap.py` (memoized candidates, single-span fast path);
- `packages/oa-erc/src/oa_erc/rules/impedance.py` and `refpath.py` (key memo, adjacent-layer memo);
- `packages/oa-model/src/oa_model/validate.py` (optional `RingKernels` argument; default
  behaviour unchanged);
- `packages/oa-ingest-kicad/src/oa_ingest_kicad/{_geom,reader}.py` (make_valid, kernels);
- `crates/oa-native/src/geom.rs` (new module; the ring predicates fit none of erc/bem/sampling)
  and `packages/oa-native/tests/test_native_ring.py`.
