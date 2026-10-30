# oa-fdtd

Yee-scheme FDTD for PCB regions (ADR-0010, WP-505 core engine): non-uniform graded grid, fused CUDA
kernels through CuPy, and a NumPy reference engine that is the test oracle and CPU fallback.
WP-506 Part A adds ADE dielectrics, SIBC copper, quasi-TEM mode ports, de-embedding and the
`EMProblem` front end; conformal PEC and Part B (NF2FF, wires, EMC bench) are open; WP-507 is the
adapter.

## Public API
- `oa_fdtd.grid.Grid(x, y, z)`: mesh lines [m]; storage layout (SoA, `(B, z+2, y+2, pitch)`, x
  fastest, rows padded to 32), Courant `dt`, inverse primal/dual lengths. `oa_mesh.rect` builds the
  lines (layers, copper-face thirds rule, grading).
- `oa_fdtd.model`: `Model` (grid, `Waveform`, `Region`, `LumpedPort`, `SoftSource`, `Probe`, walls
  `pec|pmc|cpml` per face, `CpmlSpec`, `drive` batch matrix) and `compile_model`, which builds the
  `uint16` averaged-material edge index (0 = PEC) and the `ca/cb` table.
- `oa_fdtd.run.run(model, engine="auto|numpy|cuda", dtype, end_db, ...) -> RunResult`; `sparams()`
  gives power-wave S from the lumped-port voltages (`S = B A^-1` with all N excitations as a batch).
  `engine="auto"` uses `oa_accel.select` (crossover 2e5 cells, memory budget via
  `MemoryBudget.require`, `AccelReport` in `RunResult.report`).
- `oa_fdtd.engine.CupyEngine` / `oa_fdtd.reference.NumpyEngine`: same equations, same arrays.
  `oa_fdtd.engine.estimate_bytes` is the memory estimator.
- `oa_fdtd.materials`: `fit_debye` / `fit_djordjevic_sarkar` / `fit_callable` -> `DebyeFit`
  (`eps_inf`, passive multi-pole weights, `err_eps`, `err_tand`; poles go to `Region.poles`).
- `oa_fdtd.subcell`: `fit_surface_impedance` (finite-slab `Zs(w)` x roughness as a passive R-L
  ladder, `SibcFit`); `oa_fdtd.subcell.pcb.build_model(EMProblem) -> BuiltModel` (graded mesh from
  `oa_mesh.rect`, Debye fits, copper boxes, via barrels, lumped and wave ports, CPML padding).
- `oa_fdtd.model`: `Conductor` (SIBC or PEC copper box), `ModePort` (quasi-TEM mode of N signal
  conductors on a plane), `Model.mode_probes`, `Model.excite` (drive a subset of ports).
- `oa_fdtd.ports`: `solve_modes` (Q1 FEM capacitance matrices, `C0^-1 C u = eps_eff u`, modal Z),
  `add_wave_planes` / `wave_sparams` (S from modal waves on three planes per port, reference
  plane at the port), `gamma_from_planes`, `shift_reference`, and the `DeembedHook` for the
  `oa_network` 2x-thru.
- `oa-fdtd run job.json` (`oa_fdtd.cli`): engine-level job; no `oa_framework` import.

## Models implemented
Fused E and H kernels (one thread per cell), float32 or float64; uniform-material z groups drop the
index read (PEC edges in such groups are zeroed by a list kernel); a CUDA graph replays 64 steps;
CPML (CFS, m=3, kappa_max 8, alpha from `f_low`) with psi only in the slabs; PEC and PMC (mirror
ghost layer) walls; modulated Gaussian (no DC) or Gaussian (DC) excitation; resistive lumped
ports; float64 running DFT on the device; deterministic fixed-order probe reductions; energy end
criterion; multi-excitation batching as the leading array dimension.

