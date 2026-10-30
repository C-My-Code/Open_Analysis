# oa-mesh: theory, error budget and validation

Meshing for the field solvers: gmsh-based 2D triangle meshes (cross-sections for the FEM, copper sheets for DC IR), a gmsh/OpenCASCADE 3D tetrahedral mesher (for Palace), and a pure-NumPy graded rectilinear mesher (for FDTD). Code: `packages/oa-mesh/src/oa_mesh/` (`xsection.py`, `sheets.py`, `_quality.py`, `_clean.py`, `occ3d/`, `rect/`). See the [theory index](../../docs/theory/README.md).

A mesh does not compute a physical quantity. Its role in the chain of trust is to bound the **discretisation error** of the solver that uses it, and to make runs repeatable. Mesh-quality numbers below are therefore indicators, not accuracy statements; the accuracy statements are in the solver pages.

## 1. Problem statement

| Mesher | Input | Output |
|---|---|---|
| `mesh_cross_section` | `CrossSection` (conductor shapes, dielectric regions, planes), `h_max` [m], order 1 or 2, `corner_refine`, `box_factor`, `grading` | P1/P2 triangle mesh in the (u, z) plane, conductors as holes with tagged boundaries, regions tagged, outer boundary tagged |
| `mesh_copper_sheets` | polygons per (layer, net) with contacts (rings, points), size fields, `h_default` [m] | triangle meshes (`SheetMesh`) with contact node sets, quality report, cleaning report |
| `oa_mesh.occ3d.mesh_problem` | `Structure3D` / `EMProblem` | tetrahedral `.msh` (gmsh 2.2 format for Palace), physical groups, quality (min SICN, min gamma), GLB preview |
| `oa_mesh.rect` | axis intervals, layers, copper faces, $f_{max}$, $\varepsilon_{r,max}$, quality preset | grid lines per axis [m], mesh statistics |

## 2. Governing equations and formulation

### 2.1 2D cross-section mesher (`xsection.py`)

1. All shapes become shapely geometry in scaled working units (so gmsh tolerances stay far below the thinnest feature), snapped to a grid. Conductors, dielectric boundaries and the outer box are noded together and polygonised into a planar subdivision (no OCC booleans).
2. Faces get a region (dielectric index, air `-1`, conductor). Faces that cannot be reached from a signal conductor without crossing a conductor (outside a coax shield, beyond an infinite ground plane) are dropped.
3. Every subdivision edge is discretised in Python with a graded size function: corner points get size $\min(\text{corner\_refine}\cdot h_{max},\ \text{shortest incident edge})$ (default `corner_refine = 0.1`), growing with ratio `grading = 1.3` (default). Each piece is a single-element gmsh line, so the boundary mesh is exactly the graded one; the interior is meshed by Frontal-Delaunay.
4. The outer truncation box is padded by `box_factor * L_ref / 2` (default `box_factor = 20`). Zero-thickness planes are curves that split the domain, or embedded curves if they end inside it.

This design follows the field behaviour: near a conductor corner the potential has an $r^{\pi/\alpha}$ singularity, so charge density diverges and uniform meshes converge slowly. Grading toward corners restores the convergence rate.

### 2.2 Copper sheet mesher (`sheets.py`)

Input polygons are repaired with shapely before gmsh sees them (`CleanOptions`: snap grid, `min_feature` opening/closing, optional `simplify`). Contacts are n-gon rings (at least 16 vertices, or auto from local size) or points. `mode="embedded"` keeps the disc meshed; `mode="hole"` removes it. Each ring edge is one mesh element, so the ring nodes are the ring vertices (radial error at rounding level; the polygon is inscribed in the drill circle). `SizeField` kinds: `contacts`, `points`, `polyline` (trace centre line, size = width/3), `boundary`; fields grow with grading 1.2 to 1.3 up to `h_default`. Narrow copper is capped at 1/3 of its inscribed-circle diameter, so a trace always has at least three elements across it.

### 2.3 3D mesher (`occ3d/`)

Copper is **not meshed as volume** by default: solid bodies are carved out after `BooleanFragments` and their faces become conductor surfaces; layers thinner than `sheet_threshold` (half the thinnest slab by default) become zero-thickness sheets. Via barrels are tubes or solid cylinders. Geometry is snapped to 1 nm and healed (slivers and slits below 1 µm removed). HXT (`Mesh.Algorithm3D = 10`) is the default 3D algorithm. Thin resin/air layers filling copper layers have their lateral size capped at `copper_layer_aspect = 4` times the layer thickness (before WP-211 their size followed $\lambda/N$ and produced sliver tetrahedra).

