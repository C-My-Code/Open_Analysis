# oa-network: theory, error budget and validation

S-parameter quality gate (passivity, reciprocity, causality, DC), vector-fit passivity enforcement, and basic network operations. Code: `packages/oa-network/src/oa_network/` (`quality.py`, `mixedmode.py`, `ops.py`). It builds on scikit-rf (`skrf`). See the [theory index](../../docs/theory/README.md).

Read this first: the PQM, RQM and CQM numbers produced here are **implementation-defined metrics in the style of IEEE 370-2020**. They are not ported from the IEEE 370 reference code (its license could not be verified) and are not bit-identical to it. CQM in particular is a project heuristic (`docs/change-requests/WP-106-ieee370-and-cqm.md`).

## 1. Problem statement

**Input.** An N-port S-parameter set $S(f)$ (complex, N x N, $f_k$ [Hz]) with real port impedances $z_0$ [ohm].

**Output.**

- `assess`: a `QualityReport` with PQM, RQM, CQM [percent, 0 to 100], max singular value [-], worst passivity frequency [Hz], max reciprocity error [-], DC status, and a verdict `pass | warn | fail`.
- `gate(fix=True)`: the same, after optional repair (DC extrapolation, passivity enforcement), with the list of fixes applied.
- `enforce_passivity`, `make_dc`, `se2gmm` (mixed mode), `renormalize`, `shift_reference_plane`.

## 2. Governing equations and formulation

### 2.1 Passivity metric (PQM)

A passive N-port has $\sigma_\max(S(f))\le1$ at all frequencies (largest singular value, computed by SVD per frequency). With tolerance $\tau$ (`passivity_tol`, default $10^{-6}$):

$$\mathrm{PQM}=100\Big(1-\frac1{N_f}\sum_k\max\big(0,\ \sigma_\max(S(f_k))-1-\tau\big)\Big).$$

This averages the violation over frequency points. A narrow violation is diluted, so the verdict also checks $\max_f\sigma_\max$ directly: `fail` if $\sigma_\max>1+10^{-3}$ anywhere.

### 2.2 Reciprocity metric (RQM)

$$\mathrm{RQM}=100\Big(1-\frac1{N_f}\sum_k\frac{\sum_{i\ne j}|S_{ij}-S_{ji}|}{N(N-1)}\Big),$$

and 100 for a 1-port. The maximum $|S_{ij}-S_{ji}|$ over all frequencies and pairs is reported separately.

### 2.3 Causality metric (CQM)

The question: does the impulse response $h(t)$ of $S$ have energy before the line's own delay? Method (`_cqm`):

1. If $f_\min>10^{-9}f_\max$, a DC point is needed. If $f_\min\le0.1f_\max$ and there are at least 3 points, DC is extrapolated by least squares on the first (up to) six points as $\mathrm{Re}\,S=a+bf^2$ (even function; only the real part at DC is used). Otherwise DC is `missing` and CQM is not evaluated (reported as 100 with a note).
2. The dominant delay $\tau$ is estimated from the phase slope of the strongest transmission entry, removed ($S\cdot e^{+j2\pi f\tau}$), and the response is linearly resampled (real and imaginary parts) onto a uniform grid from 0 to $f_\max$ with step $\le$ `max_df_fraction` (default 1 %) $\cdot f_\max$ (at most 8192 points), then the delay is put back.
3. A cosine-squared taper is applied from 50 % of the grid up to $f_\max$ (`_TAPER = 0.5`, so the top half of the band) and an inverse real FFT gives $h(t)$. The module docstring says "top quarter"; the code constant gives the top half. The code is what runs.
4. $r_{nc}$ is the share of $\sum|h|$ lying at negative time, more than $2/f_\max$ before $t=0$ (guard `_GUARD = 2`; the module docstring says $1/f_\max$, the code and the WP-106 change request use $2/f_\max$):

$$\mathrm{CQM}=100\,(1-K\,r_{nc}),\qquad K=\texttt{CQM\_SCALE}=10 .$$

$K$ is chosen so that the small non-causal fraction of physical lossy lines maps onto the 80 % acceptance threshold. It is not a value from the standard.

### 2.4 Verdict (`_verdict`)

