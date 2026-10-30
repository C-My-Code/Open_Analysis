# oa-physics: theory, error budget and validation

Material and via physics shared by the solvers: causal dielectric models, skin effect, surface impedance, roughness, resistivity versus temperature, and analytic via estimators. Code: `packages/oa-physics/src/oa_physics/` (`dielectric.py`, `conductor.py`, `via.py`). See the [theory index](../../docs/theory/README.md).

## 1. Problem statement

| Quantity | Inputs | Output |
|---|---|---|
| Complex permittivity $\varepsilon(f)=\varepsilon'-j\varepsilon''$ | model (constant, Djordjevic-Sarkar, multi-pole Debye, table); $f$ [Hz] | relative permittivity [-]; $\tan\delta=\varepsilon''/\varepsilon'$ |
| Skin depth $\delta$ [m], surface resistance $R_s$ [ohm/sq], surface impedance $Z_s$ [ohm/sq] | conductivity [S/m], $f$, roughness model | $\delta$, $Z_s(f)$ |
| Roughness power-loss multiplier $K(f)\ge1$ [-] | model parameters (rms [m], nodule radius [m], surface ratio [-], $R_z$ [m]) | $K$ |
| $\rho(T)$, sheet resistance [ohm/sq], etched trace width | material, $T$ [K], thickness [m], etch spec | ohm m, ohm/sq, m |
| Via R, L, C, stub resonance, pi-model S-parameters | height, drill, plating, pad and antipad diameters [m], $\varepsilon_r$, stub length | ohm, H, F, Hz, 2-port S |

Sign convention: $\varepsilon=\varepsilon'-j\varepsilon''$ with $e^{+j\omega t}$. Temperatures are in kelvin.

## 2. Governing equations and formulation

### 2.1 Dielectrics (`dielectric.py`)

**Constant Dk/Df.** $\varepsilon=D_k(1-jD_f)$. This is not causal: a constant loss tangent over a band cannot satisfy Kramers-Kronig.

**Djordjevic-Sarkar (wideband Debye-like, causal).**

$$\varepsilon(\omega)=\varepsilon_\infty+\frac{\Delta\varepsilon}{m_2-m_1}\,\log_{10}\!\frac{\omega_2+j\omega}{\omega_1+j\omega},\qquad m_i=\log_{10}\omega_i .$$

`ds_parameters` solves for $\Delta\varepsilon$ and $\varepsilon_\infty$ so that $\varepsilon(f_\text{ref})=D_k(1-jD_f)$ exactly. Defaults (`oa_model.materials.DjordjevicSarkar`): $\omega_1=10^4$ rad/s, $\omega_2=10^{12}$ rad/s, $f_\text{ref}=1$ GHz. `fit_ds` fits $(D_k,D_f)$ at a reference point by nonlinear least squares (`scipy.optimize.least_squares`) over a table, with the band corners fixed.

**Multi-pole Debye.** $\varepsilon=\varepsilon_\infty+\sum_k\Delta\varepsilon_k/(1+jf/f_k)$ with an optional DC conductivity term $-j\sigma_{dc}/(\omega\varepsilon_0)$.

**Table.** Linear interpolation of $D_k$ and $D_f$ in $\ln f$ (flat outside the table, because `np.interp` clamps).

**`as_causal`** maps Constant to DS and Table to a DS fit.

**`kk_residual`** is a check, not a model: it rebuilds $\varepsilon'$ from $\varepsilon''$ with the subtractive Kramers-Kronig integral

$$\varepsilon'(\omega)=\varepsilon_\infty+\frac{2}{\pi}\,\mathrm{PV}\!\int_0^\infty\frac{\omega'\varepsilon''(\omega')}{\omega'^2-\omega^2}\,d\omega'$$

