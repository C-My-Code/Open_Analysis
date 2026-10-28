# 01 — Design Data Ingest (KiCad first, Altium-ready)

Status: research, 2026-10-27. Scope: getting board geometry, stackup, nets and components into the Open_Analysis data model. Solver physics is covered elsewhere.

---

## 0. Local findings (this machine)

KiCad install: `D:\Program Files\KiCad\10.0`, **KiCad 10.0.5** (`kicad-cli version`).

| Check | Result |
|---|---|
| `kicad-cli pcb export --help` | Subcommands: `3dpdf, brep, drill, dxf, gencad, gerbers, glb, hpgl (removed in 10), ipc2581, ipcd356, odb, pdf, ply, pos, ps, stats, step, stl, stpz, svg, u3d, vrml, xao` |
| `kicad-cli pcb` | `drc, export, import, render, upgrade` |
| `kicad-cli pcb import --help` | **Headless import of non-KiCad boards** with `--format auto, pads, altium, eagle, cadstar, fabmaster, pcad, solidworks` and a JSON/text import report. |
| `kicad-cli pcb export ipc2581` | Options: `--version` (default `C`), `--units`, `--precision` (default 6), `--compress`, BOM field mapping, `--variant`. |
| `kicad-cli pcb export odb` | Options: `--units`, `--precision` (default 2), `--compression zip`, `--check-zones` (refill before export), `--variant`. |
| Bundled Python | 3.11.5. `import pcbnew` **works headless**: `pcbnew.Version()` = 10.0.5. Site-packages also ship **numpy 2.4.2, scipy 1.17.1, shapely 2.2.0**, wx, requests. |
| `import kipy` | **Not installed** (`ModuleNotFoundError`). The package is `kicad-python` on PyPI, and it needs the GUI running anyway (see §2.3). |
| pcbnew headless load speed | `cm5_minima` (3.4 MB, 6 layers): 0.26 s. `jetson-agx-thor-baseboard` (**88 MB**, 10 Cu layers, 25k tracks, 125 zones): **5.8 s**. |
| kicad-cli export speed (cm5_minima) | IPC-2581C: 4.8 s, 11.6 MB XML. ODB++: 1.7 s, 0.5 MB zip. |
| IPC-2581 content check | The output has a `Stackup` with per-layer `thickness`, `Dielectric type="DIELECTRIC_CONSTANT"` (4.45) and `LOSS_TANGENT` (0.020), material text ("2116 RC58%"), padstacks, polygons and net-tagged `Set`s. |
| SWIG stackup access | `BOARD_STACKUP.GetList()` returns an opaque `SwigPyObject` that cannot be iterated or indexed in Python. **The dielectric stackup is effectively unreachable through SWIG**, so it has to be parsed from the file. |

Format versions seen in the bundled demos: `20241030`, `20241229` (KiCad 9.0), `20250513`, `20250907` (9.x/10 dev), and `20260206` (`generator_version "10.0"`).

Syntax I confirmed by **writing a board with pcbnew 10.0.5** and reading back the saved file:

```lisp
(via micro (at 14 10) (size 0.25) (drill 0.1) (layers "F.Cu" "In1.Cu") (net "") (uuid ...))
(via buried (at 12 10) (size 0.4) (drill 0.2) (layers "In1.Cu" "In3.Cu") ...)
(via (at 10 10) (size 0.6) (drill 0.3)
     (tertiary_drill (size 0.5) (layers "B.Cu" "In2.Cu"))      ; = bottom backdrill to In2
     (layers "F.Cu" "B.Cu")
     (padstack (mode front_inner_back) (layer "Inner" (size 0)) (layer "B.Cu" (size 0)))
     ...)
```

pcbnew 10 exposes `VIATYPE_THROUGH/BLIND/BURIED/MICROVIA/NOT_DEFINED`, `BACKDRILL_MODE_*`, primary/secondary/tertiary drill start and end layers, `PAD_DRILL_POST_MACHINING_MODE_COUNTERBORE/COUNTERSINK`, and via tenting, covering, plugging, filling and capping modes.

The probe scripts are in the session scratchpad and are not part of the repo.

---

## 1. The `.kicad_pcb` S-expression format (KiCad 9/10)

Official reference: https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/ and https://dev-docs.kicad.org/en/file-formats/sexpr-intro/. **The page is not fully current.** It still lists only `blind`/`micro` vias, and it covers neither padstacks nor backdrill. For 9/10 details, treat the KiCad source (`pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr_parser.cpp`, `pcb_io_kicad_sexpr.cpp` at https://gitlab.com/kicad/code/kicad) and files written by real boards as the ground truth.

### 1.1 General structure
- `(kicad_pcb (version YYYYMMDD) (generator "pcbnew") (generator_version "10.0") (general (thickness 1.6) (legacy_teardrops no)) (paper ...) (title_block ...) (layers ...) (setup (stackup ...) ...) [nets] footprints... gr_* ... segment/arc/via ... zone ... group ... generated ... (embedded_fonts no))`.
- Units are **mm**, as decimal text. KiCad's internal unit is 1 nm (int32), so the file round-trips exactly at 1e-6 mm. Y points **down**, and angles are in degrees.
- `version` is a date stamp. A parser should accept a range of versions and warn outside it. `kicad-cli pcb upgrade` normalizes old files to the current format.