## Verification (ADR-0010 section 9, both engines; GPU tests need `OA_RUN_GPU=1`)
| Case | Result |
|---|---|
| 1 PEC cavity TE101/TE011/TM110, uniform and graded, Richardson over 3 grids | error < 0.05 %, observed order 2.0 (NumPy f64; CUDA f32) |
| 2 CPML, 10 cells, 0.1-40 GHz, both faces | worst -84 dB (normal incidence); late-time energy < -80 dB |
| 3 parallel-plate TEM line, lumped ports R = eta h / w, PMC sides | S11 < -45 dB, abs(abs(S21) - 1) < 1e-4 up to 20 GHz at 0.5 mm cells (NumPy f64, CUDA f32) |
| 4 Debye ADE (Djordjevic-Sarkar 4.3, 8 poles) on a plate line, lumped and mode ports | S21 within 0.02 dB (0.05 dB mode ports), phase 1 deg, S11 within 0.01, both engines |
| 5 SIBC copper plates (finite slab, 2e5 S/m, 0.25 mm) | S21 loss within 2 % of the analytic ABCD line, phase 0.1 deg, both engines |
| 7 closed-box energy, 1e5 steps | float64 < 1e-6 (NumPy and CUDA); float32 CUDA 3.3e-7 |
| Yee dispersion vs the exact relation | within 1e-3 (f64) |
| GPU vs NumPy | f32 within 1e-4 of the f64 probe DFTs; f64 GPU within 1e-10; same-device runs bitwise equal, graph equals eager |

B-PERF-01 plain kernel (256^3 vacuum, 10-cell CPML on all faces, RTX 4080 SUPER, 192 steps,
`bench/throughput.py`): 6.0 Gcells/s float32 (target >= 3.0), 3.2 float64; NumPy float64
0.014 Gcells/s. The PCB-like case (ADE) belongs to WP-506.

## Port normalisation (fixed in WP-506)
The lumped-port loss is semi-implicit, so the resistor carries `(E^n + E^{n+1}) / 2`, not the
recorded `E^{n+1}`. `run.sparams` applies `cos(w dt / 2)` to the port voltages
(`port_voltage_factor`); before, abs(S) was off by `1 / cos(w dt / 2)`, which is the O(f^2) drift
(3.8e-3 at 20 GHz, 0.5 mm cell, Courant 0.95). The same factor holds for mode ports.

## WP-506 Part A models
- **ADE:** Debye poles per edge (averaged like eps), `E^{n+1} = ca E^n + cb (curl H - 1/2 sum
  (1 + k_p) J_p^n)`, `J_p^{n+1} = k_p J_p^n + beta_p dE`; the static `dt beta / 2` joins eps in the
  material table. Kernels `kernels/physics.cu`, mirrored in the NumPy engine.
- **SIBC:** copper boxes have PEC interiors; tangential edges on a face are set from the outside
  H samples (edges on two faces average the two). Recursive convolution of the ladder
  `R0 + sum R_p s tau_p / (1 + s tau_p)` (bilinear sections). E is written at `t_{n+1}` from
  `H^{n+1/2}` (a half-step phase of `w dt / 2`, negligible).
- **Mode ports:** the template is the Q1 potential gradient on the Yee edges of the port plane; the
  sheet conductivity is `kappa eps_e` (a local admittance following the dielectric), `kappa` from
  the modal impedance, and the voltage probe projects with the same weights. The port sits on a
  PMC wall like a lumped port.
- **Wave planes:** `wave_sparams` extracts S from the modal voltages on three planes per port
  (gamma and the forward and backward waves, referred to the port plane). The port-region loss
  and reflection cancel, which the plain port S does not give (0.2 dB offset at 10 GHz).

## B-EM-01 microstrip thru (bench/thru.py, RTX 4080 SUPER, float32)
Testkit thru (0.38 mm on 0.2 mm FR-4 Djordjevic-Sarkar, 35 um copper, 25 mm), wave ports, medium
mesh (78 624 cells, 12 160 steps, -70 dB end criterion), against the cascaded 2-D FEM RLGC
(`oa_impedance`, converged, same material) at the modal port impedance (47.9 ohm):
IL error 0.005 dB at 10 GHz, 0.038 dB at 20 GHz, worst 0.038 dB over 0.2-20 GHz (target 0.05);
max |S11| -28.4 dB (target below -29 where the reference is below -30); 1.6 s run, 0.02 GiB.

## Stability detection
`run` raises `ModelError("FDTD_UNSTABLE")` when the field energy is not finite, or when, after the
excitation has ended (`2 * delay_tau * tau`), it exceeds `GROWTH_LIMIT` (100, i.e. 20 dB) times its
post-excitation minimum. Both checks run with the end-criterion energy checks every `check_every`
steps, so they are inactive with `end_db=None`. Slow growth below 20 dB, or growth while the
source is still on, is not detected. Tests: `tests/test_stability.py`.

## Known gaps
- Dey-Mittra conformal PEC is not implemented: via barrels and diagonal polygons are staircased.
- Mode ports are static quasi-TEM (no full-wave 2-D eigenmode solve), single dielectric
  frequency (band-centre eps) for the template and impedance; the local `oa_fdtd.ports.modal`
  solver stands in for the `oa_impedance` FEM/BEM (change request WP-506-modal-solver).
