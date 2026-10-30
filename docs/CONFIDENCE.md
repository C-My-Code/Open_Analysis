<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# How to read Open_Analysis results

This page explains what the accuracy statements on results mean, where errors come from, and how to turn a number into a decision about margin. Numbers here come from [`docs/validation/results.json`](validation/results.json) (run of 2026-10-29, commit `0cc834dc47ea`, Windows 11, Python 3.13.14), [FOLLOWUPS.md](FOLLOWUPS.md) and [BENCHMARKS.md](BENCHMARKS.md). When the validation suite is re-run, the page is regenerated and these numbers may change; the regenerated page wins.

One rule applies throughout: **a validation error is not an uncertainty on your design.** It tells you how well the code solves the equations for known cases. It says nothing yet about whether the equations describe your board, or whether your material data and fabrication tolerances are what you assumed. Sections 3 and 4 separate those.

## 1. Accuracy classes

Every result item carries an accuracy label, so that nobody quotes a screening number as a field-solved one.

The programmatic label (`ResultItem.accuracy`, type `AccuracyClass` in `oa_results`) has five values:

| Label | Meaning (from the code) |
|---|---|
| `reference` | Converged, independently checked result |
| `validated` | Method validated against references for this class of problem |
| `screening` | Fast closed-form or rule-based result, good for triage only |
| `estimate` | Order-of-magnitude |
| `unverified` | The producer did not say. Treat as unverified |

The method families behind them are described in [agents/accuracy.md](agents/accuracy.md) (`closed_form`, `2d_field`, `2d_sheet`, `3d_full_wave`, `estimate` and others). The two vocabularies are not fully merged yet (FOLLOWUPS: "CR WP-204-accuracy-class"). In practice, today:

- `impedance xs` with a closed-form topology reports `accuracy_class: screening`.
- `pi.dc` field items report `validated`.

For an engineer the classes work out as follows.

| Class | What it is | Model assumption | Where it breaks |
|---|---|---|---|
| **Closed form** | Hammerstad-Jensen, Cohn, Wheeler, IPC-2141, CPW formulas | Uniform cross-section, quasi-TEM, fits to a stated range of w/h, er, t/h | Outside the fitted range (results carry `validity: extrapolated` or `invalid`); embedded microstrip uses an approximate exponent of 1.55 with no primary source (FOLLOWUPS); no closed form for broadside-coupled lines; no dispersion for coupled lines or CPW |
| **2D quasi-static** (BEM; FEM reference) | Laplace/Poisson solve of the cross-section, giving C, L, then Z0, eps_eff and RLGC(f) with skin-effect models | Quasi-TEM: the field is the static field. Cross-section is uniform along the line | Above the frequency where the line stops being quasi-TEM (dispersion, higher modes). At discontinuities (vias, stubs, plane splits, connectors). Where copper is a zero-thickness sheet: DC resistance is undefined |
| **2D sheet** (DC IR) | Conduction on copper sheets with via barrels as resistors | DC, uniform sheet resistance, ideal pad equipotentials | Series elements and multi-rail trees (not done). Thermal feedback on resistivity (planned) |
| **3D full wave** (openEMS today; Palace in progress; `oa-fdtd` in progress) | Maxwell's equations on a mesh (FDTD or FEM), ports, S-parameters | Geometry as meshed, materials as given, ports as modelled | Mesh too coarse; port or boundary artefacts; the band outside which the dielectric fit is poor; thin copper treated as a sheet |
| **Screening / rule** | ERC rules and DM/CM estimates | Heuristic thresholds on geometry | By design only for triage; the EMI estimates are not a level prediction |

## 2. What "validated against X within Y" means

A statement such as "BEM vs exact Cohn stripline, 0.044 %" has four parts. Check each one.

1. **The reference X.** Its class is stated per case: *analytic* (an exact formula or construction), *published* (a value from a cited source), *cross-solver* (another solver of the same problem), *measured* (none yet), *self-computed* (produced by this project; regression only). Only the first four, when marked independent, count as validation.
2. **The tolerance Y.** It is fixed per case before the run. The tiers are T0 analytic (<= 0.5 % impedance, <= 0.1 % DC resistance), T1 published (1 to 3 %), T2 cross-solver (2 to 5 %), T3 measured (within measurement uncertainty).
3. **The domain.** The case checks that structure, in that parameter range, on that mesh. The suite does not show the same accuracy elsewhere. For example, the openEMS results are validated for a 23 mm microstrip from 1 to 10 GHz and a via transition from 4 to 20 GHz only (FOLLOWUPS, "EM flow").
4. **What is shared.** BEM vs FEM agreement shows the two numerical methods solve the same equations consistently. They share the quasi-static model, so it does not validate that model (the validation page marks those cases `independent: no`).

