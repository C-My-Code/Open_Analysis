# Benchmarks: erc

[Back to benchmarks](index.md)

| Case | Sub-case | Device | Status | Target | Measured accuracy | Median warm | Cold | Peak host MB | Peak GPU MB | Threads | Owner / note |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [B-ERC-01](index.md#b-erc-01) | combined | cpu | **pass** | acc <= 0 (mismatches (exact)); time <= 0.5 s per run; mem <= 2048 MB | 0 | 0.3911 s | 3.76 s | 462.9 | - | 8 |  |
| [B-ERC-01](index.md#b-erc-01) | native_vs_python | cpu | **pending** | acc <= 0 (mismatches) | - | - s | - s | - | - | - | WP-509: oa-native ERC not implemented |
| [B-ERC-01](index.md#b-erc-01) | tracks100k | cpu | **pending** | acc <= 0 (mismatches); time <= 30 s per run; mem <= 2048 MB | - | - s | - s | - | - | - | WP-509: synthetic board generator and native ERC path not implemented |
| [B-ERC-01](index.md#b-erc-01) | tracks10k | cpu | **pending** | acc <= 0 (mismatches); time <= 3 s per run; mem <= 2048 MB | - | - s | - s | - | - | - | WP-509: synthetic board generator and native ERC path not implemented |
