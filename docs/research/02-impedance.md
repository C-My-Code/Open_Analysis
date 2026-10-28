# 02 - Transmission-Line Impedance and 2D Cross-Section Solving

Status: research report (scope: closed-form impedance models, frequency-dependent RLGC, 2D quasi-static field solvers, validation data, implementation recommendation).
Date: 2026-10-27

Conventions: `eta0 = 376.7303 ohm`, `u = W/h`, `t` = copper thickness, `h` = dielectric height trace-to-plane, `b` = plane-to-plane spacing (stripline), `K(k)` = complete elliptic integral of the first kind of *modulus* k (SciPy `ellipk` takes the *parameter* m = k^2), `k' = sqrt(1-k^2)`.

Numbers marked **[computed]** came from a pure-Python implementation of the formulas below and are suitable as regression seeds. Items marked **[verify]** should be checked against the primary paper before being used in code.

---

## 1. Closed-form models

### 1.1 General accuracy picture

| Structure | Recommended closed form | Typical accuracy inside validity range | Known weak / bad |
|---|---|---|---|
| Surface microstrip | Hammerstad-Jensen 1980 (+ HJ thickness correction); Wheeler 1977 as alternative | Z0 < 0.03 %, eps_eff < 0.2 % vs. exact quasi-static for zero thickness; ~1 % with thickness correction | IPC-2141 `87/sqrt(er+1.41)` (errors of 2-10 %, worse outside 0.1<W/h<2.0) |
| Microstrip dispersion | Kirschning-Jansen 1982 (eps_eff(f)); Kirschning-Jansen 1983 / Jansen-Kirschning (Z0(f)) | 0.6 % up to f*h = 25 GHz*mm | Getsinger (simple, few %) |
| Embedded (buried) microstrip | Cover-correction of eps_eff (Bahl/Brooks style) | 2-5 % | All closed forms; use solver |
| Symmetric stripline | Cohn 1954 exact (t=0) + Wheeler 1978 thickness form (Wadell 3.5) | exact (t=0); ~0.5 % with thickness for W'/(b-t)<10 | IPC-2141 stripline `60/sqrt(er) ln(1.9b/(0.8W+t))` |
| Asymmetric (offset) stripline | Parallel-combination approximation (Wadell 3.6) | 2-5 % | All closed forms for strong offset; use solver |
| CPW (no bottom ground) | Conformal mapping (Wen 1969; Ghione-Naldi; Simons 2001) | ~1 % for t=0; finite-thickness Gupta correction few % | Finite ground width neglected |
| CPWG (grounded CPW) | Conformal mapping (Ghione-Naldi 1987; Simons) | 1-3 % for infinite lateral grounds, no via fence | Wide gaps (G>h): quasi-microstrip, formula OK but ground width/vias matter |
| Edge-coupled microstrip | Kirschning-Jansen 1984 (with 1985 errata); Hammerstad-Jensen 1980 coupled | eps_eff,e/o < 0.7 %, Z0e/o < 0.6 % for 0.1<=u<=10, 0.1<=g<=10, 1<=er<=18 | IPC `Zdiff = 2Z0(1-0.48 exp(-0.96 s/h))` |
| Edge-coupled stripline | Cohn 1955 exact (t=0) + Wadell thickness corrections | exact (t=0); 1-2 % with thickness | IPC `Zdiff = 2Z0(1-0.347 exp(-2.9 s/b))` |
| Broadside coupled stripline | Cohn 1960 (t=0, centered); Shelton 1966 (offset) | 1-3 % | Anything with thick copper/prepreg asymmetry; use solver |

Canonical references:
- B. C. Wadell, *Transmission Line Design Handbook*, Artech House, 1991 (ISBN 0-89006-436-9). The standard compilation; ch. 3 covers microstrip and stripline, ch. 4 covers coupled lines.
- E. Hammerstad and O. Jensen, "Accurate Models for Microstrip Computer-Aided Design," IEEE MTT-S Int. Microwave Symp. Digest, 1980, pp. 407-409. doi:10.1109/MWSYM.1980.1124303
- H. A. Wheeler, "Transmission-Line Properties of a Strip on a Dielectric Sheet on a Plane," IEEE Trans. MTT-25, no. 8, pp. 631-647, Aug. 1977.
- H. A. Wheeler, "Transmission-Line Properties of a Strip Line Between Parallel Planes," IEEE Trans. MTT-26, pp. 866-876, Nov. 1978.
- M. Kirschning and R. H. Jansen, "Accurate Model for Effective Dielectric Constant of Microstrip with Validity up to Millimetre-Wave Frequencies," Electronics Letters 18(6), pp. 272-273, 1982.
- R. H. Jansen and M. Kirschning, "Arguments and an Accurate Model for the Power-Current Formulation of Microstrip Characteristic Impedance," AEU 37, pp. 108-112, 1983.
- M. Kirschning and R. H. Jansen, "Accurate Wide-Range Design Equations for the Frequency-Dependent Characteristic of Parallel Coupled Microstrip Lines," IEEE Trans. MTT-32, no. 1, pp. 83-90, Jan. 1984; corrections MTT-33, p. 288, Mar. 1985.
- S. B. Cohn, "Characteristic Impedance of the Shielded-Strip Transmission Line," IRE Trans. MTT-2, pp. 52-57, 1954; "Shielded Coupled-Strip Transmission Line," IRE Trans. MTT-3, pp. 29-38, Oct. 1955; "Characteristic Impedances of Broadside-Coupled Strip Transmission Lines," IRE Trans. MTT-8, pp. 633-637, 1960.
- J. P. Shelton, "Impedances of Offset Parallel-Coupled Strip Transmission Lines," IEEE Trans. MTT-14, pp. 7-15, 1966.
- R. N. Simons, *Coplanar Waveguide Circuits, Components, and Systems*, Wiley, 2001.
- G. Ghione and C. Naldi, "Coplanar Waveguides for MMIC Applications: Effect of Upper Shielding, Conductor Backing, Finite-Extent Ground Planes, and Line-to-Line Coupling," IEEE Trans. MTT-35, no. 3, pp. 260-267, 1987.
- K. C. Gupta, R. Garg, I. Bahl, P. Bhartia, *Microstrip Lines and Slotlines*, 2nd ed., Artech House, 1996.
- The Qucs technical documentation reproduces most of these equations in implementable form: https://qucs.sourceforge.net/tech/technical.html (chapters "Microstrip components", "Coplanar components").

