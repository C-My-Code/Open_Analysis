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

### `mesh_copper_sheets(pieces, *, h_default, size_fields=(), clean=None, grading=1.3) -> tuple[SheetMesh, ...]`

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

## Tests

`pytest packages/oa-mesh` (the 100 x 80 mm / 900-feature case is marked `slow`).
Golden suite `tests/golden/mesh2d/` holds regression signatures (counts + hash, self-generated, not
independent references; skipped when the gmsh version differs from the recorded one).
