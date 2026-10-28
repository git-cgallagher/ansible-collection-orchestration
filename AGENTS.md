# AGENTS.md — git_cgallagher.orchestration

Ansible collection (public repo) holding strategy plugins. Read `~/Code/AGENTS.md` first for workspace-wide
rules.

## Layout
- `plugins/strategy/time_offset.py` — the only plugin. `OffsetScheduler` (pure, unit-tested) hands out start
  slots per queue key. The key is `('task', task._uuid)` by default or `('key', <time_offset_key>)`; the tags
  keep a user key from colliding with a uuid. `StrategyModule` subclasses ansible-core's `free` strategy.
- `tests/unit/` — scheduler tests with a fake clock.
- `tests/integration/targets/time_offset/` — `runme.sh` drives playbooks against six `connection: local`
  hosts; `probe.py` logs real start times and `check_starts.py` asserts on them.
- `meta/runtime.yml` `requires_ansible` and the CI matrix in `.github/workflows/ansible-test.yml` must
  cover the same versions.

## How the plugin hooks into core (read before changing it)
- It does **not** copy `free.run()`. It overrides `_queue_task` (to hold a task until its start slot) and
  `_process_pending_results` (which `free` calls on every pass, to release held tasks). Those two
  signatures have stayed the same from 2.18 through devel. The minimum is 2.19 (2.18 was dropped near its EOL).
- A held host **must** be marked in `_blocked_hosts`. `free` has already advanced the host past the task
  and would otherwise hand it the next one. On ≤2.21, `free` sets the flag before `_queue_task`; on devel
  (ansible/ansible commit `0e69d86c0`, 2026-07-01), base `_queue_task` sets it. The plugin sets it
  itself, so it works either way.
- `throttle` is enforced again at release: `free` checks it before `_queue_task` but counts only running workers,
  so held tasks all slipped past it (fixed 2026-10-28, integration case 11). `_can_start` checks throttle *and*
  `OffsetScheduler.gap_elapsed` against the real last start, because a task throttle held past its slot no longer
  matches the reservations.
- Skipped tasks take no slot: `_when_is_false` calls `Task._resolve_conditional(task.when, task_vars)`, the
  same check `TaskExecutor` makes (same signature in 2.19, 2.21 and devel). It may only ever answer "skip"
  when that's certain. Any exception, and any looped task, means "hold". A wrong "skip" for a task that then
  runs would start it outside its slot; a wrong "hold" only costs time. Integration case 10 pins it.
- The `OffsetScheduler` is a **class attribute** on purpose: Ansible builds a new strategy object for every
  `serial` batch, and per-instance state reset the gap at each batch boundary (caught in review 2026-10-27,
  pinned by integration case 7). Task `_uuid`s are stable across batches.
- Never `sleep` in these hooks. One loop drives every host.
- Templating uses `TemplateEngine`, the engine `free` itself uses since 2.19. It's a private import, so watch it on devel.

## Testing
`ansible-test` needs the checkout at `ansible_collections/git_cgallagher/orchestration/`. Run
`ansible-test {sanity,units,integration} --docker default` from there. macOS has no `timeout` binary, so
`runme.sh` only runs as-is inside the test container. The integration run takes about a minute.
CI runs on every PR, on every push to `main`, and **monthly on a schedule** (`17 13 1 * *`). The schedule
exists because the plugin hooks ansible-core internals, so an upstream release can break it with no change
here. On scheduled runs only, `continue-on-error` is off for the devel jobs, so a devel failure fails the run
and GitHub emails its owner. ⚠️ GitHub disables scheduled workflows in a public repo after **60 days with no
commits**, so the monthly run stops silently if the repo goes quiet. Re-enable it under Actions →
ansible-test.
⚠️ Gotcha: devel's test container fails to start on GitHub's `ubuntu-latest` runners (`ansible-test` exits
while remounting `/sys/fs/cgroup`). It hit integration first, then units (2026-10-27), then sanity (2026-10-28), and
retrying doesn't help. CI therefore runs **all** devel tests in the `devel-venv` job with
`ansible-test --venv --requirements`, which installs the sanity linters into the venv; no container. Released
versions run fine in the container.
⚠️ Gotcha: `probe.py` timestamps a task when its Python process starts on the host, not when Ansible launches
it (the moment the plugin actually spaces). On a busy runner that adds up to ~0.4s of variation, which once
failed a 0.85s floor with a 0.776s gap, on devel, 2026-10-27. All 1s-gap checks now share `MIN_GAP=0.7` in
`runme.sh`. If CI flakes, widen the tolerance; don't drop the assertion.

## Releasing
**Galaxy follows `main` automatically** (`.github/workflows/galaxy-publish.yml`). On every push to `main` it asks
Galaxy whether the `galaxy.yml` version exists. If it doesn't, the workflow builds, publishes (and waits for Galaxy's
import), then tags `v<version>` and creates a GitHub release with the tarball. Galaxy versions are immutable,
so **to release, bump `version` in `galaxy.yml` and add a `CHANGELOG.md` entry in the same PR**. Merges that don't
bump it publish nothing.
- The token is the `GALAXY_API_KEY` secret in the **`galaxy` environment**, which only `main` can deploy to.
  It isn't a repo secret, so PR workflows can never read it. Until it's set, the workflow skips publishing with a
  warning and stays green. After adding it, re-run the workflow (Actions → galaxy-publish → Run workflow).
- `ansible-core` is pinned in that workflow, and Dependabot doesn't update pip pins inside workflow steps, so bump
  it by hand when the CI matrix moves on.
- Namespace is `git_cgallagher` on purpose. Galaxy creates a namespace automatically from the GitHub username
  at first login, turning hyphens into underscores, so `git-cgallagher` becomes `git_cgallagher` with no
  request needed. `cgallagher` and `appalachiancloud` were both considered and were free on Galaxy
  (2026-10-27), but either needs a request on the Ansible forum. Changing the namespace later renames the
  collection's full name and breaks existing `strategy:` lines, so treat it as a major version.
- One-time setup by the owner: log in to galaxy.ansible.com with GitHub (this creates `git_cgallagher`), create
  an API token, then run `gh secret set GALAXY_API_KEY --env galaxy --repo git-cgallagher/ansible-collection-orchestration`.
  It prompts for the value, so the token never reaches shell history or chat.

## Repo security (public repo)
- `main` is protected by the ruleset **"main protection"**: changes go through a PR, force-push and deletion are
  blocked, there's no bypass, and the 9 `stable-*` checks (`sanity|units|integration (stable-2.19|2.20|2.21)`)
  are required. Devel checks (`sanity (devel, venv)`, `units (devel, venv)`, `integration (devel, venv)`) are **not** required: a red devel
  check is an early warning about upstream core, not a merge blocker. When the supported versions change,
  update the matrix, `meta/runtime.yml` **and** the ruleset's required checks together, or PRs block on
  checks that no longer run.
- Actions are pinned to commit SHAs with a version comment; `.github/dependabot.yml` bumps them monthly.
  Never replace a pin with a tag.
- Secret scanning, push protection, Dependabot alerts and security updates, and private vulnerability
  reporting are on (`SECURITY.md`). Turn on CodeQL default setup (Python) once Python code is on `main`.
- The devel job pip-installs an unpinned ansible-core tarball. That's acceptable because the workflow is
  `permissions: contents: read`, has no secrets and uses `pull_request`, not `pull_request_target`. Keep it
  that way.

## Conventions
- License is GPL-3.0-or-later, and must stay so: the plugin subclasses GPL code from ansible-core.
- Branch + PR; `main` is protected by convention.
