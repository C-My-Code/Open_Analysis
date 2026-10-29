# Benchmarks: impedance

[Back to benchmarks](index.md)

| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | Peak host MB | Peak GPU MB | Threads | Owner / note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [B-IMP-01](index.md#b-imp-01) | sections | cpu | **fail** | acc <= 0.05 (% max error vs exact); time <= 0.02 s per section; mem <= 200 MB | 0.2317 | 0.2087 s | 1.438 s | 126.8 | - | 8 | over: accuracy |
| [B-IMP-02](index.md#b-imp-02) | set24 | cpu | **ref-pending** | acc <= 0.5 (% Z0 vs converged); time <= 0.03 s per section; mem <= 300 MB | - | - s | - s | - | - | - | WP-510: converged FEM references and the 24-structure generator are not produced yet (accuracy closure 2D) |
| [B-IMP-03](index.md#b-imp-03) | rlgc | cpu | **ref-pending** | acc <= 1 (% R(f)); time <= 0.5 s per section; mem <= 500 MB | - | - s | - s | - | - | - | WP-510: converged eddy-current FEM references not produced yet |
| [B-IMP-04](index.md#b-imp-04) | batch | cpu | **pending** | time <= 20 s per run; mem <= 4096 MB | - | - s | - s | - | - | - | WP-504: batch case generator and the oa-accel path (solve_many on GPU) are not implemented |
