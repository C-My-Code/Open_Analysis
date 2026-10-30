# oa-erc: theory, error budget and validation

Electrical rule checks (ERC/EMI) over a board model. Code: `packages/oa-erc/src/oa_erc/` (`rules/*.py`, `engine.py`, `base.py`). Each rule's `explanation` and `references` fields in the code hold its rationale; this page collects them and states what kind of evidence stands behind each threshold. See the [theory index](../../docs/theory/README.md).

The key point for a skeptical reader: **the geometry in each rule is computed exactly; the thresholds are mostly rules of thumb.** A violation means "this geometry matches a pattern that design guidance associates with a problem", not "this board will fail". The accuracy class of `erc` results is `rule` (`docs/specs/ai-integration.md`).

## 1. Problem statement

**Input.** A `Board` (stack-up, copper, vias, components), a design intent (which nets are high-speed, impedance targets, rise times, clock frequencies, tolerances), per-rule configuration, optional waivers.

**Output.** A `ViolationSet`: for each violation, rule id, severity, nets, layers, location [m], optionally `measured`, `limit` and `unit`, a typed remediation, and a fingerprint. Output order is deterministic (rules in id order; violations sorted by rule, layers, nets, location, fingerprint). Net scope: nets the intent marks `high_speed`; if none is marked, every `signal` net is assumed high-speed.

## 2. Governing equations and formulation

Geometry comes from `oa_geometry` (see [oa-geometry](../oa-geometry/THEORY.md)): the `ReferenceMap` gives, per segment span, the reference layer, reference net, coverage fraction and edge distance. Rules are pure generators over that data. Three kinds of quantity appear.

**Exact geometry.** Distances, run lengths, plane coverage, void clusters (shapely/GEOS, 1 nm comparison grid).

**Closed-form physics used inside a rule.**

- Effective permittivity for wavelength estimates (`base.eps_eff`): $\varepsilon_{eff}=D_k$ on inner layers, $(D_k+1)/2$ on outer layers (a crude microstrip estimate).
- Wavelength criterion: $s\le\lambda/N$ with $\lambda=c/(f\sqrt{\varepsilon_{eff}})$; $N=20$ for stitching/decap rules (ED-003, PD-001), $N=10$ for the RF via fence (RF-001). Defaults use $f=1$ GHz (ED-003, PD-001) and 10 GHz (RF-001).
- Via stub resonance (`oa_physics.via.stub_resonance`): $f_0=c/(4\ell\sqrt{D_{k,eff}})$.
- Rectangular plane-cavity modes (EM-011): $f_{mn}=\dfrac{c}{2\sqrt{\varepsilon_r}}\sqrt{(m/a)^2+(n/b)^2}$ on the minimum rotated rectangle of the overlap of two adjacent planes, flagged within a $\pm5\%$ window of a clock harmonic.
- Delay for skew (DP-002): $\sum \ell_i\sqrt{\varepsilon_{eff}}/c$ plus the via barrels.
- Impedance (IM-002): the cross-section at each sample is solved by the `impedance` service ([oa-impedance](../oa-impedance/THEORY.md)) and compared to $Z_\text{target}(1\pm\text{tol})$. This is the only rule that calls a field solver; it inherits that solver's accuracy class.

