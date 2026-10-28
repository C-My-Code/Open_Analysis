# WP-208: discrepancies found by the ingest cross-validation

Status: proposed. No contract and no package outside `tests/integration/ingest/**` and `tests/golden/ingest_cross/**` was edited. Every difference below is whitelisted with its cause in `tests/golden/ingest_cross/exceptions.toml` (id in brackets) so the suite stays green; the tests also pin the size and direction of each one.

Summary of the run (KiCad 10.0.5): the 11 WP-102 boards and the demos `pic_programmer`, `stickhub`, `cm5_minima` compare with zero unexplained differences. Native reader against `expected.yaml`: counts exact, z within 1 nm, per-net per-layer copper area within 0.1 % (two exceptions, finding 1). Native against IPC-2581: counts exact, area within 0.5 %, z within 1 um after removing the forced mask offset, Dk and Df exact.

## 1. `expected.yaml` copper area ignores the backdrill [`expected-area-ignores-backdrill`]
Boards `via_transition` and `combined`, net `SE2`, layers `L7` and `L8`: `expected.yaml` says 9.15939942e-07 m^2, both readers (and `oa_geometry`) give 6.34357294e-07 m^2. The difference is exactly one via pad, pi * (0.3 mm)^2 = 2.827e-7 m^2 per layer. The backdrill (end layer `L7` is itself drilled, so `L7` and `L8` are removed) deletes the barrel pad there; pcbnew's polygon conversion used by the generator keeps it.
**Proposal (WP-102 owner):** regenerate the two numbers with the backdrilled pads removed, or document in the README that `copper_area` ignores backdrills. Impact: two numbers in two files; the whitelist entry then disappears.

## 2. `Component.rotation` of bottom-side parts differs by pi between the readers [`ipc-bottom-component-rotation`]
KiCad writes a bottom-side footprint with orientation 0 as `<Xform rotation="180.0" mirror="true"/>`. The IPC reader keeps 180 degrees, the KiCad reader keeps the KiCad orientation (0). Seen on `via_transition` (TP9, TP11, TP13) and `power_two_layer_vias` (J2). Pad positions agree to 1 um, so only the component record differs. Only orientation 0 was measured; the rule for other angles is not verified.
**Proposal (WP-103 owner):** derive the component rotation from the mirror flag (bottom side: report the angle the KiCad reader would report), and add a test on a bottom-side part with a non-zero orientation.

## 3. IPC-2581 export of the KiCad 9 syntax file has a different fill [`ipc-v9-export-fill-differs`]
`microstrip_v9`: the saved fill (read natively, equal to `expected.yaml`) stops 0.5 mm from the board edge; the GND fill in `board.ipc2581.xml` reaches 0.375 mm from it. GND copper area on L2..L4 is +0.99 % (0.002718 m^2 against 0.002691 m^2), over the 0.5 % acceptance. The v10 board with identical copper agrees to 1e-13. Cause not confirmed: either the exporter refills pre-10 files on load, or the WP-102 generator exported before it refilled.
**Proposal (WP-102 owner):** re-export the IPC file from the refilled board and check whether the difference remains. If it does, it is a KiCad behaviour to note in `oa-ingest-ipc2581` README (fills of pre-10 files in an IPC export are recomputed).

## 4. `microstrip` and `microstrip_v9` are not equal "except `meta`"
WP-208 acceptance asks for equal Boards except `meta`. They are equal only after removing item ids (`k:<uuid>`; the two files have different uuids), list order and padstack numbering. The test compares that canonical form (`canonical_board`) and passes. One real difference remains: the v10 file writes `(tenting (front yes) (back yes))` on its vias, the v9 file has no tenting token, and the KiCad reader reports `ViaProtection.tented_top/bottom = False` for it. Whether KiCad 9 reads an absent token as "not tented" is not verified.
**Proposal:** amend the acceptance to "except meta, item ids and ordering"; WP-101 owner to confirm the v9 tenting default (or the generator to write the token). The test currently asserts the difference so a change is noticed.

