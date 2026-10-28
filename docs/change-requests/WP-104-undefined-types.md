# WP-104: types named but not defined in interfaces.md section 2, and builder gaps

Status: informational (no frozen field changed). Implemented as local definitions in `oa_geometry.models`.

## 1. Types the spec references without defining

`TopoNode`, `TopoEdge`, `Stub`, `Island`, `ViaTransition`, `DrillGeom`, `LayerIndex`, `BoundaryCrossing`, `ResolvedTerminal` are used by section 2 but have no field list. WP-104 defines them (see `packages/oa-geometry/src/oa_geometry/models.py`, every field carries a unit). Summary:

- `TopoNode(id, kind in pad|via|junction|vertex|end|zone, position, layer, ref)`; via contacts are one node per layer (`via:<id>@<layer>`), joined by `barrel` edges.
- `TopoEdge(id, kind in segment|via|zone, a, b, length, layer, seg_id, s_start)`.
- `Stub(kind in branch|pad_stub|via_stub, end_node, from_node, length, edges)`.
- `Island(id, net, layers, items: ItemRef[], area, bbox)`.
- `ViaTransition(via, net, position, from/to_layer, from/to_ref, ref_net_changes, return_vias, stitching_caps)`.
- `ResolvedTerminal(kind, position, z, layer, net, pads, extent)` with `.to_em() -> EMTerminalGeom`.
- `BoundaryCrossing(net, layer, seg_id, face, position, direction, width, z_bottom, z_top)`.
- `LayerIndex.query(geom, predicate) -> tuple[ItemRef, ...]` plus `query_indices`, `nearest`, `geometry(i)`.

Request: fold these into interfaces.md section 2 at the next spec revision, or tell WP-104 what to change.

## 2. Additive keyword arguments (signatures stay compatible)

- `topology(net, *, root=None)` and `segments(net, *, root=None)`: the driver pad (item id) the tree is rooted at; the spec says "driver pad if intent gives one" but passes no intent.
- `reference_map(*, k_buffer=3.0)`.
- `resolve_terminal(t, *, near=None, layer=None)`: `NearestReferenceTerminal` is defined relative to the positive terminal, which `resolve_terminal(t)` alone cannot know.
- `via_stub(board, via, used_layers)` is a module function (the model has no stackup); `BoardGeometry.via_stub(via, used_layers)` wraps it. It returns the longer of the top/bottom remnants; `via_stubs` returns both. Backdrill depth convention (barrel ends at the inner surface of `last_removed_layer`, or `depth` from the face) is in `oa_geometry.vias`; please confirm against WP-101's finding for KiCad.

## 3. Acceptance criteria that do not match oa_testkit builders

- `power_plane_board`: nets are `VDD`/`GND` (not `+1V0`) and there is no builder parameter to remove the via array. Tests build the variants with `model_copy` (`vias=()`, split plane zone).
- `via_transition_board` has no backdrilled via and a 4-layer stack (the spec text mentions L6/L8). The stub tests derive the numbers from the stackup z values of the same board with a `Backdrill` added.
- `cut_region` on `via_transition_board`: the auto bbox (signal extent + margin) contains both trace ends, so "exactly 2 trace crossings" holds only for an explicit bbox cutting both traces; the test uses `bbox=(5, -4, 20, 4) mm`.

## 4. Behavioural choices to confirm

- Reference copper = zone fills and graphic shapes of any net, plus all copper of `ground`/`power` nets; own-net copper never references. Reference layer = nearest layer whose best net covers >= 25 % of the `W + 2kh` footprint; else the best partial cover; else `None`.
- `coverage` is exact: area-weighted over each span's footprint strip; `0` coverage means `below`/`above` is `None`.
