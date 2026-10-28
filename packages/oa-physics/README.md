# oa-physics

Dk/Df(f), roughness, rho(T) and analytic via models (WP-106).

Public API (interfaces.md section 10): `eps_complex`, `ds_parameters`, `fit_ds`, `as_causal`,
`kk_residual`, `skin_depth`, `roughness_factor`, `surface_impedance` (causal by default: K(f)
fitted with first-order sections `c_k jf/(jf+f_k)`), `resistivity`, `sheet_resistance`,
`etch_widths`, `cannonball_huray_parameters`.

`oa_physics.via`: `barrel_inductance` (Goldfarb-Pucel), `pad_capacitance` (Johnson),
`via_pair_inductance`, `barrel_resistance`, `stub_resonance`, `estimate`, `pi_model_sparams`.
Registered as service `via_model` (entry point `oa_physics.via:make_service`).

Sign convention: `eps = eps' - j eps''`. Temperatures are in kelvin.

Golden suite: `tests/golden/physics/`. Known gap: the cannonball tile area is unverified (see
`docs/change-requests/WP-106-ieee370-and-cqm.md`).