### 2.4 Rectilinear mesher for FDTD (`rect/`)

Rules (ADR-0010 section 2; `_build.py`, `_axis.py`):

- Maximum cell $d_{max}=\lambda_{min}/N$ in the densest dielectric: $d_{max}=\dfrac{c_0}{f_{max}\sqrt{\varepsilon_{r,max}}\,N}$, with $N=15,20,30$ for `coarse`, `medium`, `fine` (`QUALITY_N`).
- Thirds rule at metal edges: the metal edge lies 1/3 of a cell inside and 2/3 outside the nearest line pair (`third_rule_lines`), which reduces the staircase edge error for field singularities at metal edges.
- At least `min_cells` cells across every dielectric layer, a line on every copper face, snapping to feature positions.
- Grading: local target size $\min\big(d_{max},\ \min_i[\text{seed}_i+(g-1)|x-x_i|]\big)$, so cells grow by at most the grading $g$ per cell away from fine regions; lines are spaced evenly in $\int dx/\text{target}$. Across fixed lines (material interfaces, metal edges) the ratio can reach about 1.5; the achieved maximum is reported in the statistics.
- `estimate_timestep`: a Courant helper, $0.99/\big(c\sqrt{\Delta x^{-2}+\Delta y^{-2}+\Delta z^{-2}}\big)$. The FDTD solver itself uses `Grid.dt` with 0.95 ([oa-fdtd](../oa-fdtd/THEORY.md)).

### 2.5 Quality metrics (`_quality.py`, `occ3d/mesher.py`)

For each triangle with edge lengths $a,b,c$ and area $A$: angles from the law of cosines; inradius $r=A/s$ with $s=(a+b+c)/2$; circumradius $R=abc/4A$; **aspect** $=R/(2r)$ (1 = equilateral). `MeshQuality` reports min and max angle, max aspect, min and max edge, total area. For tetrahedra: **minSICN** (minimum signed inverse condition number, 1 = ideal) and **gamma** (inscribed to circumscribed radius ratio) from gmsh `getElementQualities`.

### 2.6 Determinism

gmsh holds one global state per process. Each call runs in `gmsh_session()` (initialise, mesh, finalise), serialised by a process lock; `import oa_mesh` never loads gmsh. Settings: one thread, `Mesh.RandomSeed = 1`, Frontal-Delaunay (`Mesh.Algorithm = 6`). The same input gives the same mesh for a fixed gmsh version. `content_hash()` digests coordinates (rounded to $10^{-12}\times$ size) and connectivity. With `workers=N` each piece is meshed in a fresh gmsh process, and the hash is identical for every worker count (checked on an 8-layer synthetic board, 1/2/4 workers). The `threads` option is opt-in and not reproducible.

## 3. Assumptions and validity range

- **Determinism is per gmsh version.** Do not mix gmsh versions in one cache. Cross-platform identity is not established: the mesh2d golden signatures were recorded on Windows and skip on a gmsh version mismatch; they have not been checked on Linux CI (FOLLOWUPS WP-109).
- **Cleaning changes geometry.** The default `simplify` is 0 (not 1 to 5 µm as first planned). Gaps below 1 µm close and are reported. The 1 nm snap and the 1 µm sliver removal are choices that can change very fine features.
- **Cross-section dropping.** Regions not reachable from a signal conductor are dropped and reported in `warnings`; if the user expects them in the solution, they are not there.
- **Truncation box.** 20 reference lengths is a choice; free-space CPW converges slowly with it (see [oa-impedance](../oa-impedance/THEORY.md)).
- **Mesh size versus physics.** The FDTD $\lambda/N$ rule bounds numerical dispersion, not skin-effect resolution, which is handled in the solver by SIBC. The 2D FEM rebuilds meshes with `h_max/sqrt(2)` globally; no adaptive error estimation exists.
- **3D copper as sheet or surface.** Valid when the copper is thin compared with the dielectric thickness (default threshold half the thinnest slab). Solid copper is available and now well conditioned (min SICN 0.333 after WP-211, 0.0167 before), but 3D solid copper meshing is slow.
- **Gmsh 2D packing algorithms** crash with embedded curves and are not offered.
- **OpenEMS adapter uses its own preset** (`coarse/medium/fine` = 10/20/30 cells per wavelength in `oa_em_openems/scene.py`), whereas `oa_mesh.rect` uses 15/20/30. The `rect` docstring says its rules "are those of `oa_em_openems.mesh`"; the coarse preset differs.

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| 2D FEM refinement stopping criterion | energy change < `rtol = 1e-3`, up to 3 refinement levels | `oa_impedance.fem` |
| Ring contacts in DC IR | < 0.5 % per 2x refinement; worst drop moves 0.15 % over 8x | `oa-pi` README |
| Triangle quality (frontal_delaunay) | min angle 25.5 deg, max aspect 2.23 on a dense 100x80 mm, 600-hole board (415 935 triangles); min angle 40.9 deg on a 100x100 mm plate (578 280 triangles) | `oa-mesh/README.md` benchmark (gmsh 4.15.2, Windows, 1 thread) |
| Alternative algorithms | `delaunay`: 28.1 deg / 2.06 and 30.6 deg / 1.87; `meshadapt`: 22.0 deg / 2.33 and 26.2 deg / 1.84 | same table |
| 3D tetrahedra, solid copper via region | min SICN 0.333 after WP-211, 0.0167 before; sheet copper 0.336 | `oa-mesh/README.md` |
| 3D tetrahedra for Palace accuracy | "poor tets will hurt Palace accuracy" (FOLLOWUPS WP-206); effect not isolated or measured | FOLLOWUPS |
| FDTD cell-size rules | $\lambda_{min}/N$ with N = 15/20/30 bound the Yee dispersion to $O((kh)^2)$; the achieved effect on S is in the FDTD page | [oa-fdtd](../oa-fdtd/THEORY.md) |

