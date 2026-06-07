"""Student-share-by-age reference loader (Task A3b).

Loads the committed CSV produced by ``scripts/seed_student_share_by_age.py``:
the probability that a person of a given age is currently in formal education
(school / vocational training / higher education). Consumed by
``braunschweig.ipf.attributed.derive_studies`` to synthesise the ``studies``
person attribute.

Schema of the CSV (one row per contiguous age band):
    age_lower      inclusive lower age bound of the band
    age_upper      exclusive upper age bound of the band
    student_share  probability in [0, 1] that a person in the band studies

Provenance: Destatis Bildungsbeteiligung / Mikrozensus 2023 (see the seed
script). The bands are contiguous and span [6, 200); ages outside the table
(notably the pre-school 0-5 cohort) receive a share of 0.0.
"""
from __future__ import annotations

import os

import pandas as pd

# CSV location relative to ``data_path`` (the eqasim-data/data tree).
DEFAULT_REL_PATH = "braunschweig/mikrozensus/student_share_by_age.csv"

# Repository-relative absolute fallback so the table can also be loaded outside
# the synpp ``data_path`` context (e.g. in unit tests via ``load_default_table``).
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                          "..", "..", ".."))
_DEFAULT_ABS_PATH = os.path.join(
    _REPO_ROOT, "eqasim-data", "data",
    "braunschweig", "mikrozensus", "student_share_by_age.csv")


def build_share_lookup(df):
    """Return a list of ``(age_lower, age_upper, student_share)`` tuples sorted by
    ``age_lower`` for fast band lookup. ``df`` must carry the three CSV columns."""
    ordered = df.sort_values("age_lower").reset_index(drop=True)
    return list(zip(
        ordered["age_lower"].astype(int).tolist(),
        ordered["age_upper"].astype(int).tolist(),
        ordered["student_share"].astype(float).tolist(),
    ))


def share_for_age(age, lookup):
    """Return the student share for a single ``age`` from a ``build_share_lookup``
    result. Ages outside every band (e.g. the pre-school cohort below the first
    band's lower bound) return 0.0 -- those persons do not "study" in the sense of
    the attribute (their education activity is Kita, handled elsewhere)."""
    a = int(age)
    for lower, upper, share in lookup:
        if lower <= a < upper:
            return share
    return 0.0


def load_default_table():
    """Load the committed reference table from the repository-relative path.

    Used by unit tests and by callers without a synpp ``data_path`` context.
    Raises a clear error if the CSV is missing (run the seed script)."""
    if not os.path.exists(_DEFAULT_ABS_PATH):
        raise RuntimeError(
            f"student_share_by_age.csv missing at {_DEFAULT_ABS_PATH}: "
            "run scripts/seed_student_share_by_age.py")
    return pd.read_csv(_DEFAULT_ABS_PATH)


def configure(context):
    context.config("data_path")
    context.config("student_share_path", DEFAULT_REL_PATH)


def _resolve_path(context):
    return os.path.join(context.config("data_path"),
                        context.config("student_share_path"))


def execute(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"student_share_by_age.csv missing at {path}: "
            "run scripts/seed_student_share_by_age.py")
    return pd.read_csv(path)


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"student_share_by_age.csv missing at {path}: "
            "run scripts/seed_student_share_by_age.py")
    return os.path.getsize(path)
