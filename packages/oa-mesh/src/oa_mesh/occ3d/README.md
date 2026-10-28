<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# oa_mesh.occ3d (WP-206)

3D OCC mesher for `Structure3D` / `EMProblem`. `import oa_mesh.occ3d` never loads gmsh.

```python
from oa_mesh.occ3d import build_occ_model, write_msh, export_glb, mesh_problem, OccOptions

model = build_occ_model(
    structure,
    sheet_threshold=50e-6,
    ports=problem.ports,
    options=OccOptions(f_max=10e9),
    boundaries=problem.boundaries,
)
model.report.groups  # physical groups: name, dim, tag, kind, model_id, measure
stats = write_msh(model, Path("out.msh"), sizes=problem.mesh)  # also out.groups.json
export_glb(model, Path("out.glb"))  # surfaces for the 3D preview
```

`mesh_problem(problem, path)` wires an `EMProblem` end to end (sheet threshold defaults to half the
thinnest slab).

## Model

* Copper is **not meshed as volume**: solid bodies are carved out after `BooleanFragments` and
  their faces become conductor surfaces (PEC / surface-impedance walls). Layers thinner than
  `sheet_threshold` become zero-thickness sheets on the dielectric face, with the lower slab
  absorbing the copper layer (no thin volume).
* Resin between traces on an inner solid layer is a `fill` of the lower slab's material.
* Via barrels are tubes (`plating < r_outer`, hollow interior keeps the dielectric) or solid
  cylinders (filled). `ViaBody.pads` are ignored: pads are already in the `ConductorBody`
  polygons. `backdrill_clearance` turns the span outside the plated range into an air void.
* Everything is snapped to 1 nm; polygons are healed with `oa_mesh._clean` (slivers and slits
  below 1 um are removed). The model is built in mm and written in metres. If the boolean fails,
  it is retried once with OCC healing; then `MeshError(MESH_GMSH_FAILED)`.
* gmsh state does not outlive a session, so `OccModel` is the pure-data plan; `write_msh` and
  `export_glb` replay it. `build_occ_model` runs a validating build and stores the report.

## Physical groups (attribute numbers)

| Name | dim | Tag | Meaning |
|---|---|---|---|
| `mat:<material>` | 3 | 1.. (sorted by name) | dielectric / background volumes |
| `cond:<body id>` | 2 | 100.. | conductor surfaces (solid copper walls or sheets) |
| `via:<via id>` | 2 | 100.. (after bodies) | via barrel surfaces |
| `port:<name>` | 2 | 1000.. | port faces (lumped quad, wave rectangle, coax ring) |
| `bc:<xmin..zmax>` | 2 | 2000.. | remaining exterior faces of the domain |

Groups are disjoint (a triangle is in at most one), so no attribute appears twice. A wave port that
crosses several materials is several coplanar surfaces in one group (see the change request); a
lumped port is one surface. `out.groups.json` maps each group to its model id, net, role,
representation and the BC type of each domain face.

## Palace (WP-301) expectations

Verified against the Palace documentation as remembered; items marked [verify] need a check
against the pinned Palace 0.18 release.

* Format: gmsh `.msh`, version 4.1 ASCII (default) or 2.2 (`msh_version="2.2"`; v2 keeps one
  physical tag per element, which is fine because the groups are disjoint).
* Attributes are the physical group tags: `config.json` lists `Domains.Materials[].Attributes`
  with the `mat:*` tags, and `Boundaries.{PEC, Absorbing, LumpedPort, WavePort, Conductivity,
  Impedance}[].Attributes` with `cond:`/`via:`/`port:`/`bc:` tags. Physical names are written but
  Palace does not use them.
* Length unit: coordinates are metres, so set `"Model": {"L0": 1.0}` [verify: Palace default is
  1e-6, i.e. micrometres].
* Tetrahedra only, linear (order 1); Palace refines/curves them itself. All surface elements in
  the file carry a physical tag; interface triangles between materials are not written.
* Thin copper as sheets: interior sheet triangles are conformal (shared nodes on both sides) and
  can take `Impedance` / `Conductivity` boundary conditions [verify interior-boundary support for
  the chosen BC type].
* Every exterior face is in exactly one group, so an unassigned face (which Palace treats as
  natural PMC) cannot occur by accident; map `bc:*` faces to PEC / Absorbing from
  `boundary_bc` in the sidecar.

## Size field

`lambda/N` per dielectric at `f_max` (N = 6 / 10 / 15 for coarse / medium / fine, override with
`expert["gmsh.cells_per_wavelength"]`), `<= thickness / 2` in thin dielectrics (not in
copper-layer resin), conductor edges `width / 4` (outer-ring width estimate), via barrels
`2 pi r / 14` (>= 12 segments), grading `MeshSettings.max_grading` (Threshold fields). Deterministic:
single thread, HXT, fixed seed; `MeshStats.content_hash` is stable for a gmsh version.
