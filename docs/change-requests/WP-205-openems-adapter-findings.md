# WP-205: openEMS adapter findings (adapter internals, for WP-108 follow-up)

Status: proposed. Raised by WP-205. Nothing in `oa-em-openems` was edited; the flow works around
each item through `MeshSettings.expert` and its own defaults (`oa_em/problem.py`).

1. **PML plateau on TL-probe wave-port runs.** With the default `PML_8` boundary, microstrip runs
   (not only stripline) stall near -31 dB energy; S-parameters then carry a -30 dB noise floor
   (S21 scatter of +-1 dB, S11 floor -30 dB) and the run only stops at `max_timesteps`. With
   `openems.absorbing = "MUR"` the same case reaches -45 dB in 9 s and S21 is within 0.2 dB of the
   RLGC estimate. `oa_em` sets `openems.absorbing = MUR` by default for problems with wave ports.
   Proposal: make MUR the default for wave-port problems, or find the PML issue (the quasi-static
   field at the low end of the pulse, air padding of a few mm).
2. **Low-frequency content.** The DC-free Gaussian has too little energy below about 0.1 f_max:
   with f_min = 0 the first sweep points (f_max/(n-1)) are noise (via transition, 0-40 GHz: S11
   -11 dB and RQM 93 below ~4 GHz). `oa_em` starts the sweep at 0.1 f_max and lets the gate
   extrapolate DC. Proposal: the adapter should warn, or report the valid band
   (`NetworkResult.validity_fmin`-like), instead of returning noisy points.
3. **`est_time_s`** is 12x (microstrip, 55 s vs 4.5 s per run) to 30x (via transition, 5077 s,
   the step estimate hits `max_timesteps`, vs about 170 s) too pessimistic. `oa_em` multiplies it
   by 0.1 (`_EST_SCALE`); the time-step estimate `6 x path_length x sqrt(eps)/c0` should be
   replaced by an estimate calibrated on measured runs.
4. **Sheet conductors**: the zero-thickness `ConductingSheet` gives the thin-strip Z0 (50.7 ohm
   vs BEM 48.7 ohm for 35 um copper, 50.9 ohm for 1 um). That is a model limit, not a mesh
   error (identical at `preset=fine`). Documented in the oa-em tests, which compare with the BEM
   thin-strip limit. A `conductor_model="pec"` thick strip would remove it at a mesh cost.
5. **Differential legs** are single-ended TL probes; the coupled mode is only recovered by
   mixed-mode conversion (`EM.DIFF_LEG_PORTS` warning).
6. **`excite=False`** is still rejected; `oa_em` always excites every port.
