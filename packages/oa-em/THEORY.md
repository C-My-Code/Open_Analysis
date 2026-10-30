# oa-em: theory, error budget and validation

The solver-neutral full-wave flow `em.sparams`: board region to `EMProblem`, to a solver adapter (openEMS, Palace, or the in-repo FDTD), to S-parameters with a quality gate. Code: `packages/oa-em/src/oa_em/` (`problem.py`, `config.py`, `analysis.py`, `checks.py`). This package implements **no solver**; its accuracy is the accuracy of the adapter it calls, plus the correctness of the problem it builds. Solver theory: [oa-em-openems](../oa-em-openems/THEORY.md), [oa-em-palace](../oa-em-palace/THEORY.md), [oa-fdtd](../oa-fdtd/THEORY.md). See the [theory index](../../docs/theory/README.md).

## 1. Problem statement

**Input.** An `EmSparamsConfig`: solver name, a region (nets, reference nets, bounding box or automatic margin, layers), a frequency plan [Hz], mesh settings, convergence settings, boundary choice, ports (`trace_end`, `lumped`, `coax`), and options.

**Output.** An N-port network $S(f)$ with port impedances, a Touchstone file, mixed-mode ports for differential pairs, a JSON sidecar (region, port map, stack-up hash, mesh statistics, convergence, quality, runtime), and a status: `ok`, `warning`, `failed_quality` or `error`. `accuracy_class` is `3d_full_wave` (`docs/specs/ai-integration.md`), with the gate verdict in `quality`.

## 2. Governing equations and formulation

There are no field equations in this package. What it does, and what physically matters about each step:

**Region cut.** `oa_geometry.cut_region` clips copper to the box. Traces crossing the box boundary become port candidates. A cut through a real board removes everything outside it, so the S-parameters describe the cut region *terminated by the chosen boundaries*, not the whole board.

**Boundaries (`problem.py`).**

- Wave faces absorb.
- Reference planes at the $z$ faces give PEC; otherwise the face absorbs with air padding $\min\big(\max(\lambda_0/10,\ 5h_{stack}),\ \lambda_0/4\big)$ with $\lambda_0=c_0/f_{max}$ (`default_air_padding`).
- Side walls are PEC when every signal conductor lies between reference planes (stripline), else absorbing.

The reasoning: a PEC wall is only a faithful stand-in for an infinite plane of copper if the plane really extends beyond the box; and an absorbing wall needs to be far enough from the fields (a fraction of a wavelength, scaling with structure height) not to perturb the near field. These rules are heuristics.

**Ports.**

- `trace_end` becomes a wave port on the face the trace crosses; with an automatic region the trace is extended to the face (`EM.PORT_EXTENDED`).
- A differential pair (2 nets) becomes two single-ended wave ports `<name>_P`, `<name>_N`; the mixed-mode conversion `oa_network.se2gmm` ([oa-network](../oa-network/THEORY.md)) gives `D_*` and `C_*` ports. This assumes the pair is symmetric and weakly coupled across the port plane; coupled-line port physics (two-mode ports) is not used.
- `lumped` goes through `resolve_terminal`; `coax` becomes `EMCoaxPort`.
- Port impedance is compared with the closed-form line impedance from the `impedance` service; a deviation above `z0_tolerance` (default 10 %) raises `EM.PORT_Z0_MISMATCH`.

**De-embedding.** Reference planes are shifted by the `deembed` offset of the port. Only the delay (a lossless matched line of that delay) is removed: `oa_network.shift_reference_plane`. Port-region mismatch is *not* removed unless the 2x-thru hook is used.

**Convergence checks (`analysis.py`).**

- Energy decay: the run is converged if the final field energy is at most `convergence.energy_decay_db` (default -40 dB) plus `energy_margin_db` (default 3 dB) below its peak; otherwise `EM.NOT_CONVERGED`.
- Mesh refinement (`refinement_check`): the same problem is re-run with a cells-per-wavelength factor of `refinement_factor = 1.4`; the maximum $|\Delta S|$ must be at most `convergence.delta_s` (default 0.05), otherwise `EM.MESH_NOT_CONVERGED`. This is a *single* refinement step, not a Richardson study, so it detects but does not quantify discretisation error.

**Quality gate.** `oa_network.gate` is applied to every result: verdict `fail` makes the status `failed_quality`.

## 3. Assumptions and validity range

