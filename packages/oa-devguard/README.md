# oa-devguard

Development-only guard. It caps memory for every Python process started from the project's
dev environment: tests, benchmarks and ad-hoc agent scripts alike.

- **Why:** the development machine froze repeatedly when scripts grew without bound. One
  equivalence script reached 125 GB.
- **How:** a `sitecustomize` module runs at interpreter startup and puts the process, and every
  child it starts, into a Windows Job Object limited to `OA_MEMCAP_GB` (default 8, maximum 16).
  A runaway process gets a `MemoryError` instead of exhausting the host.
- **Disable:** set `OA_MEMCAP_GB=0`. On non-Windows platforms the guard does nothing.
- **Public API:** `oa_devguard.install()` and `oa_devguard.limit_gb()`.
- **Scope:** dev dependency group only; never part of a product install.
