# WP-202: `method="fem"` is only registered once `oa_impedance.fem` has been imported

Raised by: WP-202 (impedance FEM). Status: proposal, local adapter in place.

## Problem

`oa_impedance.methods.get("fem")` raises `MethodNotAvailable` until something has registered
`"fem"`. The WP-105 text says `"fem"` "resolves lazily", but nothing imports
`oa_impedance.fem`, and WP-202 may not edit WP-105 files (`methods.py`, `service.py`,
`__init__.py`). So `ImpedanceServiceImpl().solve(xs, method="fem")` fails in a fresh process
unless the caller imported `oa_impedance.fem` first.

## Local adapter (WP-202)

Importing `oa_impedance.fem` calls `methods.register("fem", solve_fem, replace=True)`.
Tests import the package explicitly.

## Proposed change

In `oa_impedance/methods.py::get`, when `name == "fem"` is missing, do
`importlib.import_module("oa_impedance.fem")` (guarded by `ImportError` -> the existing
`MethodNotAvailable`) and retry. Impact: 4 lines in WP-105 code, no contract change; keeps
`import oa_impedance` light (scikit-fem and gmsh are only imported when FEM is requested).

## Other contract notes

- `oa_results` still has no `Accuracy` model (see WP-105-accuracy-fields-missing.md). FEM follows
  the WP-105 adapter: container `accuracy_class` is `"reference"` (converged) or `"validated"`, plus
  `ValidityFlag(code="accuracy")` carrying `accuracy_class=2d_field; converged=...;
  tolerance_basis=golden:impedance_fem`. The loss override (`with_fem_loss`) adds
  `ValidityFlag(code="loss_model")`.
- `ImpedanceResult` has no field for the refinement history; it is written to `notes`.
