# Changelog

## 1.0.0 — 2026-10-27

- Initial release: `cgallagher.orchestration.time_offset` strategy. It works like `free`, but spaces each
  task's start across hosts by `time_offset_seconds` (plus optional `time_offset_jitter`) without
  serializing the task. Originally proposed as ansible/ansible#80519 (strategy feature request, closed
  because core no longer accepts new plugins).
- Tests: unit tests for the start-slot scheduler; an integration target that measures real per-host start
  times, covering the gap, overlap, no repeated delay, gap 0, jitter, task-level vars, handlers,
  `include_tasks` and bad values.
- CI: `ansible-test` sanity/units/integration on ansible-core 2.18–2.21 and devel, plus a weekly run
  against devel.
