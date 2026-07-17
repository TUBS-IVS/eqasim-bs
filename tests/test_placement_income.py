import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.placement_income import (
    INCOME_LABEL_BOUNDS_EUR, label_expected_eur, draw_own_income_eur,
    donor_expected_income_eur,
)
from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP, INCOME_GROUP_MIDPOINT_EUR
from braunschweig.popsim.income_kreis_control import INCOME_MIN_EUR, INCOME_OPEN_TOP_MAX_EUR


def test_label_bounds_cover_all_codebook_labels():
    assert set(INCOME_LABEL_BOUNDS_EUR) == set(INCOME_CLASS_BY_GROUP.values())


def test_closed_label_bounds_match_committed_midpoints():
    # The codebook ranges and the committed midpoints must be two views of the SAME
    # brackets: (low+high)/2 == midpoint for every closed bracket. under_500 is skipped
    # here only because its committed midpoint (250) is checked implicitly and its
    # EXPECTED value differs by design (the draw floors at INCOME_MIN_EUR=100, so
    # label_expected_eur returns 300; covered by test_expected_eur_matches_draw_in_expectation).
    for group, label in INCOME_CLASS_BY_GROUP.items():
        low, high = INCOME_LABEL_BOUNDS_EUR[label]
        if high is None or label == "under_500":
            continue
        assert (low + high) / 2.0 == pytest.approx(INCOME_GROUP_MIDPOINT_EUR[group])


def test_draw_respects_own_bracket_bounds_and_nan():
    rng = np.random.RandomState(7)
    labels = pd.Series(["900_1500"] * 200 + ["over_7000"] * 200 + [np.nan] * 3)
    eur = draw_own_income_eur(labels, rng)
    closed = eur[:200]
    assert (closed >= 900).all() and (closed < 1500).all()
    top = eur[200:400]
    assert (top >= 7000).all() and (top <= INCOME_OPEN_TOP_MAX_EUR).all()
    assert np.isnan(eur[400:]).all()
    assert (eur[:400] >= INCOME_MIN_EUR).all()


def test_draw_is_deterministic_per_seed():
    labels = pd.Series(["2000_2600"] * 50)
    a = draw_own_income_eur(labels, np.random.RandomState(11))
    b = draw_own_income_eur(labels, np.random.RandomState(11))
    np.testing.assert_array_equal(a, b)


def test_expected_eur_matches_draw_in_expectation():
    rng = np.random.RandomState(3)
    exp = label_expected_eur()
    labels = pd.Series(["over_7000"] * 200_000)
    eur = draw_own_income_eur(labels, rng)
    assert eur.mean() == pytest.approx(exp["over_7000"], rel=0.02)
    # Closed bracket: uniform mean == (max(low, INCOME_MIN_EUR)+high)/2.
    assert exp["under_500"] == pytest.approx((INCOME_MIN_EUR + 500.0) / 2.0)


def test_donor_expected_income_maps_hheink_and_flags_missing():
    donors = pd.DataFrame({"H_ID": [1, 2, 3], "hheink_gr1": [3, 15, 99]})
    s = donor_expected_income_eur(donors)
    assert s.loc[1] == pytest.approx(label_expected_eur()["900_1500"])
    assert s.loc[2] == pytest.approx(label_expected_eur()["over_7000"])
    assert np.isnan(s.loc[3])


def test_unknown_nonnull_labels_warn_and_stay_nan(caplog):
    import logging
    rng = np.random.RandomState(5)
    labels = pd.Series(["900_1500", "bogus_label", np.nan, "keine Angabe", "900_1500"])
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.placement_income"):
        eur = draw_own_income_eur(labels, rng)
    assert np.isnan(eur[[1, 2, 3]]).all()
    assert (~np.isnan(eur[[0, 4]])).all()
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("2/5" in m for m in warning_messages)


from braunschweig.popsim.control_spec import CatalogControl, GEO_100M, GEO_KREIS
from braunschweig.popsim.placement_income import donor_control_signatures


