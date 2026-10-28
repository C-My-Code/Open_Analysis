# WP-206: port faces, signatures and conductor meshing

Status: proposal, from WP-206 (3D OCC mesher).

## 1. "Every port face is a single surface"

A wave port is a rectangle on the boundary that normally crosses several materials (dielectric,
air) and the cross-section of the signal copper. After `BooleanFragments` the rectangle is split
at material interfaces, so it cannot be one surface in a conformal mesh. Implemented reading:

* a lumped port (a quad inside one dielectric) is exactly one surface;
* a wave port is one physical group whose surfaces are coplanar and tile the port rectangle minus
  the copper cross-section (copper is not meshed as volume).

If Palace needs one surface per wave port, WP-301 can merge same-material volumes upstream; say so
and WP-206 will fuse them before fragmenting.

## 2. Signatures (interfaces.md section 9)

`build_occ_model(structure, *, sheet_threshold, ports)` and `write_msh(model, path, *, sizes)` give
no frequency, so lambda/10 at f_max cannot be computed. Added keyword-only, defaulted parameters:
`build_occ_model(..., options: OccOptions | None, boundaries: Boundaries | None)` (`OccOptions.f_max`
default 10 GHz) and `write_msh(..., f_max=None, msh_version="4.1")`. `mesh_problem(problem, path)`
reads f_max from the `EMProblem`. `OccModel` is a plan (gmsh state cannot outlive a session);
`write_msh` replays it. `sizes` is optional.

## 3. Conductors are surfaces

Solid copper is carved out of the volume mesh; conductor faces are physical surface groups
(`cond:`, `via:`). Palace then sees PEC or surface-impedance walls. Volume per material is
"slab volume minus conductors". `ConductorBody.representation="sheet"` and `"auto"` below
`sheet_threshold` give zero-thickness sheets at the dielectric face of the copper layer.

## 4. Misc

* `ViaBody.pads` are ignored (already in `ConductorBody.polygons`; including them double-counts).
* Backdrill: `ViaBody` has no backdrill radius, so the plated span is the only information. A void
  is modelled only if `OccOptions.backdrill_clearance` is set. Consider a `backdrill_radius` field.
* Physical-group naming and numbering is defined in `oa_mesh/occ3d/README.md`; interfaces.md does
  not specify it.
