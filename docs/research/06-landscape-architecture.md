# 06 - Product Landscape and Software Architecture

Status: research draft, 2026-10-27
Scope: the commercial feature landscape, prior open-source work, the electrical rule check (ERC/"EDRC") rule set, tech stack options, project scaffolding, and a recommended architecture with module interfaces.
Out of scope (covered by other reports): ingest file formats in detail, solver physics and numerics.

Confidence notes: product feature lists come from vendor pages and trade press, so they reflect marketing scope rather than verified capability. Items marked **[verify]** need a primary-source check before anyone relies on them.

---

## 1. Commercial feature matrix

### 1.1 Feature categories by product

| Category | HyperLynx (Siemens) | SIwave (Ansys) | Sigrity / Clarity (Cadence) | Polar Si9000e / Speedstack | Saturn PCB Toolkit | Altium built-in |
|---|---|---|---|---|---|---|
| Stackup editor and fab documentation | Yes (LineSim/BoardSim stackup) | Yes | Yes | **Speedstack core** | No | Layer Stack Manager |
| 2D cross-section impedance / RLGC field solver | Yes (BEM) | Yes | Yes | **Si9000e core (BEM, 100+ structures)** | Closed-form (IPC-2141 / Wadell) | Simbeor MoM 2D solver |
| Insertion loss, roughness models (Huray, Groisse, Hammerstad) | Yes | Yes | Yes | Yes | No | Yes (roughness, causal dielectric) |
| Pre-layout "what-if" SI (schematic topology) | LineSim | Partial | SystemSI / Topology Workbench | No | No | No |
| Post-layout reflection / crosstalk simulation (IBIS) | BoardSim | Yes | PowerSI / Sigrity SI | No | No | Yes (reflection + crosstalk, older engine) |
| SerDes channel: S-params, eye, COM, IBIS-AMI | Yes (SerDes wizard, COM) | Yes (with Circuit/HFSS) | SystemSI, Clarity | No | No | No |
| DDR interface timing / batch DDR wizard | Yes | Yes | Yes | No | No | xSignals length/delay only |
| Hybrid / 3D-full-wave extraction (S-params of nets, vias, connectors) | HyperLynx Full-Wave / Advanced Solvers | **SIwave hybrid FEM, links to HFSS** | Clarity 3D, PowerSI | No | No | No (EM via Simbeor add-on) |
| Via modeling (stubs, backdrill, antipads) | Yes | Yes | Yes | No | Via current/inductance calc | Backdrill definition |
| PI: DC IR drop, current density, via currents | HyperLynx PI | SIwave DCIR | PowerDC | No | Via/trace ampacity calc | Power Analyzer (by Keysight) |
| PI: AC PDN impedance, decap optimization, target Z | HyperLynx PI | SIwave | OptimizePI / PowerSI | No | Basic decap reactance | No |
| Plane resonance / cavity, EMI near-field | Yes | Yes (resonance, far-field) | Yes | No | No | No |
| Electro-thermal co-sim (IR drop heating) | HyperLynx Thermal / PI co-sim | Yes (with Icepak) | PowerDC electro-thermal | No | Temp rise (IPC-2152) | No |
| Electrical DRC (return path, plane crossing, stubs, etc.) | **HyperLynx DRC: 90+ rules, Python custom rules** | Partial | Sigrity Aurora / Allegro in-design checks | No | No | Some via design rules |
| Length/delay matching, time-domain tuning | Reports | Reports | Allegro constraint manager | No | Propagation delay calc | xSignals, Simbeor delay |
| Design-flow integration | Xpedition, PADS, import from most CADs | ODB++, Allegro, Altium via ANF | Allegro native | Exports to many CAD | Standalone Windows | Native |

