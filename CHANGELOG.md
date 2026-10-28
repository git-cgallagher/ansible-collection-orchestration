# Changelog

## 0.2.1 — 2026-10-28

- A `when:` that calls a lookup (`lookup()`, `query()`, `q()`) is no longer evaluated up front, so the lookup
  runs once per host instead of twice. New variable `time_offset_check_when` (default `true`): set it to
  `false` to stop the up-front check entirely, for example when a `when:` reaches an expensive lookup through
  a variable.
- Fix: `throttle` didn't limit tasks that had been held for their start slot. `free` checks throttle by
  counting running workers, and held tasks weren't running yet, so they all passed the check and started
  together once released (6 at once with `throttle: 2` in testing). Held tasks now start only when their
  throttle has room. Each start is also checked against the real start before it, so tasks throttle held
  past their slot still start at least a gap apart.
- Docs: a README section on what the gap does and doesn't guarantee. It spaces task *starts* (use `throttle` to
  cap how many run at once), measured when Ansible launches each task, before any connection or module
  start-up time.
- CI: a monthly scheduled run tests new ansible-core releases even when no PR is open. On that run a devel
  failure fails the workflow, so GitHub emails a notice; on PRs, devel stays a non-blocking warning.

## 0.2.0 — 2026-10-28

- Tasks whose `when:` is already false no longer take a start slot. They used to be held for a slot like any
  other task, which under a shared `time_offset_key` pushed back every task behind them; in the test play,
  the last real start moved from ~23s to ~7s after the play began (just the 8 starts that actually run, 1s apart). The condition is checked with the same
  code Ansible uses when it runs the task; when it can't be decided up front (it needs values only the
  worker sets, or the task loops), the task is held as before.

## 0.1.0 — 2026-10-27

- Initial release: `git_cgallagher.orchestration.time_offset` strategy. It works like `free`, but spaces each
  task's start across hosts by `time_offset_seconds` (plus optional `time_offset_jitter`) without
  serializing the task. Originally proposed as ansible/ansible#80519 (strategy feature request, closed
  because core no longer accepts new plugins).
- `time_offset_key`: tasks with the same key share one queue of start slots, so the gap holds between
  different tasks that hit the same target (for example `"{{ mgmt_server }}"`). It's templated per host.
- Tests: unit tests for the start-slot scheduler; an integration target that measures real per-host start
  times, covering the gap, overlap, no repeated delay, gap 0, jitter, task-level vars, handlers,
  `include_tasks`, `serial` batches, shared and per-host keys (with a no-key control case) and bad values.
- Release automation: `galaxy-publish` workflow publishes each new `galaxy.yml` version to Galaxy when it lands
  on `main`, then tags it and creates a GitHub release. The token lives in a main-only `galaxy` environment.
- CI: `ansible-test` sanity/units/integration on ansible-core 2.19–2.21 and devel. All devel tests (sanity, units, integration) run with `--venv` (no container), because devel's test container can't
  start on GitHub runners.
- Security: actions pinned to commit SHAs (Dependabot keeps them current), `SECURITY.md` with private
  vulnerability reporting, and a `main` ruleset requiring a PR and the stable-version checks.
- Collection namespace is `git_cgallagher`, which Galaxy creates automatically from the GitHub username.
