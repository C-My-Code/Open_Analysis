# Theory documentation

Open_Analysis estimates whether a design will work: impedance targets, SI margins, IR drop, EMI. A number is only useful if you can check the math behind it and know how far to trust it. Each package listed here has a `THEORY.md` with the same six sections: problem statement, governing equations and the discretisation actually used in the code, assumptions and validity range, error sources with measured magnitudes, validation evidence with known gaps, and references. Numbers in those pages are quoted from tests, `docs/validation/results.json`, `docs/BENCHMARKS.md` and `docs/FOLLOWUPS.md`, with the case named. Anything that could not be verified is marked **[verify]**.

For the user-level statement of how to read an accuracy class and combine errors, see [CONFIDENCE.md](../CONFIDENCE.md). For the planned probability-of-success estimate, see [ADR-0012](../adr/0012-uncertainty-quantification.md) (status Proposed).

## Index

| Package | What it computes | Page |
|---|---|---|
| oa-impedance | Z0, Zdiff, eps_eff, RLGC(f), loss: closed forms, 2D BEM, 2D FEM reference | [THEORY](../../packages/oa-impedance/THEORY.md) |
| oa-physics | Dk/Df(f) causal models, skin effect, surface impedance, roughness, via estimators | [THEORY](../../packages/oa-physics/THEORY.md) |
| oa-network | S-parameter quality gate (PQM, RQM, CQM), passivity enforcement, mixed mode | [THEORY](../../packages/oa-network/THEORY.md) |
| oa-fdtd | GPU/CPU Yee FDTD: CPML, Debye ADE, SIBC copper, ports, S extraction | [THEORY](../../packages/oa-fdtd/THEORY.md) |
| oa-pi | DC IR drop: sheet-resistance FEM, via barrels, contacts | [THEORY](../../packages/oa-pi/THEORY.md) |
| oa-erc | Rule checks: physical rationale and threshold source per rule | [THEORY](../../packages/oa-erc/THEORY.md) |
| oa-geometry | ReferenceMap (return-path inference), cross-section extraction | [THEORY](../../packages/oa-geometry/THEORY.md) |
| oa-mesh | gmsh 2D/3D meshes, rectilinear FDTD mesher, quality metrics, determinism | [THEORY](../../packages/oa-mesh/THEORY.md) |
| oa-em | `em.sparams` flow: region, boundaries, ports, convergence, quality gate | [THEORY](../../packages/oa-em/THEORY.md) |
| oa-em-openems | openEMS adapter: mesh, materials, ports, S extraction | [THEORY](../../packages/oa-em-openems/THEORY.md) |
| oa-em-palace | Palace (FEM) adapter: configuration, ABC, ports, accuracy results | [THEORY](../../packages/oa-em-palace/THEORY.md) |
| oa-accel | GPU against CPU numerical-equivalence rules | [THEORY](../../packages/oa-accel/THEORY.md) |

Not covered yet (no `THEORY.md`): `oa-qs`, `oa-thermal`, `oa-emi`, `oa-si`, `oa-ingest-*`, `oa-model`, `oa-results`, `oa-jobs`, the AI layer (`oa-assistant`, `oa-mcp`). `oa-emi` and the EMC predictions of ADR-0011 are planned and not implemented in the form that ADR describes [verify current status].

## Chain of trust

The path from the files you give Open_Analysis to a statement like "this design will probably work":

```text
input data  ->  models  ->  validation  ->  accuracy class  ->  (planned) uncertainty quantification
```

**1. Input data.** Board geometry (from KiCad or IPC-2581), stack-up, materials (Dk, Df, roughness, copper conductivity), design intent (targets, rise times, tolerances). Errors here are not solver errors. The ingest suite checks counts exactly and copper area to 0.1 % on synthetic boards; the cross-section and return-path extraction are checked on idealised builder boards ([oa-geometry](../../packages/oa-geometry/THEORY.md)). Material numbers are what you or a datasheet supply. Nothing in the current validation suite measures how wrong a datasheet Dk or an as-built etch can be; this is the largest term in most real designs and is the subject of the planned uncertainty work.

**2. Models.** Each analysis uses a stated physical model: for example a 2D quasi-TEM field solve for impedance, a thin-sheet resistive network for DC IR, a full-wave time-domain solve for S-parameters, a geometric rule for ERC. The `THEORY.md` pages state the equations, what discretisation is used, and when the model stops being valid (quasi-static limit, thin-copper treatment, CFL limit, absorbing-boundary distance).

