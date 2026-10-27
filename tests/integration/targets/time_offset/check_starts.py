"""Assert on the start times recorded by probe.py."""
from __future__ import annotations

import argparse
import collections
import sys


def load(path):
    starts = collections.defaultdict(list)
    with open(path) as f:
        for line in f:
            label, dummy, when = line.split()
            starts[label].append(float(when))
    return starts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('log')
    parser.add_argument('label')
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--min-gap', type=float, help='smallest allowed gap between consecutive starts')
    parser.add_argument('--max-gap', type=float, help='largest allowed gap between consecutive starts')
    parser.add_argument('--max-span', type=float, help='largest allowed time from first to last start')
    parser.add_argument('--overlaps', type=float, metavar='HOLD',
                        help='the second host must start before the first host finished a task held open HOLD seconds')
    parser.add_argument('--expect-collision', type=float, metavar='SECONDS',
                        help='the smallest gap must be below SECONDS (proves the starts were NOT spaced)')
    parser.add_argument('--not-delayed-after', nargs=2, metavar=('LABEL', 'SECONDS'),
                        help='the last start must be within SECONDS of the last start of LABEL')
    args = parser.parse_args()

    all_starts = load(args.log)
    starts = sorted(all_starts[args.label])
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    span = starts[-1] - starts[0] if starts else 0.0
    print('%s: %d starts, span %.3fs, gaps %s' % (args.label, len(starts), span, ', '.join('%.3f' % g for g in gaps)))

    failures = []
    if len(starts) != args.count:
        failures.append('expected %d starts, got %d' % (args.count, len(starts)))
    if args.min_gap is not None and gaps and min(gaps) < args.min_gap:
        failures.append('smallest gap %.3fs is below %.3fs' % (min(gaps), args.min_gap))
    if args.max_gap is not None and gaps and max(gaps) > args.max_gap:
        failures.append('largest gap %.3fs is above %.3fs' % (max(gaps), args.max_gap))
    if args.max_span is not None and span > args.max_span:
        failures.append('span %.3fs is above %.3fs' % (span, args.max_span))
    if args.overlaps is not None and len(starts) > 1 and starts[1] >= starts[0] + args.overlaps:
        failures.append('second host did not start until the first finished: the task ran serially')
    if args.expect_collision is not None and gaps and min(gaps) >= args.expect_collision:
        failures.append('smallest gap %.3fs is not below %.3fs: starts were spaced when they should not be'
                        % (min(gaps), args.expect_collision))
    if args.not_delayed_after:
        label, seconds = args.not_delayed_after[0], float(args.not_delayed_after[1])
        previous_last = max(all_starts[label])
        if starts[-1] - previous_last > seconds:
            failures.append('last start is %.3fs after the last %s start, allowed %.3fs'
                            % (starts[-1] - previous_last, label, seconds))

    for failure in failures:
        print('FAIL: %s' % failure)
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
