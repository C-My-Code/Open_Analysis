# WP-109: interfaces.md section 9 details to pin down

Status: proposal. Contract file: `docs/specs/interfaces.md` section 9 (frozen). Implemented locally; nothing in the spec was edited.

## 1. Problem

Section 9 leaves four things open that WP-202, WP-204 and WP-206 will depend on.

1. **`XsMesh` and `SheetPiece`/`SizeField`/`contacts` are only sketched in comments.** The signature `mesh_copper_sheets(...) -> tuple[TriMesh2D, ...]` cannot carry the `contacts` map ("mapping contact_id -> node indices returned in .contacts").
2. **The determinism option name is wrong for gmsh 4.15.** The spec says `General.RandomSeed`; gmsh 4.15.2 rejects it (`Could not set option 'General.RandomSeed'`). The option is `Mesh.RandomSeed`.
3. **No error type is named** for meshing failures; ai-integration.md section 6 requires `ErrorInfo{code, message, path, hint}`.
4. **"Not re-entrant, workers only"** is stated, but threads are a realistic caller (tests, `run_inline`).

## 2. What was implemented (local adapter)

- `XsMesh` (pydantic `OAModel`): `order`, `nodes (N,2)`, `triangles (M,3|6)` (P2 order `v0 v1 v2 m01 m12 m20`, gmsh/VTK ordering), `region (M,)` (dielectric index >= 0, `-1` air, `-2 - conductor_index` for optional conductor interiors), `conductor_edges` (one `EdgeSet` per conductor in `CrossSection.conductors` order; edges are `(K,2)` or `(K,3) [n0, n1, mid]`), `outer_edges`, `groups` (physical-group table: name, dim, tag, kind, source id), `quality`, `gmsh_version`, `box`, `warnings`.
- `SheetMesh(TriMesh2D)`: a `TriMesh2D` subclass, so the declared return type `tuple[TriMesh2D, ...]` still holds. Adds `piece_id`, `net`, `contacts: tuple[ContactNodes]` (`id`, ordered ring node indices, radial error), `groups`, `quality`, `cleaning`, `gmsh_version`.
- `SheetPiece(id, layer, net, z, polygons: tuple[oa_model.Polygon], contacts: tuple[Contact])`, `Contact(id, x, y, radius, segments, mode)`, `SizeField(kind, size, grading, ...)`.
- `mesh_copper_sheets(pieces, *, h_default, size_fields=(), clean=None, grading=1.3)`: extra keyword arguments only.
- `mesh_cross_section(xs, *, h_max, order, corner_refine, box_factor, grading=1.3, mesh_conductors=False, snap, circle_segments)`: extra keyword arguments only.
- `Mesh.RandomSeed = 1`, single thread, `Mesh.Algorithm = 6`, `Mesh.Algorithm3D = 10`.
- `gmsh_session()` is serialized by a process-wide lock (threads are safe, not parallel) and raises `MeshError(MESH_SESSION)` on re-entry or when other code already initialized gmsh.

## 3. Proposed spec change

Replace the section 9 comments with the types above (field names as implemented in `packages/oa-mesh/src/oa_mesh/types.py`), change `General.RandomSeed` to `Mesh.RandomSeed`, and state that threads are serialized.

## 4. Impact

WP-202 reads `XsMesh` directly. WP-204 reads `SheetMesh.contacts` and `SheetMesh.nodes/triangles`. WP-206 only needs `gmsh_session()` and the `MeshError` convention; `oa_mesh/occ3d/` is untouched.