### 1.2 Microstrip - Hammerstad-Jensen (1980)

Zero-thickness, quasi-static:

```
f(u)   = 6 + (2*pi - 6) * exp( -(30.666/u)^0.7528 )
Z01(u) = (eta0 / (2*pi)) * ln( f(u)/u + sqrt(1 + (2/u)^2) )       # air-filled impedance

a(u)   = 1 + (1/49)*ln( (u^4 + (u/52)^2) / (u^4 + 0.432) ) + (1/18.7)*ln( 1 + (u/18.1)^3 )
b(er)  = 0.564 * ((er - 0.9)/(er + 3))^0.053
eeff(u,er) = (er+1)/2 + (er-1)/2 * (1 + 10/u)^(-a(u)*b(er))

Z0 = Z01(u) / sqrt(eeff)
```

Stated accuracy (vs. exact quasi-static): Z01 better than 0.01 % for u <= 1 and 0.03 % for u <= 1000; eeff better than 0.2 % for er <= 128, 0.01 <= u <= 100.

Thickness correction (HJ 1980):

```
du1 = (t/(pi*h)) * ln( 1 + 4*e / ( (t/h) * coth^2( sqrt(6.517*u) ) ) )
dur = 0.5 * (1 + 1/cosh( sqrt(er - 1) )) * du1
u1 = u + du1 ;  ur = u + dur
Z0   = Z01(ur) / sqrt( eeff(ur, er) )
eeff_t = eeff(ur, er) * ( Z01(u1) / Z01(ur) )^2
```

### 1.3 Microstrip - Wheeler (1977), as given in Wadell sec. 3.2

```
dW  = (t/pi) * ln( 4*e / sqrt( (t/h)^2 + ( (1/pi) / (W/t + 1.1) )^2 ) )
W'  = W + dW * (1 + 1/er)/2
X   = 4h/W'
A   = (14 + 8/er)/11 * X
Z0  = eta0 / (2*pi*sqrt(2*(1+er))) * ln( 1 + X*( A + sqrt( A^2 + pi^2*(1 + 1/er)/2 ) ) )
```