The validation page counts a case as passed only when status is `pass`. `known gap`, `[verify]`, `pending` and `skipped` are never counted. Where a number could not be checked against its source it is shown but excluded.

## 3. Sources of error and typical magnitudes

The total uncertainty of a prediction has three parts. They are different in kind and are combined differently (section 4).

### 3.1 Model error (the physics approximation)

The equations being solved are an approximation of the board.

| Analysis | Model error source | Size, from the validation data |
|---|---|---|
| Closed-form impedance | Curve fit to a range of geometries | Up to 0.23 % against exact references across 45 independent analytic cases (shared with the BEM cases); the HJ fits are the "implementation lock" cases, which check the code against the formula, not the formula against physics |
| 2D BEM / FEM | Quasi-TEM assumption | Not measured. BEM vs FEM cannot show it. Needs measured coupons (planned, no data yet) |
| Skin effect and roughness | Hammerstad, Groisse, Huray models | Cannonball-Huray tile area (36 r^2) is an unverified placeholder and may overestimate the roughness factor K (FOLLOWUPS, WP-106). No published-curve check yet |
| Dielectric dispersion | Single-pole Debye fit in the openEMS adapter | About 11 % tan-delta error over 0.5 to 10 GHz (FOLLOWUPS) |
| Copper thickness in 3D | openEMS models copper as a sheet | Z0 +4.1 % against the 35 um BEM value; matches the thin-strip BEM to -0.43 % (known gap) |
| DC IR | Ideal pad equipotentials; no series elements | Not quantified; analytic cases do not include it |
| 3D full wave | Material model, ports, finite computational box | Cross-solver agreement only so far (section 3.2) |
| EMC (planned) | Cables, enclosure, room, source model | Design targets only, +-6 dB to +-10 dB for the full-wave bench, +10/-20 dB for screening ([ADR-0011](adr/0011-emc-compliance-prediction.md) section 1.3). "Engineering judgement for planning, not validated results." |

### 3.2 Numerical error (discretisation and convergence)

The error of solving the chosen equations. This is the part the validation suite measures best.

| Analysis | Reference | Result |
|---|---|---|
| Z0, eps_eff (BEM) | analytic (Cohn, CPW, wire over plane, coax) | max 0.23 %, median 0.02 % over 45 independent cases. Worst: CPW Z0 at 0.23 % (tolerance 0.3 %); the CPW free-space case is 0.22 % off from truncating the domain at 20 times the width (FOLLOWUPS) |
| Z0 (BEM vs FEM) | 2D FEM | 123 of 124 cases pass; max 0.43 %, median 0.06 %. One known gap: eps_eff on a narrow, high-er microstrip is 0.58 % off against a 0.5 % spec (WP-202 accepted 0.7 %) |
| DC resistance | analytic | strip, annulus, via arrays and `power_strip`: at most 0.06 % except the 1.6 mm via barrel at 0.57 % (tolerance 1 %) |
| DC IR drop | raster FVM vs FEM | 0.68 to 0.78 % (tolerance 1 %). The published `kicad-irdrop` cross-check (3.04 mohm, tolerance 3 %) is pending |
| 3D, openEMS Z0 | our BEM | -0.43 % against the thin-strip BEM (tolerance 3 %) |
| 3D, openEMS insertion loss, 23 mm microstrip, 10 GHz | our BEM RLGC cascade | 0.78 dB vs 0.69 dB: a 0.089 dB difference, 11 % of the loss (tolerance 0.2 dB). The M1P target is 0.05 dB |
| 3D, max \|S11\| 1 to 10 GHz | gate | -33.8 dB against a limit of -25 dB |
| 3D, openEMS vs Palace | cross-solver | pending (WP-301). An earlier 0.5 dB microstrip discrepancy is under investigation (WP-501) |
| Touchstone quality | PQM | 100 against a gate of 99 |

Convergence is also a result field: `converged`, `validity` and `quality` on result items. A result that did not converge, or whose quality gate failed, should not be used for a decision however good the label.

### 3.3 Input uncertainty (what you gave the tool)

This is typically the largest term for impedance and S-parameter work, and the tool does not yet propagate it. It is planned as [ADR-0012](adr/0012-uncertainty-quantification.md).

