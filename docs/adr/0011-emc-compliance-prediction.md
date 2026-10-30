# ADR-0011: EMC compliance prediction (MIL-STD-461, CISPR, FCC Part 15, DO-160): conducted and radiated emissions from the board in the standard test setup

- Status: Accepted (2026-10-29). Design only; the build is M2, after the M1P gate.
- Deciders: project owner (direction: "overall board emissions testing to estimate compliance, including MIL-STD-461"; performance bar "as good as or better than what is out there"), architecture owner
- Supersedes: the WP-406 sketch row in TASKS.md ("EMI estimates, `oa-emi`"). It is replaced by WP-420 to WP-426.
- Related: ADR-0004 (solvers as jobs), ADR-0007 (one command catalog, agent contracts), ADR-0009 (`oa-accel`, GPU budget), ADR-0010 (`oa-fdtd`), research 07 §3 (EMI), [BENCHMARKS.md](../BENCHMARKS.md) §5 (B-EMC cases)
- Work packages: WP-506 Part B (oa-fdtd EMC features), WP-420 to WP-426 (M2)

## Context
- Users want a pre-compliance answer before the board goes to an EMC lab. "Will this board, in this box, with this harness, pass MIL-STD-461 CE102/RE102 (or CISPR 32/25, FCC Part 15, DO-160 Section 21)? If not, which net or structure is the culprit, and by how many dB?"
- The incumbents are Ansys EMC Plus (an FDTD solver with a cable-harness solver, formerly EMA3D), Ansys HFSS/SIwave, and Dassault CST Studio Suite with Cable Studio (a hybrid of a 3D field solver and multiconductor transmission-line cable models). Their features as we understand them: a 3D field solver; a hybrid cable model; a standard-setup template; limit-line overlays. [verify each against current vendor documentation; we quote no vendor accuracy or runtime numbers, see BENCHMARKS.md honesty rules.]
- What we already have or have planned:
  - The rule engine (oa-erc, the E1-E13 rule set of research 07 §3.1). It ranks layout risks but predicts no levels.
  - The SI channel engine (WP-401), which can give I(z, f) along each net.
  - DC IR and the AC PDN (oa-pi, WP-204/WP-403). These give rail impedances and current paths.
  - oa-network: cascades, renormalisation and the quality gate.
  - oa-fdtd (ADR-0010): a GPU FDTD engine with SIBC, CPML and multi-pole dielectrics. NF2FF and field dumps were explicitly deferred past M1P.
- Research 07 §5.5 risk 6 says closed-form CM estimates are good only to about ±10 dB and must never be presented as compliance prediction. This ADR keeps that rule. It adds a full-wave path that models the standard setup, and every number carries an explicit uncertainty band.

## Decision

### 1. Scope and honesty
1. **Pre-compliance prediction, not certification.** Every result, report header and MCP summary carries the fixed sentence: *"Predicted emissions for the modelled setup. Not a compliance test; only an accredited test of the hardware decides compliance."* No result field is called `pass`/`fail`. The verdict vocabulary is `margin_likely_positive`, `marginal`, `margin_likely_negative`, decided from the margin and its uncertainty band (§1.4).
2. **What dominates the error.** The board is rarely the whole story. Cables (common-mode current on the harness), the enclosure (apertures, seams, bonding), and the setup (room resonances below about 80-250 MHz, absorber quality, rod-antenna coupling to the bench, LISN bonding) dominate radiated emissions. The model includes the harness and the bench because of this. The enclosure is a user-described box: none, a closed metal box with apertures, or an imported STEP. The room is modelled as open space (CPML) by default, and the reflections of an absorber-lined room are an uncertainty contribution, not a modelled effect.
3. **Expected accuracy class (design targets; WP-426 replaces them with measured statistics).**

   | Path | Typical band | Basis |
   |---|---|---|
   | CE101/CE102 conducted (circuit model, known filter and source parameters) | ±3 dB, rising to ±6 dB near input-filter resonances and above 5 MHz | the LISN is fully specified; the error comes from component parasitics and the source model |
   | RE102 full-wave bench (§5b), harness geometry as in the standard | ±6 dB target, ±10 dB worst case | the cable CM current is modelled; the room, the antenna near field and the as-built harness are not |
   | RE102 screening (§5a) | +10/−20 dB (asymmetric: built to over-predict) | closed forms; research 07 §5.5 |
   | RE101 (Biot-Savart near field) | ±6 dB | known current loops; the enclosure is treated as non-magnetic |

   These numbers are engineering judgement for planning, not validated results. They become claims only once WP-426 has measured them, and the result's `Accuracy.tolerance_basis` names which of the two applies.
