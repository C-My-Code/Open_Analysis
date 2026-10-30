# oa-erc

ERC/EMI rule engine and the M1 rules (WP-201). Implements interfaces.md 3.5 (Rule protocol), 3.6
(catalogue, EMI aliases) and 3.7 (Violation), plus ai-integration section 5 and 13 (A7, A8).

## Public API

```python
from oa_erc import run_rules, sort_violations, propose_dru, apply_dru, compact_summary

vs = run_rules(
    board,
    intent=intent,
    rule_ids=["RP-001", "E4"],
    rule_config={"ED-002": {"min_distance": 0.0015}},
)  # direct, no jobs
```

- Analysis `kind="erc"` (`oa_erc.analysis.ErcAnalysis`, entry point `erc`): one task per rule,
  reduced to a `ViolationSet` item. `ErcConfig`: `rules`, `exclude` (ids or aliases),
  `rule_config` (per-rule thresholds), `severity` (override per rule), `waivers`
  (`oa_model.setup.Waiver`), `as_of` (date for waiver expiry; set it for reproducible output).
- Waivers match by fingerprint, or by rule id and/or net globs; expired ones do not apply and
  give `ERC.WAIVER_EXPIRED`. Waived violations stay in the set with `waived` set.
- Output is deterministic: rules in id order, violations sorted by (rule, layers, nets, location,
  fingerprint); fingerprints are hashes of (rule, items, 10 um location, layers).
- `compact_summary(vset)`: counts by rule and the worst 5 within 2 KiB (change request
  WP-201-violationset-summary).
- `propose_dru(violations, path, existing=...)` returns a `DruProposal` (text, diff, base hash)
  for the `dru_rule` actions. It never writes. `apply_dru` is dry-run by default and writes only
  with `allow_writes=True, dry_run=False` and an unchanged base (ai-integration 10.3).

## Scope and intent

`ctx.nets_in_scope()` = nets the intent marks `high_speed`; if the intent marks none, every
net of kind `signal` is assumed high-speed. Every `Config` has a `nets: NetSelector`.
IM-002 needs an impedance target (net class, diff pair or intent).

## Rules (all `default_severity`, thresholds and citations are in each rule's `Config` and
`references`; `explanation` holds the physics)

| ID (alias) | Rule | Key default |
|---|---|---|
| RP-001 (E2) | Gap/slot in the adjacent reference plane | run < 5 mm, bounded by reference |
| RP-002 | Antipad/void cluster under the trace | >= 3 dips, coverage < 0.8 |
| RP-003 | Reference net changes along the route | GND-to-GND ignored |
| RP-004 (E4) | Layer change without return via / stitching cap | 2.5 mm / 5 mm |
| RP-007 (E3) | No adjacent reference plane | run >= 5 mm |
| ED-001 | Trace near a plane edge | < 3h, run >= 2 mm |
| ED-002 (E1) | Trace/via near board edge | max(1 mm, 3h); edge launches ignored |
| ED-003 (E5) | Edge stitching via pitch | lambda/20 at 1 GHz |
| IM-001 | Width differs from net-class width | 2 %; skipped with an impedance target |
| IM-002 | Z0/Zdiff (BEM) out of tolerance | intent tolerance, 10 % |
| IM-003 | Neck-down too long | 5 mm |
| IM-005 | Stub too long | 3 mm or t_r v / 10 (one-way delay < t_r / 10) |
| DP-001 / 002 / 003 | Uncoupled length, skew, gap variation | 2 mm, 1 ps, 25 % over 2 mm |
| VI-001 | Via stub length / resonance | 0.25 mm; dead-end vias skipped |
| VI-002 | Vias per routed net | 4 |
| XT-001 (E8) | Parallel coupled length | 3W centre spacing, 10 mm |
| PD-001 (E6) | Decap too far from IC power pin | lambda/20 at 1 GHz |
| RF-001 | Via fence pitch | lambda/10 at 10 GHz |
| EM-011 (E11) | Plane-cavity mode near a clock harmonic | needs `clock_frequencies`, 5 % |

Known gaps: stripline gaps are judged only when no side is referenced; XT-001 has no broadside
(adjacent-layer) mode; ED-003 covers the board outline only; ED-001 sees only partially covered
runs; PD-001 has no per-pin coverage mode; EM-011 does not check the "fed by a high-speed via"
condition; RP-004 uses straight-line distances.

## Rule authoring guide (third-party rules)

1. Subclass `oa_erc.ErcRule`: set `id`, `title`, `category`, `aliases`, `default_severity`,
   `requires` (service names), `references`, `explanation`, and a `Config` (subclass of
   `oa_erc.RuleConfig`; every field needs a default and a `description` with its unit).
2. Implement `check(ctx, cfg)` as a pure generator of violations built with `self.violation(...)`.
   Give a `TypedRemediation` with at least one `TypedAction`; use `applies_via="board_edit"` for
   anything a human must change in the EDA tool, `dru_rule`/`netclass`/`stackup`/`setup` when
   Open_Analysis can propose the change. Threshold rules set `measured`, `limit` and `unit`.
3. Register in your `pyproject.toml`:
   ```toml
   [project.entry-points."open_analysis.rules"]
   "XX-001" = "my_pkg.rules:MyRule"
   ```
   The engine (`discover_rules`) and `oa_framework.Registry.load()` pick it up; a duplicate id
   with a different class is an error. Rules must not import other analysis packages: reach the
   impedance solver through `ctx.services().get("impedance")`.

## Performance (WP-509)

10k tracks, all rules, IM-002 on every net (`tests/test_builders_perf.py` board): 13.1 s before
WP-509, 6.2 s after (`docs/perf/PROFILE-509.md`). Violation sets of the WP-102 boards (at their
intent and at a deliberately wrong 40 ohm target) are byte-identical.

## Golden suites and tests

`tests/boards/kicad10/*/expected.yaml` is the oracle (`test_boards.py`); `test_contract.py` covers
the AI contract, SARIF validity, waivers, DRU and the analytic checks (stub resonance, cavity
f10 = 1.807 GHz); `test_builders_perf.py` has negative/injected fixtures and the 10k-track timing.
Change requests: `docs/change-requests/WP-201-*.md`.

## Theory and validation

Equations, numerical method, validity range, error budget and validation evidence: [THEORY.md](THEORY.md).
