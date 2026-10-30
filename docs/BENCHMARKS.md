# Benchmarks (M1P gate)

Status: v1, 2026-10-29 (§5, the M2 EMC cases, added the same day per ADR-0011). This file is the benchmark specification that gates milestone **M1P** ([ROADMAP](ROADMAP.md)) and through it the M1 release. The harness that runs it, records results and publishes them is WP-304 ([TASKS](TASKS.md)). GPU design: [ADR-0009](adr/0009-gpu-acceleration.md); FDTD engine: [ADR-0010](adr/0010-gpu-fdtd-engine.md).

Goal set by the project owner: results **as good as or better than** the commercial incumbents (Ansys HFSS/SIwave/Q3D, Siemens HyperLynx, Polar SI9000, Sonnet), with openEMS and Palace as the open baselines, using the user's GPU where it helps.

Honesty rules for this file:
- A reference value is one of: **analytic** (exact formula); **converged** (our own high-order solution with a documented convergence study, cross-checked by a second, independent method); **published** (a cited paper, book, standard or vendor document with a checkable number); **measured** (a cited measurement with stated uncertainty).
- Anything we could not check is marked **[verify]** and is excluded from gating until it is checked. No number below was typed from memory as if it were a reference.
- Commercial tools: we found no reproducible, public runtime or accuracy numbers for the cases below, and commercial licence terms commonly restrict publishing benchmark results [verify per licence]. We therefore do **not** quote commercial numbers. Parity is defined by (a) matching the convergence criteria those tools document and (b) user-contributed runs on licensed tools (TASKS open question 4), published only with permission.

---

## 1. Reference machine and measurement protocol

| Item | Value |
|---|---|
| CPU | AMD Ryzen 9 9950X3D, 16 cores / 32 threads |
| RAM | 47 GB visible to Windows |
| GPU | NVIDIA RTX 4080 SUPER, 16 GB, compute capability 8.9, driver 621; CUDA visible inside WSL2 |
| OS | Windows 11 (native runs); WSL2 Ubuntu (Palace) |

**Run profile `bench`** (only the holder of the heavy/GPU token runs it, TASKS rule 11):
- `OA_NUM_THREADS = OMP_NUM_THREADS = 8` (half the physical cores);
- host memory cap 16 GB per benchmark process tree (job object on Windows; `systemd-run` scope in WSL);
- GPU budget `OA_GPU_MEM_GB = 12`;
- no other heavy process running. The harness checks the 1-minute CPU load before it starts and refuses to start above 25 %.

Rule 10's defaults (2 threads, 8 GB) still apply to all other tests. The harness also records each case once under them, for information.

**Measurement:**
- Wall time is end to end as a user sees it (`oa ...` command or service call): geometry, meshing, solve, post-processing and quality gate. The cold first run (JIT compile, cache fill) is reported separately. **The gated value is the median of 3 warm runs.**
- Host peak memory: process-tree peak working set (Windows job object) or `memory.peak` of the cgroup scope (WSL). GPU peak: CuPy pool high-water mark plus the context size reported by the driver.
- Every result records the commit, `AccelReport` (device, driver, library versions), solver versions, mesh/grid statistics and the convergence log.
- "CPU" targets use the CPU backend, with no GPU visible (`OA_DEVICE=cpu`). "GPU" targets use `OA_DEVICE=cuda` with `fallback="error"`, so a silent CPU fallback cannot pass a GPU gate.

**Status values:** `pass` (accuracy, runtime and memory all inside targets), `fail`, `ref-pending` (the reference is not produced yet), `[verify]` (reference unchecked, not gating).

---

## 2. Summary of cases and gates

