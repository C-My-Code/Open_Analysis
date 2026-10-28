# 03 - Signal Integrity Analysis: Scope, Methods, Building Blocks, Roadmap

Status: research draft, 2026-10-27
Scope: signal integrity (SI) for KiCad boards: what to simulate and how. Data ingest, 2D impedance/RLGC solvers, PI, and 3D full-wave are covered in other reports. This one uses their outputs: per-segment RLGC(f) matrices, via models, and S-parameters.

---

## 1. Features high-speed designers expect

These are the features HyperLynx SI/LineSim/BoardSim, Ansys SIwave/SI Wave-PI, Cadence Sigrity and Keysight ADS/SIPro put in front of users. They split into four groups.

### 1.1 Topology simulation (pre-layout and post-layout)
- **Pre-layout ("LineSim"-style) what-if.** The user draws an abstract topology: driver, line segments with Z0/delay or stackup+width, vias, stubs, terminations, receivers. They sweep termination values, stub lengths and stackup. This is the cheapest feature to build and the most used for parallel buses such as DDR address/command fly-by and clock trees.
- **Post-layout ("BoardSim"-style) extraction.** Each routed net is pulled from the PCB as a tree of segments. Each segment carries its layer, width, length, and coupled neighbours. Vias and pads are discretes. The same solver back-end then runs on it.
- **Batch/net-sweep mode.** Every net in a net class is simulated with default IBIS models. The output is a table of overshoot, undershoot, ringback, settling, monotonicity and flight time with pass/fail flags. HyperLynx "batch mode" is the reference point. Designers rely on it for screening.

### 1.2 Reflections and ringing
- Metrics: overshoot/undershoot against absolute-max, ringback into the threshold region, non-monotonic edges, settling time, and receiver noise margin (Vih/Vil against waveform).
- Termination advisor: suggest series R, parallel/Thevenin, AC (RC) or diode termination from Z0, driver impedance and line delay. The well-known closed-form rules apply, for example Rs = Z0 - Rdrv for a point-to-point source termination.
- TDR/TDT plots: impedance against distance, from the step response or from S11 by IFFT.

### 1.3 Crosstalk
- **NEXT and FEXT** in both time and frequency domain. Rules of thumb: backward (NEXT) saturates once the coupled length exceeds half the rise length. Forward (FEXT) grows with coupled length and edge rate, and is zero in a homogeneous dielectric (stripline). Microstrip FEXT is significant because the inductive and capacitive coupling ratios are unequal.
- **Coupled-length/parallelism rules.** Spacing against maximum parallel run length, DRC-style. KiCad already has a coarse "parallel length" rule, but it does not compute any electrical crosstalk.
- **Aggressor/victim reports.** Peak crosstalk in mV or % on each victim, with the contributing aggressors ranked, odd/even switching assumptions, and the sum across aggressors (worst-case aligned, or statistical).
- Differential-pair-to-pair crosstalk, given as mixed-mode SDD21 of the aggressor-to-victim ports.

### 1.4 Differential signalling
- Intra-pair skew (P/N length and delay mismatch), inter-pair skew (lane-to-lane for DDR byte lanes, PCIe lanes, HDMI), and phase-tolerant matching per segment, not only total length.
- Differential and common-mode impedance (Zdiff, Zcomm) per segment from the 2D solver.
- **Mode conversion**: SCD21/SDC21 and SCD11 caused by asymmetry such as skew, uneven via placement, fibre-weave effect, and asymmetric bends. Compliance specs bound these, for example USB/PCIe SCD21 masks.

### 1.5 Frequency-domain channel metrics
- Insertion loss (IL = |SDD21|), return loss (|SDD11|), insertion-loss deviation (ILD) from a fitted curve, ICR (insertion loss to crosstalk ratio), and power-sum crosstalk (PSXT/PSNEXT/PSFEXT). These are how Ethernet (802.3 Clause 93/120/162), PCIe and USB channels are specified.
- Impedance profile (TDR) and the delay profile.

### 1.6 Vias, stubs, backdrill
- Via stub resonance: f_res is roughly c / (4 * L_stub * sqrt(Dk_eff)). A 1.5 mm stub in FR-4 (Dk about 4) puts the notch near 25 GHz, and a 3 mm stub near 12.5 GHz. That notch is what kills 25G+ lanes.
- Backdrill what-if: given the stackup and the backdrill depth or stub tolerance, recompute the via model and channel IL/RL.
- Antipad size and ground/stitching-via count drive via impedance. Users expect a "via wizard" with presets.

### 1.7 Eye diagrams, BER, jitter, equalization
- PRBS (7/15/31) transient eye with eye height and width, plus mask tests.
- **Statistical eye** with BER contours (1e-12 and 1e-15) that include ISI, crosstalk, Tx/Rx jitter (RJ/DJ) and noise.
- Equalization: Tx FFE (de-emphasis presets, e.g. PCIe P0-P10), Rx CTLE (pole/zero families from the specs), DFE, and IBIS-AMI models for vendor SerDes.
- Bathtub curves and the COM (Channel Operating Margin) figure of merit.

