"""Tests for Task A3b -- synthesise the ``studies`` person attribute and add it
to the HTS matching keys.

``studies`` was hardcoded to ``False`` in ``braunschweig.ipf.attributed``. It is
now synthesised from age + a regional education-participation share per age band
(``braunschweig.data.education.student_share``, seeded from official Destatis
Bildungsbeteiligung figures -- NOT derived from the later campus assignment, so
there is no chicken-egg with the education-gravity university stage).

With ``reactivate_person_attributes`` ON, ``studies`` is sampled per person and
added to ``matching_attributes`` so students receive student activity-day donors.
With the flag OFF the legacy constant ``False`` is preserved and the matching keys
are unchanged (byte-identical).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.ipf.attributed import derive_studies  # noqa: E402
from braunschweig.data.education.student_share import (  # noqa: E402
    build_share_lookup, share_for_age,
)


# A small synthetic share-by-band table: (lower_inclusive, upper_exclusive, share)
SHARE_BANDS = pd.DataFrame({
    "age_lower": [6, 18, 20, 25, 30],
    "age_upper": [18, 20, 25, 30, 200],
    "student_share": [1.0, 0.6, 0.4, 0.1, 0.0],
})


def test_share_for_age_picks_the_containing_band():
    lookup = build_share_lookup(SHARE_BANDS)
    assert share_for_age(5, lookup) == 0.0     # below schooling age -> no band
    assert share_for_age(10, lookup) == 1.0    # 6..17
    assert share_for_age(19, lookup) == 0.6    # 18..19
    assert share_for_age(22, lookup) == 0.4    # 20..24
    assert share_for_age(27, lookup) == 0.1    # 25..29
    assert share_for_age(40, lookup) == 0.0    # 30+


def test_studies_share_by_band_matches_target_within_tolerance():
    rng = np.random.RandomState(0)
    # 5000 persons per band so the realised share concentrates on the target.
    ages = np.concatenate([
        rng.randint(6, 18, 5000),
        rng.randint(18, 20, 5000),
        rng.randint(20, 25, 5000),
        rng.randint(25, 30, 5000),
        rng.randint(30, 80, 5000),
    ])
    df = pd.DataFrame({"age": ages})
    studies = derive_studies(df["age"], SHARE_BANDS, random_seed=42)
    for _, b in SHARE_BANDS.iterrows():
        mask = (df["age"] >= b["age_lower"]) & (df["age"] < b["age_upper"])
        realised = studies[mask].mean()
        assert abs(realised - b["student_share"]) < 0.03, (
            f"band {b['age_lower']}-{b['age_upper']}: "
            f"realised {realised:.3f} vs target {b['student_share']:.3f}")


def test_studies_is_deterministic_for_a_fixed_seed():
    df = pd.DataFrame({"age": np.arange(6, 90)})
    a = derive_studies(df["age"], SHARE_BANDS, random_seed=7)
    b = derive_studies(df["age"], SHARE_BANDS, random_seed=7)
    assert a.tolist() == b.tolist()


def test_zero_share_band_yields_no_students():
    df = pd.DataFrame({"age": np.full(2000, 50)})
    studies = derive_studies(df["age"], SHARE_BANDS, random_seed=1)
    assert not studies.any()


def test_full_share_band_makes_everyone_a_student():
    df = pd.DataFrame({"age": np.full(2000, 10)})
    studies = derive_studies(df["age"], SHARE_BANDS, random_seed=1)
    assert studies.all()


def test_studies_added_to_matching_keys_when_flag_on():
    from synthesis.population.matched import resolve_matching_columns
    configured = ["sex", "age_class", "has_license"]
    cols = resolve_matching_columns(configured, reactivate_person_attributes=True)
    assert "studies" in cols
    # appended LAST (lowest matching priority) and the configured keys keep order
    assert cols == ["sex", "age_class", "has_license", "studies"]


def test_matching_keys_unchanged_when_flag_off():
    from synthesis.population.matched import resolve_matching_columns
    configured = ["sex", "age_class", "has_license"]
    cols = resolve_matching_columns(configured, reactivate_person_attributes=False)
    assert cols == configured        # byte-identical legacy matching keys
    assert cols is not configured    # but a fresh list (no in-place mutation)


def test_studies_not_duplicated_if_already_configured():
    from synthesis.population.matched import resolve_matching_columns
    configured = ["sex", "studies", "age_class"]
    cols = resolve_matching_columns(configured, reactivate_person_attributes=True)
    assert cols.count("studies") == 1
    assert cols == configured


def test_default_sentinel_still_expands():
    from synthesis.population.matched import (
        resolve_matching_columns, DEFAULT_MATCHING_ATTRIBUTES,
    )
    cols = resolve_matching_columns(["*default*"], reactivate_person_attributes=False)
    assert cols == DEFAULT_MATCHING_ATTRIBUTES


def test_committed_student_share_csv_is_well_formed():
    """The committed reference CSV must load, be normalised (shares in [0, 1]),
    and have contiguous, ordered age bands so every age maps to one band."""
    from braunschweig.data.education import student_share as ss
    df = ss.load_default_table()
    assert {"age_lower", "age_upper", "student_share"} <= set(df.columns)
    assert (df["student_share"] >= 0).all() and (df["student_share"] <= 1).all()
    assert (df["age_lower"] < df["age_upper"]).all()
    ordered = df.sort_values("age_lower").reset_index(drop=True)
    # bands are contiguous (no overlap, no hole between consecutive bands)
    assert (ordered["age_upper"].values[:-1] == ordered["age_lower"].values[1:]).all()