def _mini_seed():
    hh = pd.DataFrame({
        "H_ID": [1, 2, 3, 4],
        "H_GEW": [1.0, 1.0, 1.0, 1.0],
        "H_GR": [2, 2, 2, 1],
        "H_MIETE": [1, 1, 2, 1],
    })
    pp = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 3, 3, 4],
        "HP_ALTER": [30, 35, 31, 36, 40, 41, 70],
        "HP_SEX": [1, 2, 1, 2, 1, 2, 2],
    })
    return hh, pp


def _mini_controls():
    return [
        CatalogControl(name="hh2", geography=GEO_100M, seed_table="households", importance=1000,
                       census_source=("hh2",), seed_expressions={"mid": "(households.H_GR == 2)"}),
        CatalogControl(name="renter", geography=GEO_100M, seed_table="households", importance=1000,
                       census_source=("renter",), seed_expressions={"mid": "(households.H_MIETE == 1)"}),
        CatalogControl(name="m3039", geography=GEO_100M, seed_table="persons", importance=1000,
                       census_source=("m3039",),
                       seed_expressions={"mid": "(persons.HP_ALTER > 29)&(persons.HP_ALTER < 40)&(persons.HP_SEX==1)"}),
    ]


def test_signatures_group_identical_donors_and_split_different_ones():
    hh, pp = _mini_seed()
    sig = donor_control_signatures(_mini_controls(), hh, pp)
    # Donors 1+2: size-2, renter, one male 30-39 member each -> identical signature.
    assert sig.loc[1] == sig.loc[2]
    # Donor 3 is an owner (H_MIETE==2); donor 4 is size-1 -> both differ.
    assert sig.loc[3] != sig.loc[1]
    assert sig.loc[4] != sig.loc[1]
    # Person control counted as member COUNT: donor 1 has exactly one matching member.
    assert sig.loc[1][2] == 1 and sig.loc[4][2] == 0


def test_signatures_fail_fast_on_inexpressible_control():
    hh, pp = _mini_seed()
    bad = [CatalogControl(name="x", geography=GEO_KREIS, seed_table="households", importance=1000,
                          census_source=("x",), seed_expressions={"mid": None})]
    with pytest.raises(ValueError):
        donor_control_signatures(bad, hh, pp)


def test_signatures_fail_fast_on_broken_expression():
    hh, pp = _mini_seed()
    broken = [CatalogControl(name="x", geography=GEO_100M, seed_table="households", importance=1000,
                             census_source=("x",), seed_expressions={"mid": "(households.NO_SUCH == 1)"})]
    with pytest.raises(ValueError):
        donor_control_signatures(broken, hh, pp)


from braunschweig.popsim.placement_income import reallocate_slots, slots_kreis_stats


def _two_kreis_slots():
    # Signature group A: donors 1 (poor, 1000 EUR) and 2 (rich, 5000 EUR), 2 clones each,
    # split across Kreise. Group B: donor 3 alone (no freedom).
    slots = pd.DataFrame({
        "H_ID":  [1, 2, 1, 2, 3, 3],
        "ars5":  ["03101", "03101", "03102", "03102", "03101", "03102"],
    })
    signatures = pd.Series({1: ("a",), 2: ("a",), 3: ("b",)})
    income = pd.Series({1: 1000.0, 2: 5000.0, 3: 3000.0})
    return slots, signatures, income