### 1.8 Compliance-style checks
| Interface | Typical checks |
|---|---|
| PCIe Gen3/4/5/6 | Channel IL budget at Nyquist (8/16/32 GHz; PAM4 Gen6 at 16 GHz Nyquist, 64 GT/s), statistical eye with reference CTLE/DFE (Seasim methodology), RL masks, Tx preset sweep |
| USB 3.x / USB4 | SDD21 and SCD21 masks, reference-equalizer eye, mated-connector budget |
| DDR4/DDR5/LPDDR | Flight-time and skew budgets (DQ to DQS, CA to CK), fly-by topology with ODT, overshoot/undershoot area against AC limits, Vref-based eye masks (DDR5: Rx mask with DFE), SSN (out of scope here; see PI) |
| Ethernet (10G-KR, 25/50/100/200/400G) | IEEE 802.3 COM (Annex 93A and later), ILD, ICR, RL; 1000BASE-T/10GBASE-T magnetics-side pairs |
| HDMI/DP/MIPI | Differential impedance windows (TDR), intra/inter-pair skew, IL masks |

Each check needs a reference test-channel definition, which is mostly public or semi-public. Spec masks are behind paywalls (PCI-SIG, USB-IF, JEDEC), so the suite should ship a user-editable mask/rule format with no copyrighted tables in it. COM is the exception: IEEE publishes the COM MATLAB reference code and parameter spreadsheets openly. Sources: https://www.ieee802.org/3/ck/public/tools/index.html and https://www.ieee802.org/3/ad_hoc/COM/public/telecon/index.html. An IEEE 802 open-source COM project is under way: https://grouper.ieee.org/groups/802/secmail/msg29914.html

### 1.9 Timing
- Flight time: the delay from the reference-load crossing to the receiver threshold crossing (IBIS "Vref/Cref/Rref" test-load convention). Slow and fast corners come from the IBIS typ/min/max columns.
- Propagation delay per layer, which is not the same on microstrip and stripline. Delay-based matching instead of length-based matching.
- **KiCad context:** KiCad 10 (March 2026) added time-domain length tuning through "Tuning Profiles" with per-layer propagation delay. It uses empirical formulas, not a field solver. Open_Analysis can add value here by feeding field-solved delays back to KiCad. Sources: https://techexplorations.com/kicad/kicad-10-review-new-features-high-speed-tuning-variants-and-more/ and https://docs.kicad.org/doxygen/classPNS_1_1MEANDER__SETTINGS.html

---

## 2. Methods

### 2.1 Channel models from per-segment RLGC
The 2D solver returns frequency-dependent per-unit-length matrices for each cross-section: R(f), L(f), G(f), C(f), each NxN for N coupled conductors.
- R(f) must include DC resistance, skin effect (sqrt f) and roughness (Huray "snowball" or Hammerstad/Groiss correction).
- G(f) and C(f) must follow a **causal dielectric model**, in practice the wideband Debye model (Djordjevic-Sarkar). A constant Dk/Df gives a non-causal model, and every time-domain method downstream will show precursors. This is the most common cause of bad results in home-grown SI tools.
- For each segment of length l, the modal solution gives the 2N-port ABCD (chain) matrix:
  - Z = R + jwL, Y = G + jwC; diagonalize ZY = T gamma^2 T^-1. Then the chain matrix is cosh/sinh of gamma*l in modal space, transformed back. For a single line: A = D = cosh(gamma l), B = Zc sinh(gamma l), C = sinh(gamma l)/Zc.
- Convert ABCD to S (reference 50 ohm single-ended, or 100/25 mixed-mode) and cascade.
- Discontinuities: width changes, layer changes (via), pads, connectors and bends. The first version treats bends as straight. Pads and short features become lumped C/L or short line segments.
- Long uniform segments come out exactly from the closed form. No spatial discretisation is needed. This is the reason to work in the frequency domain first.

### 2.2 Via models
1. **Analytic/semi-analytic pi or T model.** Barrel L from the partial-inductance formula, pad/antipad C from coax or parallel-plate approximations. Valid to a few GHz. Fast, and good for DDR and USB2 class signals.
2. **Physics-based via model.** The via is modelled as a coax/radial-waveguide segment per plane pair, with parallel-plate cavity impedance (Zhang, Fan, Drewniak; Missouri S&T EMC lab, "physics-based via model"). It handles stubs, return vias and plane-pair resonances to roughly 20-40 GHz at small cost. **This is the sweet spot for the suite**, since it gives backdrill what-ifs without full-wave runs.
3. **3D-extracted S-parameters** from the 3D full-wave report (openEMS / Palace / Elmer). Imported as Touchstone blocks. Used for validation and for critical 56G/112G vias.

