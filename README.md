# git_cgallagher.orchestration

Ansible strategy plugins that control *when* tasks start across hosts.

The first plugin is **`time_offset`**: the `free` strategy, except that two hosts never start the same
task closer together than a gap you choose. Tasks still run on many hosts at once; they just don't all
start at the same instant.

## Why

Some failures only happen when many hosts hit the same backend at exactly the same moment:

- **vSphere guest customization** fails silently when VMs cloned at the same instant end up with
  matching UUIDs.
- **Delegated lookups** (for example, several hosts asking one management server for the closest domain
  controller) fail with generic errors when they arrive at once. Running them again works.

Ansible's built-in tools don't fit this case well:

| Approach | Hosts in the task at once | 20 hosts, 60 s task |
|---|---|---|
| `free` / `linear` (default) | all, starting at the same instant | ~60 s |
| `throttle: 1` | 1, so the task runs serially | ~20 min |
| `serial: 1` | 1 for the whole play | ~20 min per task |
| **`time_offset`, 2 s gap** | all, starting 2 s apart | ~60 s + 19 × 2 s ≈ 98 s |

**Every task respects the gap.** No two hosts ever start the same task closer together than
`time_offset_seconds`, for every task, every handler and every `serial` batch.

**What you pay for that is usually small.** `free` doesn't make hosts wait for each other between tasks.
So once the first offset task has spread them out, they reach each later task already spaced at least a gap
apart, and there's rarely anything to hold back. The total extra time is about `(hosts − 1) × gap`,
plus a little more whenever hosts bunch back up (for example, after a task that waits on the same
external event for everyone).

