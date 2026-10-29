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

ReferenceMap kernels (spatial index, reference-plane queries) are not in this part. They rest on
shapely polygon clipping and need a Rust geometry engine; see
`docs/change-requests/WP-508-refmap-scope.md`.