### 2.3 S-parameter cascading and mixed-mode
- Two-port cascades use T (transfer) matrices. N-port networks of arbitrary topology (branches, stubs, multi-drop) are better handled with the **generalized network/"system of S-parameters" node-equation method**: solve (I - S·Γ) b = a for all internal nodes at each frequency. This is the method SignalIntegrity uses and documents in Pupalaikis' book "S-Parameters for Signal Integrity" (Cambridge, 2020): https://www.cambridge.org/core/books/sparameters-for-signal-integrity/2FD44B994CD054E4B844B358E8903A09. scikit-rf's `connect`, `innerconnect` and `Circuit` classes implement the same idea.
- Mixed-mode conversion: Smm = M S M^-1 with the standard (Bockelman-Eisenstadt) transformation. scikit-rf provides `se2gmm`/`gmm2se` with arbitrary port ordering.
- Renormalisation (`renormalize`) handles 50 to 42.5/85 ohm references (PCIe), Z0 = 45 ohm (DDR), and so on.

### 2.4 Time-domain approaches

| Method | Best for | Notes |
|---|---|---|
| **SPICE transient with lossy-line elements** (ngspice LTRA/TXL/CPL, Xyce LTRA/TRA; commercial: HSPICE W-element) | Nonlinear drivers (IBIS I/V, V/T tables), DDR/parallel buses, overshoot/timing | W-element accepts tabulated RLGC(f) and is state of the art, but proprietary. ngspice's TXL (single) and CPL (coupled) are the usable open equivalents. RLGC is constant per element (with an R~sqrt(f) skin term in TXL/CPL), so a frequency-dependent fit or a ladder approximation is needed for wideband accuracy. |
| **Method of characteristics (MoC)** | Our own engine for lossy coupled lines driven by nonlinear terminations | Decouple into modes. Each mode's propagation function e^{-gamma l} and Zc(f) are vector-fitted to rational functions and applied by recursive convolution. This is how W-element/universal line models work (Gustavsen and Semlyen; "idempotent" universal line model). Moderate effort, high value: removes the SPICE dependency for most nets. |
| **Impulse/step-response convolution** | LTI channel plus simple linear or AMI drivers; PRBS eyes | IFFT of the causal, passive, DC-extrapolated S21 gives h(t). The waveform is the bit stream convolved with h(t). Superposition handles crosstalk: sum each aggressor's pulse response with its own pattern. PyBERT works this way. |
| **Statistical eye / Peak Distortion Analysis (PDA)** | Fast BER contours, compliance (COM, PCIe Seasim, OIF StatEye) | PDA (Casper et al., VLSI 2002) finds the worst-case eye and pattern from the pulse-response cursors analytically. Statistical eye convolves per-cursor PDFs (ISI, each crosstalk aggressor, jitter) for BER contours. PDA was about 20% pessimistic but 2-3 orders faster than full statistics in the original paper. Sources: https://people.engr.tamu.edu/spalermo/ecen689/link_modeling_pda_casper_vlsi_2002.pdf and https://bmas.designers-guide.org/2007/2-6_presentation.pdf and https://www.ibis.org/summits/feb07/old/muranyi.pdf |
| **Frequency-domain + linear terminations** | Pre-layout what-if, IL/RL/crosstalk | Exact for linear Rs/Cin models. Compute the transfer function with terminations, then IFFT. Gives near-instant results. |

Recommended sequence: frequency domain plus convolution first, then statistical eye, then our own MoC/recursive-convolution transient with table-based IBIS. SPICE handles the hard nonlinear cases and serves as a cross-check.

### 2.5 Causality, passivity, and vector fitting
- **Checks:** IEEE 370-2020 defines the passivity, reciprocity and causality quality metrics (PQM/RQM/CQM). Its reference code (MATLAB, IEEE GitLab / opensource.ieee.org/elec-char/ieee-370) also does time-domain enforcement. scikit-rf already implements IEEE P370 de-embedding. Sources: https://scikit-rf.readthedocs.io/en/latest/examples/networktheory/IEEEP370%20Deembedding.html and https://au.mathworks.com/help/rf/ug/applications-of-ieee-370.html and https://harrisburg.psu.edu/content/open-source-de-embedding
- **Passivity:** the largest singular value of S(f) must stay at or below 1 at every frequency. Measured or 3D data often violates this slightly at high frequency.
- **Causality:** Kramers-Kronig consistency, checked through the time-domain impulse (energy before t=0) or with the Triverio/Grivet-Talocia causality check.
- **DC extrapolation:** convolution needs S(0). Measured or 3D data usually starts at 10 MHz or above, so extrapolate with the magnitude trend and phase linear in f, or with a rational fit. Errors here show up as DC offsets in eyes.
- **Vector fitting (VF):** fits S(f) to sum of r_k/(s - p_k) + d + s·e with stable poles. Supports time-domain recursive convolution, SPICE export (equivalent circuit), passivity enforcement (residue perturbation) and interpolation. scikit-rf has `skrf.vectorFitting.VectorFitting` with `passivity_test`, `passivity_enforce` (singular-value perturbation with `preserve_dc`) and SPICE subcircuit export: https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.vectorFitting.VectorFitting.html and https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.vectorFitting.VectorFitting.passivity_enforce.html. Limits: passivity is supported for S-parameters only, and large N-port fits (24+ ports) are slow, so fit coupled blocks one at a time.
- **Mandatory pipeline step:** every S-parameter block that enters the time domain (imported, 3D-extracted, or our own cascade) goes through check, DC extrapolation, optional enforcement, then a reported quality score. Treat it as a gate, not a feature.

