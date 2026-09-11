"""Measure the fine child age band crossing rate of the diary plan match (issue #386).

Runs the completed-donor build's diary-match step on the RAW MiD 2023 B1 delivery in BOTH
arms of the flag and prints the crossing counts. The shared prefix (member completion +
weekend plan match) is byte-identical between the arms -- it never sees the flag -- so it
is computed ONCE and the RNG state captured right after it is replayed into both arms.
That is exactly what two separate ``build_completed_donor`` calls would do, at half the
~1 GB MiD Wege load.

Not part of the pipeline: the measurement script behind ADR-0118, kept so the figures it
produced stay reproducible rather than being quoted from a chat log. Run:

    python scripts/measure_donor_match_fine_child_bands.py --mid-dir <raw MiD CSV dir>

RESULT (2026-09-11, raw MiD 2023 B1 delivery, random_seed 1234, 485,709 donor persons,
74,898 remaps of which 4,673 are 6-13-year-olds; runtime ~93 min):

    fine_child_age_bands = False   2,371 / 4,673 crossings (50.74 %)   levels {0: 74896, 2: 2}
    fine_child_age_bands = True        0 / 4,673 crossings ( 0.00 %)   levels {0: 74896, 2: 2}

Two readings. (1) 50.74 % is what blind drawing from two roughly equally sized halves gives,
i.e. the coarse band carried essentially NO information inside 6-13. (2) The match-level
histograms are IDENTICAL, so the refinement costs no additional ladder relaxation -- the donor
pool holds a same-fine-band donor for practically every child.

SCOPE: this measures the DIARY match only (it was run against the diary-only revision of the
change). The same refinement also applies to member completion and the weekend match, whose
effect is NOT in these numbers.
"""
import argparse
import logging
import sys
import time

import numpy as np

from braunschweig.popsim import completed_donor as cd
from braunschweig.popsim import diary_facts, diary_plan_match, mid
from braunschweig.popsim import seed as seedmod
from braunschweig.popsim import weekend_plan_match

FLAGS = dict(exclude_rbw_legs=True, exclude_holidays=True, drop_leading_arrive_home_leg=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mid-dir", required=True,
                        help="Directory holding MiD2023_Haushalte/Personen/Wege.csv")
    parser.add_argument("--random-seed", type=int, default=1234)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    started = time.time()
    completion_rng = np.random.RandomState(args.random_seed + cd.COMPLETION_RNG_OFFSET)
    households, persons, _completeness, _completion = mid.load_completed_donor(
        args.mid_dir, completion_rng=completion_rng,
        day_filter_values=seedmod.ALL_REPORTING_KERNWO)
    persons, _trace, _report = weekend_plan_match.reassign_weekend_plan_sources(
        households, persons, rng=completion_rng)
    print(f"[measure] completed donor: {len(households)} households / {len(persons)} persons "
          f"({time.time() - started:.0f} s)")

    facts = diary_facts.compute_diary_facts(mid.load_mid_wege(args.mid_dir))
    rng_state = completion_rng.get_state()

    reports = {}
    for fine in (False, True):
        arm_rng = np.random.RandomState()
        arm_rng.set_state(rng_state)
        _out, _diary_trace, report = diary_plan_match.reassign_diaryless_plan_sources(
            persons, persons, facts, rng=arm_rng, hard_employment=True,
            fine_child_age_bands=fine, **FLAGS)
        reports[fine] = report

    print("\n=== issue #386: fine child age band crossings (raw MiD 2023 B1) ===")
    print(f"random_seed                    : {args.random_seed}")
    print(f"split child age range          : {diary_plan_match.SPLIT_CHILD_AGE_RANGE}")
    for fine, report in reports.items():
        denominator = max(report.n_remapped_in_split_child_band, 1)
        print(f"\nfine_child_age_bands = {fine}")
        print(f"  persons                      : {report.n_persons}")
        print(f"  remapped                     : {report.n_remapped} "
              f"({100.0 * report.share_remapped:.2f} %)")
        print(f"  remapped 6-13-year-olds      : {report.n_remapped_in_split_child_band}")
        print(f"  ...crossing a fine band      : {report.n_crossed_fine_child_age_band} "
              f"({100.0 * report.n_crossed_fine_child_age_band / denominator:.2f} % of them)")
        print(f"  match levels                 : {report.match_level_counts}")
        print(f"  employment boundary crossings: {report.n_crossed_employment_boundary}")
    print(f"\n[measure] total {time.time() - started:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
