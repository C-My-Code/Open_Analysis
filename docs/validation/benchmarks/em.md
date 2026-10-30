# Benchmarks: em

[Back to benchmarks](index.md)

| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | Peak host MB | Peak GPU MB | Threads | Owner / note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [B-EM-01](index.md#b-em-01) | oa_fdtd | cpu | **pending** | not gated | - | - s | - s | - | - | - | WP-505/506/507: oa-fdtd exists (WP-505/506/506A) but is not gated; measured on GPU: IL error 0.005 dB at 10 GHz and 0.038 dB at 20 GHz, S11 -28.4 dB (target -29), 1.6 s, 0.02 GiB |
| [B-EM-01](index.md#b-em-01) | openems | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-510: reference R1 (2D RLGC cascade) not produced yet; the openEMS adapter exists but is not gated |
| [B-EM-01](index.md#b-em-01) | palace | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-501: converged Palace reference and converged configuration not produced yet; Palace stays CPU-only (no CUDA in WSL, M1P decisions) |
| [B-EM-02](index.md#b-em-02) | oa_fdtd | cpu | **pending** | not gated | - | - s | - s | - | - | - | WP-505/506/507: oa-fdtd exists (WP-505/506/506A) but this case is not gated or run yet |
| [B-EM-02](index.md#b-em-02) | openems | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-510: reference R1 (2D RLGC cascade) not produced yet; the openEMS adapter exists but is not gated |
| [B-EM-02](index.md#b-em-02) | palace | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-501: converged Palace reference and converged configuration not produced yet; Palace stays CPU-only (no CUDA in WSL, M1P decisions) |
| [B-EM-03](index.md#b-em-03) | oa_fdtd | cpu | **pending** | not gated | - | - s | - s | - | - | - | WP-505/506/507: oa-fdtd exists (WP-505/506/506A) but the 40 GHz differential via was not run; vias are staircased (no conformal PEC) |
| [B-EM-03](index.md#b-em-03) | palace | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-501: converged Palace reference and converged configuration not produced yet; Palace stays CPU-only (no CUDA in WSL, M1P decisions) |
| [B-EM-04](index.md#b-em-04) | oa_fdtd | cpu | **pending** | not gated | - | - s | - s | - | - | - | WP-505/506/507: oa-fdtd exists (WP-505/506/506A) but this case is not gated or run yet |
| [B-EM-04](index.md#b-em-04) | palace | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-501: converged Palace reference and converged configuration not produced yet; Palace stays CPU-only (no CUDA in WSL, M1P decisions) |
| [B-EM-05](index.md#b-em-05) | oa_fdtd | cpu | **pending** | not gated | - | - s | - s | - | - | - | WP-505/506/507: oa-fdtd exists (WP-505/506/506A) but this case is not gated or run yet |
| [B-EM-05](index.md#b-em-05) | openems | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-510: reference R1 (2D RLGC cascade) not produced yet; the openEMS adapter exists but is not gated |
| [B-EM-06](index.md#b-em-06) | oa_fdtd | cpu | **pending** | not gated | - | - s | - s | - | - | - | WP-505/506/507: oa-fdtd exists (WP-505/506/506A) but this case is not gated or run yet |
| [B-EM-06](index.md#b-em-06) | palace | cpu | **ref-pending** | not gated | - | - s | - s | - | - | - | WP-501: converged Palace reference and converged configuration not produced yet; Palace stays CPU-only (no CUDA in WSL, M1P decisions) |