| ID | Case | Accuracy gate | Runtime gate (CPU / GPU) | Memory ceiling | Today |
|---|---|---|---|---|---|
| B-IMP-01 | Exact 2D cross-sections | Z0 ≤ 0.05 % | ≤ 20 ms per section (CPU) | 200 MB | 0.001-0.22 %; 16-39 ms |
| B-IMP-02 | SI9000-class structure set (24) | Z0 ≤ 0.5 %, Zdiff ≤ 1 %, εeff ≤ 0.3 % | ≤ 30 ms SE / ≤ 60 ms diff (CPU) | 300 MB | εeff up to 0.59 % off FEM |
| B-IMP-03 | Frequency-dependent RLGC and loss | R(f) ≤ 1 %, α ≤ 2 % | ≤ 0.5 s per section, 50 freqs (CPU) | 500 MB | partly covered |
| B-IMP-04 | Batch throughput, 1000 sections × 50 f | as B-IMP-02 | ≤ 20 s / ≤ 5 s | 4 GB host, 4 GB GPU | ≈ 16-39 s static only |
| B-DC-01 | DC IR accuracy | ≤ 0.1 % analytic; ≤ 1 % converged; ≤ 3 % published | - | - | passes (kicad-irdrop not run) |
| B-DC-02 | DC IR scale | as B-DC-01 | 3e5: ≤ 5 s CPU; 1e6: ≤ 15 s / ≤ 8 s | 4 GB host, 4 GB GPU | 3e5 in 10.5 s |
| B-ERC-01 | ERC exactness and scale | exact match | combined ≤ 0.5 s; 10k ≤ 3 s; 100k ≤ 30 s | 2 GB | 1.8 s; 34.9 s; - |
| B-ING-01 | Ingest and ReferenceMap at scale | counts exact, areas ≤ 0.5 % | Jetson parse ≤ 2.5 s; RefMap 50k ≤ 3 s | 3 GB | 9.9 s; 51 s |
| B-EM-01 | Microstrip thru 25 mm, to 20 GHz | IL ≤ 0.05 dB; \|S11\| ≤ 1 dB to −30 dB | FDTD ≤ 10 s GPU; Palace ≤ 300 s CPU / ≤ 90 s GPU; openEMS ≤ 60 s | 2 GB GPU; Palace 6 GB | IL 0.74 dB (openEMS) vs 1.24 dB (Palace) |
| B-EM-02 | Stripline thru: exact lossless + lossy FR-4 | exact: \|S21\| ≤ 0.01 dB, phase ≤ 0.5°; lossy as B-EM-01 | FDTD ≤ 10 s GPU; Palace ≤ 300 s / ≤ 90 s | as B-EM-01 | openEMS stalls at −33 dB |
| B-EM-03 | Differential via transition, 8 layers, to 40 GHz | SDD21 ≤ 0.1 dB to 20 GHz, ≤ 0.25 dB to 40 GHz; SDD11 ≤ 1.5 dB above −30 dB | FDTD ≤ 5 min GPU; Palace ≤ 20 min GPU / ≤ 60 min CPU | 8 GB GPU; Palace 12 GB | not run |
| B-EM-04 | CPWG launch with via fence, to 40 GHz | \|S21\| ≤ 0.1 dB; \|S11\| ≤ 2 dB above −25 dB | FDTD ≤ 3 min GPU; Palace ≤ 15 min GPU | 6 GB GPU; Palace 12 GB | not run |
| B-EM-05 | 2.4 GHz antennas (patch, IFA) | f_res ≤ 0.5 % (converged), ≤ 1 % (published); S11 min ≤ 1 dB | FDTD ≤ 60 s GPU; openEMS ≤ 10 min | 4 GB GPU | not run |
| B-EM-06 | High-Q filters (10 GHz hairpin; MSL notch) | f_c ≤ 0.2 %; passband IL ≤ 0.1 dB; BW ≤ 1 % | Palace fast sweep ≤ 10 min GPU / ≤ 30 min CPU; FDTD ≤ 15 min GPU | 12 GB | not run |
| B-PERF-01 | FDTD kernel throughput | float32 vs float64 \|ΔS\| ≤ 1e-4 | ≥ 3.0 Gcells/s plain; ≥ 1.5 Gcells/s ADE+CPML | - | no engine |

---

## 3. Case specifications

### B-IMP-01 Exact 2D cross-sections
- **Structures:**
  - coax ln(b/a) = 1 in air: 59.958 Ω (analytic, `60·ln(b/a)`);
  - wire over plane (`60·acosh(h/a)`) and two-wire (analytic);
  - zero-thickness centred stripline, W/b = 0.1…2.0 (Cohn 1954, exact conformal map);
  - Cohn coupled stripline Z0e/Z0o (exact);
  - CPW S = 1, G = 0.5, zero thickness, infinite ground: 120.57 Ω (εr = 1), 51.88 Ω (εr = 9.8) (exact conformal map; values already locked in ROADMAP M1).
- **Reference:** analytic. These are the only cases where "better than commercial" is checkable without a licence, so the gate is tight.
- **Accuracy:** BEM Z0 and εeff ≤ 0.05 % of exact. Today the CPW case is 0.22 % off from 20× box truncation (FOLLOWUPS); fixing it is WP-510.
- **Runtime:** median ≤ 20 ms per section, CPU, static. **Memory:** ≤ 200 MB.

### B-IMP-02 SI9000-class structure set
- **Structures:** 24 PCB cross-sections:
  - surface, coated and embedded microstrip;
  - symmetric and offset stripline;
  - CPWG;
  - edge-coupled microstrip and stripline;
  - broadside-coupled stripline.

  Each uses 35 µm (1 oz) and 18 µm copper with trapezoidal etch, εr 3.0-4.5 and W/h 0.3-3, chosen to span the Polar SI9000 structure library.
- **Reference (converged):** 2D FEM (`oa_impedance.fem`) at P2 and P3 on 3 refinement levels, Richardson-extrapolated, with an error estimate ≤ 0.05 %. A BEM run at 4× panel density must agree within 0.1 %, which makes it an independent second method.
- **Reference (published):** Polar SI9000 application-note values and Wadell, *Transmission Line Design Handbook* (Artech House, 1991), worked examples. **[verify]**: all published slots are empty today (FOLLOWUPS, WP-302). They gate only once filled from checked sources.
- **Accuracy:**
  - Z0 ≤ 0.5 % vs converged (stretch 0.2 %);
  - Zdiff ≤ 1 % (stretch 0.5 %);
  - εeff ≤ 0.3 %. This closes the 0.57-0.59 % BEM εeff gap on narrow high-εr microstrip (FOLLOWUPS, WP-202).

  Once a published slot is filled, the same tolerances apply to it.
