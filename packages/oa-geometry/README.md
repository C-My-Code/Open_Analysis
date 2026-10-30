# oa-geometry

Derived board geometry (WP-104): copper unions, spatial indexes, net topology, the `ReferenceMap`
(return-path description shared by ERC/EMI/SI/PI), cross-sections along nets, 3D region cuts.

Contracts implemented: `docs/specs/interfaces.md` section 2 (see
`docs/change-requests/WP-104-undefined-types.md` for the types the spec leaves undefined).
Golden suite: `tests/golden/geometry/`.

```python
from oa_geometry import BoardGeometry, RegionSpec

geo = BoardGeometry.build(board, arc_tol=1e-6)  # cached per Board instance
geo.copper("L1", "SIG")  # MultiPolygon; net=None -> all nets
geo.copper_by_net("L2")
geo.voids("L2", "GND")
geo.outline()
geo.drills()
geo.strtree("L1").query(geom)  # tuple[ItemRef, ...]
geo.topology("SIG")
geo.islands("GND")
geo.via_stub(via, ["L1"])
rm = geo.reference_map()  # spans per segment (coverage, edge distance), via transitions
(seg,) = geo.segments("SIG")  # RoutedSegment, oriented away from the root
xs = geo.cross_section_at(seg, 10e-3)  # canonical CrossSection
samples = geo.sample_net("SIG")  # XsSample with events
cut = geo.cut_region(RegionSpec(nets=("SIG",), bbox=(...)))  # Structure3D + boundary crossings
geo.resolve_terminal(PadTerminal(component="U1", pin="1"))
```

Errors are `GeometryError` subclasses (`code`, `message`, `path`, `hint`).

## Cross-section sampling (WP-509)

`sample_net` cuts all samples of a segment in bulk: poses, cut lines, STRtree queries and copper
intervals are shapely array calls. Samples whose geometric signature (layer, widths, reference
layers and nets, copper intervals along the cut, partner offset) is equal share one canonical
`CrossSection` build and differ only in `provenance`, so IM-002 hashes each shared section once.
`tests/test_sampling.py` checks the bulk path against one `cross_section_at` per sample.

## Conventions

- Metres, board frame. Curves have sagitta error <= `arc_tol`; polygons are inscribed. Unions are
  not snapped (areas of disjoint primitives add exactly); the 1 nm grid is the comparison
  tolerance and the `CrossSection` canonicalization grid.
- Segment ids: `<track>` / `<track>#k` (multi-segment track) / `<id>.j` (cut at a T-junction).
- Cross-section `u` runs along the left normal of the travel direction. Aggressors are appended
  as further `signal` conductors; the differential partner (from `board.diff_pairs`) is always
  included, ordered P, N.
- Reference definition and thresholds: docstring of `oa_geometry.refmap`.

## Performance (synthetic 4-layer boards, `tests/synthetic.py`, Windows laptop)

Before WP-509 (`reference_map()` in pure Python):

| tracks | build + copper unions | `reference_map()` |
|---|---|---|
| 10k | 1.4 s | 5.4 s |
| 50k | 9.2 s | 51 s |

Native clipping (WP-509b, optional `oa-native`; `OA_NATIVE_CLIP=0` forces GEOS): the cut-interval
clipping of `sampling._u_intervals` and the footprint coverage and edge distance of
`refmap._bulk_cover` run in Rust for straight pieces. Intervals and coverage match GEOS; the
`edge_distance` floats differ from GEOS by up to about 3e-15 relative (different but equivalent
arithmetic), so ReferenceMap JSON hashes of boards with covered footprints change while the
structure, nets, layers and coverage stay identical. Compare with a relative tolerance of 1e-12.

After WP-509 (memoized candidate layers and a single-span fast path, `docs/perf/PROFILE-509.md`):
`reference_map()` on the 10k board takes about 1.3 s (including a one-off 0.4 s scipy import); 50k
extrapolated to about 5 s against the 3 s target. WP-509b then added the native clipping kernel
described above (10k: 1.01 s → about 0.6 s; 50k extrapolates to about 3.3 s, not yet measured).

## Typing

`mypy --strict` is clean. The shapely-heavy modules disable a few error codes at the top of the
file because shapely's array-vs-scalar overloads are not expressible in the stubs.

## Theory and validation

Equations, numerical method, validity range, error budget and validation evidence: [THEORY.md](THEORY.md).
