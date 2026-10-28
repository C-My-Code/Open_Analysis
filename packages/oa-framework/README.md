# oa-framework

Plugin protocols and shared runtime types (interfaces.md sections 3-5).

Public API: `Analysis`, `Plan`, `Task`, `TaskOutput`, `Resources`, `PlanContext`, `RunContext`,
`Rule`, `RuleContext`, `Reader`, `ReadOptions`, `load`, `Services`, `ImpedanceService`,
`SolverAdapter` (+ `AdapterCapabilities`, `PreparedRun`, `EMSolveOutput`), `ExternalRunner`,
`ExternalCommand`, `ExternalResult`, `JobService` (+ `AnalysisRequest`, `JobStatus`, `JobEvent`),
and `Registry` (`Registry.load()` through entry points, `Registry.for_tests(...)` without).

Errors are structured as `{code, message, path, hint}` (`ErrorInfo`, `FrameworkError`,
`to_error_info`) so humans, the REST API and AI agents see the same shape.

Golden suites: none. `BoardGeometry` is `Any` until `oa_geometry` (WP-104) exists.
