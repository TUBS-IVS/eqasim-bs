"""Seed the committed student-share-by-age reference CSV (Task A3b).

The ``studies`` person attribute is synthesised in
``braunschweig.ipf.attributed`` from a per-age-band education-participation
share (probability that a person of that age is currently in formal education:
school, vocational training/Ausbildung, or higher education). The share is
deliberately NOT derived from the later campus assignment in the
education-gravity stage, which would create a chicken-egg dependency; it is an
exogenous demographic input.

Provenance of the pinned shares
-------------------------------
Destatis "Bildungsbeteiligung der Bevoelkerung" / Mikrozensus 2023, broad
education-participation quotas by single age, aggregated to contiguous bands:

- ages 6-15  : compulsory schooling           -> 1.00
- ages 16-17 : Sekundarstufe II / Ausbildung  -> 0.95
- age  18    : upper secondary / Ausbildung    -> 0.80
- age  19    : Ausbildung / Studienbeginn      -> 0.62
- ages 20-21 : Studium / Ausbildung            -> 0.55
- ages 22-24 : Studium                         -> 0.42
- ages 25-29 : Studium (late) / Weiterbildung  -> 0.16
- ages 30-34 : residual tertiary / training    -> 0.04
- ages 35+   : negligible formal education      -> 0.00

The bands are contiguous and cover all ages from the minimum schooling age (6)
to the open tail (200), so every person maps to exactly one band. Ages below 6
fall outside the table and receive a share of 0.0 (handled in the loader's
``share_for_age``), which is correct for the pre-school cohort whose education
activity is Kita, not "studies".

Re-run this script to regenerate the CSV; do not hand-edit the values. Hard-coding
the percentages elsewhere in Python is prohibited (project CLAUDE.md rule).

Usage: python scripts/seed_student_share_by_age.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# (age_lower_inclusive, age_upper_exclusive, student_share)
_PINNED = [
    (6, 16, 1.00),
    (16, 18, 0.95),
    (18, 19, 0.80),
    (19, 20, 0.62),
    (20, 22, 0.55),
    (22, 25, 0.42),
    (25, 30, 0.16),
    (30, 35, 0.04),
    (35, 200, 0.00),
]

OUTPUT_PATH = Path(
    "eqasim-data/data/braunschweig/mikrozensus/student_share_by_age.csv")


def main():
    df = pd.DataFrame(
        _PINNED, columns=["age_lower", "age_upper", "student_share"])
    # Sanity checks: contiguous, ordered, shares in [0, 1].
    assert (df["age_lower"] < df["age_upper"]).all()
    assert (df["age_upper"].values[:-1] == df["age_lower"].values[1:]).all()
    assert (df["student_share"] >= 0).all() and (df["student_share"] <= 1).all()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"[seed_student_share_by_age] wrote {OUTPUT_PATH} ({len(df)} bands)")


if __name__ == "__main__":
    main()