`fail` if $\sigma_\max>1.001$, or PQM < 99, or RQM < 99, or the DC requirement (`require_dc`, default `extrapolatable`) is not met. Otherwise `warn` if CQM < 80 or any fix was applied. Otherwise `pass`.

### 2.5 Vector fitting and passivity enforcement

`enforce_passivity` calls `skrf.vectorFitting.VectorFitting`: `vector_fit(n_poles_real=2, n_poles_cmplx=n)` with $n$ stepping through 6, 8, 10, 12, 16, 24 complex pairs (so 14 to 50 poles) until `passivity_enforce(n_samples=1000, f_max=2 f_max, preserve_dc=False)` yields a passive model (`is_passive()`). The rational model is evaluated back on the original frequency grid.

$$S_{ij}(s)\approx d+sh+\sum_p\frac{r_p}{s-a_p}$$

Vector fitting is the pole-relocation method of Gustavsen and Semlyen (1999); passivity enforcement perturbs residues to remove singular values above one. The exact algorithm variants are those of the installed scikit-rf version [verify against the scikit-rf docs for the pinned version]. If no order succeeds, the last attempt is returned with `passive=False`. `VFModel.rms_error` records the worst-case fit RMS error. Note that the repaired network replaces the measured or simulated data by the fitted model, which also smooths and may remove real features; the `gate` result is `warn` with a `passivity_enforced(vf, N poles)` entry, never silent.

`make_dc` extrapolates to DC either with `skrf`'s linear extrapolation or by evaluating a 12-pair vector-fit at $f=0$ (real part).

### 2.6 Mixed-mode conversion (`mixed_mode_matrix`, `se2gmm`)

For ports ordered $(+1,-1,+2,-2,\dots)$ the unitary wave transform is $b_{mm}=Ma_{se}$ with $d=(a_+-a_-)/\sqrt2$, $c=(a_++a_-)/\sqrt2$, ordered $(d_1\dots d_p,c_1\dots c_p)$, and $S_{mm}=M S_{se}M^T$. The result is normalised to $2z_0$ (differential) and $z_0/2$ (common) per pair, which is what the transform implies for equal single-ended $z_0$. $M$ is orthogonal, so passivity and reciprocity are preserved.

### 2.7 Renormalisation and reference-plane shift

`renormalize` delegates to scikit-rf. `shift_reference_plane` multiplies $S_{ij}$ by $e^{\pm j\omega(\tau_i+\tau_j)}$ (lossless matched line of delay $\tau$ per port; `deembed` removes the lead-in, `extend` adds it).

## 3. Assumptions and validity range

- **Real port impedances only.** Complex reference impedances are not handled by the metrics.
- **PQM and the verdict test the data, not the physics.** A set with sigma_max slightly above 1 from numerical noise (for example FDTD with an early-time truncation) fails unless the user raises `passivity_tol`.
- **CQM is not sensitive to mild non-causality.** A constant-Dk, constant-Df line with Df = 0.02 scores about 95 and passes; Df = 0.2 scores about 50 and warns (WP-106 change request). Do not read a passing CQM as proof of causality for typical PCB loss tangents.
- **CQM needs DC or an extrapolatable low end.** With $f_\min>0.1f_\max$ it is not evaluated and the verdict is `fail` unless `require_dc="none"`. The delay estimate assumes a single dominant transmission path and a roughly linear phase.
- **Frequency grid.** The causality evaluation assumes the S-parameters cover the band where the response is non-negligible; truncating a lossy response at $f_\max$ with significant remaining magnitude leaks energy into negative time. A sparse grid is resampled linearly, which adds its own interpolation error.
- **Vector fitting** assumes a smooth, band-limited response representable by 14 to 50 poles. Sharp resonances, long delays (many sample periods) and noisy data may need more poles, and in that case the repaired model may be passive but inaccurate; check `rms_error`.
- **Mixed-mode conversion** assumes the ordering `+1, -1, +2, -2, ...` and equal single-ended $z_0$ across a pair.

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| Metrics for a clean passive causal line | PQM = RQM = 100, CQM > 95 (201 points, DC to 20 GHz, DS Dk 4 Df 0.02, 30 mm) | `test_passive_line_metrics` |
| Lossless line S matrix | $\sigma_\max=1$ to numerical precision; CQM > 99; round trip exact | `test_lossless_line_s_matrix_is_exact_and_causal` |
| Passivity enforcement change | at most 0.2 dB change in the S entries on the test case (assertion `np.max(abs(db)) <= 0.2`) | `test_enforce_passivity_fixes_violation_with_small_change` |
| DC extrapolation | $S_{21}(0)$ within 0.01 of the analytic 0.96 (skrf linear) or 0.02 (rational) | `test_gate_dc_fix_and_make_dc` |
| CQM scale factor $K=10$ | arbitrary; calibrated on synthetic lines only | WP-106 change request |
| Difference to the IEEE 370 reference metrics | **unknown**; never compared | FOLLOWUPS WP-106 |