### 1.2 Layers
- `(layers (0 "F.Cu" signal) (4 "In1.Cu" signal) ... (2 "B.Cu" signal) (9 "F.Adhes" user "F.Adhesive") ...)`.
- **KiCad 9 changed layer IDs**: F.Cu=0, B.Cu=2, In*n*.Cu=2n+2 (even numbers), and the technical layers are odd. Never hard-code ordinal IDs. Map by canonical name, and treat the optional third string as the user alias.
- The copper layer type can be `signal`, `power`, `mixed`, or **`jumper`** (seen in the Jetson demo, 9.x/10). Up to 32 Cu layers are allowed, and KiCad 9 made user layers unlimited ("User.N").
- The physical order comes from the **stackup**, not from layer IDs.

### 1.3 Stackup (`setup > stackup`)
```lisp
(stackup
  (layer "F.Mask" (type "Top Solder Mask") (color "Green") (thickness 0.01) [(material ..) (epsilon_r ..) (loss_tangent ..)])
  (layer "F.Cu" (type "copper") (thickness 0.035))
  (layer "dielectric 1" (type "core"|"prepreg") (thickness 1.51) (material "FR4") (epsilon_r 4.5) (loss_tangent 0.02)
         [(addsublayer (thickness ..) (material ..) (epsilon_r ..) (loss_tangent ..))]...)   ; multi-ply dielectrics
  ...
  (copper_finish "ENIG") (dielectric_constraints no) [(edge_connector ..) (castellated_pads yes) (edge_plating yes)])
```
- This covers what SI/PI needs: per-dielectric thickness, Dk (`epsilon_r`), Df (`loss_tangent`) and material name, plus copper thickness. There is **no frequency dependence** (Djordjevic-Sarkar etc.), **no copper roughness**, and **no copper conductivity**. Those have to live in our own sidecar file or material library, keyed by material name.
- A dielectric can be split into `addsublayer` plies. Our model should flatten them into separate dielectric slabs.
- Mask layers carry Dk and thickness, which matters for microstrip.
- If a board has no stackup section, KiCad uses defaults (1.6 mm total, 35 µm Cu). Treat that as "stackup unknown" and warn.

### 1.4 Nets and net classes
- **KiCad 10 change:** nets are written **by name only**, as `(net "GND")` on items, and the **top-level `(net N "name")` table is gone**. The v10 demo file has zero top-level `(net` lines, while KiCad 9 files still write `(net 0 "") (net 1 "GND") ...` and the items carry `(net 3 "GND")` or `(net 3)`. A parser must handle **both** forms. A forum discussion attributes the change to git/VCS friendliness: https://forum.kicad.info/t/upgrading-from-ver-6-to-version-10/67939
- **Net classes are not in `.kicad_pcb`.** Since KiCad 7 they live in `<project>.kicad_pro` (JSON) under `net_settings`: `classes` (track_width, clearance, diff_pair_width/gap, via sizes, priority, and in v10 a `tuning_profile`), `netclass_patterns` (glob to class, e.g. `/*USB_*` to `90ohm`) and `netclass_assignments`. Classes such as "90ohm"/"100ohm" are useful **impedance-intent hints** for our tool.
- Custom DRC rules live in `<project>.kicad_dru` (S-expression).
- Diff pairs are implicit, by naming convention (`*_P/*_N`, `*+/*-`). We need our own pairing heuristic, with a user override.

### 1.5 Tracks and arcs
- `(segment (start x y) (end x y) (width w) (layer "F.Cu") (net "N") (uuid ..) [(locked yes)])`.
- `(arc (start ..) (mid ..) (end ..) (width ..) (layer ..) (net ..) (uuid ..))` is a **three-point arc** (start, mid, end) and has no centre or radius. Compute the centre ourselves.
- `(generated (type tuning_pattern) ...)` objects describe meanders, but the copper they produce is also present as ordinary segments and arcs. Ignore the `generated` objects for geometry and keep them as metadata (target length/delay).
- Teardrops are written as zones with `(teardrop (type padvia|track_end))` attributes, and their fills are in `filled_polygon`.