This collection started as a feature request to ansible-core,
[ansible/ansible#80519](https://github.com/ansible/ansible/issues/80519). Core no longer accepts new
plugins, so it lives here.

## Install

From [Ansible Galaxy](https://galaxy.ansible.com/ui/repo/published/git_cgallagher/orchestration/):

```bash
ansible-galaxy collection install git_cgallagher.orchestration
```

Or in a `requirements.yml`, pinned to a release:

```yaml
collections:
  - name: git_cgallagher.orchestration
    version: ">=0.2.0,<0.3.0"
```

To try unreleased changes, install straight from `main` on GitHub:

```bash
ansible-galaxy collection install git+https://github.com/git-cgallagher/ansible-collection-orchestration.git
```

Requires ansible-core 2.19 or later. Releases are listed on the
[GitHub releases page](https://github.com/git-cgallagher/ansible-collection-orchestration/releases)
and in [CHANGELOG.md](CHANGELOG.md).

## Usage

Set the strategy, then set the gap as an ordinary variable. The examples below cover the essentials;
**[docs/examples.md](docs/examples.md) has 28 more**:
- basics: blocks, jitter, computed gaps, per-environment settings;
- shared-target keys: per-server queues, load balancers, keys across plays;
- combining with `throttle`, `serial`, handlers, includes, conditionals and loops;
- real-world recipes: vSphere, Active Directory, package mirrors, staggered reboots, rate-limited APIs,
  cloud provisioning, Git servers and certificate authorities.

### Offset every task in a play

```yaml
- name: Build Windows VMs
  hosts: new_vms
  gather_facts: false
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 2    # minimum gap between two hosts starting the same task
    time_offset_jitter: 1     # plus 0–1 s of random delay, so hosts don't fall into a fixed rhythm
  tasks:
    - name: Clone and customize VM
      community.vmware.vmware_guest:
        name: "{{ inventory_hostname }}"
        template: win2022-template
        state: poweredon
      delegate_to: localhost
```

### Offset only the tasks that collide

```yaml
- name: Configure Windows servers
  hosts: windows
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Install features
      ansible.windows.win_feature:
        name: Web-Server
      # no offset: runs exactly as under free

    - name: Find closest domain controller
      ansible.windows.win_shell: nltest /dsgetdc:{{ ad_domain }}
      delegate_to: "{{ mgmt_server }}"
      vars:
        time_offset_seconds: 3
```

### Space everything that hits a shared target

Without a key, the gap applies between hosts running the *same* task. Two *different* tasks that call the
same server can still start at the same moment: host 1 starts "Pre-stage the computer account" just as
host 2 starts "Find closest domain controller", and `mgmt01` gets both at once. Give those tasks a shared
`time_offset_key` and they draw start slots from one queue:

```yaml
- name: Join Windows servers to the domain
  hosts: windows
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Everything that goes through the management server
      vars:
        time_offset_seconds: 3
        time_offset_key: "{{ mgmt_server }}"
      delegate_to: "{{ mgmt_server }}"
      block:
        - name: Find closest domain controller
          ansible.windows.win_shell: nltest /dsgetdc:{{ ad_domain }}

        - name: Pre-stage the computer account
          microsoft.ad.computer:
            name: "{{ inventory_hostname_short }}"
```

The key is templated per host. If different hosts use different management servers, each server gets its
own queue, and hosts going to `mgmt01` don't wait on hosts going to `mgmt02`. The gap also applies to one
host running two keyed tasks back to back, since that's still two hits on the same target.

Tasks that are skipped don't get in the way. When a host reaches a task whose `when:` is already false,
the task goes straight through to be skipped and takes no start slot, so a shared key over a play full of
conditional tasks only spaces the ones that actually run.

### Make it the default

```ini
# ansible.cfg
[defaults]
strategy = git_cgallagher.orchestration.time_offset
```

```yaml
# group_vars/vsphere_builds.yml
time_offset_seconds: 2
```

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `time_offset_seconds` | `0` | Minimum gap, in seconds, between two hosts starting the same task. Fractions are allowed. |
| `time_offset_jitter` | `0` | Extra random delay, from 0 up to this many seconds, added to each gap. |
| `time_offset_key` | the task | What to space starts against. Tasks with the same key share one queue of start slots, so the gap holds between *any* of them on *any* hosts. Templated per host. |
| `time_offset_check_when` | `true` | Whether to evaluate a task's `when:` before holding it, so skipped tasks take no start slot. Set `false` if evaluating it an extra time is a problem (see Limitations). |

These are normal variables, so variable precedence applies: a task-level value overrides a play-level
one, and either can be set per group or host or templated (`"{{ base_gap * 2 }}"`). A non-numeric or
negative value fails the play with an error naming the variable. With both at `0` (or unset), a task
runs exactly as under `free`.

## How it works

`time_offset` subclasses ansible-core's `free` strategy and changes one step: handing a task to a
worker process.

1. When a host is ready for a task, the plugin reserves the earliest start slot for that task: now, or
   the previous host's slot plus the gap (and jitter), whichever is later.
2. If the slot is now, the task starts right away. Otherwise the host is held, marked as busy so `free`
   doesn't give it its next task, until the slot arrives.
3. `free` polls about every millisecond. On each pass, the plugin starts any held tasks whose slot has
   come.

Nothing sleeps in the strategy loop, so a held host never slows down other hosts or other tasks.

## What the gap does and doesn't guarantee

**It spaces when tasks start, not how many run at once.** Each host waits only for its start slot, not
for earlier hosts to finish. A task that takes longer than the gap therefore overlaps on many hosts: a
60 s task with a 2 s gap can have up to 30 hosts inside it at once. If the backend breaks on *how many
run at the same time*, rather than *how many start at the same moment*, add `throttle` to the task too:

```yaml
- name: Clone and customize VM
  community.vmware.vmware_guest:
    name: "{{ inventory_hostname }}"
    template: win2022-template
  delegate_to: localhost
  throttle: 4                    # at most 4 clones running at once...
  vars:
    time_offset_seconds: 2       # ...and no two starting within 2 s of each other
```

**It spaces when Ansible launches each task, not when the work reaches the target.** After launch,
Ansible still connects to the host, copies the module over and starts it. That takes a variable amount of
time per host, so two tasks launched 1 s apart can reach the target closer together than that. The
variation is small for `delegate_to: localhost`. Over WinRM or a slow SSH connection it can be a second or
more. Set the gap comfortably larger than that variation.

## Limitations

- **Without a key, spacing is per task.** A task counts as "the same task" the way `throttle` counts it.
  Tasks loaded by `include_tasks` with a host-specific file name count as different tasks; give them a
  shared `time_offset_key` if they should be spaced together.
- **Skipped tasks take no start slot, as long as the plugin can tell up front.** Before holding a task,
  the plugin evaluates its `when:` with the same code Ansible uses when it runs the task. If the condition
  is false, the task isn't held. If the condition can't be decided yet, the task is held as usual. That
  covers a condition that needs a value only set as the task runs, and any task with a `loop` (its `when:`
  is evaluated per item).
- **Lookups in `when:` run once, as usual.** A `when:` that calls `lookup()`, `query()` or `q()` isn't
  checked up front, so the lookup runs only when the task does, as under any strategy. The task then waits
  for its slot even if it ends up skipped. One case isn't caught automatically: a `when:` that reaches a
  lookup *through a variable* (`when: api_status == 'ok'`, where `api_status` is a lookup). The up-front
  check evaluates it once more. If that lookup is expensive or has side effects, set
  `time_offset_check_when: false` on the task, block or play.
- **A `loop` is spaced per host, not per item.**
- **The `TASK [...]` banner** (the `v2_playbook_on_task_start` callback) prints when a task is scheduled.
  `v2_runner_on_start` fires when it really starts.
- **`throttle` and the gap work together.** The gap spaces when tasks start; `throttle` caps how many run
  at once. A task that `throttle` holds past its slot still starts at least a gap after the previous one.
- **Meta tasks** (`meta: flush_handlers` and the like) run inside the strategy, not on a worker, so they
  aren't spaced. They don't touch remote systems.
- **Whatever `free` doesn't support isn't supported here either:** `run_once`, `any_errors_fatal`,
  `max_fail_percentage`, and modules that bypass the host loop (such as `pause`).
- **It relies on ansible-core strategy internals** (`_queue_task`, `_process_pending_results`,
  `_blocked_hosts`, `Task._resolve_conditional`). Every PR is also tested against ansible-core `devel` to catch upstream changes early.

## Releasing

Galaxy follows `main`. To release, bump `version` in `galaxy.yml` and add a `CHANGELOG.md` entry in the PR.
When it merges, the `galaxy-publish` workflow publishes that version to Galaxy, tags it and creates a GitHub
release. Merges that don't change the version publish nothing.

## Development

Tests use `ansible-test`, which needs the collection checked out at
`ansible_collections/git_cgallagher/orchestration/`:

```bash
git clone https://github.com/git-cgallagher/ansible-collection-orchestration.git \
  ansible_collections/git_cgallagher/orchestration
```

Then, from that directory, with ansible-core installed and Docker running:

```bash
ansible-test sanity --docker default
```

```bash
ansible-test units --docker default
```

```bash
ansible-test integration --docker default
```

The integration target runs playbooks against six local hosts. Each host records when every task
*actually* started, and `check_starts.py` asserts that:

- starts are spaced by the gap;
- tasks still overlap (not serial);
- later tasks aren't delayed again;
- a gap of 0 behaves like `free`;
- task-level variables and jitter work;
- `throttle` still caps how many run at once when combined with a gap;
- a lookup in `when:` runs once per host, not twice, and `time_offset_check_when: false` stops the
  up-front check for lookups reached through variables;
- a task whose `when:` is false takes no start slot, so skipped tasks never delay the ones that run;
- a shared `time_offset_key` spaces different tasks against each other, a per-host key gives independent
  queues, and with no key those starts collide (the control case);
- bad values fail with a clear message.

CI runs all three test types against ansible-core 2.19, 2.20, 2.21 and devel.

### Molecule

The Molecule scenario in `extensions/molecule/default/` runs the strategy against four containers reached over
the `community.docker.docker` connection, so every task goes over a real remote connection rather than
`connection: local`. It checks that starts are spaced by the gap while the hosts still overlap, that later tasks
aren't delayed again, and that two tasks sharing a `time_offset_key` are spaced against each other. Molecule
builds this checkout and installs it as `git_cgallagher.orchestration` first, so it tests the packaged
collection. Run it from the repo root with Docker running (no `ansible_collections/` path needed):

```bash
pip install -r extensions/molecule/requirements.txt
```

```bash
molecule test
```

It takes about a minute and runs in CI on the newest stable ansible-core.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). The plugin subclasses ansible-core's GPL-licensed `free`
strategy.