---

## 3. Driver and receiver models

### 3.1 Behavioural starting point (MVP)
- Ideal trapezoidal or PRBS voltage source with Rs (output impedance) and a rise time (10-90% or 20-80%), plus an optional series package RLC. The receiver is Cin plus an optional Rterm/ODT to Vtt.
- This covers pre-layout DDR/clock what-ifs, impedance and termination studies, and every linear-channel metric. Implementing it is trivial and it maps directly onto the frequency-domain engine.

### 3.2 IBIS (I/V and V/T tables)
- IBIS 7.x gives pullup, pulldown, POWER_clamp and GND_clamp I/V tables, rising and falling V/T waveforms (with fixtures), C_comp, package RLC or [Package Model]/EBD, and typ/min/max corners.
- Simulating it needs a nonlinear time-domain solver: the standard "k_u(t)/k_d(t) switching coefficient" formulation, solved from two V/T waveforms (Muranyi's method). Run it inside SPICE (ngspice has no native IBIS element; convert to a B-source/table subcircuit, as KiCad's own IBIS-to-SPICE converter does) or in our own MoC engine.
- KiCad already ships an IBIS parser and IBIS-to-SPICE converter in C++ (eeschema `sim/kibis`, since KiCad 7). We can reuse it in-process or treat it as a reference.
- **Python parsers:**
  - `pyibis-ami` / PyAMI (capn-freako/PyAMI): IBIS and AMI parsing (`parse_ibis_file`), AMI model wrapper (ctypes to `.dll`/`.so`), AMI parameter parsing. BSD-3-Clause style license text (Copyright 2012 David Banas). Very active: v10.0.0 on 2026-10-19. https://github.com/capn-freako/PyAMI and https://pyibis-ami.readthedocs.io/en/latest/ibis.html
  - `ecdtools` (eerimoq/ecdtools): MIT, small IBIS loader (`ecdtools.ibis.load_file`, `get_model_by_name`). v0.7.0 on 2026-07-29, 15 stars. https://github.com/eerimoq/ecdtools and https://pypi.org/project/ecdtools
  - `ibischk` (IBIS Open Forum): the official parser/checker, free binary. Run it as a validation step on user models. https://ibis.org/tools/tools.htm
- Recommendation: parse with PyAMI (BSD) or KiCad's kibis, run `ibischk` when present, and convert tables to our internal behavioural model.

### 3.3 IBIS-AMI
- Algorithmic models (Tx FFE, Rx CTLE/DFE/CDR) are shared libraries with a fixed C API (`AMI_Init`, `AMI_GetWave`, `AMI_Close`) plus an `.ami` parameter file. Statistical flow (Init only, impulse in/out) and time-domain flow (GetWave) both exist.
- PyAMI loads AMI DLLs and PyBERT drives them in both flows. **Wrap PyAMI**: rewriting the AMI host is not worthwhile.
- Platform risk: vendor AMI binaries are often Windows x64 or Linux x64 only, and some are 32-bit. Run them in a separate process so a crashing DLL cannot take down the suite.

---

## 4. Open-source building blocks

GitHub data (license SPDX, last push, latest release) was pulled from the GitHub API on 2026-10-27.

