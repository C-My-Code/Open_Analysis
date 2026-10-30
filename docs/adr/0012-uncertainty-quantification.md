<!--
SPDX-FileCopyrightText: 2026 Open_Analysis contributors
SPDX-License-Identifier: GPL-3.0-or-later
-->

# ADR-0012: Uncertainty quantification: the probability that a design meets its spec

- Status: Proposed (2026-10-29). Design only; the build is M2, after the M1P gate.
- Deciders: project owner, architecture owner
- Related: ADR-0007 (one command catalog, agent contracts), ADR-0009 (`oa-accel`), ADR-0010 (`oa-fdtd`), ADR-0011 (EMC prediction, uncertainty budget), [CONFIDENCE.md](../CONFIDENCE.md), [BENCHMARKS.md](../BENCHMARKS.md) section 5b (B-UQ cases)
- Work packages: WP-430 to WP-436 in [TASKS.md](../TASKS.md)

## Context

- Users want an estimate of the chance of success: will this channel meet its margin, will this board pass RE102. Today every analysis returns one nominal number plus an accuracy class. [CONFIDENCE.md](../CONFIDENCE.md) section 4 tells the user to combine model, numerical and input errors by hand. That is slow and easy to get wrong.
- The largest term is usually the input: Dk/Df, etch, laminate thickness, roughness and stack-up are known only to a tolerance. The validation suite measures none of it, because it uses exact inputs.
- Open_Analysis solvers are cheap in some places (a BEM cross-section takes 16 to 39 ms, FOLLOWUPS and ADR-0009) and expensive in others (an openEMS microstrip thru takes 31 s, a Palace p1 run 169 s, an RE102 bench up to 45 minutes on a GPU, B-EMC-07). Sampling methods that are fine for the first are impossible for the last. The design needs several propagation methods behind one interface.
- A probability that is reported without its assumptions is worse than no probability. A customer who needs confidence that the estimates are sound needs to see the assumed distributions and the evidence for the model-error term next to the number.

## Decision

Add a package `oa-uq` and a result type `UqResult`. It estimates the probability that a chosen quantity meets a chosen spec, given stated input distributions and a calibrated model-error term. It reports P(pass) with a confidence interval, the margin distribution, and a ranking of the inputs that matter.

### 1. The question being answered

A **spec** is a predicate on an analysis output: `Spec{quantity, op, limit, unit}` with an optional band over frequency or position. Examples:

- Z0 of net X within 50 ohm +-10 % at every sample along the net.
- Insertion loss at 8 GHz no worse than -3 dB.
- Worst load drop on rail V <= 3 % of vset.
- Eye height or channel margin >= a limit (from the SI engine, WP-401).
- RE102 level below the limit line at every frequency in 2 MHz to 18 GHz (margin M(f) > 0).

The **output** is P(pass) = P(spec satisfied), taken over three sources of randomness:

1. input uncertainty X (section 2),
2. model error delta (section 3),
3. numerical error, handled as a bound or a surrogate-error term (section 4), not sampled.

P(pass) is always conditional on the stated distributions of X and delta. The result says so (section 6).

### 2. Inputs: tolerance distributions

Each uncertain parameter has a distribution, a level in a hierarchy, and a source.

| Parameter group | Parameter examples | Default form |
|---|---|---|
| Laminate | Dk, Df (each as a scale factor on the material model, so frequency dependence is kept) | Normal or truncated normal on a relative factor |
| Etch / width | trace width, gap, etch factor (trapezoid) | Normal on an absolute offset [m], correlated within a layer |
| Thickness | dielectric heights, copper thickness, plating | Normal or uniform on a relative factor, correlated within a stack |
| Roughness | roughness model parameters (Hammerstad rms; Huray nodule radius and ratio) | Lognormal |
| Stack-up | prepreg flow, core vs prepreg, layer registration | Discrete scenarios plus continuous offsets |
| Electrical | load current, regulator output, source level and edge time | User-specified |
| EMC setup | harness length and height, source level (dB), CM conversion | User-specified or ADR-0011 presets |

