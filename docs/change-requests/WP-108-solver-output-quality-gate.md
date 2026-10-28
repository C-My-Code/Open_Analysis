# WP-108: `EMSolveOutput.network` cannot be built without a quality verdict

Status: proposed. Raised by WP-108 (openEMS adapter). No spec was edited.

## Problem

`oa_framework.em.EMSolveOutput.network` is documented as "quality NOT yet assessed: oa_em fills
it", but `NetworkResult.quality` is a required field (interfaces.md section 4.1/4.5). An adapter
cannot return a `NetworkResult` that has not been through the gate, and it cannot return a
`NetworkResult` without a `QualityReport`.

## What WP-108 does meanwhile (local adapter behaviour)

`OpenEMSAdapter.collect` calls the gate through the contract (`oa_network.quality.assess(skrf.Network)
-> QualityReport`, injectable via `OpenEMSAdapter(assessor=...)`). If `oa_network.quality` cannot be
imported (WP-106 not merged) it fills a conservative `QualityReport` with `verdict="fail"` and a note
saying the network was not assessed. It never reports `pass` without running the gate.

## Proposed change (architecture owner to decide)

Either:

1. Fix the comment: adapters return a `NetworkResult` that already carries the gate's report, and
   `oa_em` may re-run the gate after de-embedding or mixed-mode conversion (it must, because those
   change the S-matrix); or
2. Add `EMSolveOutput.raw_sparams` (freqs, S, z0, port names, no quality) and keep
   `NetworkResult` for the post-gate object.

Option 1 needs no type change. Impact on WP-205: it re-assesses after its own post-processing in
either case.

## Related notes (no contract change needed)

- `AdapterCapabilities.dispersive_dielectric` has no value for "one Debye pole fitted over the
  band"; the adapter reports `"single_frequency_fit"` (closest) and documents the fit error in its
  diagnostics and `mesh_stats["dielectric_fits"]`.
- openEMS needs one excitation run per port; ports with `excite=False` make `S_jj` unknowable, so
  the adapter rejects such problems. WP-205 should set `excite=True` on every port for openEMS.
