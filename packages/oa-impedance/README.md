# oa-impedance

Closed-form transmission-line models, a 2D quasi-static BEM, the `impedance` service and the
`impedance.xsection` analysis (WP-105). The FEM reference solver (`oa_impedance.fem`) and the
profile analysis (`oa_impedance.profile`) belong to WP-202 / WP-203 and plug in through
`oa_impedance.methods.register`.

The FEM reference solver (`oa_impedance.fem`, WP-202) is described in its own section at the end.

## Public API

```python
from oa_impedance.service import (
    ImpedanceServiceImpl,
)  # entry point  open_analysis.services:impedance

svc = ImpedanceServiceImpl()
svc.solve(
    xs, freqs=None, method="auto" | "closed_form" | "bem" | "fem"
)  # -> oa_results ImpedanceResult
svc.solve_many(xss, freqs, method="auto")  # dedups by CrossSection.key(), threads
svc.quick(xs)  # closed form only, < 1 ms
from oa_impedance import methods

methods.register("fem", fn)  # fn(xs, freqs) -> ImpedanceResult
from oa_impedance import closed_form as cf  # every function returns a CFResult
from oa_impedance.analysis import ImpedanceXsection  # entry point  open_analysis.analyses
```

`freqs=None` means static (`[0.0]`, lossless). Results are cached by `xs.key()` + frequencies +
method + mesh in an in-memory LRU, with an optional `job_cache` mapping hook.

### Closed forms (`oa_impedance.closed_form`, each returns validity)

| Family | Model |
|---|---|
| microstrip | Hammerstad-Jensen 1980 + HJ thickness; Wheeler 1977; Kirschning-Jansen 1982 eeff(f) and Jansen-Kirschning 1983 Z0(f) |
| embedded microstrip | HJ + cover correction (always flagged approximate) |
| stripline | Cohn 1954 (t = 0) + Wheeler 1978 thickness; offset stripline by parallel combination |
| CPW / CPWG | Ghione-Naldi / Simons conformal mapping + Gupta thickness (Hilberg check) |
| edge-coupled | Kirschning-Jansen 1984 static even/odd (+1985 errata); Cohn 1955 stripline (+ Wheeler-dW thickness) |
| IPC-2141 | legacy, `legacy=True`, never chosen by `auto` |
| loss | Wheeler incremental-inductance conductor loss, filling-factor dielectric loss |

Validity: each result carries `RangeCheck`s against the published ranges. `extrapolated` = outside
the range but within 3x, `invalid` = beyond that. `auto` falls back to the BEM when the closed form
is `invalid`. Broadside-coupled stripline (Cohn 1960) has no closed form here (see gaps); it is
solved by the BEM.

### BEM (`oa_impedance.bem`)

Flat constant panels, analytic log integrals (potential and normal field, self term included),
one total-charge unknown per boundary panel, collocation at panel centres. Boundary = noded union
of conductor outlines, dielectric region boundaries and zero-thickness ribbons. Corner grading by
`size = clip(alpha * d)` with `d` the distance to the nearest singular point (corner, ribbon end,
junction). Strips thinner than `2e-3` of their width (and `ribbon` planes) collapse to
zero-thickness ribbons whose free charge is recovered from the interface operator, which is
what makes true thin strips (t = 1e-6) accurate where thick-face pairs are ill-conditioned.
Open boundaries are truncated at `20 * characteristic length` (note in every result). L comes from
the vacuum capacitance (`L = mu0 C0^-1`), R(f) from the surface-impedance perturbation using the
vacuum charge (Wheeler rule generalised), with `oa_model` roughness models (all material and roughness
physics comes from `oa_physics`; tabulated Dk/Df is interpolated in ln f). Complex permittivity gives C(f), G(f); dispersive materials are
solved at up to 6 log-spaced anchors and interpolated (cubic, log f). Pure NumPy/SciPy; BLAS is
limited to one thread for these small systems (`threadpoolctl`), which matters on loaded machines.
Numba is not used: the NumPy kernel meets the budget.

Modal decomposition (`modal.py`): `Zc = (ZY)^1/2 Y^-1`, eigenmodes, mixed-mode
`Zdiff = Zc11+Zc22-Zc12-Zc21`, `Zcomm = (Zc11+Zc22+Zc12+Zc21)/4`; for N = 2 the modal order is
`[even-like, odd-like]`. A section without a ground conductor uses its last signal as reference
(N-1 mutual modes, noted in the result).

