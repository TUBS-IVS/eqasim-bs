"""Runs braunschweig.popsim.mid.donor.load_completed_donor on the raw MiD 2023 files and prints its
invalid-household log line (run manifest prod100-expected-message-levels-2026-11-04). Reads the raw files
and writes nothing.

Usage: python felix_real_loader_run.py <repo root> <mid2023_raw directory>
"""
import logging
import sys

import numpy as np

repo, raw = sys.argv[1], sys.argv[2]
sys.path.insert(0, repo)

from braunschweig.popsim import mid  # noqa: E402

records = []


class Collect(logging.Handler):
    def emit(self, record):
        records.append((record.levelname, record.getMessage()))


logging.getLogger().addHandler(Collect())
logging.getLogger().setLevel(logging.INFO)
households, persons, completeness, completion = mid.load_completed_donor(
    raw, completion_rng=np.random.RandomState(0))
print("households %d, persons %d after the day filter and the member completion" % (len(households), len(persons)))
for level, message in records:
    if "[mid.load_completed_donor]" in message:
        print("%-7s %s" % (level, message))