Input error for a mesh is the CAD/ingest geometry (position, width, thickness); see [oa-geometry](../oa-geometry/THEORY.md).

## 5. Validation evidence

- **Determinism / regression**: `tests/golden/mesh2d/` holds counts and hashes of recorded meshes. These are **self-generated regression signatures, not independent references**, and are skipped when the gmsh version differs from the recorded one.
- **Quality**: benchmark runs in the package README (table above), reproducible with `benchmarks/bench_wp211.py`. The 8-layer synthetic board (1.47 M triangles) meshes in 47.0 s single process and 39.5 / 22.2 / 13.2 s with 1 / 2 / 4 workers, hashes identical.
- **Downstream correctness** is the real test: the cross-section meshes underpin the FEM, which agrees with exact solutions to 0.001 to 0.11 % and with the BEM to 0.43 % ([oa-impedance](../oa-impedance/THEORY.md)); the sheet meshes underpin DC IR, which agrees with analytic cases to 0.05 % ([oa-pi](../oa-pi/THEORY.md)).

**Known gaps and open issues.**

- Quality thresholds are reported, not enforced: no mesh is rejected for poor quality; the solver relies on the generator's behaviour.
- Linux CI has not confirmed the mesh2d signatures (FOLLOWUPS WP-109).
- The Palace 3D mesh quality versus accuracy link is unmeasured. Palace accuracy work (WP-501/510) found other, larger terms first ([oa-em-palace](../oa-em-palace/THEORY.md)).
- 3D mesh time: the performance test takes 42 s (solid) and 61 s (both variants) under load; the via region about 15 s standalone (FOLLOWUPS WP-206). Interior sheet BCs are untested in Palace (WP-301).
- `oa_mesh.occ3d` wave ports are coplanar face sets with extra parameters (change request WP-206-port-surfaces-and-signatures); `ViaBody.pads` are ignored.
- The package README introduction says "WP-109 (2D) is implemented; `occ3d/` is reserved for WP-206" although `occ3d` and `rect` are implemented and documented further down; the introduction is stale.

## 6. References

- C. Geuzaine, J.-F. Remacle, "Gmsh: a three-dimensional finite element mesh generator with built-in pre- and post-processing facilities", *Int. J. Numer. Methods Eng.* 79(11), 2009, pp. 1309-1331.
- Frontal-Delaunay: S. Rebay, "Efficient unstructured mesh generation by means of Delaunay triangulation and Bowyer-Watson algorithm", *J. Comput. Phys.* 106(1), 1993 [verify].
- Corner singularities and graded meshes: standard finite element theory (any text on the $r^{\pi/\alpha}$ corner singularity); no specific citation is used by the code.
- A. Taflove, S. C. Hagness, *Computational Electrodynamics*, 3rd ed., 2005 (cell-size and dispersion rules). The 1/3-2/3 rule at metal edges is taken from openEMS practice (https://docs.openems.de) per the code comments [verify wording there].
- Project: `docs/adr/0005-meshing-gmsh.md`, `docs/adr/0010-gpu-fdtd-engine.md` section 2, `docs/specs/interfaces.md` section 9, `docs/change-requests/WP-109-interface-details.md`.