## 5. Validation evidence

Golden suite `tests/golden/network_quality/` (five Touchstone fixtures, regenerated by `packages/oa-network/tests/make_fixtures.py`). Each case checks only the verdict and DC status, with the S-parameters constructed analytically:

| Fixture | Construction | Expected verdict |
|---|---|---|
| `passive_line` | 30 mm lossy line, DS Dk 4 Df 0.02, DC to 20 GHz | pass |
| `passivity_violation` | $S_{21},S_{12}$ scaled by 1.05 | fail |
| `non_reciprocal` | $S_{12}$ scaled by 0.8 | fail |
| `non_causal_const_dk` | same line with constant Dk 4 Df 0.2 | warn |
| `starts_at_10mhz` | `passive_line` sampled from 10 MHz | pass, DC `extrapolated` |

Unit tests (`packages/oa-network/tests/test_quality.py`) also check port order and decoupling of mixed-mode (S_dd21 equals the line S_21, S_dc terms vanish to $10^{-12}$, port impedances 100, 100, 25, 25 ohm) and the renormalisation of a series resistor to 25 ohm to $10^{-6}$ relative.

Integration evidence: the quality gate runs on every EM solve. For the openEMS microstrip case the gate verdict is `pass` with PQM 100 (`em.openems.quality_pqm`, `docs/validation/results.json`). On the via-transition fixture, S-parameters below about 0.1 f_max from a DC-free FDTD pulse fail the gate (S11 about -11 dB, RQM 93, sigma_max 1.09 at 0.1 f_max, comment in `oa_em/problem.py`), which is why the EM flow starts the sweep at 0.2 f_max for openEMS and relaxes the DC requirement.

**Known gaps.**

- No comparison with the IEEE 370 reference implementation (license not verified). CQM is "weakly sensitive" (FOLLOWUPS WP-106); calibrate it against the reference if the license allows.
- The golden cases test verdict labels, not metric values; metric values are covered by a few asserted bounds.
- No test of passivity enforcement on measured or multi-port (more than 2 ports) data; the enforcement test uses a 2-port fixture. [verify: no multi-port enforcement test found]
- Mixed-mode conversion is tested on an uncoupled pair only.

## 6. References

- IEEE Std 370-2020, *Standard for Electrical Characterization of Printed Circuit Board and Related Interconnects at Frequencies up to 50 GHz*. The standard's text is not in the repository; the code implements metrics "in the spirit of" it.
- B. Gustavsen, A. Semlyen, "Rational approximation of frequency domain responses by vector fitting", *IEEE Trans. Power Delivery* 14(3), 1999, pp. 1052-1061.
- B. Gustavsen, "Improving the pole relocating properties of vector fitting", *IEEE Trans. Power Delivery* 21(3), 2006, pp. 1587-1592 [verify].
- B. Gustavsen, "Fast passivity assessment for S-parameter rational models via a half-size test matrix", *IEEE Trans. MTT* 56(12), 2008 [verify; named by the scikit-rf docs for its passivity enforcement, version-dependent].
- D. E. Bockelman, W. R. Eisenstadt, "Combined differential and common-mode scattering parameters: theory and simulation", *IEEE Trans. MTT* 43(7), 1995, pp. 1530-1539 [verify pages].
- K. Kurokawa, "Power waves and the scattering matrix", *IEEE Trans. MTT* 13(2), 1965, pp. 194-202.
- scikit-rf documentation: VectorFitting, `passivity_enforce`, IEEE P370 de-embedding (URLs in `docs/research/03-signal-integrity.md`).
- Project: `docs/specs/interfaces.md` section 4.5; `docs/research/03-signal-integrity.md` section 2.5.