- **Frequency range.** Validated 1 to 10 GHz (microstrip) and 4 to 20 GHz (via transition); the full 0 to 40 GHz band is not validated (FOLLOWUPS WP-205).
- **Low-frequency floor.** A DC-free FDTD pulse puts little energy below about $0.1f_{max}$; S-parameters below are noise (observed S11 about -11 dB, RQM 93, $\sigma_{max}$ 1.09 on the via fixture). For openEMS the sweep therefore starts at $0.2f_{max}$ (`_FMIN_FRACTION`) and the quality gate runs with `require_dc="none"` (`EM.FMIN_RAISED`). The default is `raise_fmin = True`.
- **Absorbing boundary workarounds.** For wave ports with openEMS the flow uses MUR instead of PML (`_WAVE_PORT_DEFAULTS`) because PML plateaued at -31 dB in the openEMS adapter; `est_time_s` is scaled by 0.1 (`_EST_SCALE`) because openEMS estimates were pessimistic (316 s estimated against 21 s actual).
- **Stripline.** Closed-box runs in openEMS stall near -33 dB energy and give non-passive S-parameters (open FOLLOWUPS item); not validated.
- **Differential path** has no solve validation; only the mixed-mode conversion is tested on an uncoupled pair.
- **Copper model.** Whatever the adapter does (openEMS: zero-thickness conducting sheet by default). The Z0 consequence is below.
- **Ports.** Coplanar, coax and coupled wave ports are unsupported by the openEMS adapter; every port must be excited (no partial S matrix).

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| openEMS sheet copper versus 35 µm trapezoid | Z0 50.73 ohm against 48.72 ohm (BEM, trapezoid): +4.1 %; against thin-strip BEM 50.95 ohm: -0.43 % | `em.openems.z0`, `em.openems.z0_35um` (known gap) |
| openEMS IL against BEM RLGC cascade | 0.689 dB against 0.778 dB at 10 GHz (23 mm line): -0.089 dB | `em.openems.il_10ghz` (gate 0.2 dB, pass) |
| Matched-line sanity | max S11 -33.8 dB (bound -25 dB) | `em.openems.s11_max` |
| Single-pole Debye fit of Djordjevic-Sarkar | about 11 % tan-delta error over 0.5 to 10 GHz | FOLLOWUPS openEMS |
| Refinement check | $|\Delta S|\le0.05$ by default | `convergence.delta_s` |
| De-embedding, port-region mismatch | not quantified here | no case |
| Input error (stack-up, Dk/Df, roughness, etch) | not quantified here | planned UQ ([ADR-0012](../../docs/adr/0012-uncertainty-quantification.md)) |

## 5. Validation evidence

Only microstrip thru and via-transition fixtures have run end to end.

- **Cross-solver**: openEMS FDTD against the BEM RLGC cascade on a 23 mm testkit microstrip (`docs/validation/results.json`, category `em`): IL, Z0 (thin-strip), S11 and PQM gate cases pass; the 35 µm Z0 case is a recorded known gap.
- **Pending**: Palace slots (4 cases, `palace_pending_wp301`), the openEMS MSL-notch and patch-antenna tutorial reproductions (published, [verify]). Of 11 `em` cases: 4 pass, 1 known gap, 6 pending.
- Unit tests use a mock adapter through `oa_testkit.harness`; `external("openems")` tests run the real solver for the microstrip thru (analytic line) and the via transition.
- Benchmarks B-EM-01 to B-EM-06 have no gated openEMS or Palace result (`docs/validation/benchmarks/em.md`: `ref-pending`). The converged reference R1 (2D RLGC cascade) and R2 (Palace p3 with AMR) are not yet produced, so the 0.05 dB gate of B-EM-01 is not evaluated for these solvers.

**Known gaps and open issues (FOLLOWUPS WP-205).**

- Test portability: the agent's test files do not run on Linux/macOS (make them skip or become portable).
- openEMS install lives only in a finished worktree; move it into the main checkout's `.oa/tools/`.
- Full 0 to 40 GHz band is not validated; stripline wave-port stall unresolved; differential path has no solve.
- Z0 3.8 % off the 35 µm BEM value: add thick-copper modelling or document.
- The B-EM-01 openEMS IL of 0.74 dB (25 mm) is 0.10 dB below the BEM figure of 0.845 dB (WP-501), twice the 0.05 dB gate.

## 6. References

- Project: `docs/specs/interfaces.md` section 5.3 (solver adapter contract), ADR-0004 (solver integration), `docs/adr/0010-gpu-fdtd-engine.md`, `docs/BENCHMARKS.md` B-EM-01 to B-EM-06, `docs/research/05-fullwave-em.md`; change requests `docs/change-requests/WP-205-*.md`.
- IEEE Std 370-2020 for the quality metrics (see [oa-network](../oa-network/THEORY.md)).
- Rautio, J. C., "An ultra-high precision benchmark for validation of planar electromagnetic analyses", cited in BENCHMARKS.md for the planned exact stripline case [verify].
