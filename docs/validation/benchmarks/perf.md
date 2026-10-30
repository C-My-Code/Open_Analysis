# Benchmarks: perf

[Back to benchmarks](index.md)

| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | Peak host MB | Peak GPU MB | Threads | Owner / note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [B-PERF-01](index.md#b-perf-01) | kernels | cuda | **pending** | acc <= 0.0001 (\|dS\| float32 vs float64) | - | - s | - s | - | - | - | WP-505: oa-fdtd exists; 256^3 + CPML measured 6.0 Gcells/s float32 and 3.2 float64 (target >= 3.0); float32 vs float64 accuracy gate and the PCB-like ADE grid throughput are not measured |