- **Parity note:** SI9000 is a 2D BEM field solver used interactively [verify method from Polar documentation]. Our runtime gate keeps us interactive.
- **Runtime:** ≤ 30 ms single-ended, ≤ 60 ms differential, CPU. **Memory:** ≤ 300 MB.

### B-IMP-03 Frequency-dependent RLGC and loss
- **Cases:**
  1. Round-wire R(f) and L_int(f) vs the exact Bessel-function solution, 1 MHz-50 GHz (analytic).
  2. Microstrip and stripline α_c and α_d from 10 MHz to 50 GHz vs a converged 2D eddy-current FEM (`mesh_conductors=True`, 3 refinement levels).
  3. Djordjevic-Sarkar fit round trip (analytic).
  4. Huray K(f) vs a digitised published curve. **[verify]**: Simonovich, DesignCon 2015, per FOLLOWUPS.
- **Accuracy:** R(f) ≤ 1 %; α ≤ 2 %; DS round trip ≤ 1e-6.
- **Runtime:** ≤ 0.5 s per section at 50 frequencies, CPU. **Memory:** ≤ 500 MB.

### B-IMP-04 Batch throughput (impedance profile scale)
- **Case:** 1000 distinct cross-sections (the union of the WP-102 boards' profiles, padded with random in-range variants under a fixed seed) × 50 log-spaced frequencies, through `ImpedanceService.solve_many`.
- **Accuracy:** each result within B-IMP-02 tolerances of its CPU float64 single-section result; GPU vs CPU within ADR-0009 §5 (≤ 1e-10).
- **Runtime:** ≤ 20 s CPU (`bench` profile), ≤ 5 s GPU. **Memory:** host ≤ 4 GB, GPU ≤ 4 GB.

### B-DC-01 DC IR accuracy
- **Cases:**
  - Analytic:
    - strip `R = Rs·L/W`;
    - annulus `Rs·ln(b/a)/2π`;
    - two layers + N vias;
    - via barrel (0.30 mm drill, 25 µm plating, 1.6 mm) 1.27 mΩ.
  - Converged: the `power_plane` board solved on 4 refinement levels with ring contacts and Richardson extrapolation.
  - Published: the kicad-irdrop 4-layer board, 3.04 mΩ (kicad-irdrop), 2.97 mΩ (padne). Both values are as cited in ROADMAP M1 **[verify]** against the repositories before gating.
- **Accuracy:**
  - ≤ 0.1 % on the analytic strip and annulus;
  - ≤ 0.5 % on the analytic via cases;
  - ≤ 1 % worst drop vs converged;
  - FEM vs raster ≤ 1 %;
  - ≤ 3 % vs each published value.
- **Parity note:** SIwave/HyperLynx PI DC results are not public for this board. A user-contributed comparison is welcome (permission required).

### B-DC-02 DC IR scale
- **Cases:**
  - the `power_plane` board refined to 3e5 and 1e6 unknowns, end to end (`oa pi dc`);
  - the raster reference at 1e7 cells, solve only.
- **Accuracy:** as B-DC-01; GPU vs CPU per ADR-0009 §5.
- **Runtime:**

  | Size | CPU | GPU |
  |---|---|---|
  | 3e5 | ≤ 5 s (today 10.5 s, mesh dominated) | - |
  | 1e6 | ≤ 15 s | ≤ 8 s (the solve itself ≤ 1 s with cuDSS) |
  | raster 1e7, solve only | ≤ 60 s | ≤ 10 s |

- **Memory:** host ≤ 4 GB at 1e6; GPU ≤ 4 GB.

### B-ERC-01 ERC exactness and scale
- **Cases:**
  - the WP-102 combined board, all M1 rules;
  - the 10k-track synthetic board with IM-002 on every net (today 34.9 s);
  - a 100k-track synthetic board under a fixed seed.
- **Accuracy:** exact match of rule id, net and layer, with location within 50 µm, against `expected.yaml`. The native path must match the Python path exactly.
- **Runtime:** combined ≤ 0.5 s (today 1.8 s); 10k ≤ 3 s; 100k ≤ 30 s, CPU. **Memory:** ≤ 2 GB.
- **Parity note:** HyperLynx DRC publishes no comparable timings [verify]. The 100k case is our proxy for a dense production board.

### B-ING-01 Ingest and ReferenceMap at scale
- **Cases:**
  - the 88 MB Jetson demo board (parse; today 9.9 s);
  - `cm5_minima`;
  - ReferenceMap on a 50k-track board (today 51 s).
- **Accuracy:** net, via and pad counts exact; per-net copper area ≤ 0.5 %; native vs Python parse produces an identical `content_hash()`.
- **Runtime:** Jetson parse ≤ 2.5 s; `cm5_minima` ≤ 1 s; ReferenceMap 50k ≤ 3 s, CPU. **Memory:** ≤ 3 GB peak for the Jetson board.

### B-EM-01 Microstrip thru, 25 mm, 0.1-20 GHz
- **Structure:** the testkit microstrip thru (0.38 mm line on 0.2 mm FR-4, Djordjevic-Sarkar, 35 µm copper, Hammerstad-Jensen Z0 50.7 Ω), with wave (mode) ports de-embedded to the line ends.
- **Reference (converged, two independent methods that must agree with each other within 0.02 dB):**
  - **R1:** 2D RLGC(f) from the converged FEM (B-IMP-03 settings, same Djordjevic-Sarkar model, same roughness, real 35 µm copper), cascaded over 25 mm and renormalised to the port reference. This is exact for a uniform line up to radiation and port effects.
  - **R2:** Palace p3 with AMR until max |ΔS| ≤ 0.002 between passes at 20 GHz, wave ports, with a per-band permittivity evaluation (Palace 0.18 has no dispersive materials, see WP-501).

  If R1 and R2 disagree by more than 0.02 dB, R2 governs and the difference is reported as a 3D effect.
- **Accuracy (every solver: oa-fdtd, Palace, openEMS):**
  - IL within **0.05 dB** of the reference at every frequency to 20 GHz;
  - |S11| within **1 dB** wherever the reference is above −30 dB, and below −29 dB wherever the reference is below −30 dB;
  - phase delay within 0.5 %;
  - quality gate pass, PQM ≥ 99.

  Today openEMS gives 0.74 dB and Palace 1.24 dB at 10 GHz, which is 0.5 dB apart. Closing this is WP-501's first deliverable.
- **Runtime:**
  - oa-fdtd ≤ 10 s on the GPU, both excitations batched, thick-SIBC copper; openEMS takes 31 s today to 10 GHz with sheet copper;
  - Palace p2, 41-point adaptive sweep: ≤ 300 s CPU, ≤ 90 s GPU (today p1, 3 points: 169 s);
  - openEMS: ≤ 60 s CPU to 20 GHz.
- **Memory:** GPU ≤ 2 GB; host ≤ 4 GB (FDTD); Palace ≤ 6 GB (the current WSL box).

### B-EM-02 Stripline thru
- **(a) Exact lossless case.** Zero-thickness centred stripline, homogeneous lossless εr = 4.0, PEC walls, wave ports. Z0 comes from Cohn's exact formula and β = ω√εr/c (TEM), so S is known in closed form. This follows the idea of Rautio's precision planar benchmark (J. C. Rautio, "An ultra-high precision benchmark for validation of planar electromagnetic analyses", *IEEE Trans. Microw. Theory Tech.* 42(11), 1994 [verify pages]).
  - Accuracy: |S21| within 0.01 dB of 0 dB; phase within 0.5° at 20 GHz; |S11| ≤ −40 dB when the port reference equals the exact Z0.
- **(b) Lossy case.** The testkit stripline thru (FR-4, 35 µm copper), with the reference and tolerances of B-EM-01. It must also reach a −50 dB energy end criterion, which tests the openEMS stall that FOLLOWUPS records at −33 dB and CPML in oa-fdtd.
- **Runtime and memory:** as B-EM-01.

### B-EM-03 Differential via transition, 8 layers, 0.1-40 GHz
- **Structure:** the testkit via-transition board extended to a differential pair: L1 to L8 through 1.6 mm, 0.25 mm drill, antipads, two return vias; 4 wave ports (2 differential), and a variant with a 0.6 mm stub.
- **Reference (converged):** Palace p3 with AMR until max |ΔS| ≤ 0.005 at 40 GHz, cross-checked by oa-fdtd at λ/40 with conformal vias. The two must agree within half of each tolerance below. For context, HFSS's documented default adaptive criterion is max ΔS = 0.02 [verify against current Ansys documentation]; our reference is 4× tighter.
- **Reference (measured):** the IEEE 370 plug-and-play fixture data ([opensource.ieee.org/elec-char/ieee-370](https://opensource.ieee.org/elec-char/ieee-370)). **[verify]** whether a via structure with full geometry is included; if so it becomes B-EM-03m with the measurement uncertainty as its tolerance.
- **Accuracy:**
  - SDD21 within 0.1 dB to 20 GHz and 0.25 dB to 40 GHz;
  - SDD11 within 1.5 dB above −30 dB;
  - phase of SDD21 within 2° to 20 GHz;
  - stub resonance frequency within 1 % (the Goldfarb-Pucel/quarter-wave estimate for a 1.5 mm stub at Dk 4 is ≈ 25 GHz, ROADMAP M1);
  - SCD21 below −30 dB where the reference is.
- **Runtime:**
  - oa-fdtd ≤ 5 min GPU, all 4 excitations;
  - Palace ≤ 20 min GPU and ≤ 60 min CPU (81-point adaptive sweep, including AMR);
  - openEMS for information only, estimated 0.5-2 h (research 05 §5).
- **Memory:** GPU ≤ 8 GB; Palace host ≤ 12 GB (raised from 6 GB for this case only, and only under the heavy token).

### B-EM-04 CPWG launch, 0.1-40 GHz
- **Structure:** CPWG on a 0.254 mm low-loss laminate (εr 3.66 class; exact Dk/Df taken from a cited datasheet in the case file), with a via fence at λ/10 pitch at 40 GHz, a 0.5 mm-long wider launch pad (connector footprint pad, no connector body), and 2 wave ports.
- **Reference (converged):** as B-EM-03.
- **Reference (published):** a vendor end-launch application note with measured S-parameters for a stated board. **[verify]**: none selected yet. The case gates on the converged reference only.
- **Accuracy:** |S21| within 0.1 dB; |S11| within 2 dB above −25 dB; the parallel-plate resonance of the fence suppressed, with no spurious notch in either reference.
- **Runtime:** oa-fdtd ≤ 3 min GPU; Palace ≤ 15 min GPU. **Memory:** GPU ≤ 6 GB; Palace ≤ 12 GB.

### B-EM-05 2.4 GHz antennas
- **Structures:**
  - **(a)** the openEMS "Simple Patch Antenna" tutorial geometry ([tutorial](https://openems.readthedocs.io/en/latest/python/openEMS/Tutorials/Simple_Patch_Antenna.html)), built through our pipeline;
  - **(b)** a 2.4 GHz PCB inverted-F antenna with published measured S11. Candidate: TI Design Note DN007 **[verify document number, geometry and data]**.
- **Reference:**
  - (a) the tutorial script run unmodified in openEMS on a fine grid, plus Palace converged;
  - (b) the measured S11 **[verify]**, plus Palace converged.
- **Accuracy:**
  - f_res (S11 minimum) within 0.5 % of converged and 1 % of the tutorial or measured value;
  - S11 minimum depth within 1 dB where it is above −25 dB;
  - −10 dB bandwidth within 3 %.
  - Gain and efficiency through NF2FF are deferred to M2; NF2FF is not in the oa-fdtd M1P scope.
- **Runtime:** oa-fdtd ≤ 60 s GPU; openEMS ≤ 10 min CPU. **Memory:** GPU ≤ 4 GB.

### B-EM-06 High-Q filters
- **Structures:**
  - **(a)** a 10 GHz microstrip hairpin bandpass filter (5th order, about 10 % fractional bandwidth) on a low-loss laminate;
  - **(b)** the openEMS MSL notch-filter tutorial reproduced through our pipeline.
- **Reference:**
  - Palace p3 with adaptive fast sweep (≥ 401 output points) and AMR until |ΔS| ≤ 0.005;
  - for (b), also the tutorial script's own output.
  - Published measured hairpin: candidate from Hong and Lancaster, *Microstrip Filters for RF/Microwave Applications* (Wiley) **[verify edition, example and data]**.
- **Accuracy:**
  - centre frequency within 0.2 %;
  - passband IL within 0.1 dB;
  - 3 dB bandwidth within 1 %;
  - stop-band rejection within 2 dB down to −50 dB;
  - (b) notch frequency within 0.5 % of the tutorial.
- **Runtime:**
  - Palace ≤ 10 min GPU / ≤ 30 min CPU;
  - oa-fdtd ≤ 15 min GPU, with the matrix-pencil extrapolation of ADR-0010 §6 if needed to stay inside the −50 dB end criterion.

  FEM with a fast sweep is the expected winner here (research 05 §5). The FDTD target is a cross-check, not a race.
- **Memory:** 12 GB (GPU or Palace host).

### B-PERF-01 FDTD kernel throughput
- **Cases:**
  - a 256³ uniform vacuum grid with a 10-cell CPML;
  - a 400 × 400 × 120 graded PCB-like grid with 6-pole ADE in 60 % of the cells, CPML and 1 % special edges.
- **Accuracy:** float32 GPU vs the float64 engine |ΔS| ≤ 1e-4 on ADR-0010 §9 cases 1-3.
- **Runtime:** ≥ 3.0 Gcells/s (plain) and ≥ 1.5 Gcells/s (PCB-like) on the RTX 4080 SUPER. For context, gprMax reached 3.4 Gcells/s on Pascal (Warren et al. 2019, ADR-0010). The NumPy CPU engine is reported for information.

---

## 4. Where parity is out of reach in M1P (stated, not hidden)
- **Planar 2.5D MoM (Sonnet/Momentum class).** No open solver exists (research 05 §1.2). High-Q planar work goes through Palace with a fast sweep, which is expected to be slower than a planar MoM on the same filter.
- **Full-board hybrid SI/PI extraction (SIwave class).** This is M4/M5. M1P benchmarks regions, not whole boards.
- **AMD GPUs.** They are tier 2 (ADR-0009 §7): reported, not gated, and there is no sparse direct solver on ROCm from Python.
- **Commercial comparison numbers** are not published until a licensed user contributes them with permission.

## 5. M2: EMC pre-compliance cases (ADR-0011)

These cases gate **M2**, not M1P. They use the same reference machine, `bench` profile, measurement protocol and honesty rules as §1. Design: [ADR-0011](adr/0011-emc-compliance-prediction.md). Work packages: WP-420 to WP-426 and WP-506 Part B. "Accuracy" here is the agreement of our numbers with the reference. The prediction's uncertainty against a real lab test is a separate quantity, reported per ADR-0011 §1.4 and calibrated by B-EMC-08.

| ID | Case | Reference | Accuracy gate | Runtime gate | Memory ceiling | Today |
|---|---|---|---|---|---|---|
| B-EMC-01 | Limit-line library exactness | published (MIL-STD-461G figures; 47 CFR 15.107/15.109) | labelled breakpoints exact; digitised ≤ 0.5 dB between two digitisations; FCC conversions ≤ 0.01 dB | lookup ≤ 1 ms | - | not built |
| B-EMC-02 | Source spectra | analytic (trapezoid Fourier series; PRBS density) | line amplitudes ≤ 0.01 dB; envelope corners exact | ≤ 1 ms per net, 1000 nets ≤ 2 s CPU | 500 MB | not built |
| B-EMC-03 | Conducted (CE102/CE101, FCC 15.107) circuit | analytic (461G Fig. 6 LISN); ngspice netlist oracle | LISN impedance ≤ 0.1 dB; buck + filter vs ngspice ≤ 0.1 dB, phase ≤ 1° | ≤ 2 s CPU per lead, 10 kHz-10 MHz | 1 GB | not built |
| B-EMC-04 | Radiated screening | analytic (Hertzian dipole, small loop); converged (oa-fdtd NF2FF) | closed forms ≤ 0.1 dB; TL-dipole sum vs NF2FF ≤ 3 dB DM | 1000 nets ≤ 30 s CPU | 2 GB | not built |
| B-EMC-05 | Thin wire over ground plane | analytic (MTL theory); converged (resolved-wire openEMS); NEC-2 oracle **[verify licence/build]** | wire-current resonances ≤ 1 %; current ≤ 0.5 dB; E at 1 m ≤ 1 dB vs resolved wire | ≤ 2 min GPU | 2 GB GPU | not built |
| B-EMC-06 | NF2FF and near-zone transform | analytic (Hertzian and half-wave dipole far and near fields); published (B-EM-05a patch) | far field ≤ 0.5 dB; field at 1 m ≤ 0.2 dB; patch gain ≤ 0.5 dB vs openEMS NF2FF | ≤ 60 s GPU per case | 4 GB GPU | not built |
| B-EMC-07 | MIL-STD-461 RE102 bench, canonical | converged (oa-fdtd fine grid) cross-checked by openEMS on the resolved-wire variant | oa-fdtd vs openEMS ≤ 0.5 dB to 1 GHz; segment overlap ≤ 0.5 dB; grid-halving change ≤ 0.5 dB | 2 MHz-18 GHz, 2 positions, both polarisations ≤ 45 min GPU | 12 GB GPU, 16 GB host | not built |
| B-EMC-08 | Published measured cases | measured **[verify]** (Hockanson 1996; Paul 1989; Shim and Hubing 2005) | ≤ 6 dB (target), ≤ 10 dB (gate) at measured peaks; FSV (IEEE 1597.1) grade "fair" or better | as B-EMC-07 | as B-EMC-07 | not built |
| B-EMC-09 | End to end on WP-102 boards | (runtime and determinism case) | bitwise-identical repeat runs; culprit order stable | screen ≤ 30 s; conducted ≤ 10 s; bench ≤ 60 min | 12 GB GPU | not built |

### B-EMC-01 Limit-line library
- **Cases:**
  - MIL-STD-461G CE101-4, CE102 (with all five voltage relaxations), RE101-1 and RE102-1…4. Each value printed on the figure is checked exactly. Values read off grid lines are flagged `digitised` and `verify`.
  - FCC §15.109(a)/(b) µV/m converted to dBµV/m.
  - FCC §15.107(a)/(b), including the log-frequency slope from 0.15 to 0.5 MHz.
- **Reference:** the figure and printed page cited in each `case.toml`. MIL-STD-461H entries are `[verify]` until checked against the official 461H text.

### B-EMC-02 Source spectra
- **Cases:** a 100 MHz clock, 50 % duty, t_r = t_f = 0.5 ns and 2 ns; asymmetric edges; 33 % duty; a PRBS-7 at 1 Gb/s; a spread-spectrum clock (0.5 % down-spread). The SSC case is checked against the time-domain FFT of a modulated waveform with a stated approximation tolerance of ≤ 2 dB.
- **Reference:** analytic. Each case is the exact Fourier series of the stated waveform.

### B-EMC-03 Conducted circuit
- **Cases:**
  1. |Z_LISN(f)| of the 461G Fig. 6 network from 10 kHz to 10 MHz, vs its analytic impedance. 461G Fig. 7 is the published plot, used for a visual cross-check only.
  2. A 12 V → 3.3 V buck at 400 kHz with a π input filter (stated L, C, ESR and ESL), with the LISN and 2 m leads as an MTL. The same netlist runs in ngspice (an `external` oracle).
  3. The FCC 15.107 network variant of case 2.
- **Reference:** analytic and ngspice. A published measured CE102 case for a stated converter is **[verify]: none selected yet**, and the case does not gate on measurement.

### B-EMC-04 Radiated screening
- **Cases:**
  - a Hertzian dipole and a small loop over an infinite PEC plane at 1 m and 3 m;
  - a 10 cm microstrip, 50 Ω, over a 10 × 5 cm finite ground plane, matched and open terminations, 30 MHz-3 GHz. The screening TL-dipole sum is compared with oa-fdtd NF2FF (B-EMC-06 settings).
- **Reference:** analytic and converged. Research 07 §5.4 already sets the ±3 dB DM target, with "trend" for CM.

### B-EMC-05 Thin wire over a ground plane
- **Cases:**
  - one 1 mm-radius wire, 2 m long, 5 cm over an infinite and a finite (2.5 × 1 m) PEC plane; 50 Ω at both ends and 50 Ω/open variants; 1-500 MHz;
  - a 3-wire bundle (MTLN hybrid) with an imbalanced termination.
- **Reference:**
  - MTL theory (exact for the infinite plane in the TEM approximation) for currents;
  - openEMS with the wire resolved (staircased, ≥ 4 cells across) for the radiated field;
  - NEC-2 as an optional `external` oracle **[verify licence and Windows build]**.
- **Accuracy:** resonance frequencies ≤ 1 %; current magnitude ≤ 0.5 dB; E at 1 m ≤ 1 dB against the resolved-wire reference.

### B-EMC-06 NF2FF and near-zone transform
- **Cases:**
  - a Hertzian dipole and a half-wave dipole: far-field patterns, plus the exact near field at 1 m (time-domain near-zone mode);
  - a dipole over an infinite PEC plane (image Green's function);
  - the B-EM-05a patch: gain and pattern, vs openEMS `nf2ff` on the same structure.
- **Reference:** analytic (Balanis ch. 4, ch. 6); openEMS NF2FF (converged).
- **Accuracy:** far field ≤ 0.5 dB (the existing M2 EMI gate); field at 1 m ≤ 0.2 dB; patch gain ≤ 0.5 dB vs openEMS, and ≤ 1 dB vs the tutorial's published value.

### B-EMC-07 MIL-STD-461 RE102 bench, canonical
- **Structure:** the ADR-0011 §2.3 bench:
  - a 2.5 × 1.0 m PEC plane;
  - a 20 × 15 × 8 cm metal EUT box bonded to the plane, with one 5 cm slot aperture;
  - one 2 m harness of 3 wires at 5 cm height, 10 cm from the front edge, LISN ports at the far end;
  - a CM source of 1 V behind 50 Ω at the box connector;
  - antenna reference points 1 m from the front edge, two positions, both polarisations.
- **Reference (converged):** oa-fdtd at half the cell size in every segment (change ≤ 0.5 dB). openEMS on the resolved-wire variant to 1 GHz; above 1 GHz openEMS is for information only (cost).
- **Runtime:** the whole 2 MHz-18 GHz plan (S1-S4) ≤ 45 min on the RTX 4080 SUPER, of which S1+S2 take ≤ 10 min. Planning numbers are in ADR-0011 §5b.7. No commercial runtime is quoted.

### B-EMC-08 Published measured cases
- **Candidates** (each **[verify]**: geometry stated fully enough to rebuild, and data readable from the paper's figures):
  - D. M. Hockanson, J. L. Drewniak, T. H. Hubing, T. P. Van Doren, F. Sha, M. J. Wilhelm, "Investigation of fundamental EMI source mechanisms driving common-mode radiation from printed circuit boards with attached cables", *IEEE Trans. EMC* 38(4), 1996 [verify pages and authors];
  - C. R. Paul, "A comparison of the contributions of common-mode and differential-mode currents in radiated emissions", *IEEE Trans. EMC* 31(2), 1989 [verify pages];
  - H. W. Shim, T. H. Hubing, "Model for estimating radiated emissions from a printed circuit board with attached cables due to voltage-driven sources", *IEEE Trans. EMC* 47(4), 2005 [verify pages].
- **Accuracy:**
  - at the measured emission peaks: ≤ 6 dB target, ≤ 10 dB gate;
  - FSV (IEEE Std 1597.1-2008) global grade "fair" or better.

  Data digitised from figures carries its digitisation uncertainty in the tolerance.
- **Our own measurements** (TASKS open question 13) become B-EMC-08m once they exist.

### B-EMC-09 End to end
- **Cases:** the WP-102 boards with clock intents and one buck converter. Run `emc.screen`, `emc.conducted` (461G CE102, 28 V) and `emc.bench` (L2 hybrid, S1-S2 only in the gate, full plan reported).
- **Accuracy:** deterministic. Repeat runs give bitwise-identical results on the same device, and culprit ordering is stable.
- **Runtime:** screen ≤ 30 s; conducted ≤ 10 s; bench S1-S2 ≤ 15 min, full ≤ 60 min.

### Parity statement (EMC)
- Ansys EMC Plus, HFSS/SIwave and CST Studio Suite with Cable Studio publish no reproducible accuracy or runtime numbers for these cases that we could find [verify]. We compare against analytic, converged and published-measured references only.
- A licensed user may contribute runs with permission (TASKS open question 14).

## 5b. M2: uncertainty-quantification cases (ADR-0012)

These cases gate the UQ package ([ADR-0012](adr/0012-uncertainty-quantification.md), WP-430 to WP-436). They test the statistics, not the physics, so every case has a reference that is exact or computed independently of `oa-uq`. They run on the CPU by default. "Accuracy" is the agreement of `oa-uq` output with the reference. All are `not built` today.

| ID | Case | Reference | Accuracy gate | Today |
|---|---|---|---|---|
| B-UQ-01 | Linear model, Gaussian inputs | analytic: P(pass) = Phi((L - a - b.mu) / sqrt(b.Sigma.b)). Example: b = (1, 2, -1), independent inputs with sigma = (1, 0.5, 1), mu = 0, L = 3 gives Phi(sqrt(3)) = 0.95837 | MC with N = 1e5: exact value inside the reported 95 % CI; first-order estimate exact to 1e-12; first-order shares exact | not built |
| B-UQ-02 | Interval coverage | statistical: B-UQ-01 repeated with 1000 seeds | the 95 % CI contains the exact value in 93 % to 97 % of repeats (binomial range); same for LHS and Sobol, whose CIs may be narrower but not over-confident | not built |
| B-UQ-03 | Ishigami function, Sobol indices | analytic (a = 7, b = 0.1): S1 = 0.3139, S2 = 0.4424, S3 = 0, ST1 = 0.5576, ST2 = 0.4424, ST3 = 0.2437 **[verify: check these values against the source at implementation]** | indices <= 0.01 absolute with N = 2^14 Sobol points; PCE-derived indices <= 0.005 | not built |
| B-UQ-04 | DC resistance of a strip with a uniform width | analytic: R = c / W with c = Rs L = 24.63 mohm mm (the `power_strip` board: 4.926 mohm at W = 5 mm); W ~ U(4.5, 5.5) mm; spec R <= 5.2 mohm; exact P(pass) = P(W >= c / 5.2) = 0.7634 | exact value inside the CI; first-order estimate flagged with a nonlinear-response warning when it differs from exact by more than the CI; adjoint sensitivity dR/dW equal to the analytic -c/W^2 within 1e-6 relative | not built |
| B-UQ-05 | PCE and surrogate adequacy | analytic: B-UQ-04 response and Ishigami | PCE order 8 vs exact CDF: Kolmogorov distance <= 0.01; leave-one-out error predicts held-out error within a factor 2; an under-sampled design is refused with `UQ.SURROGATE_INADEQUATE` | not built |
| B-UQ-06 | Model-error calibration recovery | synthetic solver with known bias and spread | recovered mean and spread inside their reported 95 % intervals in 93 % to 97 % of 1000 synthetic suites; fewer than the minimum number of independent cases gives `calibration = "insufficient"`, never `calibrated`; self-computed cases do not enter the fit | not built |
| B-UQ-07 | Determinism and backend parity | bitwise on one backend | repeat runs bitwise identical; CPU and GPU batches agree within the ADR-0009 tolerance classes; the case passes with `OA_DEVICE=cpu` | not built |
| B-UQ-08 | EMC margin with correlated model error | analytic: two frequencies, margin M = 0 at both, Gaussian model error with equal spread and correlation rho; P(both pass) = 1/4 + arcsin(rho) / (2 pi), which is 1/3 for rho = 0.5. Single frequency: P = Phi(M / sigma) | exact value inside the CI; the ADR-0011 RSS budget reproduced to 0.01 dB when all terms are independent Gaussians; the product of per-frequency probabilities is shown to be wrong for rho != 0 | not built |
| B-UQ-09 | Honesty rules | contract tests | a `UqResult` cannot be built without `inputs` and `model_error`; `summary()` always includes the assumptions and the calibration status and stays within the size caps; an out-of-domain run returns `UQ.OUT_OF_DOMAIN` with no `p_pass`; accuracy is capped at `estimate` when any calibration is `insufficient` | not built |

Notes:
- B-UQ-04 uses the `power_strip` geometry and the analytic strip resistance already checked in the DC IR suite ([validation](validation/dc-ir.md)); the distribution on W is an arbitrary test input, not a fabrication tolerance.
- Real-solver UQ runs (impedance, DC IR, SI, EMC) have no separate exact reference. They inherit the accuracy gates of the cases in sections 2 and 5 and are reported with the model-error status of ADR-0012 section 3, not as gated B-UQ results.

## 6. Changing this file
Targets may be **tightened** at any time. Loosening a target needs an ADR amendment that records the measured evidence and the project owner's sign-off. References marked [verify] become gating only in the commit that adds the checked citation to the case's `case.toml`.