4. **How uncertainty is reported.** Every prediction carries an **uncertainty budget**: a list of contributions, each with a sign convention, a magnitude in dB and a basis.
   - Contributions: source model, CM conversion, cable layout, room/absorber, antenna and near-field, numerical (solver convergence).
   - They are combined as root-sum-square of the independent terms, plus a signed bias (the screening path's deliberate over-prediction).
   - Output: the predicted level L(f) with a band [L − U₋, L + U₊] in dB.
   - Verdicts: margin M = limit − L. The verdict is `margin_likely_positive` if M > U₊, `margin_likely_negative` if M < −U₋, else `marginal`.
   - The budget follows the GUM approach (JCGM 100:2008). MIL-STD-461H §4.3 reportedly forbids using *measurement* uncertainty when judging compliance (secondary source, [verify against the 461H text]). Our band is *prediction* uncertainty, so it is a different quantity, and the report says so.
   - Mapping onto the frozen `Accuracy` contract (ai-integration §4.1), without a spec change:
     - the compliance-prediction item uses `accuracy_class="estimate"`, `tolerance_rel = 10^(U₊/20) − 1` (field or voltage ratio), `tolerance_basis` naming the budget, and `assumptions` listing the dominant terms;
     - the underlying field solution of a full-wave bench run is a separate item with `accuracy_class="3d_full_wave"`, `converged` and `quality` set. The solver's accuracy and the prediction's accuracy are therefore never conflated.
     - The asymmetric band (U₋, U₊) and the full budget sit in the `EmcPrediction` payload.

### 2. Standards and limit lines

#### 2.1 MIL-STD-461: revision
- **Current revision: MIL-STD-461H, released 17 April 2026, superseding MIL-STD-461G (11 December 2015).** Sources: [In Compliance, "MIL-STD-461H: A Review"](https://incompliancemag.com/mil-std-461h-a-review/); [Interference Technology, "What's new in MIL-STD-461H"](https://interferencetechnology.com/whats-new-in-mil-std-461h/); [Applus+ accreditation note](https://www.appluslaboratories.com/global/en/news/accredited-emc-testing-mil-std-461h). These are secondary sources. **[verify: the 461H document itself was not yet on everyspec.com when this ADR was written. WP-420 obtains it from ASSIST/quicksearch.dla.mil.]**
- Reported 461H changes that affect us (secondary source, [verify each against the 461H text]):
  - CE101 extends to 20 kHz for surface ships and submarines.
  - CE101/CE102 apply only to power leads that interface directly with the platform power bus.
  - RE101: each EUT surface is scanned.
  - **No change to RE102 or to limit values.**
  - Bench setup (§4.3.8.6): primary power leads are routed as individual conductors, not bundled; the input power lead goes first at 10 cm from the front edge, then its return. This changes our harness template (§5b.2).
  - Stepped receivers step at ≤ 0.25 of the bandwidth (was 0.5); above 1 GHz the step is iteratively halved at the worst-case frequency. This affects how we model "what a receiver would read", §3.6.
  - 461H applies to programmes started after its release. Many programmes stay on 461G or 461F, so the library carries **461G and 461H as separate standard IDs** (and 461F on user request).
- Licensing: MIL-STD-461G carries "DISTRIBUTION STATEMENT A. Approved for public release; distribution is unlimited." (front page). Limit values may therefore be encoded and shipped, with citations. We do not redistribute the PDF.

#### 2.2 MIL-STD-461G: tests in scope and limits (from the standard; page numbers are the printed pages)
Emission tests in scope: **CE101, CE102, RE101, RE102**. CE106 (antenna terminals) and RE103 (antenna spurious and harmonic outputs) are out of scope, as are all susceptibility tests (CS1xx, RS1xx).

| Test | Applicability (461G) | Limit (461G figure) | Encoded breakpoints |
|---|---|---|---|
| CE101 | 30 Hz-10 kHz, power leads incl. returns; surface ships, submarines, Army aircraft, Navy ASW aircraft (§5.4.1) | Fig. CE101-1/2/3 (ships and subs: DC, 60 Hz, 400 Hz; depends on input power), Fig. CE101-4 (Navy ASW and Army aircraft) p. 32 | CE101-4, dBµA: curve #1 (source voltage above 28 V) 110 flat from 30 Hz to 1 kHz, then falling to 90 at 10 kHz; curve #2 (28 V or below) 100 → 80. Breakpoints read off grid lines, **[verify: digitised]**. CE101-1/2/3: **[verify: digitise in WP-420]**. |
| CE102 | 10 kHz-10 MHz, all power leads incl. returns (§5.5.1) | Fig. CE102-1, p. 37, all applications | Basic curve dBµV: 94 at 10 kHz (labelled), falling log-linearly to 60 at 500 kHz (the knee read off the 500 kHz grid line, **[verify]**), flat 60 to 10 MHz. Relaxation by nominal source voltage (AC and DC): 28 V basic; 115 V +6 dB; 220 V +9 dB; 270 V +10 dB; 440 V +12 dB (table on the figure). |
| RE101 | 30 Hz-100 kHz, H field at 7 cm from the enclosure and cable interfaces (§5.17.1-2). Navy aircraft: ASW platforms only | Fig. RE101-1 (Army) p. 106, Fig. RE101-2 (Navy) p. 107, dBpT | RE101-1: 180 at 30 Hz falling to 110 at 100 kHz (end points read off the grid, **[verify: digitised]**). RE101-2: **[verify: digitise]**. |
| RE102 | 2 MHz-18 GHz ground and Air Force aircraft; 10 kHz-18 GHz ships, submarines, Army and Navy aircraft, space (§5.18.1). Above 30 MHz both polarisations (§5.18.2) | Fig. RE102-1 ships p. 114; RE102-2 submarines p. 115; RE102-3 aircraft and space p. 116; RE102-4 ground p. 117, dBµV/m | See below. |

RE102 breakpoints. Values printed on the figure are marked (L); values read off grid lines are marked (D) and are **[verify]**:
- **RE102-3, aircraft and space:**
  - Fixed wing external (from 2 MHz) and helicopters (from 10 kHz): 60 (D) at 10 kHz → 24 (L) at 2 MHz, flat to 100 MHz, rising to 69 (L) at 18 GHz.
  - Fixed wing internal, < 25 m nose to tail: 34 (L) flat 2-100 MHz → 79 (L) at 18 GHz.
  - Fixed wing internal, ≥ 25 m: 44 (L) → 89 (L).
  - The rising segment is 45 dB over 100 MHz-18 GHz, i.e. 20 dB/decade, consistent across all three.
- **RE102-4, ground:** Navy Mobile & Army: 24 (L) flat 2-100 MHz → 69 (L) at 18 GHz. Navy Fixed & Air Force: 44 (L) → 89 (L).
- **RE102-1, surface ships:**
  - Below deck: 90 (D) at 10 kHz → 56 (L) at 100 MHz → 102 (L) at 18 GHz.
  - Above deck and exposed below deck: 70 (D) → 36 (L) → 82 (L).
- **RE102-2, submarines:**
  - External to pressure hull: 60 (D) at 10 kHz → 24 (L) at about 2 MHz (D), flat to 100 MHz (D) → 69 (L) at 18 GHz.
  - Internal: about 88 (D) at 10 kHz → 50 (D) at about 1 MHz (D), flat to 100 MHz (D) → 95 (L) at 18 GHz.

Receiver bandwidths (461G Table II, §4.3.10.3.1, 6 dB bandwidths, no bandwidth correction allowed):

| Frequency | 6 dB RBW |
|---|---|
| 30 Hz - 1 kHz | 10 Hz |
| 1 kHz - 10 kHz | 100 Hz |
| 10 kHz - 150 kHz | 1 kHz |
| 150 kHz - 10 MHz | 10 kHz |
| 10 MHz - 30 MHz | 10 kHz |
| 30 MHz - 1 GHz | 100 kHz |
| above 1 GHz | 1 MHz |

Detector: "A peak detector shall be used for all frequency domain emission and susceptibility" measurements (461G §4.3.10.1). Dwell times and step rules (Table II, plus the 461H quarter-bandwidth step) are encoded as data and used only by the "receiver reading" model (§3.6).

#### 2.3 MIL-STD-461G test setup geometry (what the bench template reproduces)
- **Ground plane** (§4.3.5): ≥ 2.25 m², smaller side ≥ 76 cm. Surface resistance ≤ 0.1 mΩ/sq. Bonded to the shielded enclosure (≤ 2.5 mΩ DC) with solid straps at least every 1 m.
- **Cables** (§4.3.8.6.1):
  - At least the first 2 m of each interconnecting cable runs parallel to the front boundary of the setup, 5 cm above the ground plane on non-conductive supports.
  - The cable closest to the front sits 10 cm from the front edge. Cables are 2 cm apart, outer surface to outer surface.
  - The remaining length is zig-zagged at the back.
  - The total length equals the installation length (at least 10 m included if the installation cable is longer).
- **Power leads** (§4.3.8.6.2): 2 m of each input power lead (incl. neutrals and returns) routed like the interconnects, then to the LISNs by the shortest path; total ≤ 2.5 m to the LISN; twisted only if twisted in the installation. Leads are not shielded (§4.3.8.6).
- **LISN** (§4.3.6, Fig. 6 p. 22): 50 µH series inductor. On the source side, 8 µF in series with 5 Ω to ground. On the EUT side, 0.25 µF coupling capacitor to the signal port, with 1 kΩ to ground and a 50 Ω receiver or termination. Bonded to the ground plane, ≤ 2.5 mΩ. CE102 measures at the LISN signal port through a 20 dB, 50 Ω attenuator (§5.5.3.2). The appendix gives a coupling-capacitor correction (Fig. A-11) [verify how it is applied].
- **RE102 antennas** (§5.18.3.2):
  - 10 kHz-30 MHz: 104 cm rod with a matching network, on a square counterpoise ≥ 60 cm per side; theoretical electrical length 0.5 m (appendix).
  - 30-200 MHz: biconical, 137 cm tip to tip.
  - 200 MHz-1 GHz: double-ridge horn, 69.0 × 94.5 cm opening.
  - 1-18 GHz: double-ridge horn, 24.2 × 13.6 cm opening.
- **Antenna position** (§5.18.3.3):
  - 1 m from the front edge of the test setup boundary, 120 cm above the floor;
  - ≥ 1 m from the walls and ≥ 0.5 m from the ceiling and floor;
  - below 200 MHz, one centred position if the boundary is ≤ 3 m wide, else N = ceil(width/3 m) positions (Fig. RE102-7); [verify the rule above 200 MHz and at ≥ 1 GHz from §5.18.3.3c(2)(c)].
- **Absorber** (§4.3.2.1, Table I): ≥ 6 dB at 80-250 MHz, ≥ 10 dB above 250 MHz. No requirement below 80 MHz, so the room is essentially reverberant there. This is why our default open-space model carries a room uncertainty term that is large below 250 MHz.
- **RE101:** 13.3 cm diameter loop sensor at 7 cm from the EUT face (§5.17.3).

#### 2.4 Commercial and civil-aviation standards
- **FCC Part 15 (47 CFR, public domain; encoded and shipped):**
  - §15.109(a), general (Class B) at 3 m: 100 / 150 / 200 / 500 µV/m for 30-88 / 88-216 / 216-960 / above 960 MHz (= 40.0 / 43.5 / 46.0 / 54.0 dBµV/m).
  - §15.109(b), Class A at 10 m: 90 / 150 / 210 / 300 µV/m (= 39.1 / 43.5 / 46.4 / 49.5 dBµV/m).
  - §15.107 conducted, 50 µH/50 Ω LISN:
    - Class B: 0.15-0.5 MHz QP 66→56, average 56→46 dBµV (decreasing with log f); 0.5-5 MHz 56/46; 5-30 MHz 60/50.
    - Class A: 0.15-0.5 MHz 79/66; 0.5-30 MHz 73/60. The lower limit applies at band edges.
  - Detectors (§15.35): CISPR quasi-peak at or below 1000 MHz; average above 1000 MHz with ≥ 1 MHz RBW, plus a peak limit 20 dB above the average limit.
  - Upper scan frequency (§15.33(b)(1)): from the highest internal frequency: < 1.705 MHz → 30 MHz; 1.705-108 MHz → 1 GHz; 108-500 MHz → 2 GHz; 500-1000 MHz → 5 GHz; above 1 GHz → 5th harmonic or 40 GHz, whichever is lower.
  - Sources: [eCFR 15.109 via LII](https://www.law.cornell.edu/cfr/text/47/15.109), [15.107](https://www.law.cornell.edu/cfr/text/47/15.107), [15.33](https://www.law.cornell.edu/cfr/text/47/15.33), [15.35](https://www.law.cornell.edu/cfr/text/47/15.35).
- **CISPR 32** (multimedia equipment; IEC copyright): Edition 2.0 (2015) + Amendment 1 (2019) is the current consolidated version ([IEC webstore 25263](https://webstore.iec.ch/en/publication/25263)). Edition 3 is at the FDIS stage; publication is expected around December 2026, and it adds AC mains limits below 150 kHz for Class B [verify: secondary sources only]. The values quoted in research 07 §3.2 are **not** shipped.
- **CISPR 25** (vehicle components; IEC copyright): Edition 5.0, 2021, 150 kHz-5925 MHz. Component methods (conducted voltage and current probe, ALSE radiated) on a bench with a ground plane, harness and artificial networks. Limits are "recommended" and agreed between OEM and supplier [verify: harness length, height, 5 µH AN and 1 m antenna distance from the 2021 text].
- **RTCA DO-160 Section 21** (airborne equipment; RTCA copyright): DO-160G is the reference edition; the status of a DO-160H is **[verify]**. Categories B, L, M, H, P, Q **[verify the list per edition]**. Conducted (current probe) and radiated limits (anechoic or reverberation-chamber method) with a peak detector.
- **Licensing decision:** for CISPR 32, CISPR 25 and DO-160 we ship **schema-complete templates without limit values**: standard ID, edition, clause and figure references, units, detectors, distances, RBW, and segment structure with empty levels. Users enter the values from their licensed copy into a project limit file (`oa-limits/1`, §2.5), and the result records the file's hash. Shipping numeric values from these standards is an open question for the project owner (facts versus copyright in the compiled tables; TASKS open question 11). FCC and MIL-STD-461 values are shipped.

#### 2.5 Limit-line model
`LimitLine{id, standard, edition, test, platform_or_class, quantity (V|I|E|H|B), unit (dBµV|dBµA|dBµV/m|dBpT|dBµA/m), detector (peak|qp|avg|rms), rbw_table, distance_m, segments[(f1, L1, f2, L2, interp="loglin"|"flat")], relaxations[(condition, dB)], source{citation, page, figure, digitised: bool, verify: bool}, licence ("public_domain"|"distribution_A"|"user_supplied")}`.
- Distance scaling (1/r in the far field only; refused in the near field without `allow_near_field_scaling`) applies only to the commercial limits.
- Loaded from packaged TOML (MIL, FCC) or from user files validated by `emc.limits.validate`.

### 3. Noise-source extraction (`oa_emi.sources`)
Every source is an `EmcSource{id, kind, location (geometry refs: net segments, polygon, pins, connector), spectrum (lines and/or density), coherence_group, model, accuracy}`. Spectra are one-sided: line amplitudes in A or V peak, or densities in A/√Hz. Coherence groups say which lines add coherently (harmonics of one clock) and which add in power (independent sources).

1. **Digital nets (SI engine).**
   - Periodic nets (clocks, strobes): trapezoidal source with the frequency and duty from intent (`clock_frequencies`) and rise/fall times from IBIS: ramp, or 20-80 % from the V-t tables, converted to 0-100 %.
   - Line amplitudes come from the exact Fourier series (not only the envelope). The two-corner envelope (1/(πτ), 1/(πt_r)) is used for screening only.
   - Data nets: PRBS/random data as a continuous density (sinc² with nulls at the bit rate) times the edge-rate roll-off.
   - Spread-spectrum clocking: harmonic n is spread over n·δ·f₀. The level in an RBW is reduced by 10·log₁₀(n·δ·f₀/RBW) when that exceeds RBW; this is an approximation, and the item states it.
   - Current: the SI channel engine (WP-401) gives I(z, f) along each net from the driver model, line and terminations. The service `si.net_currents(net, f)` is a new requirement on WP-401 (via a change request at WP-421 start); without it, the screening path falls back to I = V/|Z₀|.
2. **Switching regulators (PI data plus user parameters).**
   - The power tree (oa-pi `pdn` service: rails, VRM components, loads) identifies the converters.
   - The user supplies or confirms f_sw, topology, V_in, V_out, I_out, the switch-node t_r/t_f and spread-spectrum settings. The datasheet values are proposed; nothing is invented.
   - DM source: the pulsed input current of the topology (for a buck, a trapezoid of I_out at duty D) as a current source into the input network.
   - CM source: the switch-node dv/dt driving the switch-node copper capacitance C_sw to the reference plane and chassis. C_sw comes from the geometry (parallel-plate plus fringe, or `qs` extraction, WP-405), and I_cm = C_sw · dv/dt is spectrally shaped.
   - Low-frequency load modulation (CE101): a user load profile i(t) → FFT, through the converter input impedance (negative incremental resistance −V²/P inside the control bandwidth; Middlebrook, IEEE IAS 1976 [verify]).
3. **Common-mode current (Hockanson et al., IEEE TEMC 38(4), 1996 [verify pages]).**
   - Current-driven: V_cm = jω·L_p,ret·I_DM. The return-path partial inductance comes from the closed forms in research 07 §3.2, or from `qs` (FastHenry2/Palace MS) when available.
   - Voltage-driven: V_cm ≈ V_trace·C_trace/C_board. Self-capacitances use the closed forms of Shim and Hubing (IEEE TEMC 47(4), 2005 [verify]) or `qs`.
   - The CM source is applied at each **cable attachment** (connector ground pins) as a Thevenin source (V_cm, Z_board).
   - Also the I/O-net coupling of E13: a victim I/O net picks up a fraction of the aggressor's voltage, by the crosstalk model of WP-402 where available.
4. **Board-intrinsic radiation.** This is DM radiation from net currents (TL-dipole sum, research 07 §3.3) and plane-cavity edge radiation (`pdn` plane-voltage map V(x, y, f), research 07 §3.3). Each is an equivalent dipole or magnetic-current set on the board, tied to its net or plane pair.
5. **Mapping and culprit ranking.**
   - Every source keeps its geometry references (net, segment IDs, polygon, pins, connector) and its ERC cross-links (violations of RP-001, RP-004, ED-002, EM-011 on the same net get the same `culprit_id`).
   - Ranking metric, per test and per source: the worst over frequency of (source contribution − limit). Also the "share" of the total predicted level at the worst frequency (coherent group sums first, then power sums across groups).
   - Output: a top-N table `{culprit_id, kind, net or structure, worst_f, contribution_dBµV(/m), margin_dB, share_%, linked_violations, suggested remediation}`, deterministic order (worst margin, then id).
6. **Receiver model** (what a receiver would read; used by every path):
   - Lines spaced wider than the RBW are read individually.
   - Lines closer than the RBW are summed inside the RBW: coherently within a coherence group, in power across groups.
   - Densities are integrated over the 6 dB RBW.
   - Peak detector (MIL, DO-160): the line peak, and for impulsive broadband content the impulse bandwidth times the spectral intensity (the impulse-bandwidth factor per CISPR 16-1-1 is [verify]).
   - QP and average (FCC/CISPR): QP = peak for CW lines. For low-PRF impulsive content the CISPR 16-1-1 pulse-response weighting applies, [verify the table before encoding]. The default is peak, which is conservative.
   - The step-size scalloping of stepped receivers (461G half-bandwidth vs 461H quarter-bandwidth steps) is reported as a bias term, not modelled per line.

### 4. CE102 / CE101 path (`oa_emi.conducted`)
- **Circuit, solved in the frequency domain with `oa_network` nodal analysis**, one solve per frequency point. Source lines are evaluated exactly at their harmonics, and densities on a grid fine enough for the RBW.
  1. LISN: the exact 461G Fig. 6 network per power lead (50 µH; 8 µF + 5 Ω; 0.25 µF; 1 kΩ; 50 Ω port), or the 50 µH/50 Ω CISPR/FCC network, or a 5 µH network (CISPR 25 [verify]), or a user network.
  2. Power leads: 2 m (or the template length) as a 2-conductor-plus-ground MTL at 5 cm over the plane. Per-unit-length L/C come from the 2D BEM (`impedance` service). This keeps the CM path (both leads against the bench) as well as the DM path.
  3. EUT input filter: from the BOM. Capacitors come from the oa-pi cap library with ESR/ESL and DC-bias derating (WP-403); inductors and CM chokes from user or vendor models (Touchstone/SPICE import via `oa_network`), with leakage inductance and winding capacitance.
  4. Board PDN: the input-rail Z(f) from `pdn` (AC lumped, WP-403) between the filter and the converter.
  5. Sources: DM current sources at the converter input; CM sources from the switch node through C_sw to the chassis or bench, with the chassis bond impedance as user data (default: an ideal bond, flagged in `assumptions`).
- **Output:** voltage at each LISN signal port (dBµV) vs the limit, with RBW-aware line and density summation (§3.6). Also the CM/DM split (computed from the line pair) and a ranking of sources and filter weaknesses: the frequency of the filter resonance with the LISN, and the Middlebrook stability check of the input filter against the converter's negative input resistance as a warning.
- **CE101:** the same circuit at 30 Hz-10 kHz (20 kHz for 461H ships and subs). The quantity is current in dBµA on each power lead (current probe), from load modulation and converter low-frequency behaviour.
- **Commercial conducted (FCC 15.107, CISPR 32, CISPR 25 voltage method):** the same circuit with the respective network and detector (QP/average modelled per §3.6).

### 5. RE102 / RE101 paths

#### 5a. Fast screening estimator (`oa_emi.radiated.screen`, seconds)
- DM: closed-form small-loop emission (Ott ch. 12; Paul ch. 8, research 07 §3.2) per net, with the loop area from the ReferenceMap (length × height to reference). Then the TL-dipole sum over the actual I(z, f) with image theory (research 07 §3.3).
- CM: the cable-antenna model. I_cm = V_cm/Z_ant, with the cable as a monopole or dipole over the plane: L ≪ λ short-monopole form, capped at the half-wave resonance; Z_ant from a lookup over cable length and height for the bench (default 2 m at 5 cm). E at 1 m from the closed forms, with the deliberate +bias of §1.3.
- RE101: Biot-Savart from the board's low-frequency current loops (the converter hot loop and power paths from the `pdn` current density), flux averaged over a 13.3 cm loop at 7 cm.
- Purpose: triage, culprit ranking, what-if. It runs on every board in under 30 s (B-EMC-04).

#### 5b. Full-wave bench (`oa_emi.bench`, oa-fdtd on the GPU)
1. **Bench network concept.**
   - One full-wave run family produces a frequency-domain **linear network** of the setup with three kinds of ports:
     - (i) source ports: cable-attachment CM/DM ports at the EUT, and equivalent-source sites on the board;
     - (ii) termination ports: LISN connection points and far cable ends, each as a 50 Ω lumped port;
     - (iii) observation channels: E at the antenna reference point, E averaged over the antenna aperture, and the rod open-circuit voltage.
   - The LISNs, filters and source impedances are attached afterwards in the frequency domain (`oa_network`). Their long time constants (50 µH, 8 µF) therefore never enter the time stepping. The FDTD ring-down is set by the 50 Ω-terminated bench, and the DFT gives valid results down to the band floor (§5b.4).
   - **Reciprocity mode.** When there are fewer observation channels than source ports, the engine excites the observation channels (a Hertzian dipole at the antenna point, per polarisation) and records the voltages and fields at all source sites. This gives every source-to-antenna transfer function in 2 × N_antenna-position runs. The predicted field is E(f) = Σₖ Hₖ(f)·Sₖ(f), so spectra, filters and clock changes are **re-evaluated without re-solving** (`emc.whatif`). Culprit ranking is exact superposition.
2. **Geometry: the standard bench template** (oa-fdtd `templates.bench`, WP-506 Part B):
   - the ground plane as a finite PEC sheet (default 2.5 m × 1.0 m), its bond straps to the floor or wall as thin wires, and an optional floor;
   - the EUT as an enclosure box (none, metal box with apertures, or STEP) bonded to the plane per the user;
   - the harness per §2.3:
     - 2 m parallel to the front edge, the front cable at 10 cm from the edge, 5 cm height, 2 cm spacing;
     - power leads first and individually for 461H;
     - the remainder modelled as a terminated stub in the zig-zag region or omitted with an assumption flag;
   - LISN ports at the source end;
   - antenna reference points 1 m from the front edge and 120 cm above the floor, multiple positions per Fig. RE102-7;
   - variants for CISPR 25 (component ALSE [verify parameters]) and for CISPR 32 / FCC tabletop (table height and distance by user entry; the semi-anechoic ground reflection and the 1-4 m height scan by two-ray post-processing per research 07 §3.4).
3. **Board representation.** The bench grid cannot resolve 0.1 mm PCB features without collapsing Δt. Two levels:
   - L1 (board-in-bench): the PCB is meshed into the bench grid when the cell budget and Δt allow (small boards, coarse features, low bands).
   - L2 (hybrid, default): a board-level oa-fdtd run on the fine grid produces (a) the Thevenin CM/DM network at each cable attachment and (b) a recorded Huygens surface around the board or enclosure. The bench run replays (b) as an equivalent source (Huygens injection, interpolated to the bench grid) and connects (a) as source ports. The back-coupling of the bench onto the board's interior field is neglected. Coupling back through the cable ports is kept, because they are network ports. Each item lists this in `assumptions`.
4. **Thin-wire cable model.**
   - Each wire is a Holland-Simpson thin wire (Holland and Simpson, IEEE TEMC 23(2), 1981 [verify pages]), radius below the cell size, with insulation correction [verify method in WP-506].
   - Bundles: the bundle's common-mode current runs on one equivalent thin wire in the FDTD grid. The internal differential modes run in an MTL (p.u.l. matrices from the 2D BEM of the bundle cross-section over the plane), coupled at the terminations and through imbalance (multiwire/MTLN hybrid; Bérenger, "A multiwire formalism for the FDTD method", IEEE TEMC 42(3), 2000 [verify]).
   - Shielded cables: transfer impedance Z_t(f), user data or the solid-braid model of Vance [verify citation].
   - Vertical drops and connections to the plane, EUT and LISN ports are lumped-loaded thin-wire segments.
5. **Antenna and observation.**
   - E is sampled at the antenna reference point and averaged over the antenna aperture plane: horn openings 69.0 × 94.5 cm and 24.2 × 13.6 cm; biconical along its 137 cm axis.
   - The **rod** is modelled explicitly as a 104 cm thin wire with its counterpoise and a high-impedance load. Its open-circuit voltage is converted with the standard's 0.5 m effective length. The rod's capacitive coupling to the setup below about 30 MHz is a known source of difference between measurement and a plane-wave assumption (461G appendix, A.5.18 rationale [verify section]); modelling the rod captures it.
   - Above 30 MHz both polarisations. Reported: the maximum over polarisation and positions, and each one separately.
6. **NF2FF and near-to-near transforms** (WP-506 Part B).
   - Far-field standards (CISPR 32/FCC at 3 m and 10 m): a Huygens box around the EUT and harness. The far field comes from the frequency-domain DFT (few frequencies, full patterns).
   - Fields at finite distance, including the 1 m MIL antenna position above 6 GHz (where 1 m is not far field for a 0.5 m EUT: 2D²/λ ≈ 30 m at 18 GHz): a **time-domain near-zone Huygens transformation to a few observation points**, with the exact 1/R and 1/R² terms (Luebbers et al., IEEE TAP 39(4), 1991, time-domain near-to-far zone transformation [verify]; Taflove and Hagness, ch. 8). It handles many frequencies and few points at the cost of one surface sum per time step.
   - The bench-plane image is used where the plane is treated as infinite, and checked by a box-size convergence test.
7. **Frequency strategy to 18 GHz: segmented runs** (the plan is computed by the template's estimator against `oa_accel.MemoryBudget`; reference GPU RTX 4080 SUPER, 12 GB budget, ADR-0009 §4).
   - Memory uses the ADR-0010 §7 model: 30 B per air cell, plus 48 B per CPML cell. Thin wires, ports and DFT points are negligible.
   - Runtimes are planning estimates from the ADR-0010 throughput targets (3 Gcells/s plain, 1.5 Gcells/s with ADE). They are not measurements.

   | Segment | Band | Air cell max (λ/20 or λ/15) | Domain | Cells | GPU memory | Steps | Est. runtime |
   |---|---|---|---|---|---|---|---|
   | S1 | 10 kHz (2 MHz) - 200 MHz | 75 mm, graded to 5 mm at the harness and 2 mm at the EUT | 5 × 4 × 3 m (bench, antenna, margins) | 2-5 M | < 0.5 GB | ≈ 2e4 (ring-down of the 50 Ω-terminated bench) | ≈ 1-2 min |
   | S2 | 200 MHz - 1 GHz | 15 mm, graded to 2 mm | 4 × 3.5 × 2.5 m | 10-20 M | < 1 GB | ≈ 3e4 | ≈ 3-5 min |
   | S3 | 1 - 6 GHz | 3.3 mm (λ/15) | 2.6 × 2.2 × 1.0 m (front region of the bench to the antenna) | ≈ 160 M | ≈ 5-6 GB | ≈ 1e4 | ≈ 10 min |
   | S4 | 6 - 18 GHz | 1.1 mm | 0.8 × 0.5 × 0.3 m Huygens box around the EUT and the first 0.5 m of harness; near-zone transform to the antenna points | ≈ 90 M | ≈ 3-4 GB | ≈ 8e3 | ≈ 5 min |

   - Below S1's floor (when the domain is ≪ λ and the CPML low-frequency behaviour or late-time drift limits accuracy), the bench network is extended by a **quasi-static fit**: the network's low-frequency asymptote (capacitive and inductive, DC-consistent), checked against an electrostatic `qs` solve of the rod and cable coupling.
   - Overlapping segments must agree within 0.5 dB in the overlap (stitching check), otherwise the run reports `EMC.SEGMENT_MISMATCH`.
   - A full 461 RE102 bench (S1-S4, 2 antenna positions, both polarisations in reciprocity mode) is planned at ≤ 45 min on the reference machine (B-EMC-07). Each segment is its own `oa_jobs` task with `Resources(gpu=True)` and the GPU lease. Segments never run concurrently on one GPU.
8. **Layering.** `oa_emi` is an analysis package. It does not import `oa_fdtd` (adapter layer) or other analyses (oa_si, oa_pi). It writes the engine-level oa-fdtd job format (ADR-0010 §10, schema published by oa-fdtd) and reaches SI, PI, impedance and qs data through services. An import-linter contract keeps this.

### 6. Outputs, commands and agent contract (ADR-0007)
- **Payload `EmcPrediction`** (in `oa_emi.model`; every field has a description with its unit):
  - identification: standard, edition, test, limit ID, antenna position and polarisation;
  - per-frequency data: `freq_hz`, `level_db`, `band_lo_db`, `band_hi_db`, `limit_db`, `margin_db`, detector, RBW;
  - the uncertainty budget;
  - the top-N culprits;
  - verdict counts.
- `summary()` stays within the ai-integration §4.2 caps: the worst margin with frequency and test, verdict counts, and the top 5 culprits.
- **Plots and report:** margin vs frequency with the limit and uncertainty band (per test, polarisation and position); level vs frequency with a per-source stacked contribution; the culprit table cross-probed to the board viewer; provenance (standard edition, limit source and hash, solver versions, grid statistics, `AccelReport`, segment plan); and the fixed disclaimer. HTML export through WP-409.
- **Commands** (one `CommandSpec` each in `oa_service`; CLI, REST and MCP are generated adapters):

| Command | MCP tool | CLI | Side effect | What it does |
|---|---|---|---|---|
| `emc.limits` | `emc_limits` | `oa emc limits [--standard S --test T]` | read | List or show limit lines, with provenance and licence flags |
| `emc.limits.validate` | `validate_emc_limits` | `oa emc limits validate FILE` | read | Validate a user `oa-limits/1` file |
| `emc.sources` | `emc_sources` | `oa emc sources` | compute | Extract noise sources and rank them (no field solve) |
| `emc.conducted` | `emc_conducted` | `oa emc conducted --standard mil461g --test ce102` | compute | CE101/CE102 or commercial conducted prediction |
| `emc.screen` | `emc_screen` | `oa emc screen --standard ... --test re102` | compute | Fast radiated screening (§5a), incl. RE101 |
| `emc.bench` | `emc_bench` | `oa emc bench --standard ... --test re102 [--segments ...]` | compute (GPU job) | Full-wave bench (§5b); returns a job |
| `emc.whatif` | `emc_whatif` | `oa emc whatif RID --set source.X.tr=2ns ...` | compute | Re-evaluate stored transfer functions with changed sources, filters or limits; no re-solve |

  Analyses: `emc.conducted`, `emc.radiated_screen`, `emc.radiated_bench`. Results are read through the existing `results.*` commands. No EMC command writes user files. Remediation proposals (an added filter or stitching via) are `board_edit` remediations, as in oa-erc.
- Deterministic ordering (frequency ascending; culprits by margin then ID) and structured errors: `EMC.LIMIT_UNKNOWN`, `EMC.LIMIT_FILE_INVALID`, `EMC.SOURCE_PARAM_MISSING`, `EMC.SEGMENT_MISMATCH`, `EMC.OUT_OF_BUDGET`.

### 7. Where we aim to beat the incumbents (and how it is checked)
- **Speed on consumer hardware:** the segmented GPU bench in tens of minutes on a 16 GB card (B-EMC-07), and screening plus conducted results in seconds.
- **Transfer-function reuse:** what-if changes to edge rates, clocks, filters and spread spectrum in seconds, with exact culprit superposition.
- **Stated uncertainty bands** with a budget and a measured basis (WP-426), instead of a single curve.
- **Open:** limit provenance, model choices and validation data are public; agents drive everything through MCP.
- Parity on the physics (hybrid cable and field model, standard bench, NF2FF) is checked against analytic, converged and published-measured references (BENCHMARKS §5). Vendor numbers are not quoted (BENCHMARKS honesty rules).

## Consequences
- oa-fdtd gains NF2FF and near-zone transforms, probes, thin wires, Huygens injection and a bench template (WP-506 Part B, M2). This is a substantial addition to an engine we own; each feature comes with analytic verification cases.
- New cross-WP requirements:
  - WP-401 exposes `si.net_currents`;
  - WP-403 exposes the input-rail Z(f), the plane-voltage map and the cap library;
  - WP-405 (optional) gives L_p,ret and C_sw.
  These are filed as change requests by WP-421 and WP-422 at their start.
- Phasing change: the ARCHITECTURE.md EMI row (closed forms in M2, TL-dipole sum in M3, NF2FF with a chamber model in M4) is pulled forward. The TL-dipole sum, NF2FF and the bench are M2. M3/M4 keep the field-solved PDN cavity source and an absorber-lined room model. The integrator updates ARCHITECTURE.md.
- Users must describe the harness, the enclosure and the converter parameters. Missing data falls back to documented defaults, each added to `assumptions`, which widens the band.
- Risks:
  - **Over-trust of predictions.** Mitigations: the disclaimer, the band-based verdicts, and the screening path's bias.
  - **Room effects below 250 MHz** (not modelled). Mitigation: an uncertainty term; an optional absorber-lined room model is deferred past M2.
  - **The thin-wire/MTLN hybrid** is the hardest new numerical feature. Mitigation: B-EMC-05 against MTL theory, a resolved-wire openEMS run and NEC-2.
  - **Limit licensing** for CISPR and DO-160. Mitigation: templates only until the owner decides.
  - **461H text not yet verified.** Mitigation: separate 461G and 461H IDs; 461H values are marked `verify` until WP-420 checks them against the official document.