- Low-frequency S11 (below about 1 GHz) comes from the port S, not the planes, and reaches -26 dB.
- B-EM-03 (differential via) is OPEN. The straight calibration line (no antipads) converges
  (medium vs fine within 0.05 dB, S11 below -50 dB). With the stitched via ring, medium and fine
  agree to 0.02 dB (cells-per-wavelength 60 and 90 differ by up to 1 dB), but |S21| is about 4.5 dB
  lower than the openEMS run of the same structure (-4.6 dB vs -0.03 dB at 3.5 GHz, phase and delay
  agree). The missing power is not dielectric or copper loss, not the z/y boundaries and not the
  port normalisation (bottom and top straight lines pass); it grows sharply with the antipad
  radius. Suspected: spurious coupling of the via/antipad into the inner plane gaps, which end in
  the port faces and the PML. Timing numbers for B-EM-03 and B-PERF-01 are deferred to a run at the
  gate on a stable machine.
- FIXED: S depended on `frequency.n_points`. Causes: static quantities (band-centre mode eps, SIBC
  fit range, Debye fit range) came from the sample list, and the plane extraction (branch
  unwrapping, smooth gamma fit) ran on the sample list, which aliases when it is sparse. Now
  `Model.f_band` (0, f_max) sets the statics, and `adapter.sim.solve_problem` extracts on a fixed
  400-point internal grid united with the requested points (the gamma fit sees the internal grid
  only). Regression: `tests/verification/test_fdtd_n_points.py` (20/41/80/160 points, B-EM-01 and
  B-EM-03, identical S).
- Power budget (float64, closed lossless boxes, `.scratch`-style check): plane extraction conserves
  `|S11|^2 + |S21|^2 = 1` to 3e-4 on the lossless stripline (B-EM-02a) and to 0.3-0.7 % on the closed
  lossless multilayer microstrip above 5 GHz; the port-sheet power `|V|^2 / R` is 2.5 % (stripline)
  to 3.5 % (multilayer, growing with f) below the delivered power. The sheet is a local
  conductance on every edge of the port plane, so it also dissipates the part of the real mode that
  is not the static template (dispersion, evanescent content), which the template-projected
  voltage does not count. The plane waves are the reliable estimate; the port S is not.
- `compile_model` host memory for a 400x400x120 grid with 6 ADE poles: peak 7.5 GiB (was 10.1 GiB),
  compile 12 s (was 67 s); the per-pole delta arrays (float64) dominate what remains.
- The matrix-pencil tail extrapolation is exercised only by B-EM-05a.
- Anisotropy (glass weave) and lumped RLC elements of the `EMProblem` are ignored with a diagnostic.
- Roughness fits to 2-3 % in Re Zs (1 um rms), the ladder cannot follow the sharp Hammerstad step.
- CPML reflection is measured at normal incidence only; oblique and evanescent cases are open.
- Material sets above 65535 averaged entries are refused; material averaging is host-side float64
  and intended for regions up to a few 1e6 cells (the vacuum fast path has no limit).
- ROCm: same `.cu` source, hiprtc; written but untested on hardware (no AMD GPU). Graph capture
  falls back to eager launches if the runtime refuses it.
- Magnetic materials, anisotropy, Mur ABC, field dumps and subgridding are not implemented.

## NF2FF compatibility
Probes are index lists with weights, summed on the device in float64 and accumulated into a
running DFT per requested frequency. A Huygens surface is the same machinery without the final sum:
the tangential E and H points of a box (`Grid.points`) as one probe each, DFT'd per point on the GPU,
so WP-506 can add the near-to-far-field transform without changing the kernels or the layout.

## Tests and golden suites
`packages/oa-fdtd/tests` (CPU always; `gpu` marker with `OA_RUN_GPU=1`; the graded NumPy cavity case is
`slow`). Golden suites `tests/golden/fdtd/{cavity,cpml,tem_line}` (analytic cases) and
`tests/golden/fdtd_physics/{ds_line,sibc_line,microstrip_thru}` (analytic ABCD lines and the FEM
RLGC reference of the thru; the `microstrip_thru` case runs only with `OA_RUN_GPU=1`).

## Theory and validation

Equations, numerical method, validity range, error budget and validation evidence: [THEORY.md](THEORY.md).
