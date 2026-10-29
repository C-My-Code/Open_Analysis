# Benchmarks: ingest

[Back to benchmarks](index.md)

| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | Peak host MB | Peak GPU MB | Threads | Owner / note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [B-ING-01](index.md#b-ing-01) | cm5_parse | cpu | **ready** | acc <= 0 (error diagnostics); time <= 1 s per run; mem <= 3072 MB | - | - s | - s | - | - | - | ready to run: heavy case, needs the heavy token and --allow-heavy |
| [B-ING-01](index.md#b-ing-01) | jetson_parse | cpu | **ready** | acc <= 0 (error diagnostics); time <= 2.5 s per run; mem <= 3072 MB | - | - s | - s | - | - | - | ready to run: heavy case, needs the heavy token and --allow-heavy |
| [B-ING-01](index.md#b-ing-01) | refmap50k | cpu | **pending** | acc <= 0.5 (% area); time <= 3 s per run; mem <= 3072 MB | - | - s | - s | - | - | - | WP-508: 50k-track generator and the native ReferenceMap are not implemented |
| [B-ING-01](index.md#b-ing-01) | wp102_parse | cpu | **pass** | acc <= 0 (failed findings (exact)) | 0 | 0.176 s | 0.2238 s | 97.53 | - | 8 |  |
