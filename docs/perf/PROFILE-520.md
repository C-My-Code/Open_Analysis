<!-- SPDX-FileCopyrightText: 2026 Open_Analysis contributors -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# WP-520 profile and results: two performance anomalies (Windows, CPU only, 2 threads)

Baseline: `main` at c658c8a. Machine shared with other agents, so single timings scatter by
about 15 %. Equivalence: base and new code run side by side (base sources on `sys.path` first).

## A. cm5_minima ERC with net classes (88 s versus 15 s)

**Finding 1, a stale compiled module.** The `.venv` of the main checkout has an `oa_native`
built before WP-509b/WP-515: it exports only `parse` and `kernel_version`, not
`cut_intervals`, `rect_cover_edge` or `bem_influence`. Every caller fell back to Python without
a word. `uv sync` never rebuilt it, because uv's cache key of a local package is its
`pyproject.toml` and its own directory, and the Rust sources live in `crates/oa-native`.
Reproduction in a fresh venv with `OA_NATIVE=0`: CLI ERC 93.8 s (the reported 88 s). With a
current build it is 49 to 58 s. The SARIF hash is the same either way.

**Finding 2, IM-002 is BEM bound, not quadratic.** Direct `run_rules`, current native build:

| | without `.kicad_pro` | with `.kicad_pro` |
|---|---|---|
| all rules | 10.6 s, 189 violations | 52 to 53 s, 256 violations |
| IM-002 | (no targets) | 41.7 to 42.6 s, 67 violations |

The four net classes give 18 differential pairs a Zdiff target. IM-002 keeps 734 sections with a
verdict, and 546 of them are distinct BEM solves (188 cache hits). All 734 are `general` or
coupled sections with 2 to 6 ground conductors (coplanar pours), so there is no closed form and
no screening bound (oa-impedance `screening`). A single-thread profile (72.5 s, 127 ms CPU per
section) splits as: dense LU 41 ms, native influence kernel 40 ms, `build_problem` (meshing,
`gap_distance`, `zone_at`) 25 ms, the rest of `_solve_eps`, `_vacuum` and `_loss` about 20 ms.
With the 4 service workers the run takes 42 s (1.7 times faster): the Python part of each
section holds the GIL. Net-class resolution and sampling take about 2 s, and there is no
quadratic step.

**CPU change.** IM-002 now collects nets until 256 sections need the BEM and solves them in one
`solve_many` call, instead of one call per net. Violations are identical, but the gain is small
(about 2 %) because the pool is GIL bound.

**What the 2x target needs.** 546 sections at 127 ms CPU cannot fit in 15 s on 2 to 4 CPU threads.
It needs one of the following, all outside the WP-520 paths:

- (a) a faster BEM per section: the LU and the influence kernel on the GPU, or an H-matrix
  path (as PROFILE-516 already says for B-IMP-04);
- (b) moving `build_problem` and `zone_at` into native code or vectorising them, so the threads
  scale;
- (c) calibrated screening bounds for coupled pairs with coplanar ground. This is an
  oa-impedance `screening` decision.

## B. ReferenceMap at 50k segments on Linux (196 s)

There were two separate causes.

1. **The 10k figure was for another board.** The "1.3 s at 10k" in the oa-geometry README and
   PROFILE-509 is the `tools/bench/gen_synth_board.py` board. The `test_perf.py` board
   (`tests/synthetic.py`) takes about 5 s at 10k, and on this machine its 50k run took 57 s,
   not the extrapolated 5 s.
2. **Three steps were quadratic.** At 50k (profile, native kernels):
   - `rect_cover_edge` took 22.7 s, compared with 0.9 s at 10k. For each piece it walked every
     ring of the plane part, and the plane has one antipad hole per via.
   - `topology._build` took 16 s, compared with 0.5 s at 10k, because it filtered
     `board.pads` and `board.vias` once per net.
   - The GEOS path (`OA_NATIVE_CLIP=0`) has the same problem in prepared `covers`: for each
     footprint it tests a point of every hole of the polygon (34 s).

Fixes, all exact (the ReferenceMap JSON hash is unchanged on the 11 WP-102 boards and on the
synthetic 2k and 10k boards, on both the native path and the GEOS path):

- `topology.items_by_net`: pads and vias of each net, cached, in board order.
- `RingSet.near`: the kernel gets only the rings whose bounding box meets the footprint box
  plus `cap` (plus 1 nm). A ring that misses the box has no edge the kernel would look at, and
  it crosses the even-odd ray from the centre an even number of times.
- `refmap._covers_part`: `covers(shell)` and no hole interior (`relate` `T********`) among the
  holes the STRtree finds near the footprint.

| 50k `reference_map()` | before | after |
|---|---|---|
| native kernels | 56.7 s | 19.8 s |
| GEOS (`OA_NATIVE_CLIP=0`) | 64.3 s | 19.1 s |
| 10k native / GEOS | 5.0 s / 5.0 s | 3.8 s / 3.9 s |

The GitHub runner was about 3.4 times slower than this machine on the old code (196 s against
57 s). The likely reasons are a slower vCPU, and possibly the GEOS path if the native build
was absent there. This could not be checked locally:

- The agent sandbox refused WSL, so the run could not be reproduced on Linux.
- No CI log was available (`gh` is not installed).

`ci.yml` runs `uv sync --all-packages`, and the ubuntu runner image ships Rust, so the
extension should be built in CI. Each new checkout gets new file times, so uv rebuilds it
there. `.github/workflows/wp-520.yml` prints `oa_native.status()` and fails on a fallback.
It also runs the 50k benchmark on ubuntu and Windows.

## Visibility of the fallback

- `oa_native.get()` logs one warning in `auto` mode when the module is missing or stale (it
  lacks a kernel of `oa_native.KERNELS`). `oa_geometry._native` logs when it falls back to GEOS.
- `oa_native.status()` returns a JSON-able dict for `oa doctor`, but the owner of `oa-service`
  still has to wire it in. Proposal: in `projectops.doctor_report`, add
  `rep["native"] = oa_native.status()` when `importlib.util.find_spec("oa_native")` finds the
  module, and `{"available": False, "fallback": True}` otherwise.
- `packages/oa-native/pyproject.toml` `[tool.uv] cache-keys` now include the crate sources.
  Touching `crates/oa-native/src/rect.rs` makes `uv sync` rebuild, which was checked. After this
  is merged, every checkout rebuilds once, because the `pyproject.toml` changed.

## Proposed CI change (integrator, `ci.yml`)

After `uv sync` in the `test` and `slow` jobs, add one step that fails when the native module
is missing or stale:

```yaml
- run: uv run --no-sync python -c "import sys, oa_native; s = oa_native.status(); print(s); sys.exit(1 if s['fallback'] else 0)"
```

## Equivalence

Exact violation JSON hashes, all rules, base against new: identical on the 11 WP-102 boards,
at their intent and at 40 ohm on every net, and on cm5_minima with and without the
`.kicad_pro`. The CLI SARIF hash on cm5_minima with the `.kicad_pro` is also identical:
base, new, and new with `OA_NATIVE=0`. Tests: `oa-geometry/tests/test_scaling.py`,
`oa-native/tests/test_status.py`, `oa-erc/tests/test_im002_batch.py`.