## 5. Backdrill conventions: no discrepancy
KiCad reader, IPC-2581 reader, `oa_geometry.via_stub` and pcbnew agree (`test_backdrill_conventions.py`):
- pcbnew `IsBackdrilledOrPostMachined` is true on `In6.Cu` and `B.Cu` of the SE2 via, so the listed end layer is cut;
- both readers give `Backdrill(side=bottom, last_removed_layer=L7, must_not_cut=L6)`;
- `barrel_z_range` ends at the top of `L7` (0.000270 m), `via_stub(SE2, [L1, L6])` = 0.00035 m on both boards, equal to `expected.yaml` `residual_barrel_below_last_used_layer`; the un-drilled twin SE1 gives 0.00062 m.
Two facts that are not discrepancies but should not be misread: (a) the IPC `MAX_STUB_LENGTH` of this via is 0.1925 mm, not the geometric residual of 0.35 mm (it equals half of z_bottom(L6) - z_bottom(L7) = 0.385 mm; a hypothesis, not confirmed); the value has no field in `Backdrill` (WP-103 change request section 2). (b) The IPC file has no backdrill diameter [`ipc-backdrill-diameter-missing`]: native 0.6 mm, IPC 0.3 mm (the via drill).

## 6. Stale-fill detection is partial (KiCad reader)
`test_stale_fill.py` on a copy of `split_plane`:
- all fills removed: `INGEST.ZONE_UNFILLED` and `fill_state="unfilled"` for every zone; `prepare_read(refill_zones="if_unfilled")` repairs it and the copper area returns to `expected.yaml` within 0.1 %;
- a track of another net laid across a fill: `INGEST.ZONE_STALE` and `stale_suspected`; after `refill_zones` the flag is gone and the fill is cut by the track plus clearance;
- zone outline halved, fill kept: **not flagged by the reader** (its test looks only for fills overlapping foreign tracks and vias). The harness function `fill_outside_outline` finds it (fill area outside the outline) and the report tool prints it.
- IPC-2581 always yields `fill_state="filled"`; a stale export cannot be detected.
**Proposal (WP-101 owner):** move the fill-outside-outline test into the reader and report it as `INGEST.ZONE_STALE`; consider the same for pads that moved after the fill.

## 7. KiCad's IPC export omits plated holes of some pads [`ipc-plated-hole-not-exported`]
`cm5_minima` J101: 12 pads (pad 1, GND, copper L1..L6, plated 0.5 mm in the KiCad file) are exported with `PADSTACK_27`, which has no `PadstackHoleDef`; the IPC reader returns SMD pads without a drill. The file, not the reader, is wrong. Consequence: through-hole count 24 natively against 12, via-in-pad and shield holes missing from the IPC board.

## 8. Native reader never uses `Pad.kind == "aperture"` [`ipc-no-aperture-pads`]
Pads with neither copper nor a hole (paste- or mask-only apertures, for example the four shield pads of crystal `Y1` on `stickhub`) are `kind="smd"` with an empty padstack. The IPC export drops them. `Pad.kind` has an `aperture` value that the KiCad reader does not produce.
**Proposal (WP-101 owner):** emit `aperture` for pads without copper and without a drill, so consumers can skip them.

## 9. Differences that are properties of IPC-2581, listed for completeness
`ipc-zone-outline-is-fill-hull`, `ipc-zone-priority-lost`, `ipc-no-rule-areas`, `ipc-mask-layers-added`, `ipc-bottom-mask-shifts-z`, `ipc-micro-via-flag-lost`, `ipc-npth-detached-from-footprint`, `ipc-unnumbered-pads-renamed`, `ipc-stacked-pads-merge`. Causes and evidence are in `exceptions.toml`.

## 10. Test collection (integrator)
The root `[tool.pytest.ini_options] testpaths` is `packages/*/tests`, so a bare `uv run pytest` does not collect `tests/integration/ingest`. Add `tests/integration/*` to `testpaths` when merging; until then run `uv run pytest tests/integration/ingest` (about 20 s, 87 tests; the `kicad` ones skip without KiCad).