| Project | License | Maintenance | What it gives us | Decision |
|---|---|---|---|---|
| **scikit-rf** (https://github.com/scikit-rf/scikit-rf) | BSD-3-Clause | Very active; v2.2.0 2026-10-18; ~950 stars | Network/Touchstone I/O, cascading (`**`, `connect`, `Circuit`), mixed-mode, renormalisation, time domain (`impulse_response`, `step_response`, windowing, DC extrapolation `extrapolate_to_dc`), vector fitting + passivity, IEEE P370 de-embedding, media/line models | **Wrap. Core dependency.** Base internal network objects on `skrf.Network`. |
| **PyBERT** (https://github.com/capn-freako/PyBERT) | BSD-3-Clause | Very active; v11.1.0 2026-10-20 | Complete serial-link sim: channel (Touchstone or built-in lossy line), Tx FFE, CTLE, DFE, CDR, jitter decomposition, bathtub, AMI | **Wrap/borrow** for the SerDes eye/BER engine in phase 3. Its GUI is Traits/Chaco, so we use the model code only. https://github.com/capn-freako/PyBERT/wiki |
| **PyIBIS-AMI** (https://github.com/capn-freako/PyAMI) | BSD-3-Clause text (GitHub shows NOASSERTION) | Very active; v10.0.0 2026-10-19 | IBIS + AMI parsing, AMI DLL host | **Wrap.** |
| **SignalIntegrity** (Pupalaikis, https://github.com/Nubis-Communications/SignalIntegrity) | **GPL-3.0-or-later** (LICENSE.txt, Teledyne LeCroy 2018) | Active; 1.5.2 2026-07-29; ~220 stars | Netlist-based S-parameter system solver, symbolic solutions, de-embedding, simulation (step/impulse, eye diagrams), TDR, RLGC fitting, causality/passivity tools; GUI. openEMS docs use it for transient post-processing (https://openems.readthedocs.io/en/latest/concepts/postproc/signalintegrity.html) | **Do not link** unless the suite is GPL-3. Use it as an **external validation oracle** (run it out-of-process in CI against our cascade results). Reimplement its system-of-S-parameters solve, which is well documented in the book. |
| **ngspice** (https://ngspice.sourceforge.io) | Modified BSD (some contrib parts GPL/LGPL) | Active; KiCad bundles it as a shared library | Nonlinear transient, LTRA, TXL (single lossy, skin effect), CPL (coupled multiconductor), B-sources for IBIS tables, XSPICE. Touchstone import is weak (no native N-port S-block for transient), so S-blocks need VF-to-subcircuit first. https://en.wikipedia.org/wiki/Ngspice | **Wrap** via the `libngspice` shared API (as KiCad does). Use it for IBIS nonlinear transient in phase 2 and as a cross-check. |
| **Xyce** (https://github.com/Xyce/Xyce) | GPL-3.0 | Active (Sandia) | Parallel SPICE, LTRA/TRA, YLIN (S/Y/Z N-port mainly for HB), `.LIN` S-parameter analysis, Touchstone output. https://xyce.sandia.gov/files/xyce/Xyce_Reference_Guide_7.9.pdf | **Optional external backend** (subprocess only, so it does not virally bind our code). Use it for very large netlists. |
| **Qucs-S / qucsator** (https://github.com/ra3xdh/qucs_s) | GPL-2.0 | Qucs-S very active; qucsator-rf maintained | GUI front-end for ngspice/Xyce/qucsator; qucsator has S-parameter and coupled-line microstrip models | Reference only. Its microstrip/coupled-line formula code is useful for sanity-checking the 2D solver. |
| **IEEE 370 reference code** (opensource.ieee.org/elec-char/ieee-370) | Open (check repo license; IEEE-SA open source, typically BSD-3) | Maintained by IEEE 370 WG | De-embedding (2xThru), quality metrics, enforcement | Reference/port the metrics; scikit-rf already has de-embedding. |
| **IEEE 802.3 COM code** | Openly downloadable MATLAB; open-source project in progress | Active ad hoc (2025) | Canonical COM implementation and channel test data | **Reimplement in Python** and validate against MATLAB/Octave on public 802.3 channels. |
| **KiCad kibis** (KiCad source, GPL-3) | GPL-3 | Active | IBIS parser + IBIS-to-SPICE | Use through KiCad (same process) or as a reference. Copying it binds our code to the GPL. |
| **ecdtools** | MIT | Low activity | Simple IBIS loader | Fallback parser. |
| **PySpice / InSpice** | GPL-3 / similar | Variable | Python bindings to ngspice | Unnecessary; write a thin ctypes binding to `libngspice` (BSD). |
| **openEMS** (GPL-3) | GPL-3 | Active | 3D FDTD via/connector extraction (covered by the 3D report) | Out-of-process producer of Touchstone. |

**License note for the architect:** the suite's own license is not settled. If it is permissive (MIT/BSD/Apache), the GPL tools (SignalIntegrity, Xyce, Qucs, KiCad internals, openEMS) can only be used as separate processes or exchange files. scikit-rf, PyBERT, PyAMI and ngspice are all permissive and can be linked.

**Wrap:** scikit-rf, PyAMI, PyBERT model code, libngspice.
**Reimplement (our own IP, small and central):** RLGC to multiconductor ABCD/S, topology-to-network assembly (generalized node solve), crosstalk extraction, statistical eye/PDA, COM, MoC/recursive-convolution transient with IBIS tables, compliance rule engine, result/report layer.

---

## 5. Crosstalk extraction from layout

1. **Spatial index.** Put all track segments and arcs into an R-tree per copper layer (shapely STRtree, or the C++ ingest layer).
2. **Candidate pairs.** For every victim segment, query neighbours within a coupling distance d_max on the **same layer**. Add **adjacent layers**, which matter for broadside coupling in dual stripline, using vertical distance h. Set d_max to about 3-5x the dielectric height to the nearest reference plane (or a user setting). Coupling falls off roughly as 1/(1+(s/h)^2), so beyond 3-5h it is negligible (below -40 dB).
3. **Parallel-run computation.** For each candidate pair, project one segment onto the other: angle tolerance (e.g. under 10-15 degrees counts as parallel), overlap interval, and spacing along the overlap. Merge consecutive overlaps along the route into **coupled sections** with roughly constant spacing (bin spacing into ±5-10%). Non-parallel crossings on adjacent layers become small capacitive coupling points, usually ignored.
4. **Screening.** Estimate the crosstalk coefficient from a lookup of the 2D solver's coupled Kb/Kf at (layer, w, s). Backward NEXT is roughly Kb * min(1, 2*TD_coupled/Tr). Forward FEXT is roughly Kf * l_coupled / Tr (microstrip). Rank the pairs and keep the ones above a threshold (e.g. 1% NEXT). Most pairs drop out here.
5. **Coupled cross-sections.** For the surviving coupled sections, ask the 2D solver for an **N-conductor cross-section** (victim plus the k strongest aggressors, typically 2-6 conductors, including diff-pair partners). Cache results by geometry key (layer, widths, spacings, stackup) because the same cross-sections repeat across a board.
6. **Network assembly.** The victim net is split at section boundaries. Coupled sections become 2N-port multiconductor line models. Uncoupled parts become single lines. Aggressor nets are pulled in with their own drivers and loads (or ideal terminations for screening).
7. **Outputs.** Per-victim waveforms or S-parameter crosstalk (NEXT/FEXT, PSXT), ranked aggressor tables, highlighted coupled sections pushed back into KiCad as markers or a DRC-like report.

This is how BoardSim's "crosstalk threshold" and SIwave's coupled-net extraction work. The extra element here is a screening model computed from the 2D solver.

---

## 6. Recommendation

### 6.1 Phased SI roadmap

**Phase 0 - Foundations (with the ingest and 2D teams)**
- Internal data model: `Net`, `Topology` (graph of nodes: pins, vias, T-junctions; edges: segments), `CrossSection` (key into the 2D solver cache), `Block` (S-param / RLGC line / lumped / IBIS).
- Touchstone I/O, scikit-rf `Network` wrapping, causality/passivity/DC-extrapolation gate.

**Phase 1 - MVP: linear frequency-domain SI (about 3-4 months)**
- Pre-layout topology editor (YAML/JSON first, GUI later) and post-layout single-net extraction with no coupling.
- RLGC to ABCD to S per segment, analytic pi via model, cascade, terminations as linear R/C/L.
- Results: IL/RL, impedance profile (TDR), step/pulse response by IFFT, delay/flight time with behavioural drivers, length/delay/skew report per net class (differential and byte-lane), feeding field-solved delays back to KiCad 10 tuning profiles.
- Batch screen of a net class with overshoot, ringback and flight time.

**Phase 2 - Post-layout coupled SI + IBIS (about 4-6 months)**
- Crosstalk extraction pipeline (Section 5), multiconductor lines, NEXT/FEXT/PSXT.
- Mixed-mode (SDD/SCD) for diff pairs, mode-conversion checks.
- IBIS via PyAMI/kibis: nonlinear transient first through **libngspice** (TXL/CPL + VF'd S-blocks), then our own MoC engine.
- Physics-based via model with stub/backdrill what-if; 3D-extracted Touchstone import.
- DDR4/5 style checks: overshoot area, flight-time and skew budgets, fly-by topology.

**Phase 3 - SerDes channel compliance (about 4-6 months)**
- Pulse response, PDA and statistical eye with BER contours, jitter models.
- Reference equalizers (FFE/CTLE/DFE) and IBIS-AMI via PyAMI (PyBERT engine).
- COM (802.3) reimplementation, PCIe/USB mask checks from a user-supplied mask format, ILD/ICR.

**Phase 4 - Advanced**
- Our own fast transient engine for whole-bus simulation (MoC with recursive convolution, multi-threaded), corner/Monte Carlo sweeps (etch, Dk, tolerance), sensitivity and optimisation (termination/backdrill/EQ).
- Fibre-weave skew, surface roughness calibration from measurement, SI/PI co-simulation (power-aware IBIS, SSN from the PI engine's PDN impedance).
- Hybrid with 3D: automatic "hot-spot" cut-out to full-wave (connector breakouts, BGA fanouts).

### 6.2 SI engine architecture

```
KiCad board (ingest team)                Stackup + materials (Dk/Df(f), roughness)
        |                                          |
        v                                          v
[1] Net topology extractor --------------> [2] Cross-section keyer
    - net graph from tracks/arcs/vias/pads      - (layer, w, s, neighbours, refs)
    - junction/stub detection                    - cache lookup -> 2D solver (RLGC(f))
    - diff-pair & bus grouping                   - via geometry -> via model (analytic/physics/3D)
    - coupled-section finder (Sec. 5)
        |                                          |
        +------------------+-----------------------+
                           v
[3] Channel model builder
    - segments -> MTL blocks (2N-port), vias -> blocks, pads -> lumped
    - network graph (blocks + ports + terminations + drivers/receivers)
    - quality gate: passivity / causality / reciprocity, DC extrapolation, VF
                           |
         +-----------------+-------------------+-------------------+
         v                 v                   v                   v
[4a] Freq-domain     [4b] LTI time domain  [4c] Nonlinear         [4d] Statistical
   node solver          IFFT, convolution,    transient              PDA / stat-eye,
   (S, mixed mode)      PRBS eyes, xtalk      libngspice | own MoC   EQ, AMI, COM
                        superposition         + IBIS tables
         +-----------------+-------------------+-------------------+
                           v
[5] Measurements & checks
    - waveform metrics, eye metrics, IL/RL/ILD/ICR, skew/flight time
    - rule engine (YAML masks/limits per interface; user-editable)
                           v
[6] Results store + reports
    - HDF5/Zarr (waveforms, S-params) + JSON summary; Touchstone/CSV export
    - KiCad back-annotation (markers, net highlights, delay → tuning profile)
```

Design rules:
- Every block exposes `S(f)` at a shared frequency grid, and where available `RLGC(f)` and a VF rational model, so any back-end can consume it.
- The frequency grid is **one global grid per analysis**: linear spacing from DC (or extrapolated) to at least 2-3x the highest significant harmonic, with Δf small enough for the longest delay (Δf < 1/(2·T_total) to avoid time aliasing).
- Solver back-ends sit behind one interface (`solve(network, analysis) -> Result`) so ngspice, Xyce and our own engines can be swapped and cross-checked.
- Heavy kernels (MTL ABCD over thousands of frequencies, convolution, statistical PDF convolution) are vectorized numpy first, then moved to Rust/C++ or numba only where profiling demands it.

### 6.3 Result types and plots
- **Frequency:** S-parameter magnitude/phase (single-ended and mixed-mode), IL/RL with masks, ILD, PSXT/ICR, Smith chart for RL, impedance vs f.
- **Time:** waveforms at pins with threshold lines (Vih/Vil, Vref), TDR impedance against distance (with layout cross-probe), step/pulse/impulse responses, crosstalk waveforms per aggressor.
- **Eye/BER:** transient eye (density heat-map), statistical eye with BER contours, bathtub curves, eye height/width table, COM value, equalizer settings.
- **Tables:** batch net screen (overshoot, ringback, flight time, pass/fail), skew/length/delay per group, aggressor ranking, compliance summary.
- **Layout overlays:** coupled sections, impedance-discontinuity locations, offending vias/stubs.

### 6.4 Validation strategy
1. **Analytic cases (unit tests):** lossless line reflections (lattice diagram), matched/mismatched termination, quarter-wave stub notch, Kb/Kf for symmetric coupled lines against closed forms, skin-effect loss scaling, Debye-model causality (Kramers-Kronig).
2. **Cross-tool regression (CI):** same channel in scikit-rf (independent cascade), SignalIntegrity (out-of-process, GPL-isolated), ngspice TXL/CPL, Xyce, and PyBERT for eyes. Tolerances: IL within 0.1 dB up to Nyquist, delay within 1%.
3. **Published benchmarks:**
   - IEEE 370 test fixtures and data quality cases (2xThru), scikit-rf examples.
   - IEEE 802.3 public channel S-parameters and COM reference results (ieee802.org/3 ck/dj/df tool and channel pages). Reproduce COM to within 0.1 dB.
   - DesignCon papers with full data (e.g. the Simbeor/Shlepnev "design insights" stripline/microstrip loss and causal model papers). Bogatin's textbook examples ("Signal and Power Integrity, Simplified") for crosstalk and reflection numbers.
   - Casper PDA paper and the IBIS ATM VHDL-AMS PDA example for the statistical eye.
4. **Measured data:** cheap test coupons (USB/PCIe-like differential lines of 2-3 lengths, via stubs with and without backdrill, coupled-line pairs) fabricated at standard fabs and measured on a VNA (community-contributed). Compare IL/RL/NEXT/FEXT and TDR. Fit roughness and Dk/Df with Eric Bogatin's "two-length" method to separate material and geometry error.
5. **Commercial correlation (when users have licenses):** HyperLynx/SIwave/ADS results on public KiCad boards (e.g. open-hardware PCIe/DDR designs). Publish the deltas.

### 6.5 Key risks
- **Data quality, not algorithms.** Constant Dk/Df, missing DC points, or non-passive 3D data give non-causal pulses and bad eyes. The quality gate (Section 2.5) is mandatory.
- **Via and 3D fidelity above 20 GHz.** Analytic vias are inadequate for 25G+ NRZ / PAM4. This depends on the 3D team.
- **IBIS nonlinear transient complexity.** k_u/k_d extraction, package models and corners are fiddly. Lean on libngspice first.
- **Compliance spec access.** PCI-SIG/USB-IF/JEDEC masks are member-only, so ship a user-editable format and avoid "compliant" claims.
- **License contamination.** SignalIntegrity, Xyce, KiCad internals and openEMS are GPL. Keep them out-of-process unless the suite chooses GPL-3.
- **AMI binaries.** Platform-specific and crash-prone. Run them sandboxed in a separate process.
- **Performance at board scale.** Thousands of nets times thousands of frequencies. Cache cross-sections and screen aggressively before solving.

---

## Sources
- PyBERT: https://github.com/capn-freako/PyBERT, wiki/release notes https://github.com/capn-freako/PyBERT/wiki/release_notes
- PyIBIS-AMI: https://github.com/capn-freako/PyAMI, docs https://pyibis-ami.readthedocs.io/en/latest/ibis.html, PyPI https://pypi.org/project/PyIBIS-AMI/
- ecdtools: https://github.com/eerimoq/ecdtools, https://pypi.org/project/ecdtools
- IBIS tools list (ibischk etc.): https://ibis.org/tools/tools.htm
- SignalIntegrity: https://github.com/Nubis-Communications/SignalIntegrity (LICENSE.txt: GPL-3.0+), announcement https://www.signalintegrityjournal.com/articles/1551-teledyne-extends-s-parameter-leadership-with-open-source-software-signalintegrity, openEMS usage https://openems.readthedocs.io/en/latest/concepts/postproc/signalintegrity.html
- Pupalaikis, "S-Parameters for Signal Integrity", Cambridge UP 2020: https://www.cambridge.org/core/books/sparameters-for-signal-integrity/2FD44B994CD054E4B844B358E8903A09
- scikit-rf: https://github.com/scikit-rf/scikit-rf, VectorFitting https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.vectorFitting.VectorFitting.html, passivity enforcement https://scikit-rf.readthedocs.io/en/latest/api/generated/skrf.vectorFitting.VectorFitting.passivity_enforce.html, IEEE P370 de-embedding https://scikit-rf.readthedocs.io/en/latest/examples/networktheory/IEEEP370%20Deembedding.html
- IEEE 370: https://au.mathworks.com/help/rf/ug/applications-of-ieee-370.html, https://harrisburg.psu.edu/content/open-source-de-embedding, https://www.signalintegrityjournal.com/articles/1169-ieee-p370-a-ffixture-ddesign-and-ddata-qquality-mmetric-sstandard-for-iinterconnects-up-to-50-ghz, https://resources.altium.com/p/ieee-p370-standard-high-speed-pcb-interconnects
- ngspice: https://en.wikipedia.org/wiki/Ngspice, license file https://sources.debian.org/src/ngspice/24-1/COPYING, devices https://sources.debian.org/src/ngspice/24-1/DEVICES
- Xyce: https://github.com/Xyce/Xyce, reference guide https://xyce.sandia.gov/files/xyce/Xyce_Reference_Guide_7.9.pdf, release notes 6.11 https://xyce.sandia.gov/files/xyce/Release_Notes_6.11.pdf
- Qucs-S: https://github.com/ra3xdh/qucs_s, Xyce/S-param discussion https://github.com/ra3xdh/qucs_s/discussions/450
- COM: https://www.ieee802.org/3/ck/public/tools/index.html, https://www.ieee802.org/3/ad_hoc/COM/public/telecon/index.html, https://grouper.ieee.org/groups/802/secmail/msg29914.html, https://jp.mathworks.com/help/signal-integrity/ug/channel-operating-margin-com.html, https://resources.altium.com/p/channel-operating-margin-isnt-so-bad
- PDA/statistical eye: Casper et al. VLSI 2002 https://people.engr.tamu.edu/spalermo/ecen689/link_modeling_pda_casper_vlsi_2002.pdf; Muranyi PDA in VHDL-AMS https://www.ibis.org/atm_wip/archive/20060905/arpadmuranyiintel/Peak%20Distortion%20Analysis%20Implemented%20in%20VHDL-AMS/PDA_in_VHDL_AMS_IBIS_ATM.pdf; statistical eye https://bmas.designers-guide.org/2007/2-6_presentation.pdf; https://ibis.org/summits/jun10/muranyi2.pdf; https://people.engr.tamu.edu/spalermo/ecen689/lecture22_ee689_isi_noise.pdf
- KiCad 10 time-domain tuning: https://techexplorations.com/kicad/kicad-10-review-new-features-high-speed-tuning-variants-and-more/, https://docs.kicad.org/doxygen/classPNS_1_1MEANDER__SETTINGS.html, https://forum.kicad.info/t/delay-tuning-time-not-length-possible/48504
- Background (not fetched in this session; standard references): Djordjevic et al., "Wideband frequency-domain characterization of FR-4 and time-domain causality", IEEE Trans. EMC 2001; Gustavsen & Semlyen, "Rational approximation of frequency domain responses by vector fitting", IEEE Trans. Power Delivery 1999; Bockelman & Eisenstadt, "Combined differential and common-mode scattering parameters", IEEE Trans. MTT 1995; Zhang/Fan/Drewniak et al., physics-based via models, IEEE Trans. EMC 2010; Bogatin, "Signal and Power Integrity - Simplified", 3rd ed.
