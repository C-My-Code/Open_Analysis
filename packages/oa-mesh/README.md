# oa-mesh

gmsh meshing for Open_Analysis. WP-109 (2D) is implemented; `src/oa_mesh/occ3d/` is reserved for WP-206.

Layer L2 (interfaces.md section 0). Contract: interfaces.md section 9 plus the details in
`docs/change-requests/WP-109-interface-details.md`.

On Linux the PyPI `gmsh` wheel needs a few system libraries it does not bundle (missing ones
show up as `OSError: libGLU.so.1: cannot open shared object file` on first use). On
Debian/Ubuntu: `sudo apt-get install libglu1-mesa libxcursor1 libxft2 libxinerama1`. The
Windows and macOS wheels are self-contained.

## Public API

```python
from oa_mesh import (
    mesh_cross_section,
    mesh_copper_sheets,  # the two meshers
    XsMesh,
    SheetMesh,
    SheetPiece,
    Contact,
    SizeField,
    CleanOptions,  # types
    MeshError,
    to_meshio,
    write_vtu,
)
```

### `mesh_cross_section(xs, *, h_max, order=2, corner_refine=0.1, box_factor=20, grading=1.3, mesh_conductors=False) -> XsMesh`

P1/P2 triangle mesh of a `CrossSection` in its own (u, z) frame.

- Conductors are holes with tagged boundaries (`XsMesh.conductor_edges`, in conductor order);
  `mesh_conductors=True` also meshes their interiors (eddy-current solves).
- Zero-thickness planes are curves that split the domain (or are embedded when they end inside it).
- Regions: dielectric index, `-1` air. Dielectric priority resolves overlaps (higher wins).
- Outer truncation box padded by `box_factor * Lref / 2`; regions that cannot be reached from a
  signal conductor without crossing a conductor (outside a coax shield, beyond an infinite
  ground plane) are dropped and reported in `warnings`.
- Refinement: graded boundary discretization with corner sizes `min(corner_refine * h_max,
  shortest incident edge)`, so a 20 nm thick trace keeps angles >= 25 degrees.
- Physical groups: `dielectric:<i>`, `air`, `conductor:<id>`, `conductor_interior:<id>`, `outer`.

### `mesh_copper_sheets(pieces, *, h_default, size_fields=(), clean=None, grading=1.3, h_far=None, workers=None, algorithm=None, threads=1) -> tuple[SheetMesh, ...]`

`SheetMesh` is a `TriMesh2D` (nodes, triangles, layer, z) plus `piece_id`, `net`, `contacts`
(contact id -> node indices, ring order), `groups`, `quality` and the `cleaning` report.

- Input polygons may be dirty: bow-ties, duplicate vertices, overlaps, holes touching or crossing
  the outline, hairline spurs, sub-micron slits. They are repaired with shapely (`CleanOptions`:
  snap grid, `min_feature` opening/closing, optional simplify) before gmsh sees them.
- Contacts are n-gon rings (vertices on the circle, `segments` or auto from local size, >= 16)
  or points. `mode="embedded"` keeps the disc meshed; `mode="hole"` removes it. Each ring edge is
  one mesh element, so the ring nodes are the ring vertices (radial error at rounding level).
- `SizeField` kinds: `contacts`, `points`, `polyline` (trace centre line, size = width/3),
  `boundary`. Fields use grading 1.2 to 1.3 up to `h_default`. Short boundary edges (antipads)
  also get graded fields automatically, and narrow copper (traces) is capped at 1/3 of its
  inscribed-circle diameter.
- Errors are `MeshError` with `code`, `message`, `path`, `hint` (`to_dict()` matches `ErrorInfo`).

WP-211 options (all opt-in; defaults reproduce the WP-109 meshes exactly):

- `workers=N` (or env `OA_MESH_WORKERS`): one gmsh process per piece through `oa_mesh._pool`, which
  launches `python -I -m oa_mesh._worker` and speaks pickle over pipes. It never re-imports
  `__main__`, so scripts need no `if __name__ == "__main__"` guard (unlike `multiprocessing`
  spawn). Each piece is meshed in a fresh gmsh session in every mode, so `content_hash` is identical
  for any worker count. Use at most 4 workers on the dev machine.
- `h_far`: interior coarsening. Size stays `h_default` within 3 x `h_default` of boundaries and
  contacts, then grows by `grading` to `h_far`. Pays off on sparse planes (power_plane: -18 %
  triangles); dense boards are dominated by feature refinement (8-layer synthetic: -0.01 %).
