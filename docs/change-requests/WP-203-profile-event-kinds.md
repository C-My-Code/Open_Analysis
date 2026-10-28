# WP-203: ProfileEvent kinds, ProfileResult summary, tuning-profile format

Status: proposed. Author: WP-203. Contract: interfaces.md section 4.1 (`ProfileResult`,
`ProfileEvent`), ai-integration.md section 4.2.

## Problem

1. `ProfileEvent.kind` is `via | layer_change | ref_change | neckdown | plane_edge | other`. The
   WP-203 text and research 02 section 5.3 also require `no_reference`, `width_change` (widening),
   `coplanar_ground` and, for pairs, coupling changes (`pair_uncoupled`, `pair_coupled`,
   `pair_gap_change`).
2. `ProfileResult._stats` reports spans only. ai-integration section 4.2 requires min / max / mean Z,
   target, tolerance, out-of-tolerance length and the worst event.
3. The KiCad 10 tuning-profile file format is not specified anywhere in the repo.

## Local adapters (no spec edited)

1. Causes without a literal map to `kind="other"`; `message` starts with `causes=a,b;` and the
   precise cause list is in the `discontinuities` table.
2. `oa_impedance.profile.ImpedanceProfileResult` subclasses `ProfileResult` and overrides
   `_digest` / `_stats`, deriving everything from the base fields (node convention: values hold
   from `s[i]` to `s[i+1]`; each chain ends with a marker node). After a JSON round trip the item
   is a plain `ProfileResult` and falls back to the span summary.
3. `oa_impedance.profile.tuning` defines our own `oa-tuning-profile/1` data structure (per layer
   delay in ps/mm, min/max, eps_eff, length, nets). No file is written.

## Proposal

- Add `no_reference`, `width_change`, `coplanar_ground`, `pair_coupling` to `ProfileEvent.kind`
  (additive), and a `causes: tuple[str, ...] = ()` field.
- Move the `ImpedanceProfileResult._stats` logic into `ProfileResult` so the summary survives
  serialization, or add optional `summary_stats` fields.
- Pin the KiCad 10 tuning-profile schema in an ADR before the exporter is written.

## Impact

Additive only. `oa_results` (WP-000A), the SI/ERC consumers of `ProfileResult.events`.