**3. Validation.** The suite in `docs/validation/` (`results.json`, produced by `tools/validation/run_validation.py`) compares results with references and classifies each reference. Of 264 cases in the 2026-10-29 run, 248 pass, 3 are recorded known gaps, 2 are `[verify]` (citation unchecked) and 11 are pending; **only 100 of the 248 passes use an independent reference**. The others are self-computed or in-repo cross-solver comparisons (for example BEM against FEM, which tests numerical consistency, not the physical assumption). The reference classes are:

- *analytic*: an exact formula or construction (strong);
- *published*: a cited paper, book or standard (strong if the citation is checked; several slots are still empty or `[verify]`);
- *cross-solver*: another solver of the same problem (checks implementation, not the model);
- *measured*: a physical measurement (**none yet**);
- *self-computed*: regression only, never counted as validation.

The benchmark gates in [BENCHMARKS.md](../BENCHMARKS.md) set the targets (for example Z0 within 0.05 % of exact, insertion loss within 0.05 dB of a converged reference). Several gates are not met today (B-IMP-01 fails at 0.23 % on the free-space CPW; B-EM-01 is not met for Palace or openEMS; B-EM-03 and others are not run). The package pages state each plainly.

**4. Accuracy class.** Every result carries an `accuracy_class` (`oa_results.AccuracyClass`):

| Class | Meaning | Typical source |
|---|---|---|
| `reference` | Field-solver or measured-quality result usable as an oracle | converged FEM |
| `validated` | Method checked against the validation suite for this regime | BEM, DC IR FEM |
| `screening` | Fast closed form or rule, good for triage | closed-form impedance, ERC |
| `estimate` | Heuristic or extrapolated | EMI estimates |
| `unverified` | No accuracy statement | |

The class is a statement about the method in a regime, not about your board. A `validated` impedance result still depends on your Dk. Where the validity range is exceeded (for example the BEM above its quasi-static frequency), the result is flagged `extrapolated` or `invalid` and the class does not hold. The finer method classes in `docs/specs/ai-integration.md` (`closed_form`, `2d_field`, `2d_sheet`, `3d_full_wave`, `rule`, ...) are carried alongside, with the tolerance basis. Note that `2d_sheet` is not yet a value of `AccuracyClass` in `oa_results` (change request WP-204-accuracy-class).

**5. Uncertainty quantification (planned, not implemented).** [ADR-0012](../adr/0012-uncertainty-quantification.md) (Proposed; build in M2) describes a package `oa-uq` that would estimate the probability P(pass) that a chosen quantity meets a chosen spec, given stated input distributions (laminate, etch, thickness, roughness, stack-up, loads) and a model-error term calibrated per (solver, accuracy class, regime) from the validation suite. It would report P(pass) with a confidence interval that includes the calibration uncertainty, the margin distribution, and a ranking of the inputs that matter. Its honesty rules state that P(pass) is conditional on the stated distributions and calibration, is not a measured yield or a compliance result, and that where independent references are too few the model-error term is a labelled prior and the accuracy is capped at `estimate`. Until it exists, the way to combine errors is by hand, as described in [CONFIDENCE.md](../CONFIDENCE.md): model error (section 5 of each page) plus numerical error plus input error.

### How to use a THEORY page to judge a result

1. Find your analysis in the index and read section 3 (validity). Is your case inside it?
2. Read section 4 (error table). Take the largest term that applies; if your input tolerances are larger, they dominate.
3. Read section 5. Is there an independent reference for your regime (microstrip below 10 GHz, DC IR on a plane)? If the evidence is cross-solver only, the model assumption itself is untested.
4. Check the known-gaps list for your feature (vias in FDTD, stripline in openEMS, free-space CPW, rough copper).
5. If a number matters for a go/no-go decision, check it with an independent method or a measurement.

## Conventions used in these pages

- Lengths in metres, frequencies in hertz, unless a quantity is written with its unit.
- "Measured" means measured by a test in this repository (with the case named), not measured on a physical board; there are no physical measurements yet.
- "Tolerance" is a test pass threshold, not an error bound. "Worst error" is the largest error observed on the listed cases.
- When code and documentation disagree, the page says which one runs.
