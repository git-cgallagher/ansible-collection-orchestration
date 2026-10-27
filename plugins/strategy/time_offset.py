# Copyright (c) 2026, Chris Gallagher
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import annotations

DOCUMENTATION = r"""
name: time_offset
short_description: Like the free strategy, but staggers when each task starts across hosts
description:
  - Behaves exactly like P(ansible.builtin.free#strategy) - every host runs through the play as fast as it can,
    without waiting for other hosts - with one addition.
  - When C(time_offset_seconds) is set, two hosts never start the same task closer together than that many
    seconds. The first host starts immediately, the second no sooner than the gap after it, and so on.
  - Unlike C(throttle), this does not serialize the task. A host waits only for its start slot, not for other
    hosts to finish, so the task still runs on many hosts at once - just not starting at the same instant.
  - The gap applies to every task, handler and C(serial) batch. Once hosts are spread out by the first offset
    task they usually reach later tasks already spaced, so those are rarely held back and the extra time is
    roughly (hosts - 1) x gap per play, plus more whenever hosts bunch back up.
  - The settings are ordinary variables, read per host and per task, so they can be set at play, group, host,
    block or task level and may be templated. Leaving both unset (or V(0)) makes a task run exactly as under
    P(ansible.builtin.free#strategy).
  - "C(time_offset_seconds) - minimum gap, in seconds (fractions allowed), between two hosts starting the same task."
  - "C(time_offset_jitter) - extra random delay, from 0 up to this many seconds, added to each gap so hosts do not
    fall into a fixed rhythm against a shared backend."
author: Chris Gallagher (@git-cgallagher)
version_added: 0.1.0
notes:
  - Starts are spaced per task, identified the same way C(throttle) identifies a task. Tasks loaded by
    C(include_tasks) with host-specific file names count as different tasks and are not spaced against each other.
  - A task with C(loop) is spaced per host, not per loop item.
  - Meta tasks run inside the strategy rather than on a worker, so they are not spaced.
  - The C(v2_playbook_on_task_start) callback (the TASK banner) fires when a host's task is scheduled; the
    C(v2_runner_on_start) callback fires when it actually starts.
  - C(throttle) still applies and counts only tasks that have started, not ones waiting for their start slot.
  - Everything that is unsupported by P(ansible.builtin.free#strategy) (C(run_once), C(any_errors_fatal),
    C(max_fail_percentage), modules that bypass the host loop) is unsupported here too.
seealso:
  - plugin: ansible.builtin.free
    plugin_type: strategy
"""

EXAMPLES = r"""
# Offset every task in the play by at least 2 seconds between hosts, plus up to 1 second of jitter.
- hosts: new_vms
  strategy: git_cgallagher.orchestration.time_offset
  vars:
    time_offset_seconds: 2
    time_offset_jitter: 1
  tasks:
    - name: Clone and customize VM
      community.vmware.vmware_guest:
        name: "{{ inventory_hostname }}"
        template: win2022-template
        state: poweredon
      delegate_to: localhost

# Offset only the task that collides on a shared target; everything else runs like free.
- hosts: windows
  strategy: git_cgallagher.orchestration.time_offset
  tasks:
    - name: Find closest domain controller
      ansible.windows.win_shell: nltest /dsgetdc:{{ ad_domain }}
      delegate_to: "{{ mgmt_server }}"
      vars:
        time_offset_seconds: 3
"""

import heapq
import itertools
import random
import time

from ansible._internal._templating._engine import TemplateEngine
from ansible.errors import AnsibleError
from ansible.plugins.strategy.free import StrategyModule as FreeStrategyModule
from ansible.utils.display import Display

display = Display()

GAP_VAR = 'time_offset_seconds'
JITTER_VAR = 'time_offset_jitter'


class OffsetScheduler:
    """Hands out start times so that no two starts of the same task are closer together than the gap."""

    def __init__(self, clock=time.monotonic, rng=random.random):
        self._clock = clock
        self._rng = rng
        self._last_start = {}

    def now(self):
        return self._clock()

    def reserve(self, key, gap, jitter=0.0):
        """Return the earliest start time for the next host running task ``key``, and claim it."""
        now = self._clock()
        last = self._last_start.get(key)
        if last is None:
            start = now
        else:
            start = max(now, last + gap + jitter * self._rng())
        self._last_start[key] = start
        return start


class StrategyModule(FreeStrategyModule):

    # Ansible creates a new strategy object for every serial batch, so the scheduler lives on the class:
    # each task's last start time then carries over and the gap holds across batch boundaries too.
    _offset_scheduler = OffsetScheduler()

    def __init__(self, tqm):
        super(StrategyModule, self).__init__(tqm)
        # min-heap of (start, seq, host, task, task_vars, play_context) waiting for their start slot
        self._deferred = []
        self._deferred_seq = itertools.count()

    def _queue_task(self, host, task, task_vars, play_context):
        gap, jitter = self._get_offset_settings(task, task_vars)
        if gap == 0 and jitter == 0:
            return super(StrategyModule, self)._queue_task(host, task, task_vars, play_context)

        start = self._offset_scheduler.reserve(task._uuid, gap, jitter)
        delay = start - self._offset_scheduler.now()
        if delay <= 0:
            return super(StrategyModule, self)._queue_task(host, task, task_vars, play_context)

        # The free strategy has already advanced this host past the task, so keep the host blocked until the
        # task really runs; otherwise free would hand the host its next task first. (ansible-core <= 2.21 sets
        # this before calling _queue_task; later versions set it inside StrategyBase._queue_task.)
        self._blocked_hosts[host.name] = True
        heapq.heappush(self._deferred, (start, next(self._deferred_seq), host, task, task_vars, play_context))
        display.vv("time_offset: starting '%s' in %.2fs" % (task.get_name(), delay), host=host.name)

    def _process_pending_results(self, iterator, one_pass=False, max_passes=None):
        # free calls this on every pass of its loop, which makes it the place to release tasks whose slot has come
        self._release_due_tasks()
        return super(StrategyModule, self)._process_pending_results(iterator, one_pass=one_pass, max_passes=max_passes)

    def _release_due_tasks(self):
        now = self._offset_scheduler.now()
        while self._deferred and self._deferred[0][0] <= now and not self._tqm._terminated:
            dummy, dummy, host, task, task_vars, play_context = heapq.heappop(self._deferred)
            super(StrategyModule, self)._queue_task(host, task, task_vars, play_context)

    def _get_offset_settings(self, task, task_vars):
        raw = dict((name, task_vars.get(name, 0)) for name in (GAP_VAR, JITTER_VAR))
        if not any(raw.values()):
            return 0.0, 0.0

        templar = TemplateEngine(loader=self._loader, variables=task_vars)
        values = []
        for name, value in raw.items():
            try:
                value = float(templar.template(value))
            except Exception as ex:
                raise AnsibleError("'%s' must be a number of seconds, got %r." % (name, value)) from ex
            if value < 0:
                raise AnsibleError("'%s' must not be negative, got %r." % (name, value))
            values.append(value)
        return values[0], values[1]
