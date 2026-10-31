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
- `_native.cut_intervals(lines, geom_of, xy, ring_start, geom_start)` and
  `_native.rect_cover_edge(lines, r_foot, r_body, cap, geom_of, xy, ring_start, geom_start)`
  (WP-509b): closed-set clipping of straight segments against (multi)polygons with holes, and
  coverage of a footprint rectangle plus the capped distance of the body rectangle to the part
  boundary. They replace the GEOS overlay in `oa_geometry.sampling._u_intervals` and
  `oa_geometry.refmap._bulk_cover` (`oa_geometry._native` hands them over; `OA_NATIVE_CLIP=0`
  forces the GEOS oracle). Tests: `tests/test_native_clip.py`.

- `oa_native.status() -> dict` (WP-520, for `oa doctor`): `mode`, `available`, `path`,
  `kernel_version`, `stale` and `missing` (kernels of `oa_native.KERNELS` the module lacks) and
  `fallback` (some work runs on the Python oracles although `OA_NATIVE` does not ask for it).
  Never raises.
- In `auto` mode `get()` logs one warning (logger `oa_native`) when the module is missing or
  stale. The fallbacks are correct but slow: without the module, ERC on `cm5_minima` with its
  net classes takes about 94 s instead of 49 s (`docs/perf/PROFILE-520.md`).

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

`uv sync` builds it as an editable workspace member. The crate lives outside this package
directory, so `[tool.uv] cache-keys` lists `crates/oa-native/Cargo.toml`, `Cargo.lock` and
`src/**/*.rs`: without them uv reused the cached build after Rust changes and the module went
stale silently (WP-520). To force a rebuild: `uv sync --reinstall-package oa-native`.

## Golden suites

None of its own; the equivalence tests above are the golden check.

## Not yet native

Per-sample Python in ERC and the ReferenceMap (pydantic section construction, edge and crosstalk
buffers and distances, span assembly), and BEM assembly. Library evaluation for WP-509b:
`i_overlay`, `geo` and a clipper2 port are general polygon overlays that snap coordinates and do
not promise GEOS' closed-set results on boundary overlaps. The hot operations are segment and
rectangle versus polygon-with-holes, so the kernels are purpose-built with f64 predicates
(`docs/change-requests/WP-509b-clipping-kernel.md`).
