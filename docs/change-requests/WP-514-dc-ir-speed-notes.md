<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# WP-514: DC IR end-to-end speed (notes and open items)

No frozen contract is edited. `MeshConfig` gains two optional fields (`h_far`, `mesh_workers`) with
defaults that keep every existing result bit-identical; interfaces.md section 6 lists `mesh` only
loosely, so this is flagged for the integrator.

## Profile, power_plane board at 1e6 unknowns (uniform mesh, `h_plane` 52.8 um), seconds

| Stage | Before (1 worker) | After uniform (auto 2 workers) | After `h_far` 1 mm |
|---|---|---|---|
| Geometry prep + network build | 0.003 | 0.003 | 0.003 |
| gmsh meshing (2 sheets, includes Python conversion) | 41.6 to 50 | 20.8 | 1.1 |
| Assembly (vectorised COO) | 0.7 to 1.0 | 0.7 | 0.03 |
| Solve (GPU cuDSS / CPU SuperLU) | 9.5 to 14.7 / 11.0 | 4.7 / 9.0 | 0.75 / 0.10 |
| Total solve_fem | 52 to 66 (GPU) / 60.6 (CPU) | 26.4 (GPU) / 31.6 (CPU) | 1.9 (GPU) / 1.3 (CPU) |

Meshing is `gmsh.model.mesh.generate` (about 90 % of the mesh stage); assembly and post-processing
were already vectorised, so no native assembly was needed.

## Equivalence

`h_far = 1 mm` on `h_plane = 0.1 mm`: load drop 14.636 mV against 14.655 mV uniform (0.13 %),
load return 9.272 against 9.287 mV (0.16 %); total rail plus return drop 0.14 % apart. At
`h_plane = 52.8 um` the load voltage differs by 0.002 % from the uniform 1e6 mesh.

## Open items

- A uniform 1e6 mesh is still 26 s (GPU) because the board has only two sheets, so per-sheet
  parallelism caps at 2x. Intra-sheet domain splitting was not attempted.
- Raster fast path not added: the WP-204 raster reference is limited to simple rectangular
  planes and cannot take arbitrary board copper without a rasteriser; not selectable here.
- Mesh cache by content hash not added (no repeat meshes in the benchmarks).
- `h_far` is opt-in, not the default, to keep goldens and existing results unchanged.
- The B-DC-02 runners now use `h_far = 1 mm` at the `h_plane` of the 1e6 (and 3e5) uniform
  mesh, and calibrate on 0.2/0.1 mm meshes (the old 0.8/0.4 mm calibration extrapolated badly:
  it produced 6.5e5 unknowns for a 3e5 target).
