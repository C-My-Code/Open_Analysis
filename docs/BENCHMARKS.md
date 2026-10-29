# Benchmarks (M1P gate)

Status: v1, 2026-10-29. This file is the benchmark specification that gates milestone **M1P** ([ROADMAP](ROADMAP.md)) and through it the M1 release. The harness that runs it, records results and publishes them is WP-304 ([TASKS](TASKS.md)). GPU design: [ADR-0009](adr/0009-gpu-acceleration.md); FDTD engine: [ADR-0010](adr/0010-gpu-fdtd-engine.md).

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

## 5. Changing this file
Targets may be **tightened** at any time. Loosening a target needs an ADR amendment that records the measured evidence and the project owner's sign-off. References marked [verify] become gating only in the commit that adds the checked citation to the case's `case.toml`.
