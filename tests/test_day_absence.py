import hashlib

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.commute_day import plan_replacement
from braunschweig.synthesis.day_absence import absence as D


def _reference(p_band=0.10, p_size=None, p_person=None):
    p_size = p_size if p_size is not None else {1: 0.5, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    # p_absent_person_by_size is a REPORTING-only reference (issue #388); NaN by default in tests
    # that do not exercise the by_size_class reference_rate/delta_pp diagnostics.
    p_person = p_person if p_person is not None else {k: float("nan") for k in range(1, 6)}
    return D.AbsenceReference(p_absent_by_band={b: p_band for b in D.AGE_BAND_LABELS},
                              p_all_absent_by_size=p_size, p_absent_person_by_size=p_person)


def _persons(n_households=400):
    # 200 singles aged 40, 200 couples (35, 36): singles feed the household stage, couples only the residual.
    rows = []
    pid = 0
    for h in range(n_households):
        ages = [40] if h % 2 == 0 else [35, 36]
        for age in ages:
            rows.append({"person_id": pid, "household_id": h, "age": age}); pid += 1
    return pd.DataFrame(rows)


def test_household_stage_marks_whole_households_and_nothing_else():
    ref = _reference(p_band=0.0)   # no residual: only the household stage acts
    out, diag = D.draw_absence(_persons(), ref, np.random.RandomState(1))
    by_hh = out.groupby("household_id")["day_absence_state"].agg(lambda s: set(s))
    assert all(states in ({D.STATE_PRESENT}, {D.STATE_ABSENT_HOUSEHOLD}) for states in by_hh)
    singles = out[out["household_size_class"] == 1]
    assert 0.35 < (singles["day_absence_state"] == D.STATE_ABSENT_HOUSEHOLD).mean() < 0.65
    assert (out.loc[out["household_size_class"] == 2, "day_absence_state"] == D.STATE_PRESENT).all()
    assert diag["n_absent_individual"] == 0


def test_residual_stage_hits_the_band_target_in_expectation():
    ref = _reference(p_band=0.10, p_size={k: 0.0 for k in range(1, 6)})
    out, diag = D.draw_absence(_persons(2000), ref, np.random.RandomState(2))
    realised = (out["day_absence_state"] != D.STATE_PRESENT).mean()
    assert 0.085 < realised < 0.115
    assert diag["by_band"]["30-44"]["residual_p"] == pytest.approx(0.10)


def test_residual_probability_is_reduced_by_what_the_household_stage_realised():
    ref = _reference(p_band=0.10, p_size={1: 0.20, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    out, diag = D.draw_absence(_persons(4000), ref, np.random.RandomState(3))
    band = diag["by_band"]["30-44"]  # 40-year-old singles share this band with the couples
    assert 0 < band["residual_p"] < 0.10
    assert abs(band["realised_rate"] - 0.10) < 0.015


def test_overshoot_gives_zero_residual_and_a_warning(caplog):
    ref = _reference(p_band=0.01, p_size={1: 0.5, 2: 0.5, 3: 0.5, 4: 0.5, 5: 0.5})
    with caplog.at_level("WARNING"):
        out, diag = D.draw_absence(_persons(), ref, np.random.RandomState(4))
    assert diag["by_band"]["30-44"]["residual_p"] == 0.0
    assert diag["n_bands_overshoot"] >= 1
    assert "overshoot" in caplog.text


def test_household_stage_off_is_an_individual_draw_at_the_band_rate():
    ref = _reference(p_band=0.10)
    out, diag = D.draw_absence(_persons(2000), ref, np.random.RandomState(5), household_stage=False)
    assert diag["n_absent_household"] == 0
    assert (out["p_household"] == 0.0).all()
    assert 0.085 < (out["day_absence_state"] != D.STATE_PRESENT).mean() < 0.115


def test_deterministic_and_row_order_independent():
    ref = _reference()
    persons = _persons()
    a, _ = D.draw_absence(persons, ref, np.random.RandomState(7))
    b, _ = D.draw_absence(persons.sample(frac=1.0, random_state=0), ref, np.random.RandomState(7))
    pd.testing.assert_frame_equal(a, b)
    assert list(a.columns) == list(D.ABSENCE_COLUMNS) and len(a) == len(persons)


def test_missing_age_raises():
    persons = _persons(); persons.loc[0, "age"] = np.nan
    with pytest.raises(ValueError, match="age"):
        D.draw_absence(persons, _reference(), np.random.RandomState(0))


def test_load_reference_reads_the_committed_tables_by_column_name():
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref = D.load_absence_reference(os.path.join(repo, "eqasim-data", "data", "braunschweig", "srv"))
    assert set(ref.p_absent_by_band) == set(D.AGE_BAND_LABELS)
    assert set(ref.p_all_absent_by_size) == {1, 2, 3, 4, 5}
    assert 0.01 < ref.p_absent_by_band["6-17"] < 0.03 and 0.07 < ref.p_absent_by_band["18-29"] < 0.10
    # Issue #388: the person-level reporting reference is filled from the committed table's
    # p_absent_person column (0.0514 for size 1, see srv2023_absence_household_by_size.csv).
    assert set(ref.p_absent_person_by_size) == {1, 2, 3, 4, 5}
    assert 0.045 < ref.p_absent_person_by_size[1] < 0.06


def test_load_reference_raises_a_named_error_on_a_by_size_table_without_the_person_column(tmp_path):
    """Final-review fix wave, MINOR finding 2: an older srv2023_absence_household_by_size.csv
    (pre-#388) has no p_absent_person column; the bare KeyError this used to raise gave no clue
    which table or column was at fault. Assert a named ValueError instead."""
    import os
    by_age = pd.DataFrame({"band": list(D.AGE_BAND_LABELS) + ["all"],
                           "p_absent": [0.05] * (len(D.AGE_BAND_LABELS) + 1)})
    by_age.to_csv(os.path.join(str(tmp_path), D.ABSENCE_BY_AGE_TABLE), index=False)
    # The pre-#388 by-size table shape: no p_absent_person / n_persons_unweighted / etc. columns.
    by_size = pd.DataFrame({"size_class": [1, 2, 3, 4, 5], "p_all_absent": [0.05] * 5})
    by_size.to_csv(os.path.join(str(tmp_path), D.ABSENCE_HOUSEHOLD_TABLE), index=False)
    with pytest.raises(ValueError, match="p_absent_person"):
        D.load_absence_reference(str(tmp_path))


def test_plan_replacement_present_general_matches_absence_state():
    # Ruling R4 (issue #370, Task 4): plan_replacement keeps a LOCAL "present" constant rather
    # than importing this package (to avoid a cross-package import from commute_day to
    # day_absence); this pin catches the two constants drifting apart silently.
    assert plan_replacement.STATE_PRESENT_GENERAL == D.STATE_PRESENT


def test_by_size_class_reports_the_realised_rate_per_household_size_reported_not_targeted():
    """Ruling R11 (final-review fix wave): by_size_class is REPORTED, never a target the draw is
    tuned against -- there is no size-class-level reference, unlike by_band."""
    ref = _reference(p_band=0.0)   # no residual: only the household stage acts, as in the sibling test
    _out, diag = D.draw_absence(_persons(), ref, np.random.RandomState(1))
    by_size_class = diag["by_size_class"]
    assert set(by_size_class) == {1, 2, 3, 4, 5}
    assert by_size_class[1]["n"] == 200 and by_size_class[2]["n"] == 400
    assert 0.35 < by_size_class[1]["realised_rate"] < 0.65   # matches the "singles" assertion above
    assert by_size_class[2]["realised_rate"] == pytest.approx(0.0)
    for size_class in (3, 4, 5):
        assert by_size_class[size_class]["n"] == 0
        assert np.isnan(by_size_class[size_class]["realised_rate"])


def test_reference_missing_size_class_or_band_raises():
    _full_person = {k: 0.1 for k in range(1, 6)}
    # Six of the seven bands: the last one ("75+") is missing.
    bands_missing_one = {b: 0.1 for b in D.AGE_BAND_LABELS[:-1]}
    with pytest.raises(ValueError, match="p_absent_by_band"):
        D.AbsenceReference(p_absent_by_band=bands_missing_one,
                          p_all_absent_by_size={k: 0.1 for k in range(1, 6)},
                          p_absent_person_by_size=_full_person)
    # Size classes 1-4 only: class 5 is missing.
    sizes_missing_one = {k: 0.1 for k in range(1, 5)}
    with pytest.raises(ValueError, match="p_all_absent_by_size"):
        D.AbsenceReference(p_absent_by_band={b: 0.1 for b in D.AGE_BAND_LABELS},
                          p_all_absent_by_size=sizes_missing_one,
                          p_absent_person_by_size=_full_person)
    # p_absent_person_by_size itself missing a size class raises the same way, even though its
    # values may be NaN (NaN is a valid VALUE, not a substitute for a missing KEY).
    person_missing_one = {k: 0.1 for k in range(1, 5)}
    with pytest.raises(ValueError, match="p_absent_person_by_size"):
        D.AbsenceReference(p_absent_by_band={b: 0.1 for b in D.AGE_BAND_LABELS},
                          p_all_absent_by_size={k: 0.1 for k in range(1, 6)},
                          p_absent_person_by_size=person_missing_one)


def test_reference_person_rate_allows_nan_but_not_out_of_range():
    valid = {b: 0.1 for b in D.AGE_BAND_LABELS}
    sizes = {k: 0.1 for k in range(1, 6)}
    # NaN is accepted (an empty size class legitimately has no rate; it is a reporting reference).
    D.AbsenceReference(p_absent_by_band=valid, p_all_absent_by_size=sizes,
                       p_absent_person_by_size={1: float("nan"), 2: 0.05, 3: 0.05, 4: 0.05, 5: 0.05})
    out_of_range = {1: 1.5, 2: 0.05, 3: 0.05, 4: 0.05, 5: 0.05}
    with pytest.raises(ValueError, match="p_absent_person_by_size"):
        D.AbsenceReference(p_absent_by_band=valid, p_all_absent_by_size=sizes,
                          p_absent_person_by_size=out_of_range)


# --- Issue #388: individual-stage eligibility by (unclipped) household size --------------------

def test_individual_stage_eligibility_excludes_small_households():
    """With individual_stage_min_household_size=2, singles (household_size=1) are excluded from
    the individual residual stage -- their realised absence stays at the household rate alone --
    while the band target is still hit overall because couples (household_size=2) carry the
    residual."""
    ref = _reference(p_band=0.10, p_size={1: 0.05, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    out, diag = D.draw_absence(_persons(4000), ref, np.random.RandomState(11),
                               individual_stage_min_household_size=2)
    singles = out[out["household_size_class"] == 1]
    singles_rate = (singles["day_absence_state"] != D.STATE_PRESENT).mean()
    assert abs(singles_rate - 0.05) < 0.006
    band = diag["by_band"]["30-44"]
    assert abs(band["realised_rate"] - 0.10) < 0.01


def test_individual_stage_min_household_size_1_is_byte_identical_to_the_default():
    """The keyword's CODE default is 1, so passing it explicitly must reproduce today's behaviour
    exactly -- not merely approximately -- because the legacy residual expression is kept verbatim
    for this path (see _residual_probability_legacy)."""
    ref = _reference(p_band=0.10, p_size={1: 0.05, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    persons = _persons(4000)
    with_keyword, diag_with = D.draw_absence(persons, ref, np.random.RandomState(13),
                                             individual_stage_min_household_size=1)
    without_keyword, diag_without = D.draw_absence(persons, ref, np.random.RandomState(13))
    pd.testing.assert_frame_equal(with_keyword, without_keyword, check_exact=True)
    assert diag_with["n_persons_ineligible_individual_stage"] == 0
    assert diag_without["n_persons_ineligible_individual_stage"] == 0


def test_individual_stage_min_household_size_1_matches_the_ada06b61_golden_hash():
    """Final-review fix wave, MINOR finding 3: the sibling byte-identity test above only compares
    the CURRENT code against itself (keyword=1 vs default), which proves the default is 1 but says
    nothing about PR #387's actual output. This test pins against PR #387 itself.

    GOLDEN_HASH was computed OFFLINE (not by this test) by running the PR #387 merge commit's
    version of this module -- `git show ada06b61:braunschweig/synthesis/day_absence/absence.py`,
    written to a scratch file outside this worktree and imported under the module name
    `absence_ada06b61` -- against this SAME fixture (`_persons()` default, `_reference()`
    defaults: p_band=0.10, p_size={1: 0.5, 2..5: 0.0}) and `np.random.RandomState(13)`, serialised
    exactly as below. Reproducing that computation: `git show ada06b61:...absence.py` into an
    isolated module, construct its two-field `AbsenceReference` with the same p_band/p_size, call
    `draw_absence(persons, ref, np.random.RandomState(13))` (no keyword -- #388 did not exist yet),
    and hash the same serialisation. This test itself only re-derives the CURRENT code's hash and
    compares it to that pre-computed ada06b61 constant -- it does not re-run ada06b61's code."""
    GOLDEN_HASH_ADA06B61 = "30157d69d5e0e3903041d8b5b3bfd3432190ddc2d2989938dbb977a6c65eef98"
    ref = _reference(p_band=0.10, p_size={1: 0.5, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    persons = _persons()
    out, _diag = D.draw_absence(persons, ref, np.random.RandomState(13),
                                individual_stage_min_household_size=1)
    serialised = out.sort_values("person_id").reset_index(drop=True)[list(D.ABSENCE_COLUMNS)] \
        .to_csv(index=False).encode()
    assert hashlib.sha256(serialised).hexdigest() == GOLDEN_HASH_ADA06B61


def test_residual_probability_legacy_and_eligible_agree_when_every_present_person_is_eligible():
    """The byte-identity claim rests on this algebraic identity: substituting target_n = target *
    n_band and realised_hh = absent_hh_n / n_band into the general eligible-pool formula recovers
    the legacy ratio exactly whenever n_eligible == n_band - absent_hh_n (every present person is
    eligible, i.e. individual_stage_min_household_size == 1)."""
    rng = np.random.RandomState(2024)
    checked = 0
    for _ in range(500):
        n_band = int(rng.randint(1, 5000))
        target = float(rng.uniform(0.0, 1.0))
        absent_hh_n = int(rng.randint(0, n_band))  # keeps realised_hh < 1.0, i.e. n_eligible > 0
        realised_hh = absent_hh_n / n_band
        n_eligible = n_band - absent_hh_n
        target_n = target * n_band
        legacy = float(min(max(D._residual_probability_legacy(target, realised_hh), 0.0), 1.0))
        eligible = D._residual_probability_eligible(target_n, absent_hh_n, n_eligible)
        assert abs(legacy - eligible) < 1e-12
        checked += 1
    assert checked == 500


def test_cannot_reach_target_warns_when_no_eligible_present_person_remains(caplog):
    """A singles-only band with individual_stage_min_household_size=2: nobody in the band is ever
    eligible for the individual stage, so a band target above the (here: zero) household rate can
    never be reached -- the code must WARN rather than silently under-shoot the target."""
    persons = pd.DataFrame({"person_id": range(2000), "household_id": range(2000), "age": [40] * 2000})
    ref = _reference(p_band=0.10, p_size={1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    with caplog.at_level("WARNING"):
        out, diag = D.draw_absence(persons, ref, np.random.RandomState(17),
                                   individual_stage_min_household_size=2)
    assert diag["by_band"]["30-44"]["n_eligible_present"] == 0
    assert (out["day_absence_state"] == D.STATE_PRESENT).all()
    assert "cannot reach its target" in caplog.text
    assert diag["n_bands_unreachable"] >= 1


def test_cannot_reach_target_warns_for_a_partially_filled_eligible_pool(caplog):
    """Final-review fix wave, Important finding 1: the earlier condition (``n_eligible == 0``
    only) missed a PARTIALLY filled eligible pool that is still too small to cover the residual.
    Reviewer's probe: 1,000 singles + 10 couples, band rate 0.10,
    individual_stage_min_household_size=2 -- the residual clips to 1.0 (every one of the 20
    eligible persons is drawn absent) and the band still only realises ~1.96 % against a 10 %
    target, which the old n_eligible == 0 check alone would never have flagged."""
    rows = []
    pid = 0
    household_id = 0
    for _ in range(1000):  # 1,000 singles, household_size=1, never eligible at threshold 2
        rows.append({"person_id": pid, "household_id": household_id, "age": 40})
        pid += 1
        household_id += 1
    for _ in range(10):  # 10 couples, household_size=2, the only eligible persons
        rows.append({"person_id": pid, "household_id": household_id, "age": 35}); pid += 1
        rows.append({"person_id": pid, "household_id": household_id, "age": 36}); pid += 1
        household_id += 1
    persons = pd.DataFrame(rows)
    ref = _reference(p_band=0.10, p_size={1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    with caplog.at_level("WARNING"):
        out, diag = D.draw_absence(persons, ref, np.random.RandomState(23),
                                   individual_stage_min_household_size=2)
    band = diag["by_band"]["30-44"]
    assert band["n_eligible_present"] == 20
    assert band["residual_p"] == pytest.approx(1.0)
    realised = (out["day_absence_state"] != D.STATE_PRESENT).mean()
    assert abs(realised - 20 / 1020) < 1e-9  # deterministic: residual 1.0 means every draw fires
    assert diag["n_bands_unreachable"] >= 1
    assert "cannot reach its target" in caplog.text
    assert "shortfall" in caplog.text


def test_individual_stage_min_household_size_diagnostics_are_reported():
    ref = _reference(p_band=0.10, p_size={1: 0.05, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    out, diag = D.draw_absence(_persons(4000), ref, np.random.RandomState(19),
                               individual_stage_min_household_size=2)
    assert diag["individual_stage_min_household_size"] == 2
    present_singles = int(((out["household_size_class"] == 1) &
                           (out["day_absence_state"] == D.STATE_PRESENT)).sum())
    assert diag["n_persons_ineligible_individual_stage"] == present_singles