**Threshold values.** From the literature rule sets (Hubing et al., Clemson CVEL, EMC'97; `docs/research/07-quasistatic-thermal-emi.md` section 3.1), design-guide defaults, or derived (IM-005, VI-001). All are `Config` fields and can be changed per project.

## 3. Rule rationale and threshold source

"Basis" says what stands behind the number: **physics** (derivable), **literature** (a published rule set, but a rule of thumb), **default** (an engineering default stated as such in the code, "not a standard limit"). Rule ids and defaults are those of the README table and the `Config` classes.

| Rule | Physical rationale | Default threshold | Basis |
|---|---|---|---|
| RP-001 (E2) | The return current under a trace follows it on the adjacent plane. A gap, slot or split forces a detour, enlarging the current loop (inductance, radiation, common-mode coupling). | run without reference shorter than 5 mm, bounded by reference on both sides | literature (Hubing 1997); the 5 mm value is a default |
| RP-002 | A line of antipads (BGA, via arrays) perforates the plane so the return meanders between holes. | at least 3 coverage dips, coverage below 0.8 | default (research 06) |
| RP-003 (E10) | A change of reference net (GND to a rail) forces the return across planes through a capacitor or stitching via. | any change except GND-to-GND | literature |
| RP-004 (E4) | A layer change needs a return path between the reference planes: a nearby same-net via, or a capacitor if the nets differ. | via within 2.5 mm, stitching cap within 5 mm (straight-line distance) | literature; distances are defaults ("1-2 mm or $\lambda/20$" in research 07) |
| RP-007 (E3) | No adjacent reference plane: the return takes an uncontrolled path. | run of 5 mm or more | literature |
| ED-001 | Near a plane edge, the return current spreads over about $\pm3h$ of the trace. If the plane ends inside that footprint the field fringes and radiates. | plane edge closer than $3h$, run of 2 mm or more | physics + literature (see note below) |
| ED-002 (E1) | Fringing fields near the board outline couple to the outside; plane is pulled back; ESD. | distance $\ge\max(1\ \text{mm},3h)$ | literature |
| ED-003 (E5) | A ground via fence shields only while its pitch is below $\lambda/20$ (slot leakage). | $\lambda/20$ at 1 GHz | literature (see note below) |
| IM-001 | Width differs from the net-class width, changing impedance without the solver. | 2 % | default |
| IM-002 | Computed Z0 or Zdiff outside the target band. | intent tolerance, default 10 % | IPC-2141A style tolerance; solver accuracy from section 5 |
| IM-003 | A narrow section raises impedance and resistance. | neck-down longer than 5 mm | default |
| IM-005 | An open stub is electrically short if its round trip is small against the rise time. | $\ell< t_r v/10$, or 3 mm if no rise time | physics-derived rule of thumb |
| DP-001 / 002 / 003 | Pair spread raises Zdiff and starts common-mode conversion; delay skew converts differential to common mode; gap variation varies Zdiff. | 2 mm uncoupled, 1 ps skew, 25 % gap change over 2 mm | defaults ("not standard limits") |
| VI-001 | The barrel beyond the last used layer is an open quarter-wave stub that notches the channel near $f_0$. | stub over 0.25 mm (10 mil) or $f_0$ below a configured limit | physics (resonance) + default (0.25 mm) |
| VI-002 | Every via adds L, C and a possible stub. | more than 4 vias per routed net | default |
| XT-001 (E8) | Parallel runs couple capacitively and inductively; noise grows with length until saturation. | centre spacing under $3W$, summed parallel length over 10 mm | folklore + default (see note below) |
| PD-001 (E6) | A decoupling capacitor supplies current in time only if its loop to the IC is short. | straight-line distance $\le\lambda/20$ at 1 GHz | literature; straight-line is a lower bound of the loop path |
| RF-001 | A ground via fence beside an RF line suppresses parallel-plate modes while pitch $<\lambda/10$ at the top frequency. | $\lambda/10$ at 10 GHz, at least `min_fence_vias` | default (research 06) |
| EM-011 (E11) | Two adjacent planes of different nets form a cavity; a clock harmonic near a mode excites it and the board radiates from the plane edges. | within 5 % of $f_{mn}$ | physics (cavity modes), 5 % window is a default |

**Notes on the folklore thresholds** (the research notes themselves warn: "Rule thresholds are folklore (20H, 3W, λ/20). Make them configurable, cite the source per rule, and avoid 'error' severity for disputed rules", `docs/research/07` risk 7):

- **20H.** Not implemented. Research 07 (E12) classifies it as informational only because its benefit is disputed; no rule in `oa-erc` checks it.
- **3H plane-edge footprint (ED-001).** A thin strip over a ground plane carries its return current with a Lorentzian profile of width $h$, $J(x)\propto1/(1+x^2/h^2)$. The fraction within $\pm a$ is $(2/\pi)\arctan(a/h)$, which is 79.5 % for $a=3h$ and 93.6 % for $a=10h$. So "3h" captures about four fifths of the return current, not nearly all of it. This is a back-of-envelope derivation, not a statement from the code or research notes [verify against a field-solver check].
- **$\lambda/20$ (ED-003, PD-001).** For an aperture of length $l$ in a shield, the common estimate of shielding effectiveness is $20\log_{10}(\lambda/2l)$ dB, which gives 20 dB at $l=\lambda/20$ and about 14 dB at $\lambda/10$ [verify: H. W. Ott]. So $\lambda/20$ buys roughly 20 dB at the stated frequency, a design choice and not a threshold below which radiation is absent. For decaps, $\lambda/20$ is a distance heuristic with no derivation in the code; the real criterion is loop inductance.
- **3W (XT-001).** Crosstalk between microstrips falls with the spacing-to-height ratio $d/h$ (for example, coupling coefficient $\propto1/(1+(d/h)^2)$ in Johnson and Graham [verify]), not with $d/W$. "3W" is equivalent only when $W\approx h$ to $2h$. For wide traces on thin dielectric it is conservative, for narrow traces on thick dielectric it is not.
- **IM-005 text versus code.** The rule text says the round trip is below a tenth of the rise time, but the formula in the code, $\ell<t_r v/10$, corresponds to a round trip $2\ell/v<t_r/5$. One of them is wrong; the code is what runs.

## 4. Assumptions and validity range

- **Straight-line distances** in RP-004 and PD-001 are lower bounds of the true current path.
- **Stripline gaps** are judged only when no side is referenced.
- **XT-001** has no broadside (adjacent-layer) coupling. **ED-003** covers the board outline only. **ED-001** sees only partially covered runs. **PD-001** has no per-pin coverage mode. **EM-011** skips E11's "fed by a high-speed via" condition, so it may flag cavity modes that are never excited.
- **Effective permittivity** is the crude $D_k$ / $(D_k+1)/2$ estimate, adequate for a screening wavelength but not for delay-sensitive decisions (DP-002 skew uses it).
- **Frequency** is a single configured frequency per rule, not derived from rise time unless the intent supplies it.
- **IM-002** is only as good as the impedance solver (Section 5), and uses the board's nominal stack-up; fabricator tolerances are not included.
- Rules reach other analysis packages only through services (`ctx.services()`), not by import.

## 5. Error sources and magnitudes

| Source | Magnitude | Evidence |
|---|---|---|
| Geometric exactness of rule outputs on constructed boards | rule id, net, layer exact; location within 50 µm gate | `tests/boards/kicad10/*/expected.yaml`, B-ERC-01 |
| Impedance solver inside IM-002 | BEM against exact solutions 0.05 to 0.23 %, BEM against FEM within 0.43 % | [oa-impedance](../oa-impedance/THEORY.md) section 5 |
| Threshold validity (does a violation predict a real problem?) | **not measured**; no correlation to measured emissions or eye closure | no case |
| False positive and false negative rates on real boards | **not measured** | no case |
| Cavity frequency | analytic check: f10 = 1.807 GHz test in `test_contract.py`; real boards deviate through fringing fields and loading [verify magnitude] | tests |

## 6. Validation evidence

Source: `docs/validation/results.json`, `docs/validation/erc.md`. The reference is `expected.yaml` of the WP-102 boards, constructed by `tools/boardgen` independently of the rule engine (class `analytic`).

- 11 of 12 ERC cases pass: `combined`, `cpwg`, `diff_pair`, `microstrip`, `microstrip_v9`, `power_plane`, `power_strip`, `power_two_layer_vias`, `split_plane`, `stripline`, `via_transition` (violation set matches the expected set, 0 mismatches).
- 1 **known gap**: the test boards miss their own impedance targets per the BEM (CPW_A/B 55.3 ohm against 50, differential pair Zdiff 139 ohm against 100, USB_D 126 ohm against 90). Either the board geometry is wrong or the IM-002 hits must be listed in `expected.yaml` (change request WP-201-expected-impedance). It is the one place where the ERC result and the "intended" design disagree, and it is unresolved.
- Benchmark B-ERC-01: combined board passes (0 mismatches, 0.39 s against a 0.5 s target); 10k-track (6.2 s after WP-509 against a 3 s target) and 100k-track cases are not passing their runtime gates (`docs/validation/benchmarks/erc.md` shows them pending; FOLLOWUPS WP-509 reports the 6.2 s figure).
- Analytic checks in `test_contract.py`: stub resonance and cavity f10.

**Known gaps** (FOLLOWUPS WP-201): single-violation and negative fixtures exist only for IM-002, RP-003 and RF-001; the other rules lack them. Microstrip boards have no net class, so IM-002 fires only with an explicit 50 ohm intent. There is no calibration of any threshold against measurements. The WP-207 stub `erc` analysis collides with the real one in four service/CLI tests (integration note).

## 7. References

- T. H. Hubing et al., Clemson CVEL PCB EMC expert-system rules, *EMC'97* (https://cecas.clemson.edu/cvel/pdf/EMCS97-444.pdf), as cited in the code; full author list and title [verify]. The paper itself was not read for this page.
- H. W. Ott, *Electromagnetic Compatibility Engineering*, Wiley, 2009 (shielding effectiveness of apertures, grounding, 20H discussion) [verify sections].
- C. R. Paul, *Introduction to Electromagnetic Compatibility*, 2nd ed., Wiley, 2006.
- H. Johnson, M. Graham, *High-Speed Digital Design: A Handbook of Black Magic*, Prentice Hall, 1993 (crosstalk versus spacing/height) [verify equation].
- IPC-2141A, *Design Guide for High-Speed Controlled Impedance Circuit Boards*, 2004 (cited by the IM rules).
- Project: `docs/research/07-quasistatic-thermal-emi.md` section 3.1 (rules E1 to E13), `docs/research/06-landscape-architecture.md` section 3.2 (rule catalogue), `docs/specs/interfaces.md` sections 3.5 to 3.7.
