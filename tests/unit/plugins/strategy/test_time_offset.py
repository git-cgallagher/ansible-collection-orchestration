# Copyright (c) 2026, Chris Gallagher
# GNU General Public License v3.0+ (see LICENSE or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import annotations

from ansible_collections.git_cgallagher.orchestration.plugins.strategy.time_offset import OffsetScheduler


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def make_scheduler(now=100.0, rng=0.0):
    clock = FakeClock(now)
    return clock, OffsetScheduler(clock=clock, rng=lambda: rng)


def test_first_host_starts_immediately():
    clock, scheduler = make_scheduler()
    assert scheduler.reserve('task', gap=2) == 100.0


def test_hosts_arriving_together_are_spaced_by_the_gap():
    clock, scheduler = make_scheduler()
    starts = [scheduler.reserve('task', gap=2) for dummy in range(4)]
    assert starts == [100.0, 102.0, 104.0, 106.0]


def test_host_arriving_after_the_gap_is_not_delayed():
    clock, scheduler = make_scheduler()
    scheduler.reserve('task', gap=2)
    clock.now = 105.0
    assert scheduler.reserve('task', gap=2) == 105.0


def test_host_arriving_inside_the_gap_waits_only_for_the_remainder():
    clock, scheduler = make_scheduler()
    scheduler.reserve('task', gap=2)
    clock.now = 101.5
    assert scheduler.reserve('task', gap=2) == 102.0


def test_tasks_are_spaced_independently():
    clock, scheduler = make_scheduler()
    assert scheduler.reserve('task-a', gap=2) == 100.0
    assert scheduler.reserve('task-b', gap=2) == 100.0
    assert scheduler.reserve('task-a', gap=2) == 102.0


def test_jitter_adds_up_to_its_value_to_each_gap():
    clock, scheduler = make_scheduler(rng=0.5)
    starts = [scheduler.reserve('task', gap=1, jitter=2) for dummy in range(3)]
    assert starts == [100.0, 102.0, 104.0]


def test_jitter_alone_staggers_starts():
    clock, scheduler = make_scheduler(rng=0.25)
    starts = [scheduler.reserve('task', gap=0, jitter=4) for dummy in range(3)]
    assert starts == [100.0, 101.0, 102.0]


def test_fractional_gap():
    clock, scheduler = make_scheduler()
    starts = [scheduler.reserve('task', gap=0.25) for dummy in range(3)]
    assert starts == [100.0, 100.25, 100.5]


def test_shared_key_spaces_different_tasks_together():
    clock, scheduler = make_scheduler()
    shared = ('key', 'mgmt01')
    starts = [scheduler.reserve(shared, gap=3) for dummy in ('task-a on host1', 'task-b on host2', 'task-a on host3')]
    assert starts == [100.0, 103.0, 106.0]


def test_gap_elapsed_tracks_real_starts_not_reservations():
    clock, scheduler = make_scheduler()
    key = ('task', 'a')
    assert scheduler.gap_elapsed(key, gap=2)
    scheduler.started(key)
    clock.now = 101.0
    assert not scheduler.gap_elapsed(key, gap=2)
    clock.now = 102.0
    assert scheduler.gap_elapsed(key, gap=2)


def test_a_late_start_pushes_back_the_next_one():
    # throttle held the first task past its slot; the next one must still wait a full gap after it really started
    clock, scheduler = make_scheduler()
    key = ('task', 'a')
    assert scheduler.reserve(key, gap=1) == 100.0
    assert scheduler.reserve(key, gap=1) == 101.0
    clock.now = 105.0
    scheduler.started(key)
    assert not scheduler.gap_elapsed(key, gap=1)
    clock.now = 106.0
    assert scheduler.gap_elapsed(key, gap=1)