**Hierarchy.** A parameter may vary at the *lot* level (one draw for the whole board, for example Dk), the *layer or net* level (width offset shared by a layer), or the *local* level (independent per feature). Sampling follows the hierarchy. This matters for pass criteria that take the worst case over a trace: a shared offset moves the whole trace together, an independent one averages out. The default for an unspecified parameter is the most conservative one, fully correlated.

**Sources.** A distribution comes from (a) the user, (b) a **preset** file `oa-uq-preset/1` (TOML; fabricator or material data sheet), or (c) a built-in default flagged `source = "default"`.
- Every preset records its source, the confidence level its tolerance refers to (a tolerance quoted as a limit is not a standard deviation), and a `verify` flag.
- Shipped numbers must cite a document. If we cannot, the preset ships as an empty template, as ADR-0011 does for the limits it cannot license.
- Distribution parameters are never filled in silently. If the user names no distribution for a parameter that the spec depends on, the run refuses with `UQ.MISSING_INPUT_DISTRIBUTION` and lists the candidates, or proceeds with a default only when asked and flags it in the result.

### 3. Model error: calibrated per solver and accuracy class

The model-error term delta is the difference between the solver's output and the truth for the same exact inputs. It is a property of the (solver, accuracy class, regime) triple, for example `(bem, 2d_field, microstrip, f < 10 GHz)`.

- **Form.** For a quantity y: `y_true = y_model + delta`, with delta additive in the natural unit of y (relative for Z0, dB for loss and EMC levels). For frequency curves, delta is a smooth random function with a stated correlation length, so a +3 dB error is not independent between neighbouring frequencies.
- **Calibration.** `oa-uq` reads the validation suite (`results.json`, schema `oa-validation-results/1`) and fits, per triple, the mean and standard deviation of the residual and an upper confidence bound on it.
  - Only cases with `independent: true` and a non-self-computed reference enter the fit. The others are shown, not used.
  - Cases with `known_gap` are used with their actual error, not their tolerance.
  - The calibration artefact `oa-uq-calibration/1` stores the cases used, their count, the commit of the run, and the method.
- **Honest handling of the data limit.** The calibration is only as wide as the data. Rules:
  - A triple with fewer than a stated number of independent cases (default 10) has `calibration = "insufficient"`. The tool uses the declared tolerance of the accuracy class as a *prior*, reports it as such, and the result's `accuracy` cannot rise above `estimate`.
  - A triple with no independent references uses a prior and says so. Today that covers 3D full wave beyond the microstrip and via cases, and all of EMC (ADR-0011 section 1.3 values are design targets, not measurements).
  - BEM vs FEM agreement is a numerical-consistency check, not a model-error measurement. It does not calibrate the quasi-static model assumption. Until measured coupons exist, the model error of "2D field, quasi-TEM" is carried as a *prior* with a wide tail, not as the 0.06 % median seen in the BEM-vs-FEM suite.
  - Measured data (planned: VNA coupon program, B-EMC-08) replaces priors when available. The calibration is versioned, and a result states which calibration it used.
- **Combination with the ADR-0011 budget.** For EMC, the RSS uncertainty budget of ADR-0011 section 1.4 is the special case where all contributions are independent Gaussians and no sampling is needed. `oa-uq` generalises it: contributions become random variables, so correlation across frequency and non-Gaussian terms are handled. The first implementation must reproduce the ADR-0011 band when given its inputs (B-UQ-08).

### 4. Propagation

The method is chosen by a cost model (the analysis `estimate`, as used by the budget system), not by the user. The user can override it. All methods return the same `UqResult`.