## Golden suites

- `tests/golden/impedance_closedform/` : HJ/Wheeler table (1e-6 lock + report values), Cohn
  stripline, Cohn coupled stripline, CPW, IPC legacy vs Wheeler (report values, 0.01 ohm).
- `tests/golden/impedance_bem/` : coax, wire over plane, two-wire, *true thin-strip* Cohn
  stripline and coupled stripline (t = 1e-6 b), CPW (rtol 3e-3 for all but the coax at 1e-3).

## [verify] resolutions (docs/research/02-impedance.md)

1. **Embedded-microstrip exponent.** The primary source could not be reached. Both candidates (2 and
   1.55) were scored against the BEM on a 12-case grid (`tests/test_embedded_exponent.py`): mean
   |dZ0| 1.2 % and 1.1 %, worst 2.4 % and 2.9 %. Default 1.55; always flagged approximate.
2. **Coupled-line equation sets.** The static Kirschning-Jansen 1984 set (v, ae, be, ao..do,
   Q1..Q10, the 377-ohm denominators) was compared line by line with the Qucs technical
   documentation (published equations) and validated against the BEM within 1 %
   (`test_bem_coupled_microstrip_vs_kirschning_jansen`). The dispersive part of the paper is not
   implemented (results are static and say so). The single-line Z0(f) R1..R17 set agrees with
   scikit-rf `kirsching_zl` to < 0.1 % (20 cross-check cases). Note the Qucs-doc thickness formula
   (11.22) is wrong, as scikit-rf also records; the HJ paper form is used.
3. **Cannonball geometry.** 14 spheres of radius r in a three-layer square-based pyramid
   (9 + 4 + 1) on a *square* 6r x 6r tile (Polar AP8195 / Simonovich 2015); the hexagonal tile is
   the earlier DesignCon 2015 variant. Stack height `(2 + 2 sqrt 2) r = 4.83 r`, so `r = Rz / 4.83`;
   surface ratio `14 * 4 pi r^2 / 36 r^2`. Used by `oa_physics.roughness_factor`.
   (Secondary-source level check: the primary white paper was not fetched.)

## Known limitations

- CPW in free space (no bottom ground) converges slowly with the ground truncation: Z0 error grows
  ~0.1 % per 3 mm of substrate depth at fixed S+2G = 2 mm (0.22 % for the 8 mm golden). CPWG and
  everything with a reference plane under the structure is unaffected.
- No dispersive correction for coupled lines or CPW (static); BEM is quasi-static and reports
  `extrapolated` above `0.1 c / (sqrt(er_max) h_char)`.
- Anisotropic `dk_z_over_dk_xy` is ignored by the BEM (noted).
- Zero-thickness ribbons carry the total sheet current in the loss integral (both faces together).
- `oa_results` has no `Accuracy` model / `ResultItem.accuracy` yet: accuracy is carried by the
  container `accuracy_class` ("screening" for closed forms, "validated" for BEM) plus a
  `ValidityFlag(code="accuracy")` whose message holds the ai-integration class
  (`closed_form` / `2d_field`) and `tolerance_basis`. See docs/change-requests/WP-105-*.md.

## `impedance.profile` (WP-203)

`oa_impedance.profile.ImpedanceProfile` (entry point `impedance.profile`) profiles nets and
differential pairs along their length: Z0 / Zdiff, eps_eff, delay, loss, discontinuity segments with
their cause, a deviation table against the targets of the design intent (net-class names such as
`90ohm` are resolved by `resolve_intent`), and a field-solved per-layer delay table.

- Config: `nets` (NetSelector; empty = signal nets with a target or in a pair), `ds`, `freqs`,
  `method`, `tolerance`, `refine_tol` (position accuracy of located transitions, default 25 um),
  `batch_size`.
- `plan` samples every net with `sample_net`, locates width / coupling / coplanar transitions by
  bisection, merges samples into runs and dedups by `CrossSection.key()`: one `layout` task plus one
  `solve` task per batch of unique keys, each calling `solve_many` once on unique sections only.
- Causes: `layer_change`, `width_change`, `neckdown`, `ref_change`, `no_reference`, `plane_edge`,
  `coplanar_ground`, `pair_uncoupled`, `pair_coupled`, `pair_gap_change`, `geometry_change`.
  `ProfileEvent.kind` is a frozen literal, so causes without a literal map to `other` and carry
  `causes=...` in `message` (see `docs/change-requests/WP-203-profile-event-kinds.md`).
