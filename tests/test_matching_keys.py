"""Tests for optimization step (1): richer, behaviourally-relevant statistical
matching keys.

The legacy Braunschweig matching keyed HTS donors on ``[sex, age_class,
has_license, studies]`` only. Step (1) adds the *exogenous* demographic anchors
that are genuinely available on BOTH sides at matching time and drive daily
mobility:

- ``employed``                -> commute vs. no commute (bool, native both sides)
- ``household_size_class``     -> trip generation (binned, native both sides)
- ``socioprofessional_class``  -> activity pattern (int, native both sides)

``car_availability`` / ``has_pt_subscription`` / ``economic_status`` are
intentionally NOT added: at the matching stage they are still placeholders
(``number_of_cars == 1`` for everyone, ``has_pt_subscription == False``) because
their real values are derived downstream in ``braunschweig.synthesis.population.
enriched`` -- matching on them would be matching on invented data.

These tests cover the pure helpers and the matching behaviour on small synthetic
frames (no full pipeline, deterministic).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from synthesis.population.matched import (  # noqa: E402
    household_size_class,
    kernel_weighted_sample,
    statistical_matching,
    urban_class_from_urban_type,
)
from braunschweig.data.bbsr.regiostar import (  # noqa: E402
    regiostar2_label,
    urban_class_by_commune,
)


class _DummyProgress:
    """Minimal stand-in for the synpp progress handle used by
    ``statistical_matching`` (only ``.update(n)`` is called)."""

    def __init__(self):
        self.total = 0

    def update(self, n):
        self.total += int(n)


def test_household_size_class_caps_at_five():
    # 1..4 stay; 5, 6, 7, ... collapse into the open-ended "5 or more" class.
    out = household_size_class(pd.Series([1, 2, 3, 4, 5, 6, 9]))
    assert list(out) == [1, 2, 3, 4, 5, 5, 5]


def test_household_size_class_accepts_string_input():
    # The synthetic target carries ``household_size`` as a string
    # (braunschweig.ipf.attributed: df["household_size"] = hh_size.astype(str)),
    # while the HTS side is integer NPERS. The class helper must harmonise both.
    out = household_size_class(pd.Series(["1", "3", "8"]))
    assert list(out) == [1, 3, 5]


def test_regiostar2_collapses_rs7_into_urban_rural():
    # Official RegioStaR-2 (BMVI/BBSR) collapse: Stadtregion 71-74 -> urban,
    # Laendliche Region 75-77 -> rural. The "(laendlich)" suffix on RS7 75-77
    # labels confirms the split.
    assert [regiostar2_label(c) for c in (71, 72, 73, 74)] == ["urban"] * 4
    assert [regiostar2_label(c) for c in (75, 76, 77)] == ["rural"] * 3


def test_urban_class_from_urban_type_crosswalk():
    # FR unite urbaine (UU2010) classes: ville-centre / banlieue / isolated city
    # all lie inside an urban unit -> "urban"; "none" (hors UU, rural) -> "rural".
    # The label set matches the German RegioStaR-2 side so the two can be matched.
    out = urban_class_from_urban_type(
        pd.Series(["central_city", "suburb", "isolated_city", "none"]))
    assert list(out) == ["urban", "urban", "urban", "rural"]


def test_urban_class_by_commune_maps_via_ags8_and_rs7():
    # rs7 lookup is keyed by AGS-8. The helper must accept both 8-digit AGS and
    # 12-digit ARS commune ids (ars_to_ags8 normalises), and return RegioStaR-2
    # labels. A commune absent from the lookup yields <NA> (observable, no silent
    # default to "rural").
    rs7_by_ags8 = {"03101000": 72, "03154007": 77}
    # ARS-12 -> AGS-8 keeps positions [0:5] + [9:12], so 03154 + VG(4) + 007.
    out = urban_class_by_commune(
        pd.Series(["03101000", "031540099007", "09999999"]),
        rs7_by_ags8,
    )
    assert out.iloc[0] == "urban"          # 03101000 -> RS7 72 -> urban
    assert out.iloc[1] == "rural"          # 12-digit ARS -> AGS-8 03154007 -> 77
    assert pd.isna(out.iloc[2])            # unmapped commune -> <NA>


def test_attach_urban_class_target_side():
    # Target-side integration helper: adds an ``urban_class`` column to the
    # synthetic population from commune_id -> RegioStaR-2, filling communes with
    # no RS7 with the explicit "unknown" sentinel (observable, never a silent
    # urban/rural default).
    from braunschweig.ipf.attributed import attach_urban_class
    df = pd.DataFrame({"commune_id": ["03101000", "03154007", "09999999"]})
    df_regiostar = pd.DataFrame({
        "commune_id": ["03101000", "03154007"],
        "regiostar7": [72, 77],
    })
    out = attach_urban_class(df.copy(), df_regiostar)
    assert list(out["urban_class"]) == ["urban", "rural", "unknown"]


def test_kernel_weighted_sample_prefers_closer_donors():
    # Optimization step (4): within-cell kNN-style similarity weighting. Two
    # equal-weight donors at continuous values 0.0 and 10.0; 1000 targets all at
    # 0.0 must overwhelmingly draw the near donor (index 0) under a tight kernel.
    donor_weights = np.array([1.0, 1.0])
    donor_values = np.array([0.0, 10.0])
    target_values = np.zeros(1000)
    rng = np.random.RandomState(0)
    uniform = rng.random_sample(1000)
    idx = kernel_weighted_sample(
        uniform, donor_weights, donor_values, target_values, bandwidth=1.0)
    near_share = float(np.mean(idx == 0))
    assert near_share > 0.95


def test_kernel_weighted_sample_reduces_to_weights_when_equidistant():
    # When every donor is equidistant from the target, the kernel factor is
    # constant and selection reduces to the survey-weight CDF: a donor with 3x
    # the weight is drawn ~3x as often.
    donor_weights = np.array([1.0, 3.0])
    donor_values = np.array([5.0, 5.0])      # both equidistant from target 0
    target_values = np.zeros(4000)
    rng = np.random.RandomState(1)
    uniform = rng.random_sample(4000)
    idx = kernel_weighted_sample(
        uniform, donor_weights, donor_values, target_values, bandwidth=1.0)
    heavy_share = float(np.mean(idx == 1))
    assert 0.70 < heavy_share < 0.80          # ~0.75


def test_statistical_matching_similarity_prefers_age_close_donors():
    """With within-cell similarity on ``age``, young targets in a single cell
    should receive young donors far more often than under the weight-only draw."""
    # One cell (all same sex), two equally-weighted donors: young (20) and old (60).
    df_source = pd.DataFrame({
        "hts_id": [10, 11],
        "person_weight": [1.0, 1.0],
        "sex": ["male", "male"],
        "age": [20, 60],
    })
    # 400 young targets in the same cell.
    df_target = pd.DataFrame({
        "person_id": list(range(400)),
        "sex": ["male"] * 400,
        "age": [20] * 400,
    })

    _, _ = statistical_matching(  # smoke: also exercises the off path determinism
        _DummyProgress(), df_source, "hts_id", "person_weight",
        df_target.copy(), "person_id", columns=["sex"],
        random_seed=7, minimum_observations=1)

    assignment, _ = statistical_matching(
        _DummyProgress(), df_source, "hts_id", "person_weight",
        df_target.copy(), "person_id", columns=["sex"],
        random_seed=7, minimum_observations=1,
        similarity_column="age", similarity_bandwidth=5.0, similarity_max_donors=2000)
    merged = assignment.merge(
        df_source[["hts_id", "age"]].rename(columns={"age": "donor_age"}), on="hts_id")
    young_donor_share = float((merged["donor_age"] == 20).mean())
    # Bandwidth 5 y, gap 40 y -> the old donor is exp(-8) ~ 0.03% as likely.
    assert young_donor_share > 0.98


def test_statistical_matching_keys_donor_by_employed():
    """When ``employed`` is a matching key and donors separate cleanly by it,
    every target person must receive a donor with the SAME ``employed`` value."""
    # Every (sex, employed) combination is represented so the full-level match is
    # always feasible (no relaxing is forced for any target cell).
    df_source = pd.DataFrame({
        "hts_id": [10, 11, 12, 13, 14, 15, 16, 17],
        "person_weight": [1.0] * 8,
        "sex": ["male", "male", "female", "female",
                "male", "male", "female", "female"],
        "employed": [True, True, False, False,
                     False, False, True, True],
    })
    df_target = pd.DataFrame({
        "person_id": [0, 1, 2, 3, 4, 5],
        "sex": ["male", "female", "male", "female", "male", "female"],
        "employed": [True, True, False, False, True, False],
    })

    df_assignment, levels = statistical_matching(
        _DummyProgress(),
        df_source, "hts_id", "person_weight",
        df_target.copy(), "person_id",
        columns=["sex", "employed"],
        random_seed=42, minimum_observations=1,
    )

    merged = df_target.merge(df_assignment, on="person_id").merge(
        df_source[["hts_id", "employed"]].rename(
            columns={"employed": "donor_employed"}), on="hts_id")
    # Every donor's employment status equals the target person's.
    assert (merged["employed"] == merged["donor_employed"]).all()
