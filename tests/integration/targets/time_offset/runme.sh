#!/usr/bin/env bash
# Integration tests for git_cgallagher.orchestration.time_offset.
# Six local hosts record when each task really starts (probe.py); check_starts.py asserts on the spacing.
# Every playbook runs under `timeout` because a host that is blocked but never released hangs instead of failing.
set -eux

HOSTS=6
# Lower bound for a measured gap when the configured gap is 1s. probe.py timestamps a task when its Python
# process starts on the host, which comes a little after Ansible launches it (the moment the plugin actually
# spaces), and that delay varies run to run. Busy CI runners have shown up to ~0.4s of that variation, so allow
# 0.3s below the gap. Unspaced starts land within ~0.2s of each other, so 0.7 still tells the two apart.
MIN_GAP=0.7
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
check "${log}" first --count "${HOSTS}" --min-gap "${MIN_GAP}" --max-gap 1.9 --max-span 8 --overlaps 3
# Hosts are already spread out after the first task, so the next task should not add more delay.
check "${log}" second --count "${HOSTS}" --min-gap "${MIN_GAP}" --not-delayed-after first 4.5
check "${log}" included --count "${HOSTS}"
check "${log}" handler --count "${HOSTS}"

# 2. Gap of 0 behaves like the free strategy: everyone starts together.
log="${log_dir}/nogap.log"; rm -f "${log}"
run play_level.yml -e probe_log="${log}" -e play_gap=0 -e hold=1
check "${log}" first --count "${HOSTS}" --max-span 1.5
check "${log}" handler --count "${HOSTS}"

# 3. Gap plus jitter: every gap lands between the gap and gap + jitter (fractional gaps are covered by the
#    unit tests, where the clock is exact).
log="${log_dir}/jitter.log"; rm -f "${log}"
run play_level.yml -e probe_log="${log}" -e play_gap=1 -e play_jitter=1 -e hold=1
check "${log}" first --count "${HOSTS}" --min-gap "${MIN_GAP}" --max-gap 2.6

# 4. Task-level variable: only that task is offset.
log="${log_dir}/task.log"; rm -f "${log}"
run task_level.yml -e probe_log="${log}"
check "${log}" unoffset --count "${HOSTS}" --max-span 1.5
check "${log}" offset --count "${HOSTS}" --min-gap "${MIN_GAP}" --max-gap 1.9

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

# 7. serial batches: the gap holds across batch boundaries, not just within a batch. The task is shorter than
#    the gap, so without carrying start times over, the next batch would start almost at once.
for batch in 3 1; do
    log="${log_dir}/serial${batch}.log"; rm -f "${log}"
    run serial.yml -e probe_log="${log}" -e batch="${batch}"
    check "${log}" batched --count "${HOSTS}" --min-gap "${MIN_GAP}" --max-gap 1.9
done

# 8. time_offset_key: two different tasks share one queue of start slots.
#    Control first: without a key, a host starts task B right after its own task A, while the next host is still
#    waiting a full gap for A, so A and B starts collide.
log="${log_dir}/nokey.log"; rm -f "${log}"
run keyed.yml -e probe_log="${log}" -e key_mode=none
check "${log}" none --count 12 --expect-collision 0.5
#    Shared key: all 12 starts (6 hosts x 2 tasks) are spaced, so the span is about 11 gaps.
log="${log_dir}/sharedkey.log"; rm -f "${log}"
run keyed.yml -e probe_log="${log}" -e key_mode=shared
check "${log}" shared --count 12 --min-gap "${MIN_GAP}" --max-gap 1.9
#    Key templated per host: odd and even hosts resolve to different keys and get independent queues, each 6 starts
#    spaced (span about 5 gaps, not 11).
log="${log_dir}/splitkey.log"; rm -f "${log}"
run keyed.yml -e probe_log="${log}" -e key_mode=split
check "${log}" q0 --count 6 --min-gap "${MIN_GAP}" --max-span 7.5
check "${log}" q1 --count 6 --min-gap "${MIN_GAP}" --max-span 7.5

# 9. A key that isn't a string fails the play with a clear message.
if run keyed.yml -e probe_log="${log}" -e key_mode=shared -e '{"time_offset_key": [1, 2]}' > "${log_dir}/badkey.out" 2>&1; then
    echo "expected the play to fail for a list time_offset_key"
    exit 1
fi
grep "'time_offset_key' must be a string" "${log_dir}/badkey.out"

# 10. A task whose 'when' is already false doesn't take a start slot. The skipped tasks never run, the two hosts
#     that do run the partial task are still spaced, and the real task isn't pushed back by the skipped ones.
log="${log_dir}/when.log"; rm -f "${log}"
run when.yml -e probe_log="${log}"
check "${log}" skipped --count 0
check "${log}" partial --count 2 --min-gap "${MIN_GAP}"
check "${log}" real --count "${HOSTS}" --min-gap "${MIN_GAP}" --not-delayed-after begin 10

# 11. throttle + gap: throttle caps how many run at once (2 here, each held 3s) and every start, including the ones
#     throttle held back past their slot, is still at least a gap after the one before. Before the fix, held tasks
#     didn't count toward throttle and all 6 ran at once.
log="${log_dir}/throttle.log"; rm -f "${log}"
run throttle.yml -e probe_log="${log}"
check "${log}" throttled --count "${HOSTS}" --min-gap "${MIN_GAP}" --max-concurrent 2 3

# 12. Lookups in 'when' run once per host, not twice. Direct lookup calls skip the up-front check automatically;
#     an indirect one (through a variable) runs twice unless time_offset_check_when is false. Each run appends one
#     line, so the expected counts are hosts (once) or 2 x hosts (twice).
count_lines() { if [ -f "$1" ]; then wc -l < "$1" | tr -d ' '; else echo 0; fi; }
direct="${log_dir}/lookup_direct.log"; indirect="${log_dir}/lookup_indirect.log"; rm -f "${direct}" "${indirect}"
run lookup.yml -e direct_log="${direct}" -e indirect_log="${indirect}" -e check_when=false
[ "$(count_lines "${direct}")" -eq "${HOSTS}" ] || { echo "direct lookup ran $(count_lines "${direct}") times, expected ${HOSTS}"; exit 1; }
[ "$(count_lines "${indirect}")" -eq "${HOSTS}" ] || { echo "indirect lookup ran $(count_lines "${indirect}") times with the check off, expected ${HOSTS}"; exit 1; }
#     Control: with the up-front check on, the indirect lookup runs twice per host. That proves the opt-out is what
#     made the difference above.
rm -f "${direct}" "${indirect}"
run lookup.yml -e direct_log="${direct}" -e indirect_log="${indirect}" -e check_when=true
[ "$(count_lines "${indirect}")" -eq $((HOSTS * 2)) ] || { echo "indirect lookup ran $(count_lines "${indirect}") times with the check on, expected $((HOSTS * 2))"; exit 1; }

# 13. A time_offset_check_when that isn't a boolean fails the play with a clear message.
if run lookup.yml -e direct_log="${direct}" -e indirect_log="${indirect}" -e check_when=maybe > "${log_dir}/badcheck.out" 2>&1; then
    echo "expected the play to fail for a non-boolean time_offset_check_when"
    exit 1
fi
grep "'time_offset_check_when' must be a boolean" "${log_dir}/badcheck.out"