- Pair profiles use the odd-mode delay; an uncoupled leg of a pair reports Zdiff = 2 Z0.
- Result items: one `profile` per net (`ImpedanceProfileResult`, summary with min/max/mean Z,
  target, out-of-tolerance length and worst event), tables `deviation`, `discontinuities`,
  `sections` (unique cross-sections), `layer_delays`, scalars `stats`, and the artifact
  `tuning_profile` (JSON of `TuningProfileSet`, schema `oa-tuning-profile/1`, a data structure only;
  nothing is written to KiCad).
- Accuracy: `accuracy_class` `validated` (2d_field) if any section used the BEM, else `screening`
  (closed_form), with a `ValidityFlag` naming the methods and the tolerance basis.
- Golden suite: `tests/golden/impedance_profile/`.

## FEM reference solver (`oa_impedance.fem`, WP-202)

`import oa_impedance.fem` registers `method="fem"` (see docs/change-requests/WP-202-fem-lazy-registration.md:
WP-105's `methods.get` does not import it by itself yet).

```python
from oa_impedance.fem import FemParams, solve_fem, solve_loss, with_fem_loss

res = solve_fem(xs, [0.0, 1e9], FemParams())  # ImpedanceResult, method="fem": C, G, L, R(f)
R, L, sol = solve_loss(xs, freqs)  # eddy-current loop R(f), L(f) only, (F, N, N)
res = with_fem_loss(bem_result, xs)  # BEM result with R(f), L(f) replaced by the FEM ones
```

- **Electrostatics** (`fem/electrostatic.py`): P2 elements on the `oa_mesh` cross-section mesh
  (conductors are holes), one Schur-complement solve per conductor, complex permittivity per region
  (C(f), G(f); dispersive materials at up to 6 log-spaced anchors, cubic interpolation, as the BEM),
  floating conductors eliminated, no-ground sections use the last signal as reference (as the BEM).
  L = mu0 eps0 C0^-1 from the vacuum solve. Open boundary: Robin `d(phi)/dn = -phi/r` on the 20 x box
  for grounded sections (error ~ 3e-5 on wire over plane, vs 3.5e-3 for a Dirichlet box); floating
  Dirichlet box with zero net charge when there is no ground. Anisotropy is ignored (noted).
- **Refinement loop**: the mesh is rebuilt with `h_max / sqrt 2` and a tighter corner size until the
  energy (C, C0) changes by less than `rtol` (default 1e-3) or `max_levels`; `converged` and the level
  table go to the result. This is global re-meshing on the energy change, not element-wise error
  estimation (see gaps).
- **Eddy currents** (`fem/eddy.py`): longitudinal vector potential `A`, per-group voltage drop
  `e_g` as an extra unknown with a prescribed current constraint (signals individually, all grounds as
  one return, floating conductors I = 0). Output is the loop impedance matrix Z(f) = R + j w L; `L_int
  = L - L_ext` with `L_ext` from the vacuum solve on the same mesh. Each conductor is a *volume*
  (interior meshed, full skin-effect solve) where `delta >= t / ibc_ratio` (t = 2A/P: strip thickness,
  wire radius; default ratio 4) and a *surface* otherwise: Leontovich BC `E = Zs K` with the
  second-order curvature correction `Zs (1 + (1 - j) delta kappa / 4)` on smooth curved boundaries and
  the `oa_model` roughness factor per face (top / bottom / side). Zero-thickness conductors are always
  surfaces (no DC resistance is represented for them).
- **Accuracy flags**: `accuracy_class` is `reference` when the refinement converged, else
  `validated`; `ValidityFlag(code="accuracy")` carries `accuracy_class=2d_field; converged=...;
  tolerance_basis=golden:impedance_fem`; `with_fem_loss` adds `ValidityFlag(code="loss_model")`.

Golden suite `tests/golden/impedance_fem/`: exact cases (coax 1e-3, wire over plane, two-wire, Cohn,
coupled Cohn, CPW at 3e-3) and the Bessel round-wire R(f)/R_dc. Cross-check table (FEM / BEM /
closed form / exact): `tests/golden/impedance_fem/crosscheck/table.{md,csv,json}`, regenerated by
`tests/fem/test_crosscheck.py` (`pytest --update-golden`).

### Accuracy notes

- FEM vs exact: coax 0.02 %, wire over plane 0.004 %, two-wire 0.09 %, Cohn / coupled Cohn (true thin
  strips) 0.001 %, CPW 0.11 % (28 fixtures: all within 0.11 %). FEM vs BEM: within 0.24 % on the
  fixtures. On 35 um thick narrow microstrips the default-mesh BEM eps_eff is up to 0.6 % below the
  FEM (and HJ is closer to the FEM): the BEM converges upwards with panel density, FEM from above.
  The 50-structure test therefore compares Z0/Zdiff at 0.5 % / 1 % but eps_eff at 0.7 %.
- R(f): round wire vs Bessel within 0.19 % from 1 kHz to 10 GHz; HF asymptote vs the BEM
  surface-impedance R within 5 % at 1, 5, 10 GHz on a 35 um stripline.

### Gaps

- No element-wise (residual) adaptive refinement: `oa_mesh` accepts only global size controls.
- Eddy solves are slow compared with the BEM (tens of seconds per frequency when the conductor
  interior is meshed at skin-depth resolution); the IBC regime is a few seconds.
- No dispersion (quasi-static), no anisotropy.

## Memory safety and scaling on real boards (WP-512)

A real board section (cm5_minima IM-002) used to build 2000-35000 panels and dense matrices that
exhausted 8 GB. Root causes, in order of weight:

1. **Uniform fine panels on thin copper.** Faces of a plane were capped at 3x the distance to the
   other face of the same conductor (15 um) over the whole 20 x window length (thousands of
   panels per face); one global curvature cap set by the tightest fillet of a mask region did the
   same to dielectric interfaces. The plane cap now relaxes with the distance to the nearest edge
   (`MeshParams.plane_relax`) and the curvature cap is local to each curved vertex.
2. **Kernel temporaries.** `influence` held ~20 copies of the n x n matrix at once (about 2 GB at
   n = 3700, times the 4 `solve_many` threads). It is now evaluated in row blocks
   (`influence_blocks`); the system matrix is assembled in Fortran order and factored in place.
3. **Zone probes wider than thin layers (accuracy bug).** The points that classify the dielectric
   beside a noded piece sat at 1e-3 x its length, which is outside a 10 um mask layer on pieces
   longer than 10 mm: long sections had wrong zones (1.6-4 % Z0 error on cm5). The offset is now
   bounded by 0.1 x the thinnest z gap.

Further controls (`MeshParams`, defaults tuned on cm5 sections and the testkit):

- `window`: open boundary at `truncation` x max(stack height, signal cluster width) when a
  reference plane spans the signals; otherwise the old full length (a weakly referenced section is
  open to the whole window). Z0 changes < 0.02 % against the untruncated solve on 40 cm5 sections.
- `far_start`, `far_ratio`: beyond `far_start` truncation lengths from the signals, panels are at
  least `far_ratio x (distance - start)`. The near field is untouched, so the goldens do not move.
- Memory budget: `solve_bem(..., budget=)`, `ImpedanceServiceImpl(section_budget_mb=)` or
  `OA_IMPEDANCE_SECTION_MB` (default 512 MB per section). The dense peak is estimated before
  assembly (`dense_peak_bytes`). Over budget, the grading is coarsened (up to 3 steps of 1.25x,
  noted in the result), then the matrix-free block-Jacobi GMRES path (`bem/iterative.py`) runs,
  then `SectionTooLarge` (`IMPEDANCE.SECTION_TOO_LARGE`, with a hint) is raised. A process-wide
  gate (`OA_IMPEDANCE_TOTAL_MB`, default 1536) keeps concurrent `solve_many` threads inside a
  total.

Gaps: GMRES with the block-Jacobi preconditioner stagnates above ~1800 panels (1e-3 residual after
120 iterations), so it is a last resort, not a speed path; no H-matrix/ACA; no oa-accel GPU dense
LU (oa-impedance does not depend on oa-accel); `cross_section_at` is unchanged (its window is
already physical; the truncation lives in the BEM). One cm5 section (3 conductors) has a singular
vacuum matrix, with the old code too.

## Theory and validation

Equations, numerical method, validity range, error budget and validation evidence: [THEORY.md](THEORY.md).