### 1.6 Vias
- The type token is none (through), `blind`, `buried` (**new in 10**; earlier versions had only "blind/buried" as one type), or `micro`. Layers are given as `(layers "start" "end")`.
- `(size d)`, `(drill d)`, `(remove_unused_layers yes)` plus `(keep_end_layers yes)` (non-functional pad removal, which matters for via stub and capacitance models), `(zone_layer_connections ...)`, `(free yes)`, `(locked yes)`, and `(teardrops ...)`.
- **KiCad 10 backdrill / post-machining** (confirmed locally): `(secondary_drill (size ..) (layers "F.Cu" "InX.Cu"))` for a top backdrill and `(tertiary_drill (size ..) (layers "B.Cu" "InY.Cu"))` for a bottom backdrill. There are also front/back post-machining modes (counterbore/countersink with size, depth and angle) and `(capping)`, `(covering (front)(back))`, `(plugging (front)(back))`, `(filling)` (IPC-4761 via protection, new in 9/10). For SI, backdrill depth gives the **remaining stub length**.
- **Padstacks (KiCad 9+):** `(padstack (mode normal|front_inner_back|custom) (layer "F.Cu"|"Inner"|"B.Cu"|"In3.Cu" (size ..) (shape ..) ...))`. Vias and pads can have **per-layer pad sizes** and shapes. If there is no `padstack` node, the via or pad is uniform (mode normal).

### 1.7 Footprints and pads
- `(footprint "Lib:Name" (layer "F.Cu") (uuid ..) (at x y [rot]) (property "Reference" "R1" ..) (property "Value" ..) ... (attr smd|through_hole [exclude_from_bom] [dnp]...) (fp_line/fp_arc/fp_poly/fp_rect/fp_circle/fp_text ...) (pad ...) (zone ...) (model "...step" ...))`.
- `(pad "1" smd|thru_hole|np_thru_hole|connect rect|circle|oval|roundrect|chamfered_rect|trapezoid|custom (at x y [rot]) (size w h) [(drill [oval] d [h]) (offset ..)] (layers "F.Cu" "F.Mask" ...|"*.Cu") (net "N") (pinfunction ..) (pintype ..) (roundrect_rratio ..) (chamfer_ratio ..) (chamfer ..) [(primitives (gr_poly ...)...)] [(padstack ...)] (zone_connect ..) (thermal_bridge_width ..) (clearance ..) (die_length ..) (die_delay ..) (uuid ..))`.
- **Pad `at` is relative to the footprint, and pad rotation is absolute (board frame), not relative.** This is a classic parser bug. Back-side footprints are already mirrored in the stored coordinates.
- `custom` pads are an anchor shape plus primitives. `die_length` (and in v10 `die_delay`) give package length for timing.
- Other v9/v10 footprint fields: `(duplicate_pad_numbers_are_jumpers ..)`, `(jumper_pad_groups ..)`, `(net_tie_pad_groups ..)`, `(component_classes ..)`, `(embedded_fonts ..)`, `(embedded_files ..)`, and footprint objects on inner layers (v10).

### 1.8 Zones
```lisp
(zone (net "GND") (net_name ...)? (layer "B.Cu") | (layers "F.Cu" "In1.Cu" ...) (uuid ..) (hatch edge 0.5)
  (priority n) (connect_pads [yes|no|thru_hole_only] (clearance 0.5)) (min_thickness 0.25)
  (fill yes (mode hatch)? (thermal_gap ..) (thermal_bridge_width ..) (island_removal_mode 0..2) ...)
  [(keepout (tracks not_allowed) (vias ..) (pads ..) (copperpour ..) (footprints ..))]   ; rule area
  (polygon (pts (xy ..) (arc (start)(mid)(end)) ...))            ; user outline
  (filled_polygon (layer "B.Cu") [(island)] (pts (xy ..)...)) ...)  ; computed copper, per layer
```
- `filled_polygon` is the **actual copper**, with thermals and clearances already cut. Each one is written as a single outline in which holes are joined to the outline through zero-width bridges, KiCad's "fractured" polygon style. Re-run a boolean (shapely or Clipper2) union to recover proper holes.
- **The fill can be stale or missing** if the user did not refill before saving. Mitigations: run `kicad-cli pcb export odb --check-zones`, or call `ZONE_FILLER` through pcbnew, or warn when zones have no `filled_polygon`.
- The old `fill_segments` form is legacy (≤ v5) and can be ignored.

### 1.9 Board outline and graphics
- The outline is made of `gr_line`, `gr_arc`, `gr_circle`, `gr_rect`, `gr_poly` and `gr_curve` (Bezier) items on `Edge.Cuts`, plus footprint `fp_*` items on Edge.Cuts. They have to be **chained into closed loops**, using KiCad's chaining tolerance (about 1 µm, configurable). The outer loop is the board and inner loops are cutouts.
- `(stroke (width ..) (type ..))` replaced plain `(width ..)` in v7+. `(fill yes|no|solid|hatch)` changed in v10 (hatched fills).
- Graphic copper (`gr_*` on a Cu layer, optionally with `(net ..)`) is real copper too. Include it.