| Cost per evaluation | Method | Notes |
|---|---|---|
| Very cheap (closed form, BEM: under about 50 ms) | Plain **Monte Carlo** or **Latin hypercube (LHS)** with 1e4 to 1e5 samples | Batch many cross-sections through the BEM in one call; GPU batching through `oa-accel` where the batch is large (B-IMP-04 is the throughput gate) |
| Cheap, smooth (DC IR, linear and quasi-static circuits) | **First-order sensitivities** (delta method), with **adjoints** for sparse-solve outputs, plus MC/LHS checks | See below |
| Moderate (SI channel cascade, circuit-level EMC conducted) | **LHS or Sobol sequences**, optionally with a **polynomial chaos expansion (PCE)** or surrogate fitted to the samples | |
| Expensive (3D full wave, RE102 bench) | **Surrogate** (sparse PCE or Gaussian process) fitted to a space-filling design of 20 to 200 solver runs, then MC on the surrogate | Needs a surrogate-error term and an out-of-sample check |

Details:

- **First order.** For y = f(X) with small input variances, Var(y) ~ g^T Sigma g with g = grad f at the mean, and the first-order share of input i is (g_i sigma_i)^2 / Var(y) when inputs are independent. Gradients come from finite differences (a few extra solves) or from **adjoints** where the analysis solves a linear system K(p) u = b: dy/dp = -lambda^T (dK/dp) u, with K^T lambda = dy/du. DC IR is the main case: one extra sparse solve gives the sensitivity of the worst drop to every conductivity and via parameter. The delta method gives a fast P(pass) estimate for near-linear responses and a cross-check for the sampled one. It is not used alone when the spec is near a nonlinearity (Z0 versus width at small gaps; eps_eff versus Dk near 1) and the result warns when sampled and first-order estimates disagree.
- **Sampling.** MC for a baseline, LHS or scrambled Sobol for variance reduction. Every run has a fixed seed that is recorded. Sample order and reduction order are fixed so results are bitwise-reproducible on one backend and agree between CPU and GPU within the ADR-0009 tolerance classes.
- **Polynomial chaos and surrogates.** Sparse PCE (Hermite for normal, Legendre for uniform inputs) is preferred for up to about 10 inputs because its coefficients give Sobol indices directly. Gaussian-process surrogates are the fallback for non-smooth responses. Every surrogate reports a **leave-one-out error** and an independent held-out error. If the surrogate error exceeds a stated fraction of the margin, the tool refuses to report P(pass) from it (`UQ.SURROGATE_INADEQUATE`) and requests more runs. A surrogate trained on solver runs also has to cover the *model-error* term, which is added after the surrogate, not learned with it.
- **Importance sampling and subset simulation** are allowed for rare events (P(fail) below about 1e-3), where plain MC would need too many samples. They are optional and not in the first release.
- **GPU batching.** Where the analysis supports it (BEM sections, multi-port FDTD with batching, DC IR sparse solves), samples are dispatched through `oa-accel` in batches under the existing memory budgets. A GPU run must produce the same `UqResult` fields as a CPU run.
- **Numerical error.** Discretisation error is not sampled. The analysis reports a convergence bound, which `oa-uq` adds to the model-error term in quadrature as a bias bound. A result that did not converge does not enter P(pass).
- **Budget.** UQ runs are jobs under the same compute budget (time, memory, CPU count). A dry run returns the planned method, the number of solver evaluations and the estimated cost.

### 5. Outputs

`UqResult` (in `oa-uq`, same container conventions as other results) contains:

| Field | Meaning |
|---|---|
| `spec` | The predicate evaluated, with units |
| `p_pass` | Estimate of P(pass) |
| `p_pass_ci` | A confidence interval on `p_pass` and its confidence level (default 95 %). Sampling error: Clopper-Pearson or Wilson for MC/LHS; for surrogates, a bootstrap that includes surrogate error. Also includes the calibration uncertainty of the model-error term (a second-level sample over calibration parameters) |
| `margin` | Distribution of (limit - value), as quantiles (for example 1, 5, 50, 95, 99 %) with units. For band specs, per frequency or position, plus the quantiles of the worst margin over the band |
| `nominal` | The deterministic result (what the analysis alone returns) |
| `sensitivity` | Ranked list of input parameters with first-order and total Sobol indices (or first-order shares), and the sign and size of the effect of a one-sigma move |
| `inputs` | The full list of assumed distributions (family, parameters, hierarchy, correlation, source, `verify` flag) |
| `model_error` | The model-error term used per solver and class: family, parameters, calibration id, number of independent cases, `calibration` status (`calibrated` or `insufficient`) |
| `method` | Propagation method, number of solver evaluations, seed, surrogate error if any |
| `accuracy` | The lowest accuracy class among the contributing analyses, capped at `estimate` if any calibration is `insufficient` |
| `provenance` | Commit, solver versions, calibration artefact id, preset ids, board hash |
| `disclaimer` | The fixed honesty sentence (section 6) |

