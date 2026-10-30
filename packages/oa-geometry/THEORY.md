# oa-geometry: theory, error budget and validation

Derived board geometry: copper unions, spatial indexes, net topology, the `ReferenceMap` (return-path inference shared by ERC, EMI, SI and PI), cross-section extraction along nets, and 3D region cuts. Code: `packages/oa-geometry/src/oa_geometry/` (`refmap.py`, `sampling.py`, `region.py`, `copper.py`, `topology.py`). It computes geometry, not fields. See the [theory index](../../docs/theory/README.md).

This package is the bridge from a board file to every solver. Its errors do not appear as solver error: a wrong cross-section gives an exact answer to the wrong question. That is why this page matters even though there is no PDE here.

## 1. Problem statement

**Input.** A `Board` (stack-up with layer z-positions and thicknesses, copper tracks, arcs, zones/fills, pads, vias, outline, net kinds `signal | ground | power`). All lengths [m]. Curves are approximated with sagitta error at most `arc_tol` (default 1e-6 m); polygons are inscribed.

**Output.**

- Copper geometry per layer and net, voids, drills.
- `ReferenceMap`: for every routed segment, spans along its length with, for the layer above and below, the reference layer, reference net, `coverage` (0 to 1) and `edge_distance` [m]; and per-via transitions with return vias and stitching capacitors.
- `CrossSection` at a position `s` along a segment: conductors, planes and dielectric slabs in the (u, z) plane, ready for `oa_impedance`.
- `Structure3D` region cuts for EM solvers.

## 2. Governing equations and formulation

### 2.1 Copper and topology

Copper is the union of track outlines (with end caps and arc approximation), zone fills (actual filled polygons, so thermal reliefs and antipads are geometry) and pads/via annuli, as shapely geometry. Unions are **not snapped**: areas of disjoint primitives add exactly. The 1 nm grid is the comparison tolerance, and nodes closer than `CLUSTER_GRID = 1e-8` m are clustered in the net topology. Spatial queries use an STRtree per layer.

### 2.2 ReferenceMap: return-path inference

Definitions, from the `oa_geometry.refmap` docstring:

- **Reference copper** of a layer is the copper of zones and graphic shapes, plus all copper of nets whose kind is `ground` or `power`. Copper of the segment's own net never references it.
- For a segment piece and a plane layer $C$ above or below it, let $h$ be the vertical distance between the trace surface and the facing plane surface. At path position $s$ the **footprint** is the cut line perpendicular to the path of length $W+2kh$, with $k=$ `k_buffer` (default `3.0`). This is the same $3h$ that the return-current spread heuristic uses (see [oa-erc](../oa-erc/THEORY.md)).
- **Coverage** is the fraction of that line covered by the copper of one net; over a span it is the area-weighted average over the footprint strip.
- **Edge distance** is the distance from the trace body to the nearest boundary of that copper (0 if the trace crosses an edge), capped at $5h$ (`EDGE_FACTOR`).
- Per side, the **reference** is the nearest layer whose best net covers at least `MIN_COVERAGE = 0.25` of the footprint. Where no layer reaches that, the best partial cover (> 0) is reported; otherwise `None`.
- Spans split wherever the reference layer, net or coverage changes (coverage differences below `_COV_MERGE = 0.1` are merged).

Via transitions (`_via_transitions`) look for return vias of the destination reference net within `RETURN_VIA_RADIUS = 5 mm` (up to `RETURN_VIA_COUNT = 4`) and stitching capacitors between the two reference nets within `STITCH_CAP_RADIUS = 10 mm`. These are straight-line distances.

This is a geometric model of where a return current can flow, not a current solution. It says "a plane is present under this trace with this coverage", not "the current takes this path".

### 2.3 Cross-section extraction

`cross_section_at(seg, s)` cuts perpendicular to the segment at position $s$, with $u$ along the left normal of the travel direction and $z$ the board z. Contents:

- the signal conductor as a trapezoid from the drawn width and the layer's etch (`oa_physics.etch_widths`); for a differential pair the partner is included, ordered P then N;
- aggressors: same-layer copper of other signal or unknown nets inside the window, nearest first;
- coplanar ground: same-layer ground/power copper inside the window, clipped at the window; copper without a net becomes a floating conductor;
- reference planes from the `ReferenceMap` span at $s$ (above and below), truncated to the real copper extent along the cut (an open end means the copper continues beyond the window);
- dielectric slabs from the stack-up between the lowest and highest plane, with inner copper layers filled with the adjacent dielectric, plus solder mask on outer layers.

The **lateral window** half-width is, by default, $W/2+\max(5h_{max},3W)$ where $h_{max}$ is the largest distance to a reference plane. Copper beyond the window is ignored. The section is then canonicalised (1 nm grid; $u=0$ at the mid-point of the signal bounding box; $z=0$ at the lowest material boundary), so equal sections hash equally (`CrossSection.key()`).