Sources: HyperLynx DRC rule count and Python rules [Siemens EDRC page](https://www.siemens.com/de-ch/products/pcb/hyperlynx/electrical-design-rule-check/), [Siemens fact sheet](https://resources.sw.siemens.com/zh-TW/fact-sheet-electrical-rule-checks/), [PCD&F announcement](https://pcdandf.com/pcdesign/index.php/editorial/menu-new-indust-prod/8170-mentor-graphics-adds-design-rule-checking-to-hyperlynx-suite); Polar [Si9000 product page](https://polarinstruments.com/products/si/Si9000.html), [Si9000 brochure](https://www.polarinstruments.com/pdf/brochures/Si9000_brochure.pdf), [Speedstack Si](https://www.polarinstruments.com/products/stackup/Speedstack_Si.html); Altium [high-speed design features](https://www.altium.com/altium-designer/features/high-speed-design), [Layer stack impedance KB (Simbeor MoM)](https://www.altium.com/cn/documentation/knowledge-base/altium-designer/validate-transmission-impedance-computed-in-layer-stack), [Altium-Simberian partnership](https://resources.altium.com/p/high-speed-design-features-growing-forward-with-altium-and-simberian-partnership); Ansys [SIwave](https://www.ansys.com/products/electronics/ansys-siwave); Cadence [Sigrity](https://www.cadence.com/en_US/home/tools/system-analysis/signal-and-power-integrity.html); [Saturn PCB Toolkit](https://saturnpcb.com/saturn-pcb-toolkit/). Rows for SIwave/Sigrity/HyperLynx SI-PI-thermal come from vendor product pages and general knowledge **[verify specific module names]**.

### 1.2 Ranking by value to an individual high-speed digital and RF designer

The ranking scores each feature by how often it changes a layout decision, divided by how costly it is to get otherwise. "Feasible OSS" rates how practical an open implementation is: **H** = straightforward with existing libraries; **M** = real engineering but known methods and open solvers exist; **L** = research-grade, or depends on closed models (IBIS-AMI DLLs) or large validation effort.

| Rank | Feature | Why it matters | Feasible OSS |
|---|---|---|---|
| 1 | Stackup + 2D cross-section impedance (single-ended, diff, CPWG), with Er/Df vs frequency | Every controlled-impedance board needs it. Today people bounce between Saturn, Polar, and fab calculators | **H** (closed-form + 2D quasi-static FEM/BEM; references: atlc, TNT-MMTL, OpenParEM2D) |
| 2 | Geometry-only electrical rule checks (return path, plane crossing, stubs, diff-pair uncoupling, edge proximity) | Catches most of the real failures in seconds, with no models needed. Nothing in KiCad does this | **H** |
| 3 | Per-net impedance profile along the route (TDR-like view of discontinuities: neckdowns, reference changes, plane voids) | Shows *where* a net deviates, not just the nominal value | **H/M** (segment by segment 2D solves + caching) |
| 4 | Insertion loss / RLGC with roughness, Touchstone export | Channel budgeting for USB3/PCIe/Ethernet; RF line loss | **M** (2D solver + Huray/Djordjevic-Sarkar models, scikit-rf) |
| 5 | DC IR drop and current density on planes | Cheap to compute, frequent real failures (FPGA cores, power stages) | **M** (2D sheet conduction FEM; KiPI proves feasibility) |
| 6 | 3D EM S-parameters of local structures (via transitions, RF launches, filters, antennas) | Key for RF; replaces HFSS for small structures | **M** (openEMS FDTD, Palace FEM; kicad-rfsim, gerber2ems show the path) |
| 7 | Length/delay matching reports with true per-layer delay | KiCad 10 already does a lot of this; value is in verification against field-solved Er_eff | **H** |
| 8 | Crosstalk estimate (coupled-length x spacing; 2D coupled-line solve) | Common review item | **H** (geometric) / **M** (quantitative NEXT/FEXT) |
| 9 | AC PDN impedance and decap selection vs target Z | Valuable for FPGA/SoC boards; models for caps are public (vendor S-params/SPICE) | **M** (plane-pair cavity models + lumped caps; KiPI) |
| 10 | Via modeling (stub resonance, backdrill what-if) | High value above ~10 Gb/s | **H** (stub-resonance formula) / **M** (3D) |
| 11 | Post-layout time-domain SI with IBIS buffers (reflections, ringing) | Useful but needs IBIS parsing + circuit sim | **M** (ngspice / PyBERT-like channel convolution; IBIS parsing exists in PyBERT/pyibis-ami) |
| 12 | Plane resonance / EMI hot spots | Useful for EMC pre-compliance | **M** |
| 13 | Electro-thermal IR drop | Nice to have | **M** |
| 14 | SerDes IBIS-AMI, COM, DDR timing wizards | Mostly needed for big SoC boards; heavy validation and closed vendor DLLs | **L** (PyBERT covers part of it) |
| 15 | Full-board hybrid solver (SIwave-class) | Huge effort; commercial tools have decades of tuning | **L** |

Takeaway: ranks 1-5 make up the minimum viable product ("Saturn + Polar + HyperLynx DRC + basic PI, integrated with KiCad"). These are high-value and highly feasible, and no open tool covers them together.

---

## 2. Existing open-source efforts

### 2.1 Active or recent projects (KiCad-centric)

| Project | What it does | License | Stack | Gaps / lessons |
|---|---|---|---|---|
| **KiPI** ([wiki](https://github.com/drewsum/KiPI/wiki)) | PI analyzer: DC IR drop (FEM conduction, electro-thermal), AC PDN impedance, switcher-coupled noise; standalone or KiCad 9/10 IPC plugin; reads KiCad 7-10, ODB++, HyperLynx | GPL-3+ (author adds a no-commercial-inclusion statement) | Python, PySide6 GUI, VTK 3D, SciPy sparse + optional PARDISO, **Triangle** mesher | Closest precedent to our architecture, and it validates PySide6+VTK+SciPy. PI only, no SI/impedance/ERC. Triangle's license forbids commercial use without permission, a trap to avoid. macOS needs a separate Python because KiCad's bundled one is too old. Zones must be filled before reading. Alpha. |
| **Z0 Suite** ([forum](https://forum.kicad.info/t/z0-suite-0-99-3-a-test-release-of-an-impedance-and-signal-analysis-plugin-for-kicad-10/72139)) | KiCad 10 IPC plugin: impedance width/gap synthesis, TDR along tracks, crosstalk, eye diagram, Monte Carlo, IR drop/PDN, board screening (plane-gap crossings) | Not stated; hosted at GitLab egloff-studio/z0-suite **[verify]** | Python (IPC API) | Very similar scope to our MVP, which shows demand. Results unvalidated (by the author's own statement), Linux-only testing, a crash on low-RAM machines. Our differentiators should be validation (golden references) and a headless CLI/CI path. |
| **kicad-rfsim** ([trendshift](https://trendshift.io/repositories/99475)) | KiCad 10 plugin that runs openEMS FDTD for S-params of an RF structure selected in the editor | MIT | Python + openEMS | Good UX pattern (select region, simulate). RF only. Note it is MIT while calling GPL openEMS, presumably via a subprocess or separate install. |
| **gerber2ems** (Antmicro) ([GitHub](https://github.com/antmicro/gerber2ems), [blog](https://antmicro.com/blog/2023/11/open-source-signal-integrity-analysis)) | Gerber + stackup to openEMS 3D model for S-params / impedance | Apache-2.0 **[verify]** | Python, openEMS | Gerber input loses net semantics. It pins a specific openEMS commit, so solver version pinning is a real issue. |
| **kicad2ems** ([forum](https://forum.kicad.info/t/kicad2ems-direct-layout-to-electromagnetic-simulation-tool/72007)), **pcbmodelgen** ([thread](https://www.diyaudio.com/community/threads/kicad-pcbmodelgen-openems-electromagnetic-field-modelling.396496/)), **pyems** ([GitHub](https://github.com/ALEEF02/pyems)) | KiCad/PCB to openEMS model generation; pyems is a higher-level Python openEMS API | GPL-3 (pyems) **[verify others]** | Python | Fragmentation: several people have each written their own "KiCad to openEMS" converter. A shared, tested geometry/mesh layer would let them converge. |
| **EMF Inspector** ([engrXiv preprint](https://engrxiv.org/preprint/view/7226)) | Desktop app: pure-Python .kicad_pcb parser, analytical EMI models (Biot-Savart, quasi-static near field, resonance), EMI heatmap, prioritized rule engine | Open source **[verify license]** | Python | Shows that analytical, first-order models plus rules are well received. The preprint argues no OSS tool combines direct parsing, board-level fields, and an EMI rule engine. |
| **kicad-happy (emc skill)** ([skills.sh](https://skills.sh/aklofas/kicad-happy/emc)), **mcp-pcb-emcopilot** ([glama](https://glama.ai/mcp/servers/d6vmtknf7g)) | LLM-agent-oriented KiCad EMC review: ~44 rule IDs incl. return-path; MCP tools for return path and impedance | Unknown | Python | Rule catalogs worth mining. They are heuristic and agent-driven rather than solver-backed, but they show that an MCP/agent interface is a real consumer of a headless API. |
| **KiCad itself** ([KiCad 10 review](https://techexplorations.com/kicad/kicad-10-review-new-features-high-speed-tuning-variants-and-more/), [dev news](https://forum.kicad.info/t/post-v9-new-features-and-development-news/58848)) | KiCad 10 (released March 2026) adds time-domain tuning profiles, propagation-delay DRC from stackup using closed-form formulas, frequency-dependent dielectric spec; PCB Calculator has closed-form TL calculators; custom DRC rule language | GPL-3 | C++ | No field solver, no return-path/reference-plane checks, no PI. We should *consume* KiCad's tuning profiles and net classes, not duplicate them. We can also generate KiCad custom DRC rules as an output. |

Forum threads that confirm the gap: [Free options for SI like HyperLynx](https://forum.kicad.info/t/free-options-for-signal-integrity-like-hyperlynx/28304), [Future plans for basic SI/PI](https://forum.kicad.info/t/future-plans-for-basic-signal-integrity-si-power-integrity-pi-funtionality/70585), [SI with pcbnew layouts](https://forum.kicad.info/t/signal-integrity-with-pcbnew-layouts/8652), [Export PCB to openEMS](https://forum.kicad.info/t/export-pcb-board-to-openems/7619/31).

### 2.2 General open SI/EM tools to wrap or learn from

| Project | Role | License | Notes |
|---|---|---|---|
| **openEMS** ([site](https://openems.de)) | 3D FDTD (EC-FDTD), Python/Matlab interface | GPL-3 | The de facto open RF solver. Install on Windows is via prebuilt zip; the Python bindings need matching builds. Easiest to use as an external process. |
| **Palace** ([docs](https://awslabs.github.io/palace/v0.16.0/), [README](https://raw.githubusercontent.com/awslabs/palace/main/README.md)) | 3D FEM (MFEM): driven, eigenmode, electro/magnetostatic, time domain; MPI/GPU | Apache-2.0 | Strong for S-params and capacitance/inductance extraction. Linux-first (Spack/Singularity), with JSON config + Gmsh mesh input, so it naturally runs as an external process. |
| **scikit-rf** ([skrf.org](https://scikit-rf.org)) | Networks, Touchstone I/O, de-embedding, TDR, media models | BSD-3 | Use it as the network/result type for all S-parameter outputs. |
| **SignalIntegrity** (Pupalaikis) ([GitHub](https://github.com/Nubis-Communications/SignalIntegrity)) | Python SI: S-param netlists, simulation, de-embedding, virtual probing | GPL-3 **[verify]** | Good reference algorithms and test cases. |
| **PyBERT / pyibisami** ([GitHub](https://github.com/capn-freako/PyBERT)) | SerDes channel/eye simulation, IBIS-AMI | BSD **[verify]** | Possible future SerDes module. |
| **OpenParEM2D / OpenParEM3D** ([openparem.org](https://openparem.org)) | 2D/3D FEM port solvers for TL impedance and S-params | GPL-3 **[verify]** | Validation cross-check for our 2D solver. |
| **atlc** (abandoned, ~2010) ([SourceForge](https://atlc.sourceforge.net)) | 2D finite-difference TL calculator on bitmaps | GPL-2 | Lesson: a bitmap-based geometry UI limited adoption. Its validated test cases are still useful. |
| **TNT-MMTL** (Mayo Clinic, abandoned) ([SourceForge](https://sourceforge.net/projects/mmtl/)) | Multilayer multi-conductor TL BEM solver (RLGC) | GPL-2 | Lesson: good physics, Tcl/Tk UI, no CAD integration, so it died when funding ended. Its BEM algorithm and cross-section library are worth studying. |
| **FastHenry / FastCap / FasterCap** ([FastFieldSolvers](https://www.fastfieldsolvers.com)) | Inductance/capacitance extraction (multipole-accelerated) | MIT-style / LGPL **[verify]** | Useful for loop inductance (via, decap mounting) extraction. |
| **Elmer FEM** ([elmerfem.org](https://www.elmerfem.org)) | Multiphysics FEM incl. electromagnetics, thermal | GPL-2+/LGPL | Possible thermal / static solver backend. |
| **scikit-fem** ([GitHub](https://github.com/kinnala/scikit-fem)) | Pure-Python FEM assembly | BSD-3 | Good fit for our own 2D cross-section and sheet-conduction solvers. |
| **ngspice** | SPICE engine | BSD-3 (mostly) | Time-domain IBIS/W-element simulation. |

### 2.3 Cross-cutting gaps (our opportunity)

1. **No shared board data model.** Each project reparses `.kicad_pcb` or Gerbers. A versioned, tested board/stackup model with ingest adapters is reusable infrastructure on its own.
2. **No validation culture.** Z0 Suite and others state their results are unvalidated. Published golden comparisons (closed-form, Polar app notes, IPC-2141, measured coupons, cross-checks against openEMS/Palace) would set us apart.
3. **No headless/CI story.** Everything is GUI-plugin-first. A CLI that fails CI on ERC violations would be new for KiCad users.
4. **Geometry-only ERC is missing everywhere**, even though it is the cheapest and highest-value item.
5. **License hazards** (Triangle, "no commercial use" riders) fragment the ecosystem. A clean OSI license with clean dependencies matters.

---

## 3. Electrical rule checks (geometry-only)

### 3.1 What commercial tools offer

HyperLynx DRC ships 90+ parameterizable rules and supports custom rules in Python ([Siemens](https://www.siemens.com/de-ch/products/pcb/hyperlynx/electrical-design-rule-check/)). Rule names documented for VX2.10 include Net Crossing Gaps, Vertical Reference Plane Change, Via Stub Length, Signal Supply, Return Path, Net Near Plane Edge, Long Stub, and Reference Plane ([Macnica PDF, Japanese](https://www.macnica.co.jp/business/semiconductor/articles/2dce6e0d85fa6ad685b04390ffefa91b.pdf)). Siemens' blog series describes the EMI/SI rule categories ([part 1](https://blogs.sw.siemens.com/electronic-systems-design/?p=6176), [part 2](https://blogs.sw.siemens.com/electronic-systems-design/2019/04/18/automated-electrical-design-rule-checking-for-faster-time-to-market-part-2/), [finding problems simulation can't](https://blogs.sw.siemens.com/electronic-systems-design/2017/03/16/finding-the-problems-that-simulation-cant/), [comparing post-layout checkers](https://blogs.sw.siemens.com/electronic-systems-design/2018/05/23/are-all-post-layout-pcb-design-rule-checkers-created-the-same/)). Signal Integrity Journal groups them into EMI checks (gap crossing, hole/slot crossing, reference change at vias with stitching vias/caps, signal-to-I/O-supply reference, near plane edge) and SI checks (single-ended and differential impedance continuity incl. neckdowns and nearby fills, via stubs, crosstalk coupled voltage) ([SIJ, "577 design rule checks"](https://signalintegrityjournal.com/articles/577-design-rule-checks-for-high-speed-pcb-design); [iConnect007 review](https://iconnect007.com/article/110736/beyond-design-a-review-of-hyperlynx-drc/110739/design)). Cadence Sigrity Aurora/Allegro and Altium have in-design equivalents for a subset (impedance, return path vision, via stubs/backdrill).

### 3.2 Proposed Open_Analysis ERC rule set (v1)

Every rule is a pure function `(BoardModel, RuleConfig, NetSelection) -> list[Violation]` that runs on precomputed geometry indexes (per-layer shapely `STRtree`, plane polygons, net graph). Priority: **P1** = MVP.

**Return path and reference planes**
| ID | Rule | Method sketch | Pri |
|---|---|---|---|
| RP-001 | Trace crosses a split/gap in its reference plane | Buffer the trace by k*h (dielectric height) and intersect it with the difference between the reference-layer outline and the copper of the reference net(s). Report crossing length and location | P1 |
| RP-002 | Trace over hole/slot/antipad cluster (voided reference) | Same as RP-001 but against drill/antipad voids; flag if the void fraction under the trace footprint exceeds a threshold ("swiss-cheese" antipads in BGA fields) | P1 |
| RP-003 | Reference plane not continuous: trace referenced to a different net partway along its route | Per-segment nearest-plane lookup and a net-change event | P1 |
| RP-004 | Layer transition without return via | At each signal via, find the reference planes above/below the start and end layers. If the nets differ or the planes are not adjacent, require a ground via (or stitching cap if the nets are different) within distance D | P1 |
| RP-005 | Reference to a non-I/O supply (signal referenced to a rail other than its driver's supply or GND) | Needs pin-to-supply mapping from the netlist/IBIS; a heuristic version uses user-declared net classes | P2 |
| RP-006 | Stitching-cap check for plane-to-plane reference changes (distance to the nearest cap bridging the two plane nets) | Component lookup by connected nets | P2 |
| RP-007 | Microstrip without an adjacent plane at all (e.g. 2-layer boards, layer with no fill under it) | Plane coverage under trace | P1 |

**Edges and proximity**
| ID | Rule | Pri |
|---|---|---|
| ED-001 | Trace too close to reference-plane edge (default ≥ 3h or 20 mil-ish, configurable) | P1 |
| ED-002 | High-speed trace / via near board edge (EMI, fringing) | P1 |
| ED-003 | Plane edge not stitched (via fence spacing > λ/20 at f_max, or user value), for RF/ground pours | P2 |
| ED-004 | GND pour slivers / acid traps / isolated (floating) copper islands near HS nets (antennas) | P2 |

**Impedance and discontinuities**
| ID | Rule | Pri |
|---|---|---|
| IM-001 | Segment width differs from the net-class target width for its layer (impedance-controlled nets) | P1 |
| IM-002 | Computed Z0 per segment outside tolerance (needs impedance solver, cached by cross-section key) | P1 |
| IM-003 | Neckdown length exceeding max (in pin fields) | P1 |
| IM-004 | Coplanar copper (pour/other net) closer than X·h, which changes Z (CPW effect unintended) | P2 |
| IM-005 | Stubs: T-junctions, unused pad stubs, test-point stubs longer than L_max (or than t_rise·v/10) | P1 |
| IM-006 | Impedance mismatch at layer change (different Z0 on different layers for the same net) | P2 |
| IM-007 | Pad/via capacitance discontinuity (large pads on HS nets, missing reference anti-pad on RF pads) | P3 |

**Differential pairs**
| ID | Rule | Pri |
|---|---|---|
| DP-001 | Uncoupled length (gap exceeds nominal + tol) total and per-section | P1 |
| DP-002 | Intra-pair skew (length and time domain using per-layer Er_eff) | P1 |
| DP-003 | Gap variation along the coupled section | P1 |
| DP-004 | Pair members on different layers, or asymmetric via transitions | P2 |
| DP-005 | Missing symmetric GND return vias at pair layer transitions | P2 |

**Vias**
| ID | Rule | Pri |
|---|---|---|
| VI-001 | Via stub length > max (from stackup: through-via length minus used span; backdrill-aware); also report stub quarter-wave resonance f = c/(4·L·sqrt(Er_eff)) against f_max | P1 |
| VI-002 | Via count per HS net > N | P1 |
| VI-003 | Non-functional pads retained on HS vias | P3 |
| VI-004 | Antipad size vs target (via Z) | P3 |

**Crosstalk and spacing**
| ID | Rule | Pri |
|---|---|---|
| XT-001 | Parallel coupled length > L_max at spacing < S (aggressor/victim classes), same and adjacent (broadside) layers | P1 |
| XT-002 | Estimated NEXT/FEXT from 2D coupled-line solve vs budget | P2 |
| XT-003 | Signals routed on adjacent signal layers without a plane between (broadside, orthogonal check) | P2 |

**Length/timing** (complements KiCad 10's tuning, does not replace it)
| ID | Rule | Pri |
|---|---|---|
| LT-001 | Group skew (bus match groups) in time domain using field-solved delays | P2 |
| LT-002 | Max length / max delay per net class | P2 |

**Power / decoupling (geometry-only)**
| ID | Rule | Pri |
|---|---|---|
| PD-001 | Decap distance to IC power pin (path length incl. via) > max | P1 |
| PD-002 | Decap mounting loop: via-to-pad distance, vias per pad | P2 |
| PD-003 | Power net narrow neck (min width along the current path vs current) | P2 |
| PD-004 | Plane-to-plane overlap / plane pair spacing | P3 |

**RF-specific**
| ID | Rule | Pri |
|---|---|---|
| RF-001 | Via fence pitch along CPWG/RF lines > λ/10 (or user) at f_max | P1 |
| RF-002 | Ground under RF pad / connector launch keep-out mismatch | P2 |
| RF-003 | Sharp 90° corners on RF nets (mitering) | P2 |
| RF-004 | Copper pour too close to an RF trace (unintended CPW coupling) | P2 |

Design notes:
- Rules need **net intent**: which nets are high-speed, diff-pair membership, reference nets, f_max / rise time. Source it from KiCad net classes and diff pair naming (`_P/_N`, `+/-`), plus a project-level `open_analysis.toml` for overrides. Infer by default, override explicitly.
- Output violations as structured records (rule ID, severity, nets, layer, geometry/marker location, measured value, limit, fix hint). Export as SARIF/JSON (for CI), KiCad `.kicad_dru` suggestions, and KiCad markers via the IPC API where possible.
- Rules should be **plugins** (entry points), so contributors can add rules without touching the core. HyperLynx's Python custom rules show users value this.

---

## 4. Tech stack options

### 4.1 Workload profile (what is actually hot)

| Workload | Typical size | Hot spot | Library solution |
|---|---|---|---|
| Parse `.kicad_pcb` (S-expr) | 1-50 MB | Python parsing is ~1-5 s on big boards | A fast S-expr parser (tokenizer in Rust or `sexpdata`-style with care); kiutils/kicad-python IPC as alternatives |
| Polygon ops (union per layer/net, buffers, intersections, clearance) | 10^4-10^6 segments | GEOS calls | **shapely 2.x** (vectorized GEOS, releases GIL) + STRtree. This is fast enough for ERC |
| 2D cross-section solve | 10^3-10^5 DOF, many repeated cross-sections | Assembly + small sparse solves | scikit-fem/scipy, or our own BEM; **cache by cross-section key** (geometry hash). Embarrassingly parallel |
| Sheet-conduction PI (DC IR) | 10^5-10^7 DOF | Meshing + sparse solve | gmsh (2D), scipy `spsolve`/`splu`, pypardiso (x86), PETSc via petsc4py for big ones; AMG via pyamg |
| AC PDN (cavity + lumped) | Many frequencies x 10^4-10^6 DOF | Repeated complex sparse solves | Reduced-order models, factor reuse |
| 3D EM (openEMS / Palace) | 10^6-10^9 cells/DOF | External solver | Run as an external process; we own geometry export + post-processing |
| Visualization: 2D board | 10^5-10^6 polygons | GPU rendering | deck.gl/WebGL, or pyqtgraph/VTK |
| Visualization: 3D board + field heatmaps | 10^6 triangles | GPU | VTK/pyvista, or vtk.js/Three.js |

Conclusion: the compute hot spots are already handled by compiled libraries (GEOS, gmsh, SuiteSparse/MKL, openEMS/Palace). A custom native core helps mainly with **parsing speed** and **custom kernels** (e.g. BEM Green's-function integrals, segment-wise impedance profile loops). Those can be added as targeted Rust (PyO3) or numba extensions later.

### 4.2 Options compared

**(a) Python core + PySide6 (Qt) desktop, pyqtgraph + VTK/pyvista (pyvistaqt)**
- Pros: one language. Mature scientific desktop pattern (KiPI uses PySide6 + VTK + SciPy, [KiPI](https://github.com/drewsum/KiPI/wiki)). In-process access to numpy arrays, so zero-copy plotting and no serialization. pyqtgraph is fast for 2D plots; VTK handles large 3D meshes and field data natively. Works offline.
- Cons: packaging is heavy (PyInstaller/Briefcase bundles of 300-600 MB with Qt + VTK; macOS notarization and code signing). Qt GUI code is harder for casual contributors and for parallel agents to test (needs pytest-qt and an offscreen platform). Custom 2D board rendering of 10^6 polygons in QGraphicsView is slow, so you end up writing VTK or OpenGL anyway. PySide6 is LGPL-3: fine for any OSS license if dynamically linked.
- Testing: GUI tests are brittle; logic must be kept out of widgets.

**(b) Python core + local web UI (FastAPI/WebSocket + React + deck.gl for 2D, Three.js or vtk.js for 3D, Plotly/uPlot/ECharts for plots)**
- Pros: a strong 2D renderer (deck.gl handles millions of polygons in WebGL, with picking and layers). The UI is decoupled by an HTTP/JSON API that is also the scripting/automation/MCP surface. The largest contributor pool (frontend devs) and the best fit for AI agents (typed API contract, Storybook, Playwright tests). The same UI can later be hosted remotely (shared results, CI artifacts as static HTML reports). Packaging: a Python wheel + prebuilt static frontend; launch as `oa serve` opening the browser, or wrap in **pywebview** or **Tauri** for a desktop feel.
- Cons: two toolchains (Python + Node/TypeScript). Large arrays need efficient transport (binary WebSocket frames / Apache Arrow / GLB / binary STL). Local server security (bind to 127.0.0.1, token). 3D field visualization is less turnkey than VTK (vtk.js exists but is less complete). Offline use requires bundling all JS (no CDN).

**(c) Rust or C++ core + Python bindings**
- Pros: speed, single-binary distribution, memory safety (Rust). Good for parsers, geometry kernels, custom BEM.
- Cons: much smaller contributor pool among SI/RF engineers, who mostly write Python/Matlab. Slower iteration on physics. Rust geometry (`geo`, `i_overlay`) is less mature than GEOS for robust boolean ops on large data. C++ means CMake + platform builds + ABI hassle. The heavy solvers are external anyway. Recommendation: **not as the core**; use it for opt-in accelerator crates (`oa-native`) built with maturin as abi3 wheels.

**(d) KiCad plugin front end (IPC API) in addition to a standalone app**
- KiCad 9+ IPC API: protobuf over a local socket, official `kicad-python` bindings; plugins run in a KiCad-managed venv; currently PCB editor only; considered equivalent to the old Action Plugins in 9.0 ([For add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/), [forum thread](https://forum.kicad.info/t/kicad-9-0-python-api-ipc-api/57236), [kicad-python docs](https://docs.kicad.org/kicad-python-main/), [FOSDEM 2025 talk](https://archive.fosdem.org/2025/schedule/event/fosdem-2025-6802-bringing-a-new-api-to-kicad/)). kicad-python is MIT per packaging metadata **[verify]**. Out-of-process, so no GPL linking question.
- Known friction: plugin loading bugs on Windows in early 9.0, PCM installs of IPC plugins crashing (per the forum), and the old SWIG API slated for removal. KiPI notes data quirks before 9.0.3.
- Pros: a zero-friction entry point (button in pcbnew), live board (no save/fill step), and the ability to cross-probe (select a net in the analysis view and highlight it in KiCad).
- Cons: depends on the user's KiCad version. The plugin venv must install our heavy scientific stack, which is fragile. Fix: make the plugin **thin**. It only extracts the board through IPC and launches or talks to the standalone app (or the `oa` CLI) through our own API.

### 4.3 Packaging matrix

| Target | (a) Qt | (b) Web | Notes |
|---|---|---|---|
| `pip install open-analysis` / `uv tool install` | Yes (PySide6 + vtk wheels exist for all 3 OS) | Yes (static UI inside the wheel) | The primary channel for engineers |
| conda-forge | Yes | Yes | Easiest way to get openEMS/Palace/PETSc alongside |
| Windows installer | PyInstaller/Briefcase + NSIS/MSIX | Same, or Tauri shell + embedded Python (python-build-standalone) | Code signing cost |
| macOS .app | Briefcase; notarization required | pywebview / Tauri | |
| Linux | AppImage/Flatpak | Same | |
| External solvers | openEMS: official Windows builds, Linux distro/source builds; Palace: Spack/Singularity, Linux-first | | Detect on PATH or use containers. Never block the core install on them |

### 4.4 Verdict on stack

Use **(b) with the API as the contract, plus (d) as a thin adapter**. Keep (a) as a credible fallback if the team prefers a single language. Rationale:
1. The headless core + CLI + service API is needed anyway for CI, agents/MCP, and the KiCad plugin. Once that API exists, the web UI is just another client, and it is the best 2D board renderer available.
2. Parallel development: the frontend can be built against a mock API from an OpenAPI schema while solver work proceeds in Python.
3. Results as self-contained HTML reports (same React components, static build) are a valuable deliverable, e.g. as CI artifacts.

Qt (a) remains reasonable. If chosen, still enforce the same core/API separation so the GUI is only a client.

---

## 5. Project scaffolding best practices

### 5.1 License

| Dependency | License | Implication if imported in-process |
|---|---|---|
| KiCad (via IPC API) | GPL-3 (KiCad), kicad-python MIT **[verify]** | Separate process: no constraint |
| openEMS | GPL-3 ([openems.de](https://openems.de)) | Importing its Python module (CSXCAD/openEMS bindings) in-process makes the combined distribution GPL-3. Subprocess invocation does not |
| Palace | Apache-2.0 ([README](https://raw.githubusercontent.com/awslabs/palace/main/README.md)) | Any license; Apache-2 is compatible with GPL-3 (not GPL-2-only) |
| gmsh | GPL-2-or-later with narrow exceptions for specific libraries; integration into distributed closed-source software is not allowed ([license exception](https://scancode-licensedb.aboutcode.org/gmsh-exception.LICENSE), [mailing list](https://onelab.info/pipermail/gmsh/2019/013215.html)) | Using the `gmsh` Python API in-process implies GPL-compatible distribution of the combined work; a permissive project can still depend on it, but the bundle is effectively GPL |
| scikit-rf | BSD-3 | Any |
| shapely / GEOS | BSD-3 / LGPL-2.1 | Any |
| numpy / scipy | BSD-3 | Any |
| scikit-fem | BSD-3 | Any |
| PySide6 / Qt | LGPL-3 (or commercial) | Any OSS license; dynamic linking |
| VTK / pyvista | BSD-3 / MIT | Any |
| React / deck.gl / Three.js / Plotly.js | MIT | Any |
| Triangle (J. Shewchuk) | Non-OSI; no commercial use without permission ([KiPI notes](https://github.com/drewsum/KiPI/wiki)) | **Avoid** as a default dependency |
| pypardiso / MKL | BSD / Intel Simplified Software License | Optional extra only |
| PETSc / MFEM | BSD-2 / BSD-3 | Any |

Options:
- **GPL-3.0-or-later for everything.** Matches KiCad and lets us import gmsh and openEMS bindings in-process with no analysis. Commercial users can still *use* it freely; they cannot embed it in closed products. Simple.
- **Apache-2.0 (or MIT) core, with GPL integrations as separately distributed optional packages** (`oa-solver-openems`, `oa-mesh-gmsh`) that talk via subprocess or are installed by the user. This maximizes reuse (fabs and EDA vendors could embed the impedance solver and ERC), and Apache-2 gives a patent grant. The cost: real discipline about the import boundary, plus a permissive mesher choice for core 2D work (e.g. our own structured/Delaunay meshing, `meshpy`, which wraps Triangle and so has the same problem, or scikit-fem's built-in meshes, or CGAL, which is GPL). This is harder.

**Recommendation: GPL-3.0-or-later, monorepo-wide**, with a `LICENSES/` folder and REUSE/SPDX headers. Reasons: gmsh is the obvious mesher for 2D/3D work, openEMS is the obvious RF solver, the target community (KiCad) is GPL, and the realistic contributors are engineers, not vendors. Keep the **data model schema and file formats** (JSON Schema, example files) under **CC-BY-4.0 or MIT** so other tools can interoperate freely. Revisit only if a concrete commercial-embedding partner appears. Require DCO sign-off (not a CLA) for contributions.

### 5.2 Monorepo layout (uv workspace)

```
Open_Analysis/
  pyproject.toml              # uv workspace root; dev deps; ruff/mypy/pytest config
  uv.lock
  packages/
    oa-model/                 # core data model (pure Python, pydantic v2/attrs; no heavy deps)
    oa-ingest-kicad/          # .kicad_pcb parser + IPC-API reader -> oa-model
    oa-ingest-odb/            # (later) ODB++/IPC-2581; Altium via these
    oa-geometry/              # shapely layers, net graph, spatial index, cross-section extraction
    oa-mesh/                  # gmsh wrappers, 2D/3D mesh generation, mesh I/O (meshio)
    oa-solve-impedance/       # closed-form + 2D quasi-static FEM/BEM, RLGC, loss models
    oa-solve-pi/              # DC IR drop, AC PDN
    oa-solve-em/              # adapters: openEMS, Palace (export, run, import results)
    oa-solve-si/              # (later) channel/TDR/IBIS, uses scikit-rf, ngspice
    oa-erc/                   # rule engine + built-in rules
    oa-results/               # result containers, HDF5/zarr + Touchstone + SARIF writers
    oa-jobs/                  # job runner, caching, process pool, external solver supervision
    oa-server/                # FastAPI app: REST + WebSocket, OpenAPI schema
    oa-cli/                   # `oa` command (typer): ingest, erc, impedance, pi, serve, report
    oa-kicad-plugin/          # thin IPC plugin (metadata.json, minimal deps)
  web/                        # React + TypeScript (Vite), deck.gl, three.js/vtk.js, plots
    src/api/                  # generated TS client from OpenAPI (orval / openapi-typescript)
  tests/
    golden/                   # reference boards + expected numerical results
    boards/                   # small open KiCad test boards (licensed CC0/CC-BY)
  docs/                       # MkDocs Material (or Sphinx) + research/
  .github/workflows/
```

Rules: `oa-model` depends on nothing heavy. Each solver depends only on `oa-model`, `oa-geometry`, `oa-results`, and optionally `oa-mesh`, never on GUI/server. The server and CLI depend on everything. An import-linter contract ([import-linter](https://github.com/seddonym/import-linter)) enforces the layering in CI.

Tooling: **uv** (workspaces, lockfile, `uv tool install`), **hatchling** or `uv_build` backend, **ruff** (lint + format), **mypy** or **pyright** (strict on `oa-model`), **pytest** + `pytest-xdist` + **hypothesis** (property tests for geometry), **pre-commit**, Node via **pnpm** for `web/`.

### 5.3 Testing with golden numerical references

- **Tiered tolerances:** analytic cases (e.g. a coax or a stripline with an exact conformal-map solution) to within 0.5%; published references (IPC-2141A, Wadell formulas, Polar app notes, Saturn values, literature tables) to within 1-3%; cross-solver checks (our 2D vs OpenParEM2D/openEMS/Palace) to within 2-5%; measured coupons, with uncertainty documented.
- **Golden files** stored as small JSON/CSV/Touchstone under `tests/golden/<case>/` with a `case.toml` (inputs, source citation, tolerance). Use a `pytest` plugin/fixture `assert_close_to_golden(result, case)`, and regenerate only via an explicit `--update-golden` flag plus review.
- **Convergence tests:** mesh refinement should show monotonic convergence. Record the observed order.
- **ERC tests:** tiny synthetic boards (generated programmatically into `oa-model`, no KiCad needed), one per rule with positive and negative cases, plus snapshot tests on real open boards (e.g. KiCad demo boards, open hardware like the Antmicro or Olimex boards, license permitting).
- **Slow/external tests** (openEMS, Palace) marked `@pytest.mark.external` and run nightly in containers.
- **Benchmarks:** `pytest-benchmark` or `asv` to track ingest/ERC/solve performance over time.

### 5.4 CI

- GitHub Actions matrix: {ubuntu, windows, macos} x {py3.11, 3.12, 3.13}: lint, type check, unit + golden tests, import-linter, web build + Vitest + Playwright smoke.
- Nightly: external solver tests in Docker (openEMS, Palace images), benchmarks, KiCad IPC integration test using KiCad's headless (`kicad-cli`) plus the IPC API in a container **[verify headless IPC availability]**.
- Release: build wheels (`uv build`), plus native abi3 wheels via `maturin`/`cibuildwheel` if `oa-native` exists. Publish to PyPI with trusted publishing; conda-forge feedstock; PCM package for the plugin.
- Dog-food: `oa erc` as a GitHub Action, emitting SARIF so violations show as code-scanning annotations.

### 5.5 Docs

MkDocs Material + mkdocstrings (API reference from docstrings), or Sphinx + MyST if heavy math/notebooks are needed. Include a **"Theory" section per solver** (equations, assumptions, validity limits, references) and a **"Validation" section auto-generated from the golden test results** (tables and plots built in CI). Example gallery via mkdocs-gallery or sphinx-gallery. ADRs in `docs/adr/` for the decisions in this report.

---

## 6. Recommendation

### 6.1 Stack

- **Core:** Python 3.11+, numpy/scipy, shapely 2, scikit-fem (own 2D solvers), gmsh (meshing), scikit-rf (networks), pyamg/pypardiso/petsc4py optional, xarray + HDF5/zarr for field results.
- **External EM:** openEMS and Palace via adapters running as **subprocesses** (container or PATH), so the core installs even without them.
- **Service:** FastAPI + WebSocket for job progress and binary data (Arrow/GLB). OpenAPI schema generated and committed.
- **UI:** React + TypeScript + Vite; deck.gl for the 2D board and heatmaps; three.js (or vtk.js) for 3D; Plotly.js or uPlot for plots. Launched via `oa serve` (browser) or pywebview for a desktop window. Static HTML report export.
- **KiCad:** thin IPC plugin (kicad-python only) that snapshots the board into `oa-model` JSON and hands it to a running `oa` server (or launches it), with cross-probing back to KiCad.
- **Native acceleration:** only after profiling, as an optional `oa-native` Rust crate (PyO3/maturin) for the S-expression parser and BEM kernels.
- **License:** GPL-3.0-or-later; schema/examples CC-BY-4.0.

### 6.2 Module decomposition and interfaces

```
            +-------------+        +------------------+
 KiCad ---> | oa-kicad-   | -----> |                  |  <---- web UI (React)
 (IPC)      | plugin      |  HTTP  |    oa-server     |  <---- oa-cli / CI / MCP agents
            +-------------+        +--------+---------+
                                            | python API
                                   +--------v---------+
                                   |     oa-jobs      |  (queue, cache, process pool,
                                   +--+---+---+---+---+   external solver supervisor)
                                      |   |   |   |
         +-------------+   +----------+   |   |   +-----------+
         | oa-erc      |   | oa-solve- |  |   |  oa-solve-em|  (openEMS/Palace adapters)
         | (rules)     |   | impedance |  | oa-solve-pi    |
         +------+------+   +-----+-----+  +-------+--------+
                |                |                |
            +---v----------------v----------------v---+
            |   oa-geometry  (+ oa-mesh for FEM)       |
            +--------------------+---------------------+
                                 |
            +--------------------v---------------------+      +------------+
            |   oa-model  (Board, Stackup, Nets, ...)  | <--- | oa-ingest-*|
            +------------------------------------------+      +------------+
                         oa-results (shared result types, writers)
```

**Interface contracts** (each one is a small, typed, versioned surface so teams/agents can work independently):

1. **`oa-model` - Board Data Model (the keystone).** Immutable, serializable (JSON Schema, versioned `schema_version`), with units in SI metres internally.
   - `Board{ outline: Polygon, stackup: Stackup, layers: list[Layer], nets: dict[NetId, Net], netclasses, diff_pairs: list[DiffPair], components: list[Component], pads, vias: list[Via], tracks: list[Track], arcs, zones: list[Zone (filled polygons per layer)], drills, constraints: DesignIntent, source: SourceInfo }`
   - `Stackup{ layers: [CopperLayer{thickness, roughness} | DielectricLayer{h, material: Material{er(f), tand(f), model: "DjordjevicSarkar"|"const"}}], finish, soldermask }`
   - `DesignIntent{ hs_nets, ref_nets, f_max, rise_time, target_impedance per netclass/layer, current per power net }`, populated from KiCad net classes plus `open_analysis.toml`.
   - Geometry primitives are plain coordinate arrays (no shapely objects in the model) to keep the model dependency-free and serializable.
   - Contract test: every ingest adapter must round-trip the shared fixtures and pass `oa_model.validate(board)`.

2. **`oa-ingest-*` - Ingest adapters.** `def load(path_or_handle, options) -> Board`, registered via entry point group `open_analysis.ingest` with a `can_read(path) -> bool` probe. Altium arrives later as another adapter (directly or through ODB++/IPC-2581).

3. **`oa-geometry` - Derived geometry service.** `BoardGeometry.from_board(board)` builds per-layer, per-net shapely unions, plane polygons, STRtree indexes, a net connectivity graph (networkx), the reference-plane map, and helpers like `cross_section_at(track_segment) -> CrossSection` and `segments_for_net(net) -> list[RoutedSegment]`. It is a pure function of the `Board`, so it is cacheable by board hash.
   - `CrossSection` is the key interface between geometry and the impedance solver: a 2D description (conductors as rectangles/trapezoids with etch factor, dielectric layers, nearby ground/coplanar copper within a window), **hashable** for caching.

4. **Solver interface (all solvers).**
   ```python
   class Analysis(Protocol):
       kind: ClassVar[str]                  # "impedance.2d", "pi.dc", "em.openems", ...
       Config: type[BaseModel]              # pydantic schema -> UI form + validation
       def plan(self, geom: BoardGeometry, cfg) -> list[Task]   # split into cacheable tasks
       def run(self, task: Task, ctx: RunContext) -> Result      # ctx: progress, cancel, scratch dir, threads
   ```
   Registered via the `open_analysis.analyses` entry point. The pydantic `Config` schema produces the UI form automatically (JSON Schema to a React form), so adding a solver needs no frontend work.
   - Lower-level pure APIs stay usable without the framework, e.g. `oa_solve_impedance.solve(cross_section, freqs) -> RLGC` (the most reused function in the project).

5. **`oa-results` - Result types.** `ScalarTable` (pandas/Arrow), `Network` (scikit-rf), `FieldOnMesh` (mesh + per-node/cell values; xarray/zarr), `NetProfile` (impedance vs distance along a net), `Violation` (ERC). Writers: JSON, HDF5/zarr, Touchstone, SARIF, CSV, HTML report. The UI renders by result **type**, not by solver, so new solvers get visualization for free.

6. **`oa-erc` - Rule plugin interface.**
   ```python
   class Rule(Protocol):
       id: ClassVar[str]; category: ClassVar[str]; Config: type[BaseModel]
       def check(self, geom: BoardGeometry, intent: DesignIntent, cfg) -> Iterable[Violation]
   ```
   Registered via the `open_analysis.rules` entry point. Rules may call `oa_solve_impedance.solve` (cached) but never the GUI.

7. **`oa-jobs` - Execution.** Content-addressed cache (`hash(inputs, solver version, config)`), local process pool, cancellation, progress events, and an external-process supervisor for openEMS/Palace (container or native). This is the boundary that keeps GPL/heavy solvers out of process when desired.

8. **`oa-server` / `oa-cli`.** Thin layers: the CLI calls the Python API directly; the server exposes `POST /projects`, `GET /boards/{id}/layers` (binary geometry tiles), `POST /analyses` (validated against `Config` schema), `GET /jobs/{id}` + WebSocket progress, `GET /results/{id}`. The OpenAPI schema is committed and a TS client is generated, so frontend and backend can evolve in parallel against the schema.

9. **`oa-kicad-plugin`.** Depends only on `kicad-python` + `httpx`. Snapshots the board through IPC into `oa-model` JSON (via a minimal adapter that has no scientific deps), posts it to the server, and handles cross-probe commands (select or highlight item IDs).

### 6.3 Suggested build order (parallelizable)

1. `oa-model` schema + fixtures (blocks everyone; small; do first, freeze v0.1 quickly).
2. In parallel: `oa-ingest-kicad`; `oa-geometry`; `oa-solve-impedance` (closed-form first, then 2D FEM) with golden tests; `web/` 2D board viewer against fixture JSON; `oa-results` + CLI skeleton.
3. `oa-erc` P1 rules (RP-001/002/003/004/007, ED-001/002, IM-001/002/003/005, DP-001/002/003, VI-001/002, XT-001, PD-001, RF-001) and SARIF output. This is the first shippable release: "`oa erc board.kicad_pcb`".
4. Per-net impedance profile view; DC IR drop (`oa-solve-pi`); KiCad plugin.
5. openEMS/Palace adapters for local 3D structures; AC PDN; insertion loss; crosstalk quantification.

### 6.4 Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Accuracy and credibility (users compare against Polar/HyperLynx) | Fatal if numbers are wrong | Golden-reference suite and a published validation page from day one. Show solver assumptions and validity range in the UI |
| KiCad API churn (IPC API still maturing; zone fill/pad quirks; plugin venv issues on Windows/macOS) | Plugin breakage | Thin plugin; file-based ingest as primary path; CI against KiCad 9 and 10 |
| External solver installation (openEMS Windows builds, Palace Linux-first/MPI) | Users cannot run EM | Make EM optional; container recipes; clear "doctor" command (`oa doctor`) |
| License contamination (Triangle, "no commercial use" riders, GPL in a permissive core) | Legal and adoption | GPL-3 project-wide; dependency license audit in CI (`pip-licenses`/REUSE lint) |
| Scope creep toward a SIwave-class full-board solver | Never ships | Hold the MVP to ranks 1-5; full-wave only for local structures |
| Two-toolchain overhead (Python + TS) | Contributor friction | Generated API clients, mock server, clear CODEOWNERS per package; Qt fallback possible because the core is UI-agnostic |
| Design-intent inference is wrong (which nets are HS, which plane is the reference) | Noisy ERC, so users ignore it | Explicit `open_analysis.toml` overrides, waivers with reasons, severity tuning, and a conservative default HS-net selection |
| Performance on large boards (10^6 segments; Python loops) | Slow ERC/profile | Vectorized shapely, caching by cross-section hash, profiling-driven Rust kernels |
| Overlap with emerging projects (Z0 Suite, KiPI, kicad-rfsim) | Duplicate effort | Reach out early; offer `oa-model` + solvers as shared libraries; consider importing KiPI-style PI as a collaboration rather than rewriting it |

---

## Sources (consolidated)

- Siemens HyperLynx EDRC: https://www.siemens.com/de-ch/products/pcb/hyperlynx/electrical-design-rule-check/ ; fact sheet https://resources.sw.siemens.com/zh-TW/fact-sheet-electrical-rule-checks/ ; blog part 1 https://blogs.sw.siemens.com/electronic-systems-design/?p=6176 ; part 2 https://blogs.sw.siemens.com/electronic-systems-design/2019/04/18/automated-electrical-design-rule-checking-for-faster-time-to-market-part-2/ ; https://blogs.sw.siemens.com/electronic-systems-design/2017/03/16/finding-the-problems-that-simulation-cant/ ; https://blogs.sw.siemens.com/electronic-systems-design/2018/05/23/are-all-post-layout-pcb-design-rule-checkers-created-the-same/
- HyperLynx DRC rule list (Macnica, JP): https://www.macnica.co.jp/business/semiconductor/articles/2dce6e0d85fa6ad685b04390ffefa91b.pdf
- PCD&F, HyperLynx DRC announcement: https://pcdandf.com/pcdesign/index.php/editorial/menu-new-indust-prod/8170-mentor-graphics-adds-design-rule-checking-to-hyperlynx-suite
- iConnect007 HyperLynx DRC review: https://iconnect007.com/article/110736/beyond-design-a-review-of-hyperlynx-drc/110739/design
- Signal Integrity Journal, design rule checks for high-speed PCB: https://signalintegrityjournal.com/articles/577-design-rule-checks-for-high-speed-pcb-design
- Polar Si9000: https://polarinstruments.com/products/si/Si9000.html ; brochure https://www.polarinstruments.com/pdf/brochures/Si9000_brochure.pdf ; Speedstack Si https://www.polarinstruments.com/products/stackup/Speedstack_Si.html ; insertion loss https://www.polarinstruments.com/products/insertion_loss.html
- Altium high-speed design: https://www.altium.com/altium-designer/features/high-speed-design ; impedance KB https://www.altium.com/cn/documentation/knowledge-base/altium-designer/validate-transmission-impedance-computed-in-layer-stack ; Simberian partnership https://resources.altium.com/p/high-speed-design-features-growing-forward-with-altium-and-simberian-partnership ; SI whitepaper https://resources.altium.com/p/adopting-signal-integrity-in-your-high-speed-design-process
- Ansys SIwave: https://www.ansys.com/products/electronics/ansys-siwave
- Cadence Sigrity: https://www.cadence.com/en_US/home/tools/system-analysis/signal-and-power-integrity.html
- Saturn PCB Toolkit: https://saturnpcb.com/saturn-pcb-toolkit/
- KiPI: https://github.com/drewsum/KiPI/wiki
- Z0 Suite: https://forum.kicad.info/t/z0-suite-0-99-3-a-test-release-of-an-impedance-and-signal-analysis-plugin-for-kicad-10/72139
- kicad-rfsim: https://trendshift.io/repositories/99475
- gerber2ems: https://github.com/antmicro/gerber2ems ; https://antmicro.com/blog/2023/11/open-source-signal-integrity-analysis
- kicad2ems: https://forum.kicad.info/t/kicad2ems-direct-layout-to-electromagnetic-simulation-tool/72007 ; pyems https://github.com/ALEEF02/pyems ; pcbmodelgen thread https://www.diyaudio.com/community/threads/kicad-pcbmodelgen-openems-electromagnetic-field-modelling.396496/
- EMF Inspector preprint: https://engrxiv.org/preprint/view/7226
- kicad-happy EMC: https://skills.sh/aklofas/kicad-happy/emc ; mcp-pcb-emcopilot https://glama.ai/mcp/servers/d6vmtknf7g
- KiCad forum SI threads: https://forum.kicad.info/t/free-options-for-signal-integrity-like-hyperlynx/28304 ; https://forum.kicad.info/t/future-plans-for-basic-signal-integrity-si-power-integrity-pi-funtionality/70585 ; https://forum.kicad.info/t/signal-integrity-with-pcbnew-layouts/8652 ; https://forum.kicad.info/t/export-pcb-board-to-openems/7619/31
- KiCad 10: https://techexplorations.com/kicad/kicad-10-review-new-features-high-speed-tuning-variants-and-more/ ; https://forum.kicad.info/t/post-v9-new-features-and-development-news/58848 ; https://alternativeto.net/news/2026/3/kicad-10-released-with-track-tuning-revamp-windows-dark-mode-design-variants-and-more
- KiCad IPC API: https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/ ; https://forum.kicad.info/t/kicad-9-0-python-api-ipc-api/57236 ; https://docs.kicad.org/kicad-python-main/ ; https://gitlab.com/kicad/code/kicad-python ; https://archive.fosdem.org/2025/schedule/event/fosdem-2025-6802-bringing-a-new-api-to-kicad/ ; license metadata https://gpo.zugaina.org/dev-python/kicad-python
- Palace: https://awslabs.github.io/palace/v0.16.0/ ; https://raw.githubusercontent.com/awslabs/palace/main/README.md
- gmsh license: https://scancode-licensedb.aboutcode.org/gmsh-exception.LICENSE ; https://onelab.info/pipermail/gmsh/2019/013215.html ; https://onelab.info/pipermail/gmsh/2019/013347.html ; https://gitlab.onelab.info/lrp/gmsh/-/commit/d4b25ed90cd6d5900c06a05e30960fc44a7e499d.diff
- openEMS: https://openems.de ; scikit-rf: https://scikit-rf.org ; scikit-fem: https://github.com/kinnala/scikit-fem ; SignalIntegrity: https://github.com/Nubis-Communications/SignalIntegrity ; PyBERT: https://github.com/capn-freako/PyBERT ; OpenParEM: https://openparem.org ; atlc: https://atlc.sourceforge.net ; TNT-MMTL: https://sourceforge.net/projects/mmtl/ ; FastFieldSolvers: https://www.fastfieldsolvers.com ; Elmer: https://www.elmerfem.org ; import-linter: https://github.com/seddonym/import-linter
