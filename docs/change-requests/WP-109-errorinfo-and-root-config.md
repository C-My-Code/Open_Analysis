# WP-109: ErrorInfo layering and root tooling config

Status: proposal. Nothing outside `packages/oa-mesh/**`, `tests/golden/mesh2d/**` and this file was edited.

## 1. ErrorInfo is not reachable from oa_mesh

Rule 7 requires `ErrorInfo{code, message, path, hint}` across package boundaries. ai-integration.md section 3.2 places `ErrorInfo` in `oa_model.errors`, but it currently lives in `oa_framework.errors`, which is a higher layer than `oa_mesh` (import-linter forbids the import). `oa_model.errors.OAError` has `code` and `message` only.

Local adapter: `oa_mesh.MeshError(OAError)` carries `code`, `message`, `path`, `hint`, `details` and `to_dict()` returning the four `ErrorInfo` fields. Callers wrap it with `ErrorInfo(**err.to_dict())`.

Proposed: move `ErrorInfo` to `oa_model.errors` (as the spec says) and give `OAError` the optional `path`/`hint` and `info()`. Then `MeshError` drops its own fields.

## 2. Root config (integrator)

`pyproject.toml` (root) is not mine, so the following is needed there:

- Add `packages/oa-mesh/src` and `packages/oa-mesh/tests` to `[tool.mypy] files`. Both pass `mypy` with the current root config plus the ignores below; `packages/oa-mesh/src` also passes `mypy --strict`. Consider adding `oa_mesh.*` to the strict override.
- Add `[[tool.mypy.overrides]] module = ["gmsh", "shapely.*", "meshio"] ignore_missing_imports = true`. The source uses inline `# type: ignore[import-untyped]` meanwhile, which works with `warn_unused_ignores` on the current stubs; if shapely ships stubs later, those ignores will be flagged and can be dropped.
- import-linter needs no change; `lint-imports` is green (5 contracts kept).
- `uv.lock` needs regenerating at merge: new dependencies of `oa-mesh` are `oa-results`, `shapely`, `meshio`.
- Golden tests find the repo-level `tests/golden/mesh2d/` via `oa_testkit.golden.golden_root()`.
