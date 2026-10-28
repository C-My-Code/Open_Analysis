# oa-jobs

Job queue, content-addressed cache, worker executors and external runners (WP-107).

- `LocalJobService(root)` implements `oa_framework.jobs.JobService` plus `estimate`. State lives
  in `<root>/.oa/` (`jobs.sqlite` in WAL mode, `cache/`, `results/`, `jobs/<id>/`). A new service
  on the same root resumes unfinished jobs.
- Executors: `inline` (coordinator thread) and `process` (spawned `ProcessPoolExecutor`).
- Cancellation: a `CANCEL` flag file per job (cooperative, visible to workers), then external
  process trees are killed. Windows uses a job object (`KILL_ON_JOB_CLOSE`, plus a commit limit as
  a backstop for the memory budget); POSIX uses process groups (SIGTERM, then SIGKILL after the
  grace period). Budgets: `max_runtime_s` and `max_memory_gb` are enforced by a watchdog thread
  (elapsed time; RSS of worker and external process trees) and reported as
  `cancel_reason="budget:max_runtime"` / `"budget:max_memory"`.
- Runners: `local`, `wsl` (`wsl.exe -d <distro> --cd <path> --`, `D:\x` <-> `/mnt/d/x`),
  `docker` (job dir bind-mounted at `/work`). Tools come from `tools.toml`
  (`%APPDATA%\open_analysis\tools.toml`, `~/.config/open_analysis/tools.toml` or
  `$OA_TOOLS_TOML`), with per-project overrides.
- `oa_jobs.doctor()` returns the data behind `oa doctor`.
- `oa_jobs.dummy` is a configurable fake analysis used by the tests
  (`registry_factory="oa_jobs.dummy:make_registry"`).

Known gaps: job `priority` is stored but does not reorder dispatch; the WSL runner does not enforce
the memory budget inside the Linux side; Docker and WSL runners have `external` tests only.
