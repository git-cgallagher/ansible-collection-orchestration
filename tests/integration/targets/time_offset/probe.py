"""Record when a task actually started on a host, then hold the task open for a while."""
from __future__ import annotations

import sys
import time

log, host, label, hold = sys.argv[1:5]
with open(log, 'a') as f:
    f.write('%s %s %r\n' % (label, host, time.time()))
time.sleep(float(hold))