on a uniform $\ln\omega$ grid (Maclaurin alternate-point principal value, 40 points per decade), extends $\varepsilon''$ by power-law tails 8 decades beyond the band, takes the unknown $\varepsilon_\infty$ as the mean offset, and returns $\max|\varepsilon'_\text{data}-\varepsilon'_\text{KK}-\text{offset}|/\overline{\varepsilon'}$. Zero means causal. The result depends on the assumed tails, so treat small values as "no evidence of non-causality", not as proof.

### 2.2 Skin effect and surface impedance (`conductor.py`)

$$\delta=\frac{1}{\sqrt{\pi f\mu_0\mu_r\sigma}},\qquad R_s=\frac{1}{\sigma\delta},\qquad Z_s=(1+j)R_s\ \ (\text{smooth, thick conductor}).$$

### 2.3 Roughness

Real-valued power-loss multipliers $K(f)$ (`roughness_factor`):

| Model | $K$ |
|---|---|
| Hammerstad (rms $R_q$) | $1+\frac{2}{\pi}\arctan\!\left(1.4\,(R_q/\delta)^2\right)$, saturates at 2 |
| Groiss (code name `Groisse`) | $1+\exp\!\left(-(\delta/2R_q)^{1.6}\right)$, saturates at 2 |
| Huray (nodule radius $a$, surface ratio SR, flat ratio) | $\text{flat}+\dfrac{1.5\,\text{SR}}{1+\delta/a+\delta^2/(2a^2)}$ |
| Cannonball-Huray ($R_z$) | Huray with $r=R_z/(2+2\sqrt2)$ and $\text{SR}=14\cdot4\pi r^2/(36r^2)$ |

The cannonball stack is 14 equal spheres in a 3-layer square pyramid (9+4+1) of height $(2+2\sqrt2)r$ on a $6r\times6r$ tile. The tile area $36r^2$ is the least certain constant; see section 5.

**Causal surface impedance** (`surface_impedance(..., causal=True)`, the default). A real $K$ applied as $Z_s=(1+j)K R_s$ scales the reactance with the resistance and is not causal. Instead the code fits (after Bracken, DesignCon 2012 [verify])

$$Z_s^{c}=Z_s\Big(1+\sum_k c_k\,\frac{jf}{jf+f_k}\Big),\qquad \mathrm{Re}\,Z_s^{c}\approx K R_s,$$

by linear least squares (ridge $10^{-8}$, weights $1/R_s$) with three first-order sections per decade over the request range extended by a decade at each end. Each section is a stable minimum-phase response, so the sum is causal by construction and the reactance follows from the resistance. `causal=False` returns $(1+j)KR_s$.

### 2.4 Resistivity and etch

$\rho(T)=\rho_0\,[1+a(T-T_\text{ref})]$ (`resistivity`), $R_s=\rho(T)/t$. `etch_widths` returns bottom and top widths of the trapezoid from either an angle ($\Delta=2t/\tan\theta$) or a width difference.

### 2.5 Via estimators (`via.py`)

| Quantity | Formula |
|---|---|
| Barrel inductance (Goldfarb-Pucel) | $L=\dfrac{\mu_0 h}{2\pi}\left[\ln\dfrac{4h}{d}+1\right]$ |
| Pad capacitance (Johnson and Graham) | $C=1.41\,\varepsilon_r\,T\,D_1/(D_2-D_1)$ pF, lengths in inches ($D_1$ pad, $D_2$ antipad, $T$ dielectric thickness the pad sees) |
| Signal/return via pair loop inductance | $L=\dfrac{\mu_0h}{\pi}\ln\dfrac{s}{r}$ |
| Barrel resistance | $R=\rho h/\bigl(\pi(r_o^2-r_i^2)\bigr)$, $r_i=r_o-t_\text{plating}$ |
| Open stub resonance | $f_0=c/(4\ell\sqrt{\varepsilon_r})$ |

`estimate` combines them into a pi model: series $R+j\omega L$ split in halves, half the total pad capacitance at each end, and a lossless open stub as a shunt admittance $j\tan(\beta\ell)/Z_\text{stub}$ at the barrel midpoint (`pi_model_sparams` cascades ABCD matrices and converts to S with reference `z0`, default 50 ohm). `t_pad` defaults to $h/4$ if not given.

## 3. Assumptions and validity range

- **Dielectric.** Single-frequency Dk/Df cannot describe a band. Djordjevic-Sarkar is a fit between the corner frequencies; outside $\omega_1..\omega_2$ it is an extrapolation. It cannot represent resonant or relaxation features inside the band, glass-weave anisotropy, humidity or temperature dependence.
- **Roughness.** The Hammerstad and Groiss models cap at $K=2$ and cannot represent the higher loss seen from very rough foil at high frequency. Huray and cannonball models need nodule parameters that are rarely published; the cannonball variant derives them from $R_z$ alone. All $K$ values assume the roughness is small compared with the conductor thickness and that $\delta$ is small compared with the conductor thickness. Below roughly the frequency where $\delta\approx R_q$ the multiplier is close to 1 by construction.
- **Skin depth and $Z_s$.** The semi-infinite approximation requires the conductor thickness to be larger than a few $\delta$. For 35 µm copper that fails below a few MHz; the DC and low-frequency resistance must come from a different model (see `oa-impedance` DC combination and `oa-fdtd` finite-slab impedance).
- **Via estimators.** Lumped, electrically short vias (height much smaller than a wavelength). They ignore the coupling to neighbouring vias, antipad shape beyond the diameter ratio, and the dispersion of the barrel and stub. The stub is a single quarter-wave resonance of a lossless line; higher resonances are the odd multiples. The Johnson-Graham pad capacitance formula is an empirical rule from a handbook, valid when $D_2>D_1$ and the pad is small compared with the dielectric thickness.

## 4. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| DS round trip at the reference point | exact to $10^{-9}$ relative | `tests/golden/physics/ds_roundtrip` |
| DS Dk, tan-delta variation across a band | not measured in this package; for Palace/openEMS fits the adapters report 0 to 3 % tan-delta (Palace, DS, README) and about 11 % for a single-pole Debye fit over 0.5 to 10 GHz (FOLLOWUPS, openEMS) | `docs/FOLLOWUPS.md`, `oa-em-palace/README.md` |
| Skin depth, $R_s$, sheet resistance, barrel R, stub resonance, via-pair L | formula agreement within the case tolerance (0.1 to 1 %) | `tests/golden/physics/*` |
| Via barrel R against the "1.27 mohm" ROADMAP figure | +0.57 % (model 1.2772 mohm). This is rounding of the quoted figure, not model error. | `docs/validation` case `dc.via_barrel_roadmap` |
| Cannonball-Huray tile area $36r^2$ | unverified; may overestimate K | FOLLOWUPS WP-106 |
| Via L and C against a 3D field solver | **not measured** in this repo | no case |

Input error (the true foil roughness, true Dk/Df of a lot) usually dominates these terms and is not measured here.

## 5. Validation evidence

The eight golden cases in `tests/golden/physics/` are **formula checks**: they confirm that the code implements the stated formula (DS round trip, skin depth at 1 GHz for copper, 1 oz sheet resistance, Hammerstad/Groiss saturation to 2, Huray formula with typical snowball values, barrel R, via-pair L, stub resonance). They do not show that the formula describes a real board; they are not independent validation. Their `source` field is `analytic`, and the tolerances range from $10^{-9}$ (DS round trip) to 2 % (Huray).

Known gaps:

- The Huray golden case checks the equation with typical values; it should be replaced with a digitised published curve (FOLLOWUPS WP-106).
- The cannonball tile area is an unverified placeholder (`CANNONBALL_TILE_AREA_FACTOR = 36.0` in `conductor.py`, marked [verify]); check against Simonovich (DesignCon 2015). The geometry comes from secondary sources.
- Via R, L and C are not compared with a field solver or a measurement.
- The causal surface impedance fit is a least-squares approximation of $K R_s$; its fit residual is not asserted by a test [verify: no golden case found].
- `kk_residual` has no calibration against known non-causal data other than the `non_causal_const_dk` network fixture (see [oa-network](../oa-network/THEORY.md)).
- Resolved by WP-513: `oa_impedance` no longer keeps a second copy (`_physics_compat` was removed). It uses this package, so tabulated Dk/Df is interpolated in $\ln f$ consistently.

## 6. References

- A. R. Djordjevic, R. M. Biljic, V. D. Likar-Smiljanic, T. K. Sarkar, "Wideband frequency-domain characterization of FR-4 and time-domain causality", *IEEE Trans. Electromagn. Compat.* 43(4), 2001, pp. 662-667.
- C. Svensson, G. E. Dermer, "Time domain modeling of lossy interconnects", *IEEE Trans. Adv. Packag.* 24(2), 2001 [verify]. Background on causal dielectric models only; not used for a formula in the code.
- E. O. Hammerstad, F. Bekkadal, *Microstrip Handbook*, ELAB Report STF44 A74169, Univ. of Trondheim, 1975.
- H. Groiss, I. Bardi, O. Biro, K. Preis, K. R. Richter, "Parameters of lossy cavity resonators calculated by the finite element method", *IEEE Trans. Magn.* 32(3), 1996, pp. 894-897 [verify: this is the paper usually cited for the Groiss roughness model; the code docstring cites only "Groisse 1996"].
- P. G. Huray, O. Oluwafemi, J. Loyer, E. Bogatin, X. Ye, "Impact of copper surface texture on loss: a model that works", *DesignCon 2010*.
- S. Hall, S. G. Pytel, P. G. Huray et al., "Multigigahertz causal transmission line modeling methodology using a 3-D hemispherical surface roughness approach", *IEEE Trans. MTT* 55(12), 2007, pp. 2614-2624 [verify].
- B. Simonovich, cannonball-stack roughness model, *DesignCon 2015*. The code and FOLLOWUPS cite it by venue and year only; title and the tile area are [verify]. The primary paper was not fetched.
- E. Bracken, "Enforcing causality of rough-copper surface-impedance models", *DesignCon 2012* [verify title].
- T. Goldfarb, R. F. Pucel, "Modeling via hole grounds in microstrip", *IEEE Microwave Guided Wave Lett.* 1(6), 1991, pp. 135-137.
- H. Johnson, M. Graham, *High-Speed Digital Design: A Handbook of Black Magic*, Prentice Hall, 1993.
- Project research: `docs/research/02-impedance.md` sections 2.2 to 2.4; `docs/research/07-quasistatic-thermal-emi.md` section 1.2 (vias).