`summary()` obeys the ADR-0007 size caps: P(pass) with its CI, the five most influential parameters, the assumption count, and the accuracy class. Full distributions are fetched with `results.get`.

**Application to the four analysis families**

| Analysis | Inputs that move the result | Spec | Notes |
|---|---|---|---|
| **Impedance** (`impedance.profile`) | Dk, trace width/etch, dielectric height, copper thickness, roughness (loss) | Z0 or Zdiff within tolerance at all samples along each net | Cheap: MC with BEM batches. Output includes sensitivity of Z0 to w and h, which is also a design aid. Model error from the impedance rows of the calibration |
| **SI eye or channel margin** (planned `oa-si`, WP-401) | The impedance inputs plus loss (Df, roughness), via geometry, termination | Margin of a metric chosen by the user (IL at a frequency, TDR peak, eye height) | The channel cascade is cheap, so the impedance parameters are sampled through it. The 3D via model enters as a surrogate or as its own model-error term |
| **DC IR drop** (`pi.dc`) | Copper thickness and conductivity, temperature, plating, load current, via diameter and plating | Worst load drop <= limit; via current density <= limit | Near-linear in current and in 1/thickness. First-order with adjoints is usually adequate and is checked with MC. |
| **EMC (RE102, CE102)** (planned, ADR-0011) | Source level, CM conversion, harness layout, enclosure aperture, room and antenna effects, plus the board inputs | M(f) = limit(f) - L(f) > 0 at every frequency | Model error dominates. Use the ADR-0011 uncertainty budget as the starting model-error prior (design targets: +-6 to +-10 dB for the full-wave bench, +10/-20 dB for screening). Model error is a correlated random function of frequency, so per-frequency probabilities are not combined as a product. The band-wide P(pass) is estimated from sample paths. Surrogates are required for the bench. The verdict vocabulary of ADR-0011 (`margin_likely_positive`, `marginal`, `margin_likely_negative`) is derived from P(pass) and fixed thresholds, and the "prediction, not certification" sentence stays |

For EMC the model error is far larger than the input effects, and the calibration is a prior (section 3). A P(pass) for RE102 will say so in the result: `calibration = "insufficient"` and accuracy at most `estimate`. That is the correct output, not a defect.

### 6. Honesty rules (enforced in code)

1. **No P(pass) without its assumptions.** `UqResult` cannot be built without `inputs` and `model_error` filled. `summary()`, the CLI text, the MCP summary, the web view and the report always print the assumed distributions (or a count with a link) and the model-error calibration status next to the number.
2. **The fixed sentence.** Every UqResult carries: *"P(pass) is conditional on the stated input distributions and the model-error calibration. It is not a measured yield and not a compliance result."*
3. **Prior vs calibrated.** A model-error term not backed by enough independent references is labelled `insufficient`, and the tool never presents it as validated.
4. **No hidden defaults.** Any default distribution is listed in `inputs` with `source = "default"`.
5. **Disagreement is surfaced.** If first-order, MC and surrogate estimates disagree by more than their CIs, the result carries a warning and the wider interval.
6. **CI includes calibration.** The interval is not just Monte Carlo noise; it includes the finite size of the calibration set.
7. **Out-of-domain.** If the board lies outside the validated domain of a solver (validity `extrapolated` or `invalid`), the run reports `UQ.OUT_OF_DOMAIN`, and P(pass) is withheld unless the user overrides with a stated model-error value.
8. **Determinism.** Same inputs, seed and versions give the same result.
9. **No network or LLM calls** (the project rule); `oa-uq` is pure compute.

