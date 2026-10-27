#!/usr/bin/env bash
# Integration tests for git_cgallagher.orchestration.time_offset.
# Six local hosts record when each task really starts (probe.py); check_starts.py asserts on the spacing.
# Every playbook runs under `timeout` because a host that is blocked but never released hangs instead of failing.
set -eux

HOSTS=6
log_dir="${OUTPUT_DIR:-$(mktemp -d)}"

run() {
    timeout 300 ansible-playbook -i inventory.ini -f 10 "$@"
}

check() {
    python check_starts.py "$@"
}

# 1. Play-level gap of 1s. Each task is held open for 3s, so a serial run would need 15s+ of starts;
#    the offset run must overlap and spread the starts about 1s apart.
log="${log_dir}/gap.log"; rm -f "${log}"
run play_level.yml -e probe_log="${log}" -e play_gap=1 -e hold=3
check "${log}" first --count "${HOSTS}" --min-gap 0.85 --max-gap 1.9 --max-span 8 --overlaps 3
# Hosts are already spread out after the first task, so the next task should not add more delay.
check "${log}" second --count "${HOSTS}" --min-gap 0.85 --not-delayed-after first 4.5
check "${log}" included --count "${HOSTS}"
check "${log}" handler --count "${HOSTS}"

# 2. Gap of 0 behaves like the free strategy: everyone starts together.
log="${log_dir}/nogap.log"; rm -f "${log}"
run play_level.yml -e probe_log="${log}" -e play_gap=0 -e hold=1
check "${log}" first --count "${HOSTS}" --max-span 1.5
check "${log}" handler --count "${HOSTS}"

# 3. Fractional gap plus jitter: every gap lands between the gap and gap + jitter.
log="${log_dir}/jitter.log"; rm -f "${log}"
run play_level.yml -e probe_log="${log}" -e play_gap=0.5 -e play_jitter=0.5 -e hold=1
check "${log}" first --count "${HOSTS}" --min-gap 0.35 --max-gap 1.6

# 4. Task-level variable: only that task is offset.
log="${log_dir}/task.log"; rm -f "${log}"
run task_level.yml -e probe_log="${log}"
check "${log}" unoffset --count "${HOSTS}" --max-span 1.5
check "${log}" offset --count "${HOSTS}" --min-gap 0.85 --max-gap 1.9

# 5. A value that is not a number fails the play with a clear message.
log="${log_dir}/bad.log"; rm -f "${log}"
if run play_level.yml -e probe_log="${log}" -e play_gap=soon -e hold=0 > "${log_dir}/bad.out" 2>&1; then
    echo "expected the play to fail for a non-numeric time_offset_seconds"
    exit 1
fi
grep "'time_offset_seconds' must be a number of seconds" "${log_dir}/bad.out"

# 6. A negative value fails the play too.
if run play_level.yml -e probe_log="${log}" -e play_gap=-1 -e hold=0 > "${log_dir}/neg.out" 2>&1; then
    echo "expected the play to fail for a negative time_offset_seconds"
    exit 1
fi
grep "'time_offset_seconds' must not be negative" "${log_dir}/neg.out"