### 1.10 What changed in KiCad 9 and 10 (ingest-relevant)
| Change | Version | Source |
|---|---|---|
| Layer ID renumbering (F.Cu=0, B.Cu=2, In = even) | 9 | local files; KiCad 9 release notes https://www.kicad.org/blog/2025/02/Version-9.0.0-Released/ |
| Per-layer padstacks (`padstack` node, front/inner/back, custom) | 9 | ibid. |
| Unlimited user layers; layer types incl. `jumper` (9.x/10) | 9/10 | local |
| IPC-4761 via protection (tenting/covering/plugging/filling/capping) | 9/10 | local |
| Embedded files/fonts in board | 9 | ibid. |
| IPC API v1 (replacement for SWIG) | 9 | https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/ |
| **Nets by name, no net table** | 10 | local (v20260206) |
| **Buried via type separate from blind** | 10 | local (pcbnew API) |
| **Backdrill and post-machining** (secondary/tertiary drill, counterbore/countersink) | 10 | local (pcbnew API and saved file) |
| Time-domain tuning, tuning profiles, `die_delay` | 10 | https://www.kicad.org/blog/2026/03/Version-10.0.0-Released/ |
| Design variants (`--variant` on exports), barcodes, hatched fills, inner-layer FP objects | 10 | ibid. |
| New importers (Allegro, PADS, gEDA); `kicad-cli pcb import` | 10 | ibid.; local |
| HPGL plotting removed | 10 | local |

---

## 2. Access methods

### 2.1 Parse the raw S-expression ourselves
- **Pros:** No KiCad install needed at runtime. Pure Python, fast, and with **no GPL entanglement**, since the file format is data, not code. We control the version handling, and it gives full access to the stackup (unlike SWIG) and to `filled_polygon`. Works the same on CI, servers and in a browser.
- **Cons:** We track format changes ourselves, roughly yearly, and they are under-documented. We have to replicate the geometry semantics: pad rotation, custom pads, arcs, fractured polygons, outline chaining, net-tie/jumper groups. Zone fills cannot be recomputed (we read whatever was saved).
- **Stability:** The format is backward compatible on read (KiCad reads every file since v4). New tokens appear each major release, so a tolerant parser that ignores unknown nodes holds up well.
- **Effort:** An S-expression tokenizer is about 100 lines. The semantic layer is about 2–3k lines for full coverage. Performance is fine: a streaming tokenizer handles 88 MB in seconds, and the bulk is `filled_polygon` point lists, which should be parsed straight into numpy arrays.