`sample_net` repeats this along a net. Samples with the same geometric signature (layer, widths, reference layers and nets, copper intervals along the cut, partner offset) share one canonical `CrossSection` and differ only in `provenance`; `test_sampling.py` checks the bulk path against one `cross_section_at` per sample. Transitions are located by bisection to `refine_tol` (25 µm default in `oa_impedance.profile`).

### 2.4 Region cut

`cut_region` clips copper to a bounding box (`clip_copper_to_bbox`), keeps holes (antipads), builds dielectric slabs, and plated vias, and reports where traces cross the box boundary (these become port candidates).

## 3. Assumptions and validity range

- **Reference identification is by net kind and coverage threshold.** A power plane counts as reference; a plane with 25 % coverage counts. A user who treats a split power plane as a poor reference may disagree. The 0.25 and 5h values are conventions (`docs/research/07` E3: "any" gap; no source given for 0.25) [verify].
- **Straight, locally uniform cross-section.** The 2D section assumes the geometry is invariant along the segment locally. Near bends, T-junctions, via fields and tapers the real structure is 3D; the profile reports such places as events but does not correct for them.
- **Finite lateral window.** Copper farther than the window is ignored. For a wide coplanar environment the window may truncate real coupling; the open end is marked so the field solver treats the boundary as copper continuing.
- **Inscribed polygons** underestimate arc areas by up to the sagitta error; with `arc_tol = 1 µm` this is negligible for traces and visible only for sub-10 µm features.
- **Dielectric stack** is taken as drawn: no resin flow, no prepreg compression, no etched-edge fill. Inner copper layers are filled with the adjacent dielectric, an approximation of resin fill between traces.
- **Stripline gaps** in a plane reference are judged only when no side is referenced (ERC note).
- **Backdrill conventions** (depth and stub definition; KiCad drills the end layer) need confirming against the reader (FOLLOWUPS WP-104).
- **`physics_temp`** has its own $\rho(T)$ formula rather than `oa_physics` (FOLLOWUPS WP-104).

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| Builder-board cross-section dimensions (width, height over plane, thickness, coplanar gap, stripline h1/h2) | exact to 1 nm (tolerance `atol = 1e-9` m) | `tests/golden/geometry/xsection_*` |
| Split-plane slot position and coverage | 1e-5 m absolute (tolerance `atol = 1e-5`); coverage 1 elsewhere | `refmap_split_plane` |
| Region-cut counts (antipad holes, crossings) | exact | `cut_region_via_transition` |
| Arc approximation | at most `arc_tol` (1 µm default) sagitta | docstring |
| Window truncation, resin-fill and etch-profile assumptions | **not quantified** | no case |
| ReferenceMap on real, messy boards (fragmented fills, hatched planes, courtyard graphics) | **not quantified**; ingest is checked against counts and copper areas, not return-path correctness | FOLLOWUPS WP-101 |

## 5. Validation evidence

Five golden suites (`tests/golden/geometry/`), all built on constructed `oa_testkit` boards whose answers are written down by hand (class `analytic`): microstrip, stripline (symmetric and offset), CPWG, split plane, and the via-transition region cut. They verify the code on idealised, simple geometry. There is no test of the ReferenceMap against an independent method (for example a 3D current solve) and none on a real production board.

Related evidence upstream: the ingest suite (33 cases, all pass, `docs/validation/ingest.md`) checks counts exactly and copper area to 0.1 % on the synthetic matrix; KiCad IPC-2581 export loses zone outline, priority and hatch, has no backdrill diameter or stub length, and writes micro vias as blind (FOLLOWUPS WP-103). These feed this package.

**Known gaps and open issues.**

- **Performance**: ReferenceMap on 50 000 tracks took 51 s (target 3 s, B-ING-01); WP-509 reports about 1.3 s at 10 000 tracks, with 50 000 extrapolating to about 5 s. The package README still prints the older 5.4 s (10k) / 51 s (50k) table. A native clipping kernel is not done.
- The testkit builders lack the variants the acceptance text expects (backdrilled via, VDD via-array switch).
- ReferenceMap is not native (change request WP-508-refmap-scope).
- Cross-section and ReferenceMap results for the 10k/50k boards are not verified against an independent implementation, only for self-consistency (bulk path against single-section path).

## 6. References

- Reference-plane and return-path rules: T. H. Hubing et al., Clemson CVEL EMC'97 (see [oa-erc](../oa-erc/THEORY.md) references).
- The $3h$ return-current footprint: see the derivation note in the [oa-erc THEORY](../oa-erc/THEORY.md) section 3 (a back-of-envelope estimate, [verify]).
- GEOS/shapely (computational geometry library): https://shapely.readthedocs.io/ . No numerical claim on this page depends on it beyond exact predicate behaviour.
- Project: `docs/specs/interfaces.md` section 2; `docs/research/07-quasistatic-thermal-emi.md` section 3.1 (final paragraph: ReferenceMap proposal); change request `docs/change-requests/WP-104-undefined-types.md`.