- `algorithm`: `frontal_delaunay` (default), `delaunay`, `meshadapt`. gmsh 2D packing crashes with
  embedded curves and is not offered.
- `threads`: gmsh's own threads (`General.NumThreads`); opt-in, not bit-reproducible. 2D meshing is
  single-threaded in gmsh, so it gave no speed-up (plate: 8.9 s at 2 threads, 10.7 s at 4).

### 2D algorithm benchmark (gmsh 4.15.2, Windows, 1 thread)

| case | algorithm | time [s] | triangles | min angle [deg] | max aspect |
|---|---|---|---|---|---|
| 100x80 mm, 600 holes + 300 rings, h=2 mm | frontal_delaunay | 14.5 | 415935 | 25.5 | 2.23 |
| | delaunay | 14.0 | 427931 | 28.1 | 2.06 |
| | meshadapt | 85.2 | 407441 | 22.0 | 2.33 |
| 100x100 mm plate, h=0.2 mm | frontal_delaunay | 9.3 | 578280 | 40.9 | 1.23 |
| | delaunay | 8.3 | 659516 | 30.6 | 1.87 |
| | meshadapt | 38.0 | 623358 | 26.2 | 1.84 |

Delaunay is at most 11 % faster and clearly worse on the plate, so Frontal-Delaunay stays the
default. Reproduce with `benchmarks/bench_wp211.py algos [plate=1]`.

### Before/after (benchmarks/bench_wp211.py)

| case | before | after |
|---|---|---|
| 8-layer synthetic (1.47 M triangles), 1 / 2 / 4 workers | 47.0 s | 39.5 / 22.2 / 13.2 s (peak 0.3 / 0.6 / 1.0 GB), hashes identical |
| power_plane via pi.dc (3 pieces, 72 k triangles) | mesh 1.29 s | 1.28 s; `h_far=4mm`: 59 k triangles |
| pi.dc 3e5-unknown plate | total 10.5 s (mesh 8.8, solve 1.4) | unchanged (single piece, uniform h) |
| 3D solid copper, via region, medium | 38.4 s, min SICN 0.0167 | 22.1 s, min SICN 0.333 |
| 3D sheet copper | 11.3 s, min SICN 0.336 | unchanged |

### Export

`to_meshio(mesh)` and `write_vtu(mesh, path)` write VTU for ParaView (regions, conductor/contact
node tags).

## gmsh session rules (`oa_mesh._session`)

gmsh keeps one global state per process.

- gmsh is imported lazily; `import oa_mesh` never loads or initializes it.
- Each meshing call initializes and finalizes gmsh inside `gmsh_session()`.
- A process-wide lock serializes threads (safe, no speed-up). For parallelism use worker
  processes, preferably started with `spawn`.
- The session is not re-entrant, and refuses to run if other code already initialized gmsh
  (`MeshError` code `MESH_SESSION`).
- Determinism: one thread, `Mesh.RandomSeed=1`, Frontal-Delaunay (`Mesh.Algorithm=6`). Same input
  gives the same mesh for a fixed gmsh version; `content_hash()` is the cache-friendly digest of
  coordinates (rounded to 1e-12 x size) and connectivity. Do not mix gmsh versions in one cache.

## 3D notes (WP-211)

HXT (`Mesh.Algorithm3D=10`) is the default; `MeshSettings.expert["gmsh.algorithm3d"]=1` selects
Delaunay (slightly faster, same quality), `gmsh.optimize="Netgen"` runs Netgen after meshing (did
not help: min SICN 0.012 and 64 s), `gmsh.num_threads` is opt-in. The 0.017 min-SICN tets were the
35 um air/resin layers that fill copper layers: their lateral size followed lambda/N. They are now
capped at `gmsh.copper_layer_aspect` (default 4) x layer thickness. `mesh_problem` already defaults
to sheet copper whenever copper is thinner than half the thinnest dielectric, which stays the
recommended Palace input; solid copper is now also well conditioned.

## Tests

`pytest packages/oa-mesh` (the 100 x 80 mm / 900-feature case is marked `slow`).
Golden suite `tests/golden/mesh2d/` holds regression signatures (counts + hash, self-generated, not
independent references; skipped when the gmsh version differs from the recorded one).