### 2.2 pcbnew SWIG Python bindings
- **Status: deprecated, and removed in KiCad 11.** The KiCad developer (forum user craftyjon, believed to be lead dev Jon Evans) said on 2026-05-27: "The SWIG API is removed in the nightlies / V11", with V11 expected around Feb/Mar 2027 (https://forum.kicad.info/t/migration-schedule-from-the-old-swig-api-to-the-ipc-api/69437). The SWIG API also has **no stability guarantee** between versions.
- **Pros (today):** Works **headless** in the bundled Python (verified: 5.8 s for an 88 MB board). It exposes KiCad's own geometry engine: `ZONE_FILLER` to refill zones, `GetEffectiveShape()` / `TransformShapeToPolygon` for exact pad and track polygons, connectivity, and DRC. Covers 100% of the object model, including backdrill.
- **Cons:** Tied to KiCad's bundled interpreter (3.11 here) and to one KiCad version. Stackup lists are not iterable from Python (verified). **GPL-3**: importing pcbnew in-process means our process links GPL code, so keep it in a separate helper process. It will be gone in 11.
- **Use:** Only as an **optional, out-of-process "KiCad oracle"** for KiCad ≤ 10. Its jobs are refilling zones, cross-checking our parser's polygons in tests, and handling exotic pads. Never make it the primary path.

### 2.3 KiCad IPC API plus `kicad-python` (kipy)
- Protobuf messages over NNG sockets. KiCad acts as the server and clients connect to it (https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/). The Python client `kicad-python` 0.8.0 (2026-08-30) is **MIT**, needs Python ≥ 3.9, and depends on protobuf and pynng (https://pypi.org/project/kicad-python/, https://gitlab.com/kicad/code/kicad-python, docs https://docs.kicad.org/kicad-python-main/).
- **The API needs a running KiCad GUI in 9 and 10** with the API server enabled. "Support for running in headless mode through kicad-cli was added for KiCad 11", and plotting/export via IPC is also an 11 feature (https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/).
- **Pros:** Officially supported, with versioned and additive protobuf schemas. Deprecated items survive at least one major version. MIT client means no license issue. Language-agnostic: protobuf works from C++, Rust and others. It can also give **live interaction**, such as cross-probing results back into the KiCad GUI and highlighting nets.
- **Cons:** No headless mode until 11, which kills batch and CI use on 9/10. Coverage is still growing: board items and nets are reachable (stackup and filled-zone coverage not verified here), and completeness for filled zones and padstack detail has to be verified per release. The API is synchronous with KiCad's UI loop.
- **Use:** A **secondary, interactive path** (a "live link" plugin), with headless batch via `kicad-cli` IPC once KiCad 11 ships. Worth tracking closely, because it is KiCad's long-term blessed route.

### 2.4 kicad-cli exports
All of these run headless in a subprocess, with no GPL linkage (we only consume the output files) and no GUI.

| Export | SI/PI usefulness | Notes |
|---|---|---|
| **IPC-2581 (rev B/C)** | **High**: full stackup with thickness, Dk and Df (verified), padstacks, holes, copper features per layer, nets, components, BOM, outline | XML, vendor-neutral, and the same importer would read Altium, Allegro, Xpedition and PADS output. Verified 4.8 s and 11.6 MB for a 6-layer board. Arcs are kept as arcs. Some KiCad-specifics are lost: zone priority, netclasses (partly), and backdrill (check whether it is mapped to IPC-2581 back-drill spans). |
| **ODB++** | High: steps, layers, features, nets (eda/data), components, drill layers, stackup in `matrix` and `misc/attrlist` | Directory or zip of text files. `--check-zones` refills zones. Very fast (1.7 s). The ODB++ spec is free from Siemens (https://odbplusplus.com/). Dk/Df is less standardized than in IPC-2581. |
| Gerber X2/X3 + Excellon drill | Medium: geometry only, with nets via X2 attributes | No stackup Dk/Df. Arcs and regions are fine. A last-resort route, since most CAD tools output it. |
| IPC-D-356 | Netlist plus test points only | Useful as a connectivity cross-check. |
| GenCAD | Components, pads, nets, routes | Legacy. Weaker than IPC-2581. |
| STEP/BREP/XAO/GLB | 3D solids (copper optional) | Useful for full-wave 3D solvers and meshing. XAO is the Salome/GMSH-friendly format. |
| `pcb stats`, `pos` | Component placement and statistics | Minor. |
| `pcb drc` (JSON) | Pre-flight sanity check | Run it before analysis and surface unconnected items. |

---

## 3. Existing parser libraries

| Library | Lang | License | Status | Coverage / notes |
|---|---|---|---|---|
| **kiutils** https://github.com/mvnmgrx/kiutils | Python dataclasses | **GPL-3** | Last PyPI release 1.4.8 on **2024-02-02** (KiCad 7/8 era); effectively stalled | Board, footprint, schematic and libraries round-trip. **No KiCad 9/10 support**: no padstacks, the layer renumbering is irrelevant to it but the new tokens are dropped, and it does not know name-only nets. GPL is a problem for a permissively licensed suite. Not recommended. |
| **kicad-skip** https://github.com/psychogenic/kicad-skip | Python | LGPL-2+ | 0.2.5, 2024-02 | Generic S-expression tree with attribute access, schematic-focused. Generic enough to read v10, but provides no semantics. |
| **sexpdata** https://github.com/jd-boyd/sexpdata | Python | BSD-2 | 1.0.2, 2024 | Generic S-expression reader. Slow on 88 MB files (pure Python, recursive). An option for a prototype only. |
| **KiCanvas** https://github.com/theacodes/kicanvas | TypeScript | MIT | Active-ish | Browser viewer with a typed `.kicad_pcb` parser. A good **reference** for a permissively licensed semantic parser, and for a future web viewer. |
| **KiCad's own parser** (`pcb_io_kicad_sexpr_parser.cpp`, `common/io/kicad/...`, `thirdparty/sexpr` / `libs/sexpr`) https://gitlab.com/kicad/code/kicad | C++ | GPL-3 | Authoritative | Read it as a **specification**, without copying code if we want a non-GPL license. Its unit tests and QA boards (`qa/data/pcbnew/`) are an excellent regression corpus. |
| **KiKit** https://github.com/yaqwsx/KiKit | Python (uses pcbnew) | MIT | Active | Depends on SWIG, so it is affected by the KiCad 11 removal. Not a parser. |
| **kicad-python (kipy)** | Python | MIT | Official, active | IPC client (§2.3). Not a file parser. |
| Rust: `kicad_parse_gen`, `kiparse`, etc. (crates.io) | Rust | MIT/Apache, various | Mostly small or old | Not mature for PCB v9/v10. |

**Conclusion:** No maintained, permissively licensed Python parser covers KiCad 9/10 boards. **Write our own**, a small tolerant tokenizer plus a typed semantic layer. Validate it against pcbnew (out of process) and against the KiCad QA corpus.

---

## 4. Altium and vendor-neutral routes

### 4.1 `.PcbDoc` format
- It is an **OLE2 / Compound File Binary** (MS-CFB) container of streams, one storage per object class: `Board6/Data`, `Nets6/Data`, `Classes6/Data`, `Components6/Data`, `Pads6/Data`, `Vias6/Data`, `Tracks6/Data`, `Arcs6/Data`, `Fills6/Data`, `Regions6/Data`, `ShapeBasedRegions6/Data`, `Polygons6/Data`, `Texts6/Data`, `Rules6/Data`, `Models/`, `ComponentBodies6/Data`, `WideStrings6/Data`, `ExtendedPrimitiveInformation/Data`, and so on.
- Records mix **"|KEY=VALUE|" text property lists** (Board6 holds the stackup as `LAYERxNAME`, `LAYERxDIELCONST`, `LAYERxDIELHEIGHT`, `LAYERxCOPTHICK`, `LAYERV7_*`) with **packed little-endian binary structs** (tracks and pads use a 1/10000 mil coordinate unit).
- The format is **undocumented and proprietary**, and it changes with Altium versions. ASCII PcbDoc also exists but is rare. Readers: Python `olefile` (BSD) https://github.com/decalage2/olefile, or Rust `cfb`.
- Polygon pours exist as `Polygons6` (definition) plus `ShapeBasedRegions6` (poured result). Like KiCad, the stored pour can be stale.

### 4.2 Open-source parsers
| Project | Lang | License | Status |
|---|---|---|---|
| **KiCad Altium importer** `pcbnew/pcb_io/altium/` (`altium_pcb.cpp`, `altium_parser_pcb.cpp`) and `common/io/altium/` https://gitlab.com/kicad/code/kicad/-/tree/master/pcbnew/pcb_io/altium | C++ | GPL-3 | **The most complete and best-maintained reader in existence.** It covers stackup, nets, classes, components, pads (including per-layer sizes), vias, tracks, arcs, polygons/regions, rules and 3D bodies. Exposed headless as **`kicad-cli pcb import --format altium`** (verified in 10.0.5). |
| pluots **PyAltium** https://github.com/pluots/PyAltium / Rust **altium** crate https://docs.rs/altium | Python / Rust | MIT | The README calls PyAltium "currently broken and incomplete"; the Rust crate is alpha (last release 2023) and SchDoc-centric. Not usable for PCB. |
| **altium2kicad** https://github.com/thesourcerer8/altium2kicad | Perl | GPL | Old. Partially superseded by the KiCad importer. |
| **AtoK** https://github.com/stevegrn/AtoK | C# | GPL-3 | PcbDoc to KiCad converter (openmcdf). Hobby-level. |
| **altiumts** https://www.npmjs.com/package/altiumts | TypeScript | (unchecked) | Experimental. Binary PCB read-only. |
| python-altium (vadmium) https://github.com/vadmium/python-altium | Python | MIT-ish | Schematic only. |

### 4.3 Vendor-neutral formats
- **IPC-2581** (rev C, 2020; open standard, free XSD, consortium at https://www.ipc2581.com/): Altium Designer exports it natively (File > Fabrication Outputs > IPC-2581), as do Cadence Allegro/OrCAD, Siemens Xpedition/PADS, Zuken and KiCad. It carries the stackup with Dk/Df, padstacks, back-drill, net-tagged features, components and BOM in one XML file. **One importer covers almost every commercial tool.** Weak spots: exporter quality varies, and some tools omit Dk/Df or write pours as unfilled outlines.
- **ODB++** (Siemens, spec free after registration at https://odbplusplus.com/): universally supported by fabs and CAM tools, and Altium exports it. Rich, with the netlist in `steps/*/eda/data`, but it is less self-describing about dielectric properties, and it is a directory tree of many text files. Good as a second neutral reader.
- **Gerber X2 + drill**: a last resort. No Dk/Df, so the user supplies the stackup.

---

## 5. Recommendation

### 5.1 Ingest architecture

```
                    +------------------------------+
 .kicad_pcb/.pro -->| A. Native KiCad reader (ours)|--+
                    +------------------------------+  |
 .xml (IPC-2581) -->| B. IPC-2581 reader (ours)    |--+-->  Neutral Board Model (oa.board)
                    +------------------------------+  |      -> validate/heal (shapely/Clipper2)
 ODB++ (.tgz/dir) ->| C. ODB++ reader (later)      |--+      -> serialize (JSON/Parquet/HDF5)
                    +------------------------------+  |      -> solvers
 .PcbDoc ---------->| D. kicad-cli pcb import      |--+  (converts to .kicad_pcb, then reader A)
                    |    --format altium (subproc) |
                    +------------------------------+
 optional oracle:  E. pcbnew SWIG helper (KiCad <=10, out-of-process): zone refill, test cross-check
 optional live:    F. kipy IPC plugin: cross-probe/highlight results in a running KiCad (9+), headless in 11
```

1. **Primary path (A): our own permissively licensed `.kicad_pcb` + `.kicad_pro` reader.**
   - A streaming tokenizer, with point lists going straight into numpy.
   - A versioned semantic layer supporting files from 20221018 (v7) through 2026xxxx (v10), with unknown tokens logged rather than fatal.
   - Reads net classes and patterns from `.kicad_pro`.
   - Normalizes on the way in: KiCad nm-exact mm to SI metres in float64, the Y axis flipped to Y-up, and arcs to centre/radius/angles.
   - Defines its own explicit format-version support table.
2. **Pre-flight via kicad-cli (subprocess, optional when KiCad is installed):**
   - `pcb upgrade` normalizes old files.
   - `pcb drc --format json` produces a sanity report.
   - Stale zones: if zones lack `filled_polygon`, offer to refill. The refill can go through the pcbnew helper (≤10), or `pcb export odb --check-zones` followed by reading the ODB++ output, or KiCad 11 IPC headless.
3. **Neutral path (B): IPC-2581 reader**, built second. It covers Altium, Allegro, Xpedition and PADS output **without** reading proprietary binaries, and it doubles as a cross-check of reader A (export the same KiCad board through kicad-cli and diff the two models in CI).
4. **Altium native (D):** `kicad-cli pcb import --format altium --report-format json` converts the board to a `.kicad_pcb`, which then goes through reader A. Subprocess-only use keeps our code free of GPL. Do **not** write our own PcbDoc parser until there is demand. If we ever do, use the KiCad importer as the spec and `olefile` as the container reader.
5. **ODB++ reader (C):** a later, lower priority. Most tools that export ODB++ also export IPC-2581.
6. **Do not depend on SWIG**, given its removal in 11. Track the IPC API and plan a kipy-based live plugin (F).

### 5.2 Neutral internal data model (sketch)

Principles:
- SI units (metres, Hz, S/m) in float64.
- A right-handed frame: X right, Y up, Z up from the bottom of the board, so the solvers get the true z-coordinates of every layer.
- Immutable after ingest. Every object carries a `source_ref` (file plus uuid) for cross-probing.
- Geometry is **polygons with holes**, arcs kept analytically where possible and discretized on demand with a tolerance.
- Pydantic or attrs for the schema, numpy arrays for coordinates, and JSON+npz or HDF5 for serialization.

```python
Board
  meta: source_tool ("kicad"/"altium"/...), source_version, file_hash, units_note, warnings[]
  outline: Region                          # polygon(s) with holes (board + cutouts)
  stackup: Stackup
  nets: dict[NetId, Net]
  net_classes: dict[str, NetClass]
  diff_pairs: list[DiffPair]
  layers_geometry: dict[LayerId, LayerGeometry]
  padstacks: dict[PadstackId, Padstack]
  vias: list[ViaInstance]
  components: dict[RefDes, Component]
  ports: list[Port]                        # created by user/automation, not by ingest
  rules: DesignRules                       # optional (min width/clearance), for meshing hints

Stackup
  layers: list[StackLayer]  # physical top->bottom order
  StackLayer: id, name, kind (conductor|dielectric|mask|paste|silk), thickness,
              z_bottom, z_top (computed),
              material: MaterialRef
              conductor: {copper_thickness, conductivity=5.8e7, roughness_model?, etch_factor?}
              dielectric: {er, tan_d, freq_ref, model: const|djordjevic_sarkar|table, plies[]}
  materials: library keyed by name; sidecar override file (oa_stackup.toml) because
             KiCad/Altium lack freq-dependent Dk/Df, roughness, sigma

Net:        id, name, class, kind (signal|power|ground|nc), is_diff_member, pins[PinRef]
NetClass:   name, track_width, clearance, dp_width, dp_gap, target_impedance? (parsed from name or set by user)
DiffPair:   p_net, n_net, (source: naming heuristic | user)

LayerGeometry (per conductor layer)
  traces: list[Trace]  # centerline path (line/arc segments) + width + net -- kept for impedance/TL extraction
  shapes: list[Shape]  # polygons with holes + net: pour fills, pads-as-copper, graphic copper
  merged(net) -> Region  # boolean union per net (computed, cached) for PI/mesh solvers

Trace: net, layer, segments [(Line|Arc)], width, source_ref
Shape: net, layer, Region (exterior + holes, arcs optional), origin (zone|pad|gr|teardrop), zone_priority?

Padstack
  id, hole: {kind: plated|npth|none, shape: round|slot, d, slot_len?, plating_thickness?}
  per_layer: dict[LayerId | "inner" , PadShape]  # covers KiCad front/inner/back/custom + Altium per-layer
  antipad/clearance per layer (when known), thermal relief params
  unused_layer_removal: bool, keep_end_layers: bool
ViaInstance
  padstack, position, net, span (start_layer, end_layer), kind (through|blind|buried|micro),
  backdrill: list[{from_side: top|bottom, to_layer, diameter, must_not_cut_layer?}],
  fill/cap/tent flags
Component
  refdes, value, footprint, side, position, rotation, attrs (dnp, exclude_from_bom), model_ref,
  pins: dict[pin_number, Pin{net, padstack|pad_shape, position, layer(s), pin_type, die_length, die_delay}]
Port  (solver-facing; not produced by ingest, but referencing ingest ids)
  name, kind (lumped|wave|coax|via|pin-group), positive: [PinRef|Point+Layer], negative: [...],
  reference impedance; auto-generators: "all pins of U1 on net group X", "VRM to sink"
```

### 5.3 Keeping it Altium-ready
- **Every format-specific detail stays in the reader.** The model holds no KiCad layer names, IDs or token strings, only canonical stack positions plus an original-name alias.
- **Per-layer padstacks are first-class.** Altium's `Pads6` has top, mid and bottom sizes plus full per-layer stack mode, which maps directly onto `Padstack.per_layer`.
- **Units:** Altium uses 1e-4 mil internally and KiCad uses nm. Convert both at the reader boundary to metres.
- **Pours:** both tools store a pour definition plus a possibly stale pour result. Model `Shape.origin=zone` with a `stale?` flag.
- **Stackup:** Altium has richer data (Layer Stack Manager impedance profiles, material library with frequency tables in newer versions). The `Stackup.dielectric.model` field must allow frequency tables from day one.
- **Rules and classes:** Altium has net classes, differential pair objects (explicit, unlike KiCad's naming heuristic) and impedance profiles. `DiffPair.source` and `NetClass.target_impedance` accommodate both.
- **Golden-test strategy:** for each test board, ingest it via the native reader and via IPC-2581, then compare the models (net count, per-net copper area, via count, stackup z-values) within tolerance. The same harness validates the Altium route (PcbDoc through kicad-cli import, compared against Altium's own IPC-2581 export).

---

## 6. Open risks

1. **KiCad format churn:** v10 removed the net table, added backdrill and buried vias, and has a new fill syntax. v11 will change things again. Mitigations: a tolerant parser, a version table, and CI against KiCad nightly QA boards.
2. **Stale or missing zone fills.** The saved copper may not match DRC-clean copper. A refill needs KiCad (SWIG ≤10, kicad-cli ODB++ `--check-zones`, or 11 headless IPC).
3. **SWIG removal in 11.** Any helper built on it is temporary.
4. **IPC API headless only in 11.** On 9/10, kipy needs the GUI.
5. **GPL boundaries.** pcbnew, kiutils and KiCad source are GPL-3. Use them only as subprocesses or as references, unless the project chooses GPL itself. **Decide the project license early.**
6. **Missing physics data in sources.** No frequency-dependent Dk/Df, no roughness, no conductivity, no plating thickness, and no explicit antipads (they are derived from clearances and zone fills). The sidecar stackup/material file is mandatory.
7. **Altium import fidelity** through the KiCad importer is good but not perfect: complex polygons, embedded boards, Altium-specific rules and impedance profiles. Validate against the Altium-exported IPC-2581.
8. **IPC-2581 exporter variance** across tools: Dk/Df presence, pour representation, and back-drill coverage. The KiCad exporter's mapping of v10 backdrill to IPC-2581 is unverified.
9. **Scale.** Boards around 100 MB with dense fills (the Jetson demo) need a streaming, numpy-backed parser and efficient polygon booleans (shapely 2 / Clipper2).
10. **The documentation gap.** The official sexpr-pcb page lags 9/10, so KiCad's C++ parser is the de facto spec.

## Sources
- KiCad PCB file format: https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/
- KiCad S-expression intro: https://dev-docs.kicad.org/en/file-formats/sexpr-intro/
- KiCad IPC API overview: https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/
- IPC API for add-on developers (headless and export only in 11): https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/
- SWIG removal in V11 (developer statement, 2026-05-27): https://forum.kicad.info/t/migration-schedule-from-the-old-swig-api-to-the-ipc-api/69437
- Headless pcbnew / IPC discussion: https://forum.kicad.info/t/headless-mode-for-pcbnew-python-api-multiple-kicad-instances-with-ipc/64063
- kicad-python on PyPI: https://pypi.org/project/kicad-python/ ; repo https://gitlab.com/kicad/code/kicad-python ; docs https://docs.kicad.org/kicad-python-main/
- KiCad 9.0 release: https://www.kicad.org/blog/2025/02/Version-9.0.0-Released/
- KiCad 10.0 release: https://www.kicad.org/blog/2026/03/Version-10.0.0-Released/
- KiCad 10 net-number removal (forum): https://forum.kicad.info/t/upgrading-from-ver-6-to-version-10/67939
- KiCad source (parser, Altium importer): https://gitlab.com/kicad/code/kicad and https://gitlab.com/kicad/code/kicad/-/tree/master/pcbnew/pcb_io/altium
- kiutils: https://github.com/mvnmgrx/kiutils ; PyPI https://pypi.org/project/kiutils/
- kicad-skip: https://github.com/psychogenic/kicad-skip
- sexpdata: https://github.com/jd-boyd/sexpdata
- KiCanvas: https://github.com/theacodes/kicanvas
- KiKit: https://github.com/yaqwsx/KiKit
- PyAltium / altium crate: https://github.com/pluots/PyAltium , https://docs.rs/altium
- altium2kicad: https://github.com/thesourcerer8/altium2kicad
- AtoK: https://github.com/stevegrn/AtoK
- altiumts: https://www.npmjs.com/package/altiumts
- python-altium: https://github.com/vadmium/python-altium
- olefile: https://github.com/decalage2/olefile
- IPC-2581 consortium: https://www.ipc2581.com/
- ODB++: https://odbplusplus.com/
- Local verification: KiCad 10.0.5 at `D:\Program Files\KiCad\10.0` (kicad-cli help, pcbnew probes, demo boards in `share/kicad/demos`).

> Verification note: items marked "local" were confirmed on KiCad 10.0.5. The Altium stream names, the KiCanvas license, the `addsublayer` token and the KiCad 9 release-note URL come from prior knowledge and were not re-fetched in this session.
