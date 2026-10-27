# Changelog

## 0.1.0 — 2026-10-27

- Initial release: `git_cgallagher.orchestration.time_offset` strategy. It works like `free`, but spaces each
  task's start across hosts by `time_offset_seconds` (plus optional `time_offset_jitter`) without
  serializing the task. Originally proposed as ansible/ansible#80519 (strategy feature request, closed
  because core no longer accepts new plugins).
- Tests: unit tests for the start-slot scheduler; an integration target that measures real per-host start
  times, covering the gap, overlap, no repeated delay, gap 0, jitter, task-level vars, handlers,
  `include_tasks`, `serial` batches and bad values.
- CI: `ansible-test` sanity/units/integration on ansible-core 2.19–2.21 and devel. Devel integration runs with `--venv` (no container), because devel's test container can't
  start on GitHub runners.
- Security: actions pinned to commit SHAs (Dependabot keeps them current), `SECURITY.md` with private
  vulnerability reporting, and a `main` ruleset requiring a PR and the stable-version checks.
- Collection namespace is `git_cgallagher`, which Galaxy creates automatically from the GitHub username.
