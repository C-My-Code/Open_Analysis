# Benchmarks

Gates and protocol: [BENCHMARKS.md](../../BENCHMARKS.md). Every row is produced by `tools/bench/run.py` from the committed files in `tests/benchmarks/results/`; nothing is typed in by hand. Times are the median of warm runs; the cold run is shown separately.

Latest file `2026-10-29-55ec8f55-Bigama.json`: AMD Ryzen 9 9950X3D 16-Core Processor, 32 logical cores, 47.1 GB, GPU none; profile `bench` (8 threads, 16 GB cap); CPU-load check skipped.

## Cases

| Case | Title | Status | Sub-cases |
|---|---|---|---|
| [B-DC-01](index.md#b-dc-01) | DC IR accuracy | **partial** | 1 of 3 pass |
| [B-DC-02](index.md#b-dc-02) | DC IR scale | **ready** | 0 of 3 pass |
| [B-EM-01](index.md#b-em-01) | Microstrip thru 25 mm, to 20 GHz | **ref-pending** | 0 of 3 pass |
| [B-EM-02](index.md#b-em-02) | Stripline thru: exact lossless + lossy FR-4 | **ref-pending** | 0 of 3 pass |
| [B-EM-03](index.md#b-em-03) | Differential via transition, 8 layers, to 40 GHz | **ref-pending** | 0 of 2 pass |
| [B-EM-04](index.md#b-em-04) | CPWG launch with via fence, to 40 GHz | **ref-pending** | 0 of 2 pass |
| [B-EM-05](index.md#b-em-05) | 2.4 GHz antennas (patch, IFA) | **ref-pending** | 0 of 2 pass |
| [B-EM-06](index.md#b-em-06) | High-Q filters (10 GHz hairpin; MSL notch) | **ref-pending** | 0 of 2 pass |
| [B-ERC-01](index.md#b-erc-01) | ERC exactness and scale | **partial** | 1 of 4 pass |
| [B-IMP-01](index.md#b-imp-01) | Exact 2D cross-sections | **fail** | 0 of 1 pass |
| [B-IMP-02](index.md#b-imp-02) | SI9000-class structure set (24) | **ref-pending** | 0 of 1 pass |
| [B-IMP-03](index.md#b-imp-03) | Frequency-dependent RLGC and loss | **ref-pending** | 0 of 1 pass |
| [B-IMP-04](index.md#b-imp-04) | Batch throughput, 1000 sections x 50 f | **pending** | 0 of 1 pass |
| [B-ING-01](index.md#b-ing-01) | Ingest and ReferenceMap at scale | **partial** | 1 of 4 pass |
| [B-PERF-01](index.md#b-perf-01) | FDTD kernel throughput | **pending** | 0 of 1 pass |

## Groups

- [dc-ir](dc-ir.md): 6 sub-cases
- [em](em.md): 14 sub-cases
- [erc](erc.md): 4 sub-cases
- [impedance](impedance.md): 4 sub-cases
- [ingest](ingest.md): 4 sub-cases
- [perf](perf.md): 1 sub-cases

## Case references

### B-DC-01

DC IR accuracy. Spec: [docs/BENCHMARKS.md#b-dc-01-dc-ir-accuracy](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: <= 0.1 % analytic; <= 1 % converged; <= 3 % published. Today: passes (kicad-irdrop not run)
- Reference class: analytic. R = Rs L / W (strip); annulus; via barrel; converged power_plane (Richardson); kicad-irdrop 3.04 mOhm / padne 2.97 mOhm [verify]
- Reference data: `tests/boards/kicad10/power_strip/expected.yaml`
- Case file: `tests/benchmarks/cases/B-DC-01/case.toml`

### B-DC-02

DC IR scale. Spec: [docs/BENCHMARKS.md#b-dc-02-dc-ir-scale](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: as B-DC-01. Runtime: 3e5: <= 5 s CPU; 1e6: <= 15 s / <= 8 s. Memory: 4 GB host, 4 GB GPU. Today: 3e5 in 10.5 s
- Reference class: converged. power_plane board refined to 3e5 / 1e6 unknowns; as B-DC-01
- Reference data: `none`
- Case file: `tests/benchmarks/cases/B-DC-02/case.toml`

### B-EM-01

Microstrip thru 25 mm, to 20 GHz. Spec: [docs/BENCHMARKS.md#b-em-01-microstrip-thru-25-mm-01-20-ghz](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: IL <= 0.05 dB; |S11| <= 1 dB to -30 dB. Runtime: FDTD <= 10 s GPU; Palace <= 300 s CPU / <= 90 s GPU; openEMS <= 60 s. Memory: 2 GB GPU; Palace 6 GB. Today: IL 0.74 dB (openEMS) vs 1.24 dB (Palace)
- Reference class: converged. R1 2D RLGC cascade and R2 Palace p3 AMR, agreeing within 0.02 dB
- Reference data: `tests/benchmarks/references/em/B-EM-01`
- Case file: `tests/benchmarks/cases/B-EM-01/case.toml`

### B-EM-02

Stripline thru: exact lossless + lossy FR-4. Spec: [docs/BENCHMARKS.md#b-em-02-stripline-thru](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: exact: |S21| <= 0.01 dB, phase <= 0.5 deg; lossy as B-EM-01. Runtime: FDTD <= 10 s GPU; Palace <= 300 s / <= 90 s. Memory: as B-EM-01. Today: openEMS stalls at -33 dB
- Reference class: converged. Cohn exact formula (lossless); R1/R2 (lossy); Rautio 1994 [verify pages]
- Reference data: `tests/benchmarks/references/em/B-EM-02`
- Case file: `tests/benchmarks/cases/B-EM-02/case.toml`

### B-EM-03

Differential via transition, 8 layers, to 40 GHz. Spec: [docs/BENCHMARKS.md#b-em-03-differential-via-transition-8-layers-01-40-ghz](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: SDD21 <= 0.1 dB to 20 GHz, <= 0.25 dB to 40 GHz; SDD11 <= 1.5 dB above -30 dB. Runtime: FDTD <= 5 min GPU; Palace <= 20 min GPU / <= 60 min CPU. Memory: 8 GB GPU; Palace 12 GB. Today: not run
- Reference class: converged. Palace p3 AMR (max dS <= 0.005) cross-checked by oa-fdtd; IEEE 370 fixture [verify]
- Reference data: `tests/benchmarks/references/em/B-EM-03`
- Case file: `tests/benchmarks/cases/B-EM-03/case.toml`

### B-EM-04

CPWG launch with via fence, to 40 GHz. Spec: [docs/BENCHMARKS.md#b-em-04-cpwg-launch-01-40-ghz](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: |S21| <= 0.1 dB; |S11| <= 2 dB above -25 dB. Runtime: FDTD <= 3 min GPU; Palace <= 15 min GPU. Memory: 6 GB GPU; Palace 12 GB. Today: not run
- Reference class: converged. as B-EM-03; vendor end-launch note [verify, none selected]
- Reference data: `tests/benchmarks/references/em/B-EM-04`
- Case file: `tests/benchmarks/cases/B-EM-04/case.toml`

### B-EM-05

2.4 GHz antennas (patch, IFA). Spec: [docs/BENCHMARKS.md#b-em-05-24-ghz-antennas](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: f_res <= 0.5 % (converged), <= 1 % (published); S11 min <= 1 dB. Runtime: FDTD <= 60 s GPU; openEMS <= 10 min. Memory: 4 GB GPU. Today: not run
- Reference class: converged. openEMS Simple Patch Antenna tutorial; Palace converged; TI DN007 [verify]
- Reference data: `tests/benchmarks/references/em/B-EM-05`
- Case file: `tests/benchmarks/cases/B-EM-05/case.toml`

### B-EM-06

High-Q filters (10 GHz hairpin; MSL notch). Spec: [docs/BENCHMARKS.md#b-em-06-high-q-filters](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: f_c <= 0.2 %; passband IL <= 0.1 dB; BW <= 1 %. Runtime: Palace fast sweep <= 10 min GPU / <= 30 min CPU; FDTD <= 15 min GPU. Memory: 12 GB. Today: not run
- Reference class: converged. Palace p3 adaptive fast sweep; openEMS MSL notch tutorial; Hong-Lancaster [verify]
- Reference data: `tests/benchmarks/references/em/B-EM-06`
- Case file: `tests/benchmarks/cases/B-EM-06/case.toml`

### B-ERC-01

ERC exactness and scale. Spec: [docs/BENCHMARKS.md#b-erc-01-erc-exactness-and-scale](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: exact match. Runtime: combined <= 0.5 s; 10k <= 3 s; 100k <= 30 s. Memory: 2 GB. Today: 1.8 s; 34.9 s; -
- Reference class: analytic. tests/boards/kicad10/*/expected.yaml (constructed by tools/boardgen, independent of the rule engine)
- Reference data: `tests/boards/kicad10/combined/expected.yaml`
- Case file: `tests/benchmarks/cases/B-ERC-01/case.toml`

### B-IMP-01

Exact 2D cross-sections. Spec: [docs/BENCHMARKS.md#b-imp-01-exact-2d-cross-sections](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: Z0 <= 0.05 %. Runtime: <= 20 ms per section (CPU). Memory: 200 MB. Today: 0.001-0.22 %; 16-39 ms
- Reference class: analytic. Cohn 1954 (exact conformal map); 60 ln(b/a) coax; CPW conformal map (Ghione-Naldi 1984); locked in tests/golden/impedance_bem
- Reference data: `tests/golden/impedance_bem`
- Case file: `tests/benchmarks/cases/B-IMP-01/case.toml`

### B-IMP-02

SI9000-class structure set (24). Spec: [docs/BENCHMARKS.md#b-imp-02-si9000-class-structure-set](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: Z0 <= 0.5 %, Zdiff <= 1 %, eeff <= 0.3 %. Runtime: <= 30 ms SE / <= 60 ms diff (CPU). Memory: 300 MB. Today: eeff up to 0.59 % off FEM
- Reference class: converged. 2D FEM P2/P3 Richardson-extrapolated; BEM at 4x density as second method; published SI9000/Wadell slots are [verify]
- Reference data: `tests/benchmarks/references/impedance/B-IMP-02`
- Case file: `tests/benchmarks/cases/B-IMP-02/case.toml`

### B-IMP-03

Frequency-dependent RLGC and loss. Spec: [docs/BENCHMARKS.md#b-imp-03-frequency-dependent-rlgc-and-loss](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: R(f) <= 1 %, alpha <= 2 %. Runtime: <= 0.5 s per section, 50 freqs (CPU). Memory: 500 MB. Today: partly covered
- Reference class: analytic. Bessel round wire; Djordjevic-Sarkar round trip; converged eddy-current FEM; Huray curve is [verify] (Simonovich, DesignCon 2015)
- Reference data: `tests/benchmarks/references/impedance/B-IMP-03`
- Case file: `tests/benchmarks/cases/B-IMP-03/case.toml`

### B-IMP-04

Batch throughput, 1000 sections x 50 f. Spec: [docs/BENCHMARKS.md#b-imp-04-batch-throughput-impedance-profile-scale](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: as B-IMP-02. Runtime: <= 20 s / <= 5 s. Memory: 4 GB host, 4 GB GPU. Today: ~16-39 s static only
- Reference class: converged. each result vs its CPU float64 single-section result; GPU vs CPU <= 1e-10 (ADR-0009 section 5)
- Reference data: `none`
- Case file: `tests/benchmarks/cases/B-IMP-04/case.toml`

### B-ING-01

Ingest and ReferenceMap at scale. Spec: [docs/BENCHMARKS.md#b-ing-01-ingest-and-referencemap-at-scale](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: counts exact, areas <= 0.5 %. Runtime: Jetson parse <= 2.5 s; RefMap 50k <= 3 s. Memory: 3 GB. Today: 9.9 s; 51 s
- Reference class: analytic. WP-102 expected.yaml; pcbnew-extracted golden for cm5_minima (tests/golden/ingest_kicad)
- Reference data: `tests/golden/ingest_kicad/cm5_minima`
- Case file: `tests/benchmarks/cases/B-ING-01/case.toml`

### B-PERF-01

FDTD kernel throughput. Spec: [docs/BENCHMARKS.md#b-perf-01-fdtd-kernel-throughput](../../../docs/BENCHMARKS.md).

- Targets (verbatim from BENCHMARKS.md section 2): Accuracy: float32 vs float64 |dS| <= 1e-4. Runtime: >= 3.0 Gcells/s plain; >= 1.5 Gcells/s ADE+CPML. Today: 6.0 / 3.2 Gcells/s measured on 256^3 + CPML; ADE+CPML not measured
- Reference class: converged. float64 engine on ADR-0010 section 9 cases 1-3
- Reference data: `none`
- Case file: `tests/benchmarks/cases/B-PERF-01/case.toml`
