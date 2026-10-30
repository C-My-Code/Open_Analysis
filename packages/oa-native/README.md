<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# oa-native

Optional compiled core (ADR-0008, WP-508): Rust crate `crates/oa-native`, PyO3 bindings, abi3
wheels built by maturin. Nothing depends on it: callers fall back to Python.

## Public API

- `oa_native.get()` returns the compiled `oa_native._native` module or `None`.
- `OA_NATIVE=0|1|auto` (default `auto`): `0` forces Python, `1` makes a missing module an
  `ImportError`, `auto` uses it when installed.
- `_native.parse(text) -> list | None`: the KiCad S-expression tree of
  `oa_ingest_kicad.sexpr.parse_py`, bit-exact (xy lists become `(N, 2)` float64 arrays). It
  returns `None` for input the oracle rejects, and `sexpr.parse` then re-parses in Python so
  the error is the oracle's.
- `_native.ring_self_intersects(ring) -> bool` and `_native.all_points_inside(ring, pts) -> bool`
  (WP-509): the polygon-ring predicates of `oa_model.validate`, same f64 expressions as the
  numpy oracle (`tests/test_native_ring.py`). `oa_ingest_kicad` hands them to `validate_board`
  through `oa_model.validate.RingKernels`.

## Contracts

The pure-Python implementation is the oracle for every kernel; the native result must be
identical (`tests/test_native_sexpr.py`: token trees on every WP-102 board and the KiCad demo
boards, `content_hash()` equal on every WP-102 board). Without the wheel, everything runs on
Python (`tests/test_fallback.py`).

## Build

```text
cd packages/oa-native
maturin develop --release --uv      # needs the Rust stable toolchain (MSVC on Windows)
```

## Golden suites

None of its own; the equivalence tests above are the golden check.

## Not yet native

ReferenceMap kernels (spatial index, reference-plane queries), the ERC cut-interval clipping and
BEM assembly are still Python/GEOS/numpy. WP-509 measured them (`docs/perf/PROFILE-509.md`): the
ReferenceMap and the cut intervals rest on GEOS polygon clipping and need a Rust clipping engine
with GEOS-identical closed-set semantics; see `docs/change-requests/WP-509-native-clipping.md`.