### 7. Interfaces

- **Package `oa-uq`** (`oa_uq`), layered between the analysis packages and `oa-service`. It calls analyses only through the analysis registry and `oa_framework` protocols (as `oa_emi` does for SI and PI data), not through imports of specific analysis packages. A new import-linter contract enforces it (added through the integrator, TASKS rule 2). It depends on `numpy` and `scipy`; surrogate and PCE libraries are chosen in WP-432 and must be GPL-compatible (ADR-0001).
- **Commands** (all through the `oa_service` catalog, so CLI, REST and MCP are thin adapters):

  | Command | CLI | Side effect | Purpose |
  |---|---|---|---|
  | `uq.run` | `oa uq run` | compute | Run a UQ for a stored setup or inline `analysis` + `config` + `spec` + `inputs`; supports `dry_run` |
  | `uq.inputs` | `oa uq inputs` | read | List the uncertain parameters of an analysis, their defaults and the presets that apply |
  | `uq.presets` | `oa uq presets` | read | List or validate presets (`oa-uq-preset/1`) |
  | `uq.calibration` | `oa uq calibration` | read | Show the model-error calibration per solver and class, with the number of independent cases |
  | `uq.sensitivity` | `oa uq sensitivity` | compute | The sensitivity ranking alone (cheaper than `uq.run`) |

  MCP tool names follow the catalog convention (for example `run_uq`). Errors are `ErrorInfo` with codes such as `UQ.MISSING_INPUT_DISTRIBUTION`, `UQ.SURROGATE_INADEQUATE`, `UQ.OUT_OF_DOMAIN`, `UQ.BUDGET_EXCEEDED`.
- **Schemas**: `oa-uq-spec/1`, `oa-uq-preset/1`, `oa-uq-calibration/1`, `oa-uq-result/1`.
- **Agent docs**: `docs/agents/uq.md` tells an agent never to quote P(pass) without the inputs and the model-error status.

### 8. Validation of the UQ itself

The UQ package is validated like any solver, with benchmark cases that have analytic answers ([BENCHMARKS.md](../BENCHMARKS.md) section 5b, B-UQ-01 to B-UQ-09):

- a linear model with Gaussian inputs, where P(pass) is exactly Phi((L - a - b^T mu) / sqrt(b^T Sigma b));
- a reciprocal model with uniform inputs, where the exact CDF is known (DC resistance R = c / W);
- the Ishigami function, which has known Sobol indices;
- coverage tests, to check that a 95 % CI contains the exact value in about 95 % of repeated runs with different seeds;
- the calibration step on a synthetic solver with known bias and spread;
- correlated EMC margins with a known orthant probability.

## Consequences

- Users get one number that answers their question, with the evidence beside it. Where the evidence is thin the output says so; for EMC that will be the case for some time.
- The tool depends on the validation suite as data. A change to `results.json` (schema or case ids) now affects calibration, so `oa-uq` pins the schema id and tests it.
- Presets create a maintenance and licensing duty (citations, `verify` flags).
- Cost: a UQ run is many solver runs. The budget system and the dry-run estimate bound it, and the 3D and EMC paths cannot be used without surrogates.
- Risk: users may read P(pass) as a yield or a compliance probability. The fixed sentence, the display rules and the agent docs exist to prevent that. They reduce the risk; they do not remove it.
- Not decided here (to be settled in the WPs): the surrogate libraries; the default number of independent cases for a calibrated triple (10 is a placeholder); the default confidence level; whether a Bayesian treatment of the calibration (hierarchical) replaces the frequentist bound in a later version.

## Open questions for the project owner

1. Which fabricator and material tolerance data may we cite and ship as presets? Without citations the presets are empty templates.
2. Will measured coupons (VNA) and, for EMC, lab measurements become available? They are the only way to move the model-error terms from priors to calibrated values.
3. Is a default input distribution acceptable when the user gives none (with the flag), or must the run refuse?
