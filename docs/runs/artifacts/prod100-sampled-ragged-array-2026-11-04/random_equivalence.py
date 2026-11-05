"""Compare two versions of the synthesis.population.sampled stage on many random censuses.

The real production input gives every household the weight 1, so a replay on it never replicates a household 0, 2 or more
times. This script covers that part: random censuses with households of equal and unequal size, whole and fractional
weights (zero included), unsorted household ids, mixed column dtypes and several sampling rates, run through both
modules with the same seed. The output frames are compared exactly (values, index, dtypes) and as pickle bytes
(protocol 4, the protocol the pipeline writes with, because the pickle is what the stage's output hash digests).

    python random_equivalence.py <repo_root> <old_module.py> [trials]

``<repo_root>`` supplies the new module (``synthesis/population/sampled.py``). Exit code 0 when all trials are
identical.
"""
from __future__ import annotations

import importlib.util
import pickle
import sys
import warnings

import numpy as np
import pandas as pd

repo_root, old_path = sys.argv[1], sys.argv[2]
trials = int(sys.argv[3]) if len(sys.argv) > 3 else 500
SEED = 20261104  # drives the random censuses; the stage's own seed is drawn per trial from it


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExecuteContext:
    def __init__(self, source, random_seed, sampling_rate):
        self._source = source
        self._config = {"random_seed": random_seed, "sampling_rate": sampling_rate}

    def config(self, name):
        return self._config[name]

    def stage(self, name):
        assert name == "source", name
        return self._source


def census(rng, kind):
    n_households = int(rng.integers(1, 60))
    if kind == "equal":
        sizes = np.full(n_households, int(rng.integers(1, 5)))
    elif kind == "single":
        n_households, sizes = 1, np.array([int(rng.integers(1, 6))])
    else:
        sizes = rng.choice([1, 2, 3, 4, 5, 6], size=n_households, p=[0.3, 0.3, 0.15, 0.15, 0.07, 0.03])
    weights = np.round(rng.uniform(0, 4, n_households), 3)
    weights[rng.random(n_households) < 0.2] = rng.integers(0, 4)  # whole-number weights, zero included
    household_ids = rng.permutation(n_households) + 1000  # not equal to the positions, not sorted in the input
    rows, person_id = [], 0
    for household_id, size, weight in zip(household_ids, sizes, weights):
        for _ in range(int(size)):
            rows.append({"household_id": int(household_id), "person_id": person_id, "weight": float(weight),
                         "household_size": int(size), "age": int(rng.integers(0, 95)),
                         "income": float(rng.normal(30000, 9000)), "sex": "f" if rng.random() < 0.5 else "m",
                         "employed": bool(rng.random() < 0.5)})
            person_id += 1
    return pd.DataFrame(rows)


def main():
    old = load("sampled_old", old_path)
    new = load("sampled_new", repo_root + "/synthesis/population/sampled.py")
    rng = np.random.default_rng(SEED)
    kinds = ["mixed"] * 3 + ["equal", "single"]
    identical_frames = identical_pickles = rows_total = non_empty = old_warned = new_warned = 0
    for trial in range(trials):
        frame = census(rng, kinds[trial % len(kinds)])
        seed = int(rng.integers(0, 2**31 - 1))
        rate = float(rng.choice([1.0, 1.0, 0.5, 0.1, 0.0]))
        with warnings.catch_warnings(record=True) as caught_old:
            warnings.simplefilter("always")
            out_old = old.execute(ExecuteContext(frame, seed, rate))
        with warnings.catch_warnings(record=True) as caught_new:
            warnings.simplefilter("always")
            out_new = new.execute(ExecuteContext(frame, seed, rate))
        old_warned += any(w.category.__name__ == "VisibleDeprecationWarning" for w in caught_old)
        new_warned += any(w.category.__name__ == "VisibleDeprecationWarning" for w in caught_new)
        pd.testing.assert_frame_equal(out_old, out_new, check_exact=True, check_dtype=True, check_index_type=True)
        identical_frames += 1
        if pickle.dumps(out_old, protocol=4) == pickle.dumps(out_new, protocol=4):
            identical_pickles += 1
        else:
            print("PICKLE DIFFERS in trial", trial)
        rows_total += len(out_new)
        non_empty += len(out_new) > 0
    print(f"trials={trials} frames identical={identical_frames} pickle bytes identical={identical_pickles} "
          f"non-empty outputs={non_empty} output rows={rows_total}")
    print(f"trials in which the old module raised the numpy ragged-array warning={old_warned}, "
          f"the new module={new_warned}")
    return 0 if identical_frames == trials == identical_pickles else 1


if __name__ == "__main__":
    sys.exit(main())