Wheeler and HJ agree within ~1 % over the practical range (table 4.1). Altium, summarizing a Polar comparison, reports Wheeler errors under ~0.7 % vs. numerical results, about 10 times better than IPC-2141 ([Altium](https://resources.altium.com/p/clearing-trace-impedance-calculators-and-formulas); Polar AP131, [JP version](https://www.polarinstruments.com/support/cits/AP131_J.pdf)).

### 1.4 The IPC-2141 "simple" formulas (do NOT ship as primary)

```
Microstrip:  Z0 = 87/sqrt(er + 1.41) * ln( 5.98 h / (0.8 W + t) )      nominal validity 0.1 < W/h < 2.0, 1 < er < 15
Stripline:   Z0 = 60/sqrt(er) * ln( 1.9 b / (0.8 W + t) )              W/(b-t) < 0.35, t/b < 0.25
Edge-coupled microstrip:  Zdiff = 2 Z0 (1 - 0.48 exp(-0.96 s/h))
Edge-coupled stripline:   Zdiff = 2 Z0 (1 - 0.347 exp(-2.9 s/b))
```

These come from IPC-D-317A / older National Semiconductor and Motorola app notes and remain in IPC-2141. IPC-2141A itself tells users to use field solvers. **[computed]** For h = 0.2 mm, t = 35 um, er = 4.3, IPC gives 36.8 ohm and Wheeler gives 41.1 ohm at W = 0.5 mm (a 10 % error). At W = 0.35 mm the values are 48.6 and 50.8 ohm (4 %). The coupled-line forms are worse because they ignore er and W in the coupling term. Ship them only as a labeled "IPC legacy" option for comparison with fab quotes.

### 1.5 Microstrip dispersion - Kirschning-Jansen (1982)

```
fn = f[GHz] * h[mm]
P1 = 0.27488 + (0.6315 + 0.525/(1 + 0.0157 fn)^20) u - 0.065683 exp(-8.7513 u)
P2 = 0.33622 (1 - exp(-0.03442 er))
P3 = 0.0363 exp(-4.6 u) (1 - exp(-(fn/38.7)^4.97))
P4 = 1 + 2.751 (1 - exp(-(er/15.916)^8))
P  = P1 P2 ((0.1844 + P3 P4) fn)^1.5763
eeff(f) = er - (er - eeff(0)) / (1 + P)
```

Validity: 0.1 <= u <= 100, 1 <= er <= 20, 0 <= fn <= 25. Accuracy: 0.6 %.

Z0(f) uses the Jansen-Kirschning 1983 power-current model, which has a longer set of R1..R17 coefficients. Implement it from the Qucs technical documentation (sec. "Microstrip line / dispersion") or the AEU paper. scikit-rf's `MLine` already implements HJ/Wheeler/Schneider quasi-static models with Kirschning-Jansen and other dispersion options ([skrf MLine source](https://scikit-rf.readthedocs.io/en/latest/_modules/skrf/media/mline.html)), so it is a BSD-licensed cross-check.

For typical PCB (h ~ 0.1-0.2 mm, f < 50 GHz), fn < 10, and dispersion shifts eeff by < 2-3 %. That is still important for phase and delay.

### 1.6 Embedded (buried) microstrip

The usual closed form keeps the microstrip Z0 geometry and corrects eeff for a cover of thickness `d` above the strip, with the same er:

```
eeff_emb = eeff_ms * exp(-2 d/h) + er * (1 - exp(-2 d/h))     # approximate (Bahl/Brooks form) [verify exponent; some sources use -1.55 H1/H]
Z0_emb   = Z0_ms(air) / sqrt(eeff_emb)
```

Accuracy is 2-5 %, and the form breaks down for different cover and substrate er, which is the normal case on real boards (prepreg plus solder mask). **Solder-mask-coated microstrip is a special case of this.** A 15-25 um LPI mask (er ~ 3.3-4) lowers Z0 by about 1-3 ohm on a 50 ohm line, and the conformal coating over a trapezoidal trace has no closed form. Use the 2D solver for these.

### 1.7 Stripline

Symmetric, zero thickness (Cohn, exact conformal mapping):

```
k  = sech( pi W / (2 b) )
Z0 = eta0 / (4 sqrt(er)) * K(k) / K(k')
```

**[computed]** For er = 1: W/b = 0.1 gives 194.23 ohm, 0.25 gives 139.92, 0.5 gives 100.43, 1.0 gives 65.35, and 2.0 gives 38.58.

Finite thickness (Wheeler 1978, Wadell eq. 3.5.1):

```
x  = t/b
m  = 2 / (1 + (2/3) x/(1-x))
dW/(b-t) = x/(pi(1-x)) * ( 1 - 0.5 ln( (x/(2-x))^2 + (0.0796 x / (W/b + 1.1 x))^m ) )
W'/(b-t) = W/(b-t) + dW/(b-t)
Q  = (4/pi) (b-t)/W'
Z0 = eta0 / (4 pi sqrt(er)) * ln( 1 + Q ( 2Q + sqrt( (2Q)^2 + 6.27 ) ) )
```

Accuracy is about 0.5 % for W'/(b-t) < 10.

Asymmetric (offset) stripline (Wadell 3.6 / Cohn): treat it as two "half" symmetric striplines in parallel:

```
Z0 ~ 2 * Z0sym(b1) * Z0sym(b2) / ( Z0sym(b1) + Z0sym(b2) ),   b1 = 2 h1 + t,  b2 = 2 h2 + t
```

This gives a few percent error, worse for strong offset or mixed core/prepreg er. Real dual-stripline (two signal layers between planes) always needs the solver.

### 1.8 Coplanar waveguide

Centre strip width S, gap G, substrate height h, zero thickness:

```
k0 = S / (S + 2G)
CPW on finite-height substrate (no bottom ground):
  k1   = sinh(pi S/(4h)) / sinh(pi (S+2G)/(4h))
  eeff = 1 + (er-1)/2 * [K(k1)/K(k1')] * [K(k0')/K(k0)]
  Z0   = 30 pi / sqrt(eeff) * K(k0')/K(k0)

CPWG (conductor-backed CPW):
  k3   = tanh(pi S/(4h)) / tanh(pi (S+2G)/(4h))
  q    = [K(k3)/K(k3')] / [K(k0)/K(k0')]
  eeff = (1 + er q) / (1 + q)
  Z0   = 60 pi / sqrt(eeff) / ( K(k0)/K(k0') + K(k3)/K(k3') )
```

Thickness correction (Gupta et al.): `S_e = S + D`, `G_e = G - D`, `D = (1.25 t/pi)(1 + ln(4 pi S / t))`. Also apply a correction to eeff (Qucs tech doc).

Elliptic ratio (Hilberg, accurate to ~3 ppm), useful for compiled code without a special-function library:

```
0 <= k <= 1/sqrt2:   K(k)/K(k') = pi / ln( 2 (1 + sqrt(k')) / (1 - sqrt(k')) )
1/sqrt2 <= k <= 1:   K(k)/K(k') = (1/pi) ln( 2 (1 + sqrt(k)) / (1 - sqrt(k)) )
```

**[computed]** Infinite-substrate CPW (eeff = (er+1)/2) with S = 1, G = 0.5: Z0 = 120.57 ohm for er = 1 and 51.88 ohm for er = 9.8.

Limits: these formulas assume infinitely wide side grounds, no top cover, and (for CPWG) ideal via stitching. PCB "coplanar ground pour" usually has finite pour width, a solder-mask overlay, and a trapezoidal edge. Use the solver for production numbers.

### 1.9 Edge-coupled lines

Edge-coupled stripline, zero thickness, centred (Cohn 1955, exact):

```
ke = tanh(pi W/(2b)) * tanh(pi (W+S)/(2b))
ko = tanh(pi W/(2b)) * coth(pi (W+S)/(2b))
Z0e = eta0/(4 sqrt(er)) * K(ke')/K(ke)
Z0o = eta0/(4 sqrt(er)) * K(ko')/K(ko)
Zdiff = 2 Z0o ;  Zcomm = Z0e / 2
```

**[computed]** For er = 1, W/b = 0.5: S/b = 0.1 gives (Z0e, Z0o) = (122.89, 69.87) ohm, 0.25 gives (114.77, 83.52), and 0.5 gives (107.15, 93.22). As S tends to infinity, both values converge to the single-line 100.43 ohm (self-consistency check).

Edge-coupled microstrip: Kirschning-Jansen 1984 gives static eeff,e/o and Z0e/o and their dispersion. It is a long equation set built on the HJ single-line functions. Use the Qucs tech doc chapter "Coupled microstrip" or KiCad's `c_microstrip.cpp` (GPL, so do not copy) as an implementation guide and re-derive from the paper. Validity is 0.1 <= u <= 10, 0.1 <= g = s/h <= 10, 1 <= er <= 18, and fn <= 15 GHz*mm for the dispersive part. Static accuracy is better than 1 %.

Broadside-coupled stripline (Cohn 1960, centred strips separated by s inside spacing b): `Z0o` and `Z0e` come from a transcendental conformal-mapping parameter k. Use the solver unless the geometry is the ideal case.

Wadell's chapter 4 also gives closed forms for coupled embedded microstrip and offset coupled stripline. These are fine for a first estimate, but errors run to several percent.

---

## 2. Frequency-dependent effects and RLGC

### 2.1 Target output

For each cross-section, the solver produces per-unit-length matrices on a log frequency grid (DC to ~100 GHz):

```
Z(w) = R(w) + j w L(w)        Y(w) = G(w) + j w C(w)
```

The SI channel simulator needs these tabulated, and they must be causal, as W-element / Touchstone RLGC tables or for direct ABCD cascading. Characteristic quantities: `Zc = sqrt(Z/Y)` and `gamma = sqrt(ZY)`. In matrix form `Zc = (ZY)^{1/2} Y^{-1}`.

### 2.2 Conductor loss and skin effect

```
delta = 1/sqrt(pi f mu0 sigma)          (Cu, sigma = 5.8e7 S/m: delta = 2.09 um at 1 GHz, 66 um at 1 MHz)
Rs    = 1/(sigma delta) = sqrt(pi f mu0 / sigma)
Zs    = (1 + j) Rs                      (surface impedance, good conductor)
```

- Wheeler incremental-inductance rule: `R(f) = (Rs/mu0) dL/dn` (dn = recession of all metal walls by delta/2). Wheeler 1942; this is how HJ and Wadell compute microstrip alpha_c in closed form. It is valid only when delta << t and the corner radius is much larger than delta.
- A practical closed-form R(f) model that covers the DC-to-skin transition:
  `R(f) ~ sqrt( Rdc^2 + (Rac(f))^2 )`, with `Rac = Rs * (P_eff)^-1`, where P_eff is the effective current-carrying perimeter from the incremental-inductance rule.
  Internal inductance: `L_int(f) = Rac(f)/(2 pi f)` in the skin regime, so that Z_int = (1+j) Rac. This is causal to first order. Exact DC-to-skin behavior needs a conductor-interior solve (see sec. 3.5).
- The return-plane loss is included automatically in a field solve, and it is significant (~20-30 % of total R for stripline).

### 2.3 Surface roughness

Roughness increases R and, in the causal form, adds internal inductance, which raises the effective Dk and delay (Simonovich 2017/2018).

| Model | Equation | Notes |
|---|---|---|
| Hammerstad-Bekkadal (1975) | `K = 1 + (2/pi) atan( 1.4 (Rq/delta)^2 )` | Saturates at 2. Under-predicts loss for modern rough foils above ~5 GHz. |
| Groisse (1996) | `K = 1 + exp( -(delta/(2 Rq))^1.6 )` | Also saturates at 2. Polar offers it. |
| Huray "snowball" (2010) | `K = A_matte/A_flat + (3/2) sum_i (N_i 4 pi a_i^2 / A_flat) / (1 + delta/a_i + delta^2/(2 a_i^2))` | Physical model with no saturation. Needs sphere radius a and surface-ratio data, usually from SEM. |
| Cannonball-Huray (Simonovich, DesignCon 2015) | Huray with a 3-layer square-pyramid stack of 14 equal spheres on a hexagonal tile; radius r and tile area derived from datasheet Rz. Stack height = (2 + 2 sqrt2) r, approximately 4.83 r **[verify]** | Needs only Rz from the foil datasheet. Widely used (Polar, Simbeor, HyperLynx). |
| Causal Huray (Bracken DesignCon 2012; Simonovich & Kochikov DesignCon 2018) | Apply K(f) to the complex surface impedance and enforce causality (Hilbert pair / Kramers-Kronig) | Needed for correct phase. See [SIJ causality article](https://www.signalintegrityjournal.com/articles/1796-how-to-enforce-causality-of-standard-and-custom-metal-roughness-models). |
| Gradient model (Grujic 2018) | Conductivity varies with depth, sigma(x) | Inherently causal. Single parameter Rq. Good alternative. |

Implementation: compute smooth R(f) and L_int(f) per conductor surface (top, bottom, and side can carry different roughness: drum side vs. treated side, oxide alternative). Multiply the surface impedance contribution by a causal K(f): `Zs_rough(f) = Zs(f) * K_c(f)`, where `K_c` is built so that `Re` and `Im` form a Hilbert pair. One simple method fits K(f) - 1 to a sum of first-order sections `sum c_k * jf/(jf + f_k)`, which is causal by construction.

Sources: Simonovich papers ([SIJ author page](https://www.signalintegrityjournal.com/authors/1-bert-simonovich); [Altium interview](https://resources.altium.com/p/model-copper-roughness-properly-a-discussion-with-bert-simonovich)); Polar roughness note [AP8203](https://www.polarinstruments.com/support/si/AP8203.pdf); P. G. Huray et al., "Impact of Copper Surface Texture on Loss: A Model that Works," DesignCon 2010; P. Huray, *The Foundations of Signal Integrity*, Wiley 2009.

### 2.4 Causal dielectric - Djordjevic-Sarkar (wideband Debye)

```
eps(w) = eps_inf + (d_eps / (m2 - m1)) * log10( (w2 + j w) / (w1 + j w) ),   w1 = 10^m1, w2 = 10^m2 (rad/s)
```

Fitting from one datasheet point (Dk, Df) at w0, with m1 ~ 4 and m2 ~ 12 typical:

```
Lg(w) = log10( (w2 + j w)/(w1 + j w) )                 (Im Lg < 0 between w1 and w2)
d_eps  = -Dk * Df * (m2 - m1) / Im{ Lg(w0) }
eps_inf = Dk - d_eps/(m2-m1) * Re{ Lg(w0) }
```

The fit is checked by computing eps(w0) = Dk(1 - j Df). Fit to multiple datasheet points by least squares. A. R. Djordjevic, R. M. Biljic, V. D. Likar-Smiljanic, T. K. Sarkar, "Wideband Frequency-Domain Characterization of FR-4 and Time-Domain Causality," IEEE Trans. EMC 43(4), pp. 662-667, Nov. 2001. A multi-pole Debye or Lorentz fit is an alternative for materials with strong dispersion. Never use constant Dk/Df in time-domain work, because it is non-causal.

Note: glass-weave anisotropy (Dk_z vs. Dk_xy) and resin-rich layers affect stripline. The solver should accept anisotropic per-layer er (diagonal tensor) eventually.

### 2.5 Dispersion

On PCBs, the quasi-static TEM assumption is adequate for stripline. Microstrip has modal dispersion (Kirschning-Jansen, sec. 1.5), which is small for thin substrates. The material dispersion of sec. 2.4 and the roughness and internal-inductance effects dominate in practice. A quasi-static 2D solver with frequency-dependent eps and Zs captures material and conductor dispersion. It does not capture microstrip geometric dispersion; apply a KJ-style correction or a full-wave 2D eigen-solve if needed above ~30-50 GHz.

### 2.6 Producing frequency-dependent RLGC from a 2D solver

1. **C(f), G(f)**: solve electrostatics with complex permittivity `eps_i(f) = eps_i' - j eps_i''` per dielectric region. This gives `Y = j w C~(f)`, so `C = Re C~` and `G = -w Im C~`. Because eps(f) is smooth, solve at roughly 10-20 log-spaced points and interpolate (or use a rational fit). For a single homogeneous dielectric, `C(f) = C0 * eps(f)/eps_ref` exactly.
2. **L_ext**: `L = mu0 eps0 * C_air^-1` (C_air = vacuum-dielectric capacitance matrix). Frequency independent.
3. **R(f), L_int(f)**: high frequency from the surface-impedance perturbation (Wheeler rule, generalized). Weight the tangential H field on each conductor boundary: `R_ij = (1/I^2) * integral of Re(Zs) H_t^2 dl`, computed from the quasi-static surface charge, since the TEM current density equals c * surface charge in the air solution. Low and mid frequency comes from a conductor-interior eddy-current solve (sec. 3.5). Blend, or use the interior solve throughout.
4. Assemble causal RLGC. Check passivity (R, G positive semi-definite) and causality (Kramers-Kronig residual) before export.

---

## 3. 2D quasi-static field solvers

### 3.1 Problem

The structure is N signal conductors plus a reference (ground) conductor in a piecewise-homogeneous dielectric cross-section. It includes trapezoidal traces (etch factor), solder mask conformal over traces, multiple prepreg and core layers, finite or infinite planes, and coplanar ground pours. Unknowns:
- Maxwell capacitance matrix C (N x N) with eps, and C0 with vacuum.
- `L = mu0 eps0 C0^-1`, plus R and G as above.

### 3.2 Method of moments / boundary element method (BEM)

- Unknowns: free charge on conductor surfaces and polarization (bound) charge on dielectric interfaces. 2D Green's function: `G(r,r') = -(1/(2 pi eps0)) ln|r - r'|`.
- Equations: potential = V_k on conductor k. On each dielectric interface, continuity of normal D: `(eps1 + eps2)/2 * sigma_b ... ` (standard form `sigma_b(r) + 2 eps0 (eps1-eps2)/(eps1+eps2) * dPhi/dn(r) = 0` with principal-value normal derivative).
- Log-kernel issue in 2D: total charge must be zero (or add a constant-potential unknown), or else the reference potential is ill-defined. Model the ground as a conductor in the system, or use images for one infinite plane. For stripline, two infinite planes need infinite images; it is simpler to truncate the planes at about 10-20 times b wide and let BEM see finite planes, with negligible error at that width.
- Discretization: piecewise-constant or linear panels, graded toward corners (charge singularity of r^(-1/3) at 90-degree edges). Analytic panel integrals of ln|r| exist in closed form. N ~ 500-3000 panels gives about 0.1 % accuracy. The matrix is dense, so a direct LU (O(N^3)) takes milliseconds to tens of ms at this size.
- Pros: discretizes only boundaries, open regions are exact (no truncation box), fast, and accurate for C. It is the method used by Polar SI9000 and most commercial "2D field solvers" (TNT-MMTL's `bem` also uses it).
- Cons: many-layer stackups produce many interface panels (each infinite dielectric interface must be truncated). Layered-media Green's functions avoid this but are complex. Conductor-interior R needs a separate method.

### 3.3 Finite element method (FEM)

- Solve `div(eps grad phi) = 0` in the dielectric region. Conductors are holes with Dirichlet V_k. Truncate the outer boundary with a box (10-20 times the structure size), Kelvin transformation, or infinite elements. `C_ij` comes from energy, `W = 1/2 V^T C V`, or from flux integrals.
- Mesh: gmsh with refinement at conductor corners and 2nd-order elements. Adaptive refinement driven by energy error makes C converge to about 0.05 %.
- Pros: arbitrary inhomogeneous and anisotropic dielectrics (diagonal tensor trivial), complex eps natively, and the same mesh can include conductor interiors for an eddy-current R(f), L(f) solve (sec. 3.5). Mature libraries are available.
- Cons: meshing and truncation overhead. It is 10-100 times slower than BEM for a single solve (still well under 1 s in 2D with sparse direct solvers).

### 3.4 Modal decomposition and differential / common-mode impedance

For lossless (or low-loss) lines:

```
Eigen-decomposition: (L C) Tv = Tv Lambda    ->  modal velocities v_m = 1/sqrt(lambda_m)
Characteristic impedance matrix: Zc = (L C)^{1/2} C^{-1}       (lossy: Zc = (Z Y)^{1/2} Y^{-1})
```

For a symmetric pair with Maxwell C (C12 < 0):

```
Zodd  = sqrt( (L11 - L12) / (C11 - C12) )      Zeven = sqrt( (L11 + L12) / (C11 + C12) )
Zdiff = 2 Zodd                                 Zcomm = Zeven / 2
```

For asymmetric pairs or pairs with a nearby aggressor, use the mixed-mode transform of Zc:

```
Zdiff = Zc11 + Zc22 - Zc12 - Zc21
Zcomm = (Zc11 + Zc22 + Zc12 + Zc21) / 4
```

This matches the definition used by Polar and HyperLynx for "differential impedance" of a 2-line structure. In inhomogeneous media the modes are not pure diff/comm, so report both the modal (eigen) impedances and the mixed-mode values, plus the modal velocities (far-end crosstalk relates to the odd/even velocity difference).

### 3.5 R and L(f) extraction

- High frequency: surface-impedance perturbation (sec. 2.6.3). It is cheap and uses the BEM charge directly.
- Full frequency range: a 2D magneto-quasistatic (eddy current) solve in A_z with conductors meshed, `-(1/mu) lap A_z + j w sigma A_z = sigma * (V-gradient)`, with net current constraints per conductor. FEM is natural (scikit-fem / FEniCSx). Alternatively use a PEEC-style filament partition of the conductors (as MMTL and FastHenry do), which gives `Z = R + jwL` for all frequencies including the DC-to-skin transition and proximity effect. Mesh density must resolve delta at the top frequency (about 2-3 elements per delta near the surface). Use the impedance boundary condition above a crossover frequency (delta < t/4) to avoid over-meshing.
- G: from the imaginary part of complex-eps C (sec. 2.6.1).

### 3.6 Open-source and reference tools

| Tool | Method | License | Maintenance (2026) | Usefulness |
|---|---|---|---|---|
| **atlc** (Arbitrary Transmission Line Calculator, D. Kirkby) | Finite difference on a bitmap cross-section; arbitrary dielectrics; computes Z0, Zodd/even | GPL-2.0 | Dormant since ~2011 (v4.6.1). Debian/Ubuntu package it ([Debian](https://packages-picconi.debian.org/sid/atlc), [SourceForge](https://sourceforge.net/p/atlc)). KiCad forum users report build fixes ([forum](https://forum.kicad.info/t/atlc-arbitrary-transmission-line-calculator-for-transmission-lines-and-directional-couplers/60362)) | Independent cross-check only. Pixel geometry limits accuracy to ~1 %. Do not link (GPL); run it as an external process for tests. |
| **TNT-MMTL** (Mayo Clinic SPPDG) | BEM (static, lossless) + lossy (filament) solver; RLGC output, HSPICE W-element | GPL ([mmtl.sourceforge.net](https://mmtl.sourceforge.net/)); NetBSD pkg v1.2.2 ([pkgsrc](https://pkgsrc.se/cad/tnt-mmtl)) | Effectively unmaintained since ~2005-2010 (Tcl/Tk GUI, C++ core) | Best existing open BEM reference. Good for generating validation data. Treat its algorithm description as a design reference. GPL prevents embedding in a permissive suite. |
| **openEMS** | 3D FDTD (EC-FDTD), Python/Matlab interface | GPL-3 | Active | No true 2D quasi-static mode. Can extract Z0 from a short 3D line with ports (slow, ~1-2 % accuracy). Use it for 3D/full-wave cross-checks (other agent's scope), not per-segment impedance. |
| **Elmer FEM** | FEM; `StatElecSolver` computes the capacitance matrix; MagnetoDynamics2D for eddy current | GPL-2 (solver) / LGPL (libs) | Active (CSC Finland) | Works as an external batch reference. Heavy as an embedded dependency. |
| **FEniCSx (dolfinx)** + **gmsh** | General FEM | LGPL-3 (dolfinx); gmsh GPL-2+ with linking exception | Very active | Powerful but heavy install (PETSc). Good for the R(f) eddy-current solver. |
| **scikit-fem** + **gmsh**/meshio | Lightweight pure-Python FEM (NumPy/SciPy) | BSD-3 (scikit-fem) | Active | **Best fit** for an embeddable FEM path: pip-installable, permissive license. |
| **FasterCap** (FastFieldSolvers) | BEM capacitance extraction incl. a 2D mode | LGPL-2.1 | Low activity | Useful C-matrix cross-check. |
| **scikit-rf `media`** (`MLine`, `CPW`, `DefinedGammaZ0`, coax, ...) | Closed-form (HJ, Wheeler, Schneider + KJ / Kobayashi dispersion, DS dielectric, roughness) | BSD-3 | Active | Use as a dependency and cross-check for microstrip/CPW. It has no stripline, and coupled-line support is limited ([MLine docs](https://scikit-rf-official.readthedocs.io/en/stable/api/media/generated/skrf.media.mline.MLine.html), [CPW source](https://scikit-rf-official.readthedocs.io/en/stable/_modules/skrf/media/cpw.html)). |
| **KiCad PCB Calculator** (`pcb_calculator/transline`) | Closed forms derived from Qucs transcalc: microstrip (HJ + KJ), coupled microstrip (KJ), stripline, CPW/CPWG, coax, twisted pair, rectangular waveguide | GPL-3 | Active (KiCad 8/9) | Our users will compare against it. Match it within its validity range, but it gives no field-solve for mask or trapezoid. |
| **Qucs / Qucs-S transcalc** | Same lineage as KiCad's | GPL-2 | Qucs-S active; transcalc rarely changes | The Qucs tech doc is the clearest public write-up of the equations. |
| **QucsStudio** (M. Margraf) | Closed forms + a built-in 2D solver/EM in recent versions | Freeware, closed source | Active | Comparison only. |
| **Saturn PCB Toolkit** | Closed forms (IPC-2141A, Wadell) | Freeware, closed | Active | Users compare against it. It reproduces the IPC formulas, so expect differences. |
| **Polar SI9000e** | BEM, lossy (Hammerstad/Groisse/Huray-Cannonball, DS dielectric), extensive structure library | Commercial | Active | The industry reference most fabs quote, and our accuracy target. |

---

## 4. Validation data for a regression suite

### 4.1 Analytic truth (exact, tolerance 1e-4 or better)
- Coax: `Z0 = (eta0/(2 pi sqrt(er))) ln(b/a)`, which is 59.958 ohm for ln(b/a) = 1 in air **[computed]**. Run it as a BEM/FEM test with round conductors.
- Round wire over infinite plane: `Z0 = (eta0/(2 pi sqrt(er))) acosh(h/r)`. Two-wire line: `Z0 = (eta0/(pi sqrt(er))) acosh(D/d)`.
- Zero-thickness symmetric stripline (Cohn, sec. 1.7) and edge-coupled stripline (Cohn, sec. 1.9): the [computed] values above. Approximate t = 0 with t = 1e-4 b and extrapolate.
- Zero-thickness CPW on infinite dielectric half-space: eeff = (er+1)/2 exactly; Z0 from sec. 1.8.
- Homogeneous-dielectric invariance: `L C = mu0 eps0 er I` exactly. This is a strong solver self-check for any geometry.
- Reciprocity and symmetry: C symmetric, mirror geometry gives C11 = C22, and the row sums of the Maxwell C are >= 0.

### 4.2 High-accuracy published numerical data
- E. Hammerstad & O. Jensen 1980: their accuracy claims are against exact quasi-static data (Wheeler 1977 / Bryant-Weiss).
- T. G. Bryant and J. A. Weiss, "Parameters of Microstrip Transmission Lines and of Coupled Pairs of Microstrip Lines," IEEE Trans. MTT-16(12), pp. 1021-1027, 1968. Classic MoM tables for single and coupled microstrip.
- Kirschning-Jansen 1984 compares against spectral-domain full-wave data (coupled microstrip dispersion).
- Wadell's Handbook: every formula has a worked numerical example. Transcribe 30-50 of these into test fixtures; the facts are not copyrightable, but cite the page.
- R. Garg & I. J. Bahl, "Characteristics of Coupled Microstriplines," IEEE MTT-27, 1979 (tables).
- Polar Instruments application notes (https://www.polarinstruments.com/support/si/), e.g. AP131 (formula vs. field-solver comparison), AP8203 (roughness), and the SI9000 user guide examples. They give structure parameters plus SI9000 results that we can reproduce.
- TNT-MMTL and atlc example files: run them locally to generate reference numbers, and store the outputs (not the code) as fixtures.
- Industry round-robin material: E. Bogatin, *Signal and Power Integrity - Simplified*, 3rd ed. (worked 50-ohm and 100-ohm examples); Simbeor / Shlepnev papers on roughness and dielectric identification with measured data (e.g. "CMP-28" validation boards, Wild River Technology), useful for loss validation.

### 4.3 Closed-form seed values **[computed]** (zero thickness)

| u = W/h | er | HJ Z0 (ohm) | HJ eeff | Wheeler-77 Z0 (ohm) |
|---|---|---|---|---|
| 0.5 | 1 | 166.700 | 1.0000 | 166.641 |
| 0.5 | 4.3 | 96.404 | 2.9901 | 96.400 |
| 1 | 4.3 | 71.751 | 3.1045 | 71.732 |
| 1.9 | 4.3 | 50.739 | 3.2612 | - |
| 2 | 4.3 | 49.192 | 3.2755 | 49.064 |
| 2 | 10.2 | 32.936 | 7.3067 | 32.961 |
| 4 | 4.3 | 30.911 | 3.4869 | 30.708 |

HJ and Wheeler differ by up to ~1 % at u = 4, er = 1, which marks the practical floor of closed-form agreement. Use the HJ numbers to regression-lock the implementation (1e-6 relative), not as physical truth.

### 4.4 Suite structure
1. `analytic/`: exact cases at 1e-4, run against both BEM and FEM.
2. `closedform/`: formula implementations locked to their paper or Wadell examples at 1e-6, plus cross-checks against scikit-rf (BSD) at 1e-3.
3. `solver_vs_reference/`: 50+ PCB structures (surface and coated microstrip, embedded microstrip, symmetric and offset stripline, CPWG, edge-coupled diff microstrip and stripline, broadside), with SI9000 or TNT-MMTL reference values at 1 % (single) / 2 % (differential).
4. `convergence/`: mesh-refinement tests asserting Richardson-extrapolated error < 0.1 %.
5. `loss/`: R(f) vs. analytic round-wire skin effect (Bessel-function solution), and DS fit round-trip.

---

## 5. Recommendation

### 5.1 Closed-form set to ship ("instant" mode, < 1 ms)
Implement from the papers in our own permissively licensed code. Do not copy GPL KiCad or Qucs code.
- Microstrip: Hammerstad-Jensen 1980 with HJ thickness correction. Dispersion: Kirschning-Jansen 1982 (eeff) plus Jansen-Kirschning 1983 (Z0).
- Embedded microstrip and solder-mask coating: HJ plus a cover correction, flagged as "approx +/-3 %".
- Stripline: Cohn exact plus Wheeler 1978 thickness correction. Offset stripline: parallel approximation, flagged as "approx".
- CPW / CPWG: Ghione-Naldi / Simons conformal mapping with the Gupta thickness correction.
- Edge-coupled microstrip: Kirschning-Jansen 1984 (+ errata). Edge-coupled stripline: Cohn 1955 plus Wadell thickness corrections. Broadside: Cohn 1960.
- Loss: Wheeler-rule conductor loss, Hammerstad / Groisse / Cannonball-Huray roughness, Djordjevic-Sarkar dielectric.
- IPC-2141 formulas: comparison-only option, labeled legacy.
- Every formula returns a `validity` flag when inputs fall outside the published range. The UI should then push the user to the solver.

### 5.2 2D solver to build
- **Primary: a custom 2D BEM written in Python with NumPy/SciPy, with hot loops in Numba.** It should be designed so the core can later move to Rust (PyO3) if needed.
  - Geometry: polygons (trapezoid traces, conformal mask via polygon offset with Shapely, BSD), dielectric layers truncated at about 20 times the stack height, finite planes.
  - Panels: linear elements, corner-graded, analytic log integrals. Explicit dielectric-interface panels at first; consider layered Green's functions later.
  - Outputs: C, C0, L, modal and mixed-mode Zc, velocities, and C~(f) with complex eps giving G(f). Conductor R(f) at high frequency comes from the surface-impedance perturbation with roughness.
  - Target: < 50 ms per typical single or differential cross-section, so a full board's segments (cached) solve in seconds.
- **Secondary / reference: FEM with scikit-fem (BSD-3) and gmsh (GPL with linking exception, pip `gmsh`).** It provides (a) independent verification of BEM, (b) the 2D eddy-current solve for R(f) and L_int(f) from DC through skin effect including proximity, and (c) anisotropic dielectrics. Keep FEniCSx optional. Do not depend on GPL solvers at runtime. Use TNT-MMTL, atlc, and Elmer only in the test-data generation pipeline.
- **Accuracy targets:**
  - Lossless Z0: within 0.5 % of converged FEM and within 1 % of Polar SI9000 for single-ended structures; Zdiff within 1.5-2 %.
  - Closed forms: within 1-2 % of the solver inside their validity range.
  - eeff and delay: within 0.5 %.
  - Insertion loss: within 5 % of SI9000 for the same material model. Measured-loss agreement (±10 %) is limited by material data, not the solver.
- Use per-unit-length R and L matrices for all conductors, including coplanar grounds treated as separate conductors, so the SI agent can handle guard traces and crosstalk.

### 5.3 Per-segment workflow along routed traces
1. **Stackup**: read the KiCad `.kicad_pcb` stackup (layer thickness, er, loss tangent, mask thickness and er, copper thickness). Add fab-supplied etch factor (trapezoid top width = W - 2t/tan(theta) or Polar-style W1/W2), roughness (Rz per side), and DS parameters. Default to datasheet values with user override.
2. **Copper geometry**: use *filled* zone polygons (from the KiCad zone fill), not zone outlines, plus tracks, pads, and arcs. The data-ingest agent supplies these. Build an R-tree (shapely STRtree) per layer.
3. **Segment slicing**: walk each net's tracks (lines and arcs) and sample cross-sections every Δs (e.g. min(0.5 mm, 2h)) plus at every event: width change, layer change (vias break the segment), and boundary crossings of reference copper.
4. **Reference detection at each sample**: take the perpendicular cut line of half-length `Lw = W/2 + max(5 h_max, 3 W, gap_search)`. Intersect it with the copper of every layer and keep the nearest conductive layers above and below that have copper under the trace footprint (± W/2 + 3h). Classify the result:
   - Copper both above and below gives stripline (symmetric or offset by distance); copper on one side only with air/mask on the other gives microstrip or embedded microstrip.
   - No copper within the window on any adjacent layer gives "no reference" and an error flag.
   - Partial coverage (plane edge within ~3h of the trace centre) gives "edge-of-plane" and a warning. Solve it with the actual truncated plane.
   - Split-plane crossing (reference net changes or a gap crosses under the trace) gives a return-path discontinuity flag, passed to the SI agent.
   - Reference-net identity is recorded (GND vs. power) for PI coupling.
5. **Coplanar ground proximity**: same-layer copper on the cut line within `g_max` (e.g. < 3h or < 2W, whichever is larger) is included as a coplanar conductor (CPWG if a plane is present). Classify as "coplanar" when the gap is < ~2h; otherwise it acts only as a weak perturbation but is still included in the solve. Same-layer copper of other signal nets becomes aggressor conductors (for coupled-pair or crosstalk mode). Diff pairs are detected from KiCad net-class pairing (`_P/_N`, `+/-`) and solved as a 2-conductor structure.
6. **Canonicalize and cache**: quantize the cross-section, then hash it to a cache key. Typical boards have only tens of unique cross-sections, so solve each once (BEM), reuse it across all samples, and produce RLGC(f) per unique key.
7. **Output**: an impedance and delay profile vs. path length per net (Z0 or Zdiff, eeff, loss/inch at chosen frequencies), and segment lists with RLGC tables for the channel simulator, with discontinuities (vias, layer change, neck-down, reference change) marked. Report a deviation flag where |Z - Ztarget| > tolerance (default ±10 %, as fab spec).
8. **Instant mode**: the same pipeline maps each canonical cross-section to the nearest closed-form model when the topology matches (e.g., microstrip with no nearby copper) for an immediate first pass. Solver results then replace those values asynchronously.

### 5.4 Risks
- **Material data uncertainty dominates.** Dk/Df vary by glass style and resin content, and pressed prepreg thickness differs from nominal. ±5 % Dk gives about ±2.5 % Z0, which exceeds solver error. We need a fab-stackup import and a material library with DS fits.
- Etch trapezoid, plating thickness, and solder-mask thickness over and between traces are fab-specific and usually unknown. Expose them as parameters with sensible defaults (Polar conventions) so users can match fab reports.
- Matching SI9000 exactly requires matching its conventions: mask thickness definitions (C1/C2/C3), trapezoid definition, and the finite plane width it assumes. Collect paired reference cases early.
- BEM with many long dielectric interfaces can be ill-conditioned or slow. Mitigate with interface truncation studies, and use layered Green's functions as a later optimization.
- Causal roughness and low-frequency R/L transitions are subtle. Validate against the Bessel-function round-wire solution and published measurement-based data (Simonovich, Shlepnev).
- Licensing: most open references (KiCad, Qucs, atlc, TNT-MMTL, openEMS, Elmer) are GPL. Reimplement from papers and use them only as external test oracles unless the project chooses GPL.
- Quasi-static validity: above ~30-50 GHz on thicker microstrip, or for CPWG with sparse vias (parasitic modes), 2D quasi-static results lose accuracy. Hand off to the 3D/full-wave agent.

---

## Sources (web)
- TNT-MMTL home: https://mmtl.sourceforge.net/ ; NetBSD package: https://pkgsrc.se/cad/tnt-mmtl
- atlc: https://sourceforge.net/p/atlc ; Debian: https://packages-picconi.debian.org/sid/atlc ; KiCad forum: https://forum.kicad.info/t/atlc-arbitrary-transmission-line-calculator-for-transmission-lines-and-directional-couplers/60362
- scikit-rf MLine: https://scikit-rf.readthedocs.io/en/latest/_modules/skrf/media/mline.html ; https://scikit-rf-official.readthedocs.io/en/stable/api/media/generated/skrf.media.mline.MLine.html ; CPW: https://scikit-rf-official.readthedocs.io/en/stable/_modules/skrf/media/cpw.html ; Media tutorial: https://scikit-rf-official.readthedocs.io/en/stable/tutorials/Media.html
- Altium on IPC vs. Wheeler accuracy (summarizing Polar): https://resources.altium.com/p/clearing-trace-impedance-calculators-and-formulas
- Polar AP131 (JP): https://www.polarinstruments.com/support/cits/AP131_J.pdf ; Polar AP8203 (roughness): https://www.polarinstruments.com/support/si/AP8203.pdf
- Simonovich roughness work: https://www.signalintegrityjournal.com/authors/1-bert-simonovich ; https://resources.altium.com/p/model-copper-roughness-properly-a-discussion-with-bert-simonovich ; causality: https://www.signalintegrityjournal.com/articles/1796-how-to-enforce-causality-of-standard-and-custom-metal-roughness-models
- Qucs technical documentation (equations): https://qucs.sourceforge.net/tech/technical.html
- scikit-fem: https://github.com/kinnala/scikit-fem ; gmsh: https://gmsh.info ; FEniCSx: https://fenicsproject.org ; Elmer: https://www.csc.fi/web/elmer ; openEMS: https://openems.de ; FasterCap: https://www.fastfieldsolvers.com
- KiCad pcb_calculator source: https://gitlab.com/kicad/code/kicad/-/tree/master/pcb_calculator/transline
