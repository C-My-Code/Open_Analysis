# WP-503 change request: `ErrorInfo` and `AccelReport` placement

Status: proposed.

## Problem
1. ADR-0009 says `oa_accel` imports `oa_model` for `ErrorInfo`. `ErrorInfo` is actually defined in
   `oa_framework.errors` (extended in `oa_service.errors`), two layers above `oa_physics`. A
   layer-next-to-`oa_physics` package cannot import it.
2. ADR-0009 §2 wants `AccelReport` in every result's provenance. `interfaces.md` has no field for it.

## Local adapter (done in WP-503)
`oa_accel.errors.ErrorInfo` is a frozen dataclass with the same four fields (`code`, `message`,
`path`, `hint`); `AccelError(info)` carries it. Codes: `ACCEL_OUT_OF_MEMORY`, `ACCEL_NO_DEVICE`,
`ACCEL_DEVICE_ERROR`, `ACCEL_UNSUPPORTED`, `ACCEL_NOT_CONVERGED`, `ACCEL_SINGULAR`,
`ACCEL_BAD_INPUT`, `ACCEL_BAD_DEVICE`, `ACCEL_BAD_CONFIG`. `ErrorInfo.to_dict()` feeds the
framework's model.

## Proposed change
- Option A (preferred): move the four-field `ErrorInfo` base to `oa_model` and have
  `oa_framework.errors` re-export it. `oa_accel` then imports it and drops its copy.
- Option B: keep the copy; adapters (`oa_service`, `oa_jobs`) convert with `to_dict()`.
- Add optional `accel: AccelReport | None` to the provenance block of `interfaces.md`;
  `AccelReport.to_dict()` and `.cache_key()` (device class + library major only) are provided.

## Impact
Additive. No existing package changes under option B.
