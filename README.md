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

With `free`, hosts don't wait for each other between tasks, so once the first offset task spreads them
out, later tasks usually already meet the gap. You pay about `(hosts − 1) × gap` once per play, not per task.

This collection started as a feature request to ansible-core,
[ansible/ansible#80519](https://github.com/ansible/ansible/issues/80519). Core no longer accepts new
plugins, so it lives here.

## Install

From GitHub (no Galaxy release yet):

```bash
ansible-galaxy collection install git+https://github.com/git-cgallagher/ansible-collection-orchestration.git
```

Or in a `requirements.yml`:

```yaml
collections:
  - name: https://github.com/git-cgallagher/ansible-collection-orchestration.git
    type: git
    version: main
```

Requires ansible-core 2.18 or later.

## Usage

Set the strategy, then set the gap as an ordinary variable.

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

## Limitations

- **Spacing is per task.** A task counts as "the same task" the way `throttle` counts it. Tasks loaded
  by `include_tasks` with a host-specific file name count as different tasks and aren't spaced against
  each other.
- **A `loop` is spaced per host, not per item.**
- **The `TASK [...]` banner** (the `v2_playbook_on_task_start` callback) prints when a task is scheduled.
  `v2_runner_on_start` fires when it really starts.
- **`throttle` still applies.** It counts only started tasks, not held ones.
- **Whatever `free` doesn't support isn't supported here either:** `run_once`, `any_errors_fatal`,
  `max_fail_percentage`, and modules that bypass the host loop (such as `pause`).
- **It relies on ansible-core strategy internals** (`_queue_task`, `_process_pending_results`,
  `_blocked_hosts`). CI runs weekly against ansible-core `devel` to catch upstream changes early.

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
- bad values fail with a clear message.

CI runs all three test types against ansible-core 2.18, 2.19, 2.20, 2.21 and devel.

## License

GPL-3.0-or-later (see [LICENSE](LICENSE)). The plugin subclasses ansible-core's GPL-licensed `free`
strategy.
