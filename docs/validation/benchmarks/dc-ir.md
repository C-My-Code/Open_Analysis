# Benchmarks: dc-ir

[Back to benchmarks](index.md)

| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | Peak host MB | Peak GPU MB | Threads | Owner / note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [B-DC-01](index.md#b-dc-01) | kicad_irdrop | cpu | **verify** | acc <= 3 (% vs published) | - | - s | - s | - | - | - | WP-510: published values are [verify]: not checked against the repositories |
| [B-DC-01](index.md#b-dc-01) | power_plane_converged | cpu | **ref-pending** | acc <= 1 (% vs converged) | - | - s | - s | - | - | - | WP-510: converged reference not produced yet |
| [B-DC-01](index.md#b-dc-01) | power_strip | cpu | **pass** | acc <= 0.1 (% error vs analytic) | 0.01857 | 0.03911 s | 4.521 s | 118.3 | - | 8 |  |
| [B-DC-02](index.md#b-dc-02) | n1e6 | cpu | **ready** | time <= 15 s per run; mem <= 4096 MB | - | - s | - s | - | - | - | ready to run: heavy case, needs the heavy token and --allow-heavy |
| [B-DC-02](index.md#b-dc-02) | n3e5_cpu | cpu | **ready** | time <= 5 s per run; mem <= 4096 MB | - | - s | - s | - | - | - | ready to run: heavy case, needs the heavy token and --allow-heavy |
| [B-DC-02](index.md#b-dc-02) | raster1e7 | cpu | **pending** | time <= 60 s per run | - | - s | - s | - | - | - | WP-504: runner not written; GPU path (oa-accel) not implemented |