| Input | Typical effect | Basis |
|---|---|---|
| Dk (and Df) of the laminate | Z0 scales roughly with 1/sqrt(eps_eff). Dk varies with frequency, resin content, glass weave and temperature | Material data come from the material library or your own table. I found no tolerance field in what I read, so treat tolerances as your input. The size of the effect for a given board is computed by re-running with another Dk (see section 4) |
| Trace width (etch) and dielectric height | Z0 sensitivity d(Z0)/d(w) and d(Z0)/d(h) are analysis outputs for the cross-section | Fabricator tolerance sheets and the actual stack-up report, not part of the tool's data |
| Copper roughness | Raises conductor loss and phase delay. Model-dependent (3.1) | The roughness model and its parameter |
| Stack-up and prepreg flow | Height under the trace after lamination | Fabricator stack-up report |
| Via and pad geometry | Dominates 3D via-transition results | Your board; ingest reads it exactly |
| Source models and currents (DC IR load, EMC sources) | Scales results linearly (DC) or by dB (EMC) | User input |

The validation suite deliberately uses exact inputs, so it tells you nothing about this term.

## 4. Combining the terms to judge margin

Until the planned uncertainty tool exists, use this procedure by hand.

1. **Take the nominal result and its accuracy class.** If the label is `screening`, stop: the number is for triage and ranking, not margin.
2. **Model error.** Look up the method's row in section 3.1. If the domain of your board is outside the validated domain (section 2, item 3), treat the model error as unknown and say so.
3. **Numerical error.** Use section 3.2, or better, check the result's own `converged` and refinement fields. Run a refinement or a second method (for example `--method bem` vs `--method fem`) and look at the difference.
4. **Input uncertainty.** Re-run with the input moved to each end of its tolerance (for example Dk -5 % and +5 %, width +-10 %) and note the change in the output. Do one parameter at a time to see which dominates.
5. **Combine.** For independent, roughly symmetric terms with similar confidence levels, add in quadrature: `U = sqrt(U_model^2 + U_num^2 + U_input^2)`. Add systematic offsets with their sign instead (for example the known +4.1 % copper-thickness offset in 3D). Do not add terms that are not independent in quadrature.
6. **Compare with the spec.** Margin M = spec limit - nominal. If M is much larger than U (for example M > 2U for a 95 % statement under a normal assumption), the design is likely to pass this check. If M is smaller than U, the result cannot tell you; reduce the dominant uncertainty first (measure a coupon, request the stack-up report, refine the mesh).

The quadrature sum assumes the terms are independent, symmetric and roughly Gaussian. Material and etch tolerances often are not, and a probability of passing cannot be read off from U alone. [ADR-0012](adr/0012-uncertainty-quantification.md) specifies how Open_Analysis will compute P(pass) from stated input distributions; that tool does not exist yet.

## 5. Limits of simulation for compliance

- **Pre-compliance is not certification.** Only an accredited test of the real hardware in the real setup decides compliance. The planned EMC results will say so in every output ([ADR-0011](adr/0011-emc-compliance-prediction.md) section 1): "Predicted emissions for the modelled setup. Not a compliance test; only an accredited test of the hardware decides compliance." Their verdicts are `margin_likely_positive`, `marginal` or `margin_likely_negative`, never pass or fail.
- **The board is rarely the whole story.** For radiated emissions, cables (common-mode current on the harness), enclosure apertures and seams, bonding, and the test setup itself (room resonances below roughly 80 to 250 MHz, absorber quality) dominate. ADR-0011 includes the harness and bench in the model for that reason and treats the room as an uncertainty term.
- **Nothing is measured yet.** There is no VNA or lab data in the validation set. The planned EMC calibration against published measured cases (B-EMC-08, gate <= 10 dB, target <= 6 dB) is not built. EMC accuracy statements today are design targets.
- **Closed-form EMI estimates** are good to roughly +-10 dB at best and are built to over-predict. Never present them as a prediction (ADR-0011, research 07).
- **Known gaps**, as of the run above: Palace cross-checks pending; no openEMS-vs-published cases; no Polar SI9000 or Wadell reference tables yet (they are listed as `[verify]` slots because no value was typed from memory); the stripline wave-port run stalls in openEMS; 0 to 40 GHz is not validated, only 1 to 10 GHz (microstrip) and 4 to 20 GHz (via).

If a decision has money or safety attached, treat Open_Analysis as a way to find problems early and to rank design alternatives, and confirm with a measured coupon or an accredited test.
