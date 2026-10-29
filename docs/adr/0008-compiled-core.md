# ADR-0008: Compiled core (hybrid Rust + mypyc), after M1

- Status: Accepted (2026-10-28)
- Deciders: project owner

## Context
- The heavy solvers (numpy/scipy, gmsh, openEMS, Palace) are already compiled.
- Measured pure-Python hot spots: the KiCad parse takes 9.9 s on an 88 MB board, and ReferenceMap takes 51 s at 50k tracks.

## Decision
Hybrid compilation:
1. Hot paths move to one Rust crate, `oa-native` (PyO3, built with maturin), shipped as prebuilt wheels for Windows, Linux and macOS: the KiCad S-expression parser, geometry and ReferenceMap, BEM matrix assembly, raster/FVM solvers, and the ERC and profile-sampling loops. Each keeps a pure-Python reference implementation that serves as the test oracle.
2. The remaining typed Python packages are compiled with mypyc where it is measurably faster.
3. End-user distribution is a native app bundle (Nuitka or PyInstaller; decided at the time).

## Timing
Work starts after the M1 features are complete and is driven by WP-304 benchmarks. The user chose this. The Rust toolchain is not yet installed.

Amendment (2026-10-29): the project owner placed milestone M1P between the M1 features and the M1 release. This ADR's work therefore runs inside M1P, as WP-508 (crate, parser, ReferenceMap) and WP-509 (ERC loops, BEM assembly, profile sampling), gated by [BENCHMARKS.md](../BENCHMARKS.md) B-ING-01 and B-ERC-01. Raster/FVM solvers go to the GPU backend instead (ADR-0009, WP-504). mypyc and the native app bundle stay after M1P.

## Consequences
- CI builds the wheels for all three OSes.
- Golden tests compare the native path against the Python reference.
- The import-linter layering is unchanged.
- Tracked as WP-414 in docs/TASKS.md.
