<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->
# WP-513: theory-audit decisions

Source: the THEORY.md audit (branch `worktree-agent-addcf70e21a6f9bb0`). No spec was edited; every
item below was resolved inside package-owned code or docs.

## 1. oa-impedance `_physics_compat` shim removed

Decision: use `oa_physics` everywhere; `oa_impedance/_dielectric.py` keeps only the static-request
rule (`f <= 0` means the datasheet point) and `is_frequency_independent`.
Evidence: `oa_model.materials.TableDkDf` documents "interpolated log-f"; for Debye/DS-type data Dk
and Df are smooth in ln f, so linear-f interpolation over-weights the upper end of a decade-wide
interval. Example: table (1e8 Hz, Dk 4.4) to (1e10 Hz, 4.0), at 1 GHz ln f gives 4.20 and linear f
gives 4.36 (new test `tests/test_dielectric.py`).
Effect: no impedance golden or validation case uses `TableDkDf`, so the goldens (`impedance_bem`,
`impedance_closedform`) and `docs/validation/impedance.md` are unchanged and the whole
`oa-impedance` suite passes unmodified (133 passed). Other differences, all negligible and inside
tolerance: `MU0 = 4e-7 pi` (oa_physics) instead of 1.25663706212e-6 (5.5e-10 relative); roughness
at f <= 0 is evaluated at 1e-9 Hz instead of returning 1 (callers skip f <= 0 or are unaffected).
Tests that imported the shim now use `oa_physics`.

## 2. oa-network CQM: code kept, docstring corrected

Decision: keep top-half taper (`_TAPER = 0.5`) and 2/f_max guard (`_GUARD = 2`).
Evidence: the package's own notes (WP-106 change request) and the golden verdicts use 2/f_max; the
research notes (03 section 2.5) only ask for "energy before t = 0" and name no taper or guard, so
IEEE 370's intent is a precursor check that tolerates the band-limit ringing of a truncated
transform. The wider taper and guard do that more conservatively (fewer false non-causal verdicts).
Narrowing them would risk changing verdicts without a reference to calibrate against.
Impact: docstring only; no numbers change.

## 3. oa-erc IM-005: code is right, text corrected

Decision: the rule is `l < t_r v / 10`, a stub length.
Evidence: docs/research/06 section 3.2 (line 142) states it literally as "longer than L_max (or
than t_rise*v/10)", and the code and README already use it. The rule text "round trip below a tenth
of the rise time" was the wrong part: `l = t_r v/10` is a one-way delay of t_r/10, a round trip of
t_r/5. Rule explanation and README now say so.
Impact: docs only; no test or WP-102 expected outcome changes.

## 4. oa-fdtd FDTD_UNSTABLE

Decision: add cheap growth detection and document the exact behaviour.
Implementation: in `run`, at each energy check after the excitation ended (`2 * delay_tau * tau`,
the Gaussian is below e^-25), raise `FDTD_UNSTABLE` if the energy exceeds `GROWTH_LIMIT = 100`
(20 dB) times its post-excitation minimum (the scheme is passive). Active only with `end_db`
set, as the energy is only computed there. Tests: `packages/oa-fdtd/tests/test_stability.py`
(non-finite, growth, and a rise during the excitation that must not trigger). README and
FOLLOWUPS updated.

## 5. Mesh presets

Decision: cells per wavelength coarse/medium/fine are 10/20/30 for all finite-difference
paths: `oa_mesh.rect` (was 15/20/30), `oa_fdtd` and the openEMS adapter (already 10/20/30), and
the `oa_em` refinement base. `oa_mesh.occ3d` (FEM tetrahedra) stays 6/10/15; the families differ
deliberately and the oa-mesh README says so.
Evidence: three of four call sites already used 10, and `oa_fdtd` passes N explicitly to
`max_cell_size`, so the rect "coarse" entry had no consumer. ADR-0010 section 2 says 15/20/30 for
coarse; ADRs are not edited here, this record supersedes that one number. Test:
`oa-mesh/tests/test_rect.py::test_presets_match_the_fdtd_family`.

## 6. Stale docs updated

- Benchmark pages (`docs/validation/benchmarks/em.md`, `perf.md`, `index.md`) and
  `docs/BENCHMARKS.md`: "oa-fdtd does not exist" replaced with the measured facts of FOLLOWUPS
  (B-EM-01: IL error 0.005 dB at 10 GHz, 0.038 dB at 20 GHz, S11 -28.4 dB, 1.6 s; 6.0 / 3.2
  Gcells/s on 256^3 + CPML; B-EM-03 not run). Done in the case.toml files and the recorded
  results JSON, then regenerated with `tools/validation/build_page.py`. Statuses stay "pending"
  (no gated run).
- `oa-geometry` README: timings now show before and after WP-509.
- `oa-mesh` README intro: three meshers, not "reserved for WP-206".
- `oa-em-palace` README: IL gap attributed to the near first-order ABC, with the 0.10 dB remainder
  split, per WP-501-palace-il-root-cause.md.
- `oa-impedance` README: shim references replaced.