def test_reallocation_moves_means_toward_targets_and_preserves_invariants():
    slots, signatures, income = _two_kreis_slots()
    # 03101 should become poorer (rf 0.8), 03102 richer (rf 1.2).
    assignment, diag = reallocate_slots(
        slots, signatures=signatures, expected_income_eur=income,
        target_factor={"03101": 0.8, "03102": 1.2},
    )
    out = slots.assign(H_ID=assignment.to_numpy())
    # Invariant 1: per-donor clone counts unchanged.
    pd.testing.assert_series_equal(
        out["H_ID"].value_counts().sort_index(),
        slots["H_ID"].value_counts().sort_index(),
    )
    # Invariant 2: per-(Kreis, signature) slot counts unchanged (controls preserved).
    def comp(df):
        return df.assign(sig=df["H_ID"].map(signatures)).groupby(["ars5", "sig"]).size()
    pd.testing.assert_series_equal(comp(out).sort_index(), comp(slots).sort_index())
    # Direction: 03101 mean decreased, 03102 increased vs before.
    before = slots.assign(y=slots["H_ID"].map(income)).groupby("ars5")["y"].mean()
    after = out.assign(y=out["H_ID"].map(income)).groupby("ars5")["y"].mean()
    assert after["03101"] < before["03101"]
    assert after["03102"] > before["03102"]
    assert diag["n_moved"] > 0 and diag["kreis_realized_after"]["03101"] < diag["kreis_realized_after"]["03102"]


def test_single_kreis_is_a_noop():
    slots = pd.DataFrame({"H_ID": [1, 2], "ars5": ["03101", "03101"]})
    signatures = pd.Series({1: ("a",), 2: ("a",)})
    income = pd.Series({1: 1000.0, 2: 5000.0})
    assignment, diag = reallocate_slots(
        slots, signatures=signatures, expected_income_eur=income, target_factor={"03101": 1.0})
    np.testing.assert_array_equal(assignment.to_numpy(), slots["H_ID"].to_numpy())
    assert diag["n_moved"] == 0


def test_all_singleton_groups_is_a_noop_with_full_no_freedom_share():
    slots = pd.DataFrame({"H_ID": [1, 2], "ars5": ["03101", "03102"]})
    signatures = pd.Series({1: ("a",), 2: ("b",)})
    income = pd.Series({1: 1000.0, 2: 5000.0})
    assignment, diag = reallocate_slots(
        slots, signatures=signatures, expected_income_eur=income,
        target_factor={"03101": 0.5, "03102": 1.5})
    np.testing.assert_array_equal(assignment.to_numpy(), slots["H_ID"].to_numpy())
    assert diag["no_freedom_slot_share"] == pytest.approx(1.0)


def test_nan_income_donors_stay_allocatable_but_excluded_from_means():
    slots = pd.DataFrame({"H_ID": [1, 2, 9, 9], "ars5": ["03101", "03102", "03101", "03102"]})
    signatures = pd.Series({1: ("a",), 2: ("a",), 9: ("a",)})
    income = pd.Series({1: 1000.0, 2: 5000.0, 9: np.nan})
    assignment, diag = reallocate_slots(
        slots, signatures=signatures, expected_income_eur=income,
        target_factor={"03101": 0.8, "03102": 1.2})
    assert (assignment.to_numpy() == 9).sum() == 2  # clone count preserved
    assert diag["nan_income_slot_share"] == pytest.approx(0.5)


def test_reallocation_is_deterministic():
    slots, signatures, income = _two_kreis_slots()
    a1, _ = reallocate_slots(slots, signatures=signatures, expected_income_eur=income,
                             target_factor={"03101": 0.8, "03102": 1.2})
    a2, _ = reallocate_slots(slots, signatures=signatures, expected_income_eur=income,
                             target_factor={"03101": 0.8, "03102": 1.2})
    pd.testing.assert_series_equal(a1, a2)


def test_slots_kreis_stats_shape():
    slots = pd.DataFrame({"H_ID": [1, 1, 2], "ars5": ["03101", "03101", "03102"]})
    donors = pd.DataFrame({"H_ID": [1, 2], "H_GR": [2, 4]})
    stats = slots_kreis_stats(slots, donors)
    assert list(stats.columns) == ["ars5", "mean_size", "hh_count"]
    assert stats.set_index("ars5").loc["03101", "hh_count"] == 2
    assert stats.set_index("ars5").loc["03101", "mean_size"] == pytest.approx(2.0)
