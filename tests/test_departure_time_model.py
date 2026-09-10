"""Tests for the departure-time model (issue #123, ADR-0114, Phase 0 Task 3).

Covers the three models of :mod:`braunschweig.popsim.departure_time_model`
(``eqasim_uniform`` / ``derounded`` / ``srv_mapped``), the two person-schema adapters, the
committed-reference loader and its validation, the quantile mapping (rank preservation,
within-bin interpolation, the coarsening ladder) and the model's guards (clipping to a
non-negative departure and to ``MAX_PLAN_TIME_SECONDS``, the median-shift warning, the
unmapped-share warning).

The synthetic fixtures below are deliberately degenerate (all reference mass in ONE 15-minute
bin) so that a mapped first departure can be asserted to land inside a known quarter hour; the
committed SrV table is exercised separately by
``test_committed_reference_maps_onto_the_reference_shape``, which checks the realised
distribution against the reference's own cumulative shape.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import departure_time_model as M
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV_DIR = os.path.join(REPO_ROOT, "eqasim-data", "data", "braunschweig", "srv")


# --------------------------------------------------------------------------- fixtures
def _reference(peak_bin=30, n_unweighted=500):
    """A two-cell reference: (education, school_age_6_17_not_employed) with ALL its mass in
    ``peak_bin`` (7:30-7:45 by default) and the pooled ("all", "all") cell with all its mass in
    bin 32 (8:00-8:15), so the coarsening ladder's two levels land in DIFFERENT quarter hours."""
    rows = []
    for segment, purpose, bins in (("school_age_6_17_not_employed", "education", {peak_bin: 1.0}),
                                   ("all", "all", {32: 1.0})):
        for b in range(M.N_BINS):
            rows.append({"universe": "at_home_zero", "segment": segment, "purpose": purpose,
                         "position": "first", "bin_15min": b,
                         "share_derounded": bins.get(b, 0.0),
                         "share_as_reported": bins.get(b, 0.0), "n_unweighted": n_unweighted})
    return pd.DataFrame(rows)


def _reference_with_pooled_purpose(peak_bin=20):
    """:func:`_reference` plus the (segment "all", purpose "education") rung, peaking in
    ``peak_bin`` (5:00-5:15 by default) -- a third quarter hour, so the rung a cell actually took
    is identifiable from the mapped departure alone."""
    pooled = pd.DataFrame([{"universe": "at_home_zero", "segment": "all", "purpose": "education",
                            "position": "first", "bin_15min": b,
                            "share_derounded": 1.0 if b == peak_bin else 0.0,
                            "share_as_reported": 1.0 if b == peak_bin else 0.0,
                            "n_unweighted": 500} for b in range(M.N_BINS)])
    return pd.concat([_reference(), pooled], ignore_index=True)


def _mid_persons(n=100):
    """MiD-schema person attributes: 10-year-olds with P_TAET 9 (pupil, not employed)."""
    return pd.DataFrame({"person_id": range(n), "HP_ALTER": [10] * n, "P_TAET": [9] * n})


def _persons(n=100):
    """The harmonised person frame ``apply_departure_time_model`` consumes (ruling A-R7)."""
    return M.persons_from_mid_schema(_mid_persons(n))


def _table(n=100):
    """Two trips per person: home -> education at a quarter-hour clock time, education -> home
    six hours later, so the whole chain shifts by the same per-person offset."""
    rows = []
    for pid in range(n):
        dep = 7 * 3600 + (pid % 4) * 900           # 7:00, 7:15, 7:30, 7:45 -- quarter-hour reports
        rows += [{"person_id": pid, "trip_index": 0, "departure_time": dep,
                  "arrival_time": dep + 900,
                  "preceding_purpose": "home", "following_purpose": "education"},
                 {"person_id": pid, "trip_index": 1, "departure_time": dep + 6 * 3600,
                  "arrival_time": dep + 6 * 3600 + 900,
                  "preceding_purpose": "education", "following_purpose": "home"}]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- person schemas
def test_person_groups_use_the_harmonised_rule():
    groups = M.person_groups(pd.DataFrame({"person_id": [1, 2, 3], "age": [10, 40, 70],
                                           "employed": [False, True, False]}))
    assert groups.tolist() == ["school_age_6_17_not_employed", "employed",
                               "senior_65plus_not_employed"]
    assert groups.index.tolist() == [1, 2, 3]


def test_persons_from_mid_schema_maps_age_and_employment():
    """HP_ALTER -> age and P_TAET in attributes.EMPLOYED_TAET -> employed (ruling A-R7)."""
    mid = pd.DataFrame({"person_id": [1, 2, 3, 4], "HP_ALTER": [10, 40, 40, 70],
                        "P_TAET": [9, 1, 5, 9]})      # 1 = full-time employed, 5 = parental leave
    harmonised = M.persons_from_mid_schema(mid)
    assert harmonised["age"].tolist() == [10, 40, 40, 70]
    assert harmonised["employed"].tolist() == [False, True, False, False]
    assert M.person_groups(harmonised).tolist() == [
        "school_age_6_17_not_employed", "employed", "adult_18_64_not_employed",
        "senior_65plus_not_employed"]


def test_persons_from_synthetic_schema_keeps_age_and_employed():
    synthetic = pd.DataFrame({"person_id": ["a", "b"], "age": [7, 30], "employed": [False, True],
                              "household_id": [1, 2]})
    harmonised = M.persons_from_synthetic_schema(synthetic)
    assert list(harmonised.columns) == ["person_id", "age", "employed"]
    assert M.person_groups(harmonised).tolist() == ["school_age_6_17_not_employed", "employed"]


@pytest.mark.parametrize("adapter, frame, missing", [
    (M.persons_from_mid_schema, pd.DataFrame({"person_id": [1], "HP_ALTER": [10]}), "P_TAET"),
    (M.persons_from_synthetic_schema, pd.DataFrame({"person_id": [1], "age": [10]}), "employed"),
])
def test_person_schema_adapters_raise_naming_the_missing_column(adapter, frame, missing):
    with pytest.raises(ValueError, match=missing):
        adapter(frame)


def test_person_groups_raises_on_duplicate_person_id():
    duplicated = pd.DataFrame({"person_id": [1, 1], "age": [10, 40], "employed": [False, True]})
    with pytest.raises(ValueError, match="duplicate"):
        M.person_groups(duplicated)


# --------------------------------------------------------------------------- de-rounding
def test_derounding_offsets_stay_inside_the_reported_precision_cell():
    """+/- 7.5 min on the quarter hour, +/- 2.5 min on the five-minute grid, 0 for an exact
    minute -- the Task 1 rule, applied to a departure given in SECONDS."""
    seconds = np.array([7 * 3600.0, 7 * 3600 + 300.0, 7 * 3600 + 420.0])   # 7:00, 7:05, 7:07
    offsets = M.derounding_offsets(seconds, np.random.RandomState(0))
    assert abs(offsets[0]) <= 450.0 and abs(offsets[1]) <= 150.0 and offsets[2] == 0.0


def test_derounding_offsets_are_reproducible_for_a_given_seed():
    seconds = np.array([7 * 3600.0, 8 * 3600.0, 9 * 3600.0])
    first = M.derounding_offsets(seconds, np.random.RandomState(11))
    again = M.derounding_offsets(seconds, np.random.RandomState(11))
    other = M.derounding_offsets(seconds, np.random.RandomState(12))
    assert np.array_equal(first, again) and not np.array_equal(first, other)


def test_derounding_offsets_reject_a_missing_departure():
    with pytest.raises(ValueError, match="NaN"):
        M.derounding_offsets(np.array([np.nan, 3600.0]), np.random.RandomState(0))


# --------------------------------------------------------------------------- srv_mapped
def test_srv_mapped_moves_every_first_departure_into_the_reference_cell_and_shifts_the_chain():
    table, diag = M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_SRV_MAPPED,
                                               random_seed=1, reference=_reference(),
                                               min_reference_n=100, min_model_n=10)
    first = table[table["trip_index"] == 0]
    assert ((first["departure_time"] >= 30 * 900) & (first["departure_time"] < 31 * 900)).all()
    later = table[table["trip_index"] == 1]
    assert np.allclose(later["departure_time"].to_numpy() - first["departure_time"].to_numpy(),
                       6 * 3600)
    assert (table["departure_time"] - table[M.OFFSET_COLUMN]).round().equals(
        _table()["departure_time"].round().astype(float))
    cell = diag["cells"][("education", "school_age_6_17_not_employed")]
    assert cell["level"] == "purpose_group" and cell["n_model"] == 100
    assert cell["n_reference"] == 500 and diag["share_unmapped"] == 0.0
    assert diag["n_persons_by_level"]["purpose_group"] == 100


def test_srv_mapped_is_rank_preserving():
    table, _ = M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_SRV_MAPPED,
                                            random_seed=1, reference=_reference(),
                                            min_reference_n=100, min_model_n=10)
    original = _table()
    first = table[table["trip_index"] == 0].set_index("person_id")["departure_time"]
    before = original[original["trip_index"] == 0].set_index("person_id")["departure_time"]
    # Persons reported at 7:00 must not overtake persons reported at 7:45 after mapping; the
    # 900 s slack covers the de-rounding, which is the only source of rank changes among two
    # reports one quarter hour apart (+/- 7.5 min each).
    assert first[before == 7 * 3600].max() <= first[before == 7 * 3600 + 2700].min() + 900


def test_srv_mapped_interpolates_uniformly_within_the_reference_bin():
    """All reference mass in one 15-min bin: n persons must spread over that bin at the
    (i + 0.5) / n quantiles, i.e. evenly, not all pile up on the bin's lower edge."""
    table, _ = M.apply_departure_time_model(_table(4), _persons(4), model=M.MODEL_SRV_MAPPED,
                                            random_seed=1, reference=_reference(),
                                            min_reference_n=100, min_model_n=1)
    first = np.sort(table[table["trip_index"] == 0]["departure_time"].to_numpy())
    expected = np.array([30.125, 30.375, 30.625, 30.875]) * 900.0
    # 1 s tolerance: the per-person offset is rounded to whole seconds before it is applied, and
    # the de-rounding cancels out of `derounding + mapping` only up to float cancellation.
    assert np.abs(first - expected).max() <= 1.0
    assert np.abs(np.diff(first) - 225.0).max() <= 1.0      # evenly spread, not on the bin edge


def test_coarsening_falls_back_to_all_all_and_counts_unmapped():
    persons = M.persons_from_mid_schema(
        _mid_persons().assign(P_TAET=1, HP_ALTER=40))     # employed adults: no (education, employed) cell
    table, diag = M.apply_departure_time_model(_table(), persons, model=M.MODEL_SRV_MAPPED,
                                               random_seed=1, reference=_reference(),
                                               min_reference_n=100, min_model_n=10)
    assert diag["cells"][("education", "employed")]["level"] == "all_all"
    first = table[table["trip_index"] == 0]
    assert ((first["departure_time"] >= 32 * 900) & (first["departure_time"] < 33 * 900)).all()

    thin, diag2 = M.apply_departure_time_model(_table(), persons, model=M.MODEL_SRV_MAPPED,
                                               random_seed=1, reference=_reference(),
                                               min_reference_n=1000, min_model_n=10)
    # No reference cell reaches min_reference_n -> nobody is mapped; the persons keep ONLY their
    # de-rounding offset (at most half a quarter hour).
    assert diag2["share_unmapped"] == 1.0 and (thin[M.OFFSET_COLUMN].abs() <= 450).all()
    assert diag2["n_persons_by_level"]["unmapped"] == 100


def test_coarsening_uses_the_purpose_all_segment_before_the_pooled_cell():
    """A (purpose, group) cell that is missing but whose (purpose, "all" segment) cell exists
    must take the purpose_all rung, not fall through to ("all", "all")."""
    reference = _reference_with_pooled_purpose()
    persons = M.persons_from_mid_schema(_mid_persons().assign(P_TAET=1, HP_ALTER=40))
    table, diag = M.apply_departure_time_model(_table(), persons, model=M.MODEL_SRV_MAPPED,
                                               random_seed=1, reference=reference,
                                               min_reference_n=100, min_model_n=10)
    assert diag["cells"][("education", "employed")]["level"] == "purpose_all"
    first = table[table["trip_index"] == 0]
    assert ((first["departure_time"] >= 20 * 900) & (first["departure_time"] < 21 * 900)).all()


def test_a_thin_model_cell_climbs_the_ladder_instead_of_dropping_out():
    """Ruling A-R11 / spec 2.2: coarsening is triggered by a thin MODEL cell too. Ten school-age
    education persons (below min_model_n = 50) must JOIN the other thin education persons at the
    (education, "all") rung and be mapped there -- not left with the donor's own start times --
    and be ranked WITHIN that pooled set of 55."""
    persons = _persons(55)
    persons.loc[persons.index[10:], "age"] = 70        # 10 school-age + 45 senior, both thin
    table, diag = M.apply_departure_time_model(_table(55), persons, model=M.MODEL_SRV_MAPPED,
                                               random_seed=1,
                                               reference=_reference_with_pooled_purpose(),
                                               min_reference_n=100, min_model_n=50)
    thin = diag["cells"][("education", "school_age_6_17_not_employed")]
    assert thin["level"] == "purpose_all" and thin["n_model"] == 10
    assert thin["n_model_pooled"] == 55 and thin["n_reference"] == 500
    assert diag["cells"][("education", "senior_65plus_not_employed")]["n_model"] == 45
    assert diag["n_persons_by_level"]["purpose_all"] == 55 and diag["share_unmapped"] == 0.0
    first = table[table["trip_index"] == 0]
    assert ((first["departure_time"] >= 20 * 900) & (first["departure_time"] < 21 * 900)).all()
    # ONE common rank order over the pooled set: 55 persons spread evenly across the target bin.
    ordered = np.sort(first["departure_time"].to_numpy())
    assert np.abs(np.diff(ordered) - 900.0 / 55.0).max() <= 1.0


def test_unmapped_only_when_even_the_pooled_model_set_is_too_thin():
    """Four persons: their own cell, the (education, "all") rung and the ("all", "all") rung all
    pool the same four persons, so no rung reaches min_model_n=50 and they stay unmapped."""
    table, diag = M.apply_departure_time_model(_table(4), _persons(4), model=M.MODEL_SRV_MAPPED,
                                               random_seed=1,
                                               reference=_reference_with_pooled_purpose(),
                                               min_reference_n=100, min_model_n=50)
    cell = diag["cells"][("education", "school_age_6_17_not_employed")]
    assert cell["level"] == "unmapped" and cell["n_reference"] == 0
    assert diag["share_unmapped"] == 1.0 and (table[M.OFFSET_COLUMN].abs() <= 450).all()


def test_an_empty_reference_cell_is_never_read_as_a_zero_distribution():
    """A cell the SrV table emits with n_unweighted 0 and NaN shares means "no reference" -- the
    ladder must skip it (here: down to the pooled cell), never map onto NaN/zero shares."""
    empty = pd.DataFrame([{"universe": "at_home_zero", "segment": "school_age_6_17_not_employed",
                           "purpose": "education", "position": "first", "bin_15min": b,
                           "share_derounded": float("nan"), "share_as_reported": float("nan"),
                           "n_unweighted": 0} for b in range(M.N_BINS)])
    reference = pd.concat([empty, _reference().query("segment == 'all'")], ignore_index=True)
    table, diag = M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_SRV_MAPPED,
                                               random_seed=1, reference=reference,
                                               min_reference_n=0, min_model_n=10)
    assert diag["cells"][("education", "school_age_6_17_not_employed")]["level"] == "all_all"
    first = table[table["trip_index"] == 0]
    assert ((first["departure_time"] >= 32 * 900) & (first["departure_time"] < 33 * 900)).all()


def test_median_shift_guard_warns_naming_the_cell_and_both_medians(caplog):
    far = _reference(peak_bin=80)          # 20:00-20:15 vs. a 7:00-7:45 model cell: ~13 h shift
    with caplog.at_level("WARNING"):
        _table_out, diag = M.apply_departure_time_model(
            _table(), _persons(), model=M.MODEL_SRV_MAPPED, random_seed=1, reference=far,
            min_reference_n=100, min_model_n=10, max_median_shift_hours=2.0)
    assert diag["n_cells_over_median_shift_guard"] == 1
    assert "median" in caplog.text and "school_age_6_17_not_employed" in caplog.text
    cell = diag["cells"][("education", "school_age_6_17_not_employed")]
    assert cell["median_abs_shift_min"] > 120.0 and cell["median_shift_min"] > 120.0


def test_a_mostly_coarsened_run_warns_with_the_level_split(caplog):
    """Observability guard: when most persons are calibrated against a POOLED reference rather
    than their own cell, the log must say so with n/total and the per-level split."""
    persons = _persons(55)
    persons.loc[persons.index[10:], "age"] = 70
    with caplog.at_level("WARNING"):
        M.apply_departure_time_model(_table(55), persons, model=M.MODEL_SRV_MAPPED, random_seed=1,
                                     reference=_reference_with_pooled_purpose(),
                                     min_reference_n=100, min_model_n=50)
    assert "mapped BELOW their own" in caplog.text and "55/55" in caplog.text
    assert "purpose_all 55/55" in caplog.text


def test_unmapped_share_above_the_threshold_warns(caplog):
    with caplog.at_level("WARNING"):
        M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_SRV_MAPPED,
                                     random_seed=1, reference=_reference(), min_reference_n=1000,
                                     min_model_n=10)
    assert "unmapped" in caplog.text


# --------------------------------------------------------------------------- derounded model
def test_derounded_offsets_stay_within_the_precision_cell():
    table, diag = M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_DEROUNDED,
                                               random_seed=3)
    assert (table[M.OFFSET_COLUMN].abs() <= 450).all() and diag["model"] == M.MODEL_DEROUNDED
    assert diag["cells"] == {} and np.isnan(diag["share_unmapped"])


def test_the_whole_chain_moves_by_one_offset_and_the_offset_is_recorded_once_per_person():
    table, _ = M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_DEROUNDED,
                                            random_seed=3)
    assert (table.groupby("person_id")[M.OFFSET_COLUMN].nunique() == 1).all()
    original = _table()
    assert np.allclose(table["arrival_time"] - table["departure_time"],
                       original["arrival_time"] - original["departure_time"])


def test_row_order_and_index_are_preserved_for_a_shuffled_table():
    shuffled = _table(20).sample(frac=1.0, random_state=0)
    out, _ = M.apply_departure_time_model(shuffled.copy(), _persons(20),
                                          model=M.MODEL_DEROUNDED, random_seed=3)
    assert out.index.tolist() == shuffled.index.tolist()
    assert out["person_id"].tolist() == shuffled["person_id"].tolist()
    # The offset a person receives must not depend on the row order of the input table.
    ordered, _ = M.apply_departure_time_model(_table(20), _persons(20), model=M.MODEL_DEROUNDED,
                                              random_seed=3)
    per_person = ordered.groupby("person_id")[M.OFFSET_COLUMN].first()
    assert out.groupby("person_id")[M.OFFSET_COLUMN].first().equals(per_person)


def test_a_person_without_a_first_departure_is_counted_and_left_alone():
    table = _table(3)
    table.loc[table["person_id"] == 2, ["departure_time", "arrival_time"]] = np.nan
    out, diag = M.apply_departure_time_model(table, _persons(3), model=M.MODEL_DEROUNDED,
                                             random_seed=3)
    assert diag["n_persons_without_first_departure"] == 1
    assert (out.loc[out["person_id"] == 2, M.OFFSET_COLUMN] == 0.0).all()
    assert out.loc[out["person_id"] != 2, M.OFFSET_COLUMN].abs().max() <= 450


def test_persons_missing_attributes_raise_naming_the_count():
    with pytest.raises(ValueError, match="attribute"):
        M.apply_departure_time_model(_table(4), _persons(2), model=M.MODEL_DEROUNDED,
                                     random_seed=3)


# --------------------------------------------------------------------------- clipping
def test_clip_keeps_times_inside_the_plan_bound(caplog):
    """A ten-hour chain mapped to a 27:30 first departure would end past MAX_PLAN_TIME_SECONDS;
    the model must pull the whole chain back to the bound and COUNT the clip."""
    long_chain = _table(4)
    long_chain.loc[long_chain["trip_index"] == 1, ["departure_time", "arrival_time"]] += 4 * 3600
    reference = _reference(peak_bin=110)                  # 27:30-27:45
    with caplog.at_level("INFO"):
        table, diag = M.apply_departure_time_model(long_chain, _persons(4),
                                                   model=M.MODEL_SRV_MAPPED, random_seed=1,
                                                   reference=reference, min_reference_n=100,
                                                   min_model_n=1)
    assert table["arrival_time"].max() <= MAX_PLAN_TIME_SECONDS and diag["n_clipped_upper"] >= 1
    assert (table["departure_time"] >= 0).all()


def test_clip_keeps_departures_non_negative():
    """A midnight first departure de-rounded backwards would go negative; the model clips it to
    zero and counts the clip (the same guard eqasim's own jitter has, made observable)."""
    midnight = _table(20)
    midnight["departure_time"] -= 7 * 3600
    midnight["arrival_time"] -= 7 * 3600
    midnight = midnight[midnight["person_id"] % 4 == 0]          # every kept person departs at 0:00
    table, diag = M.apply_departure_time_model(midnight, _persons(20).iloc[::4].copy(),
                                               model=M.MODEL_DEROUNDED, random_seed=2)
    assert diag["n_clipped_lower"] >= 1 and (table["departure_time"] >= 0).all()


def test_the_lower_clip_uses_the_persons_earliest_departure_not_the_first_trip_row():
    """An unordered chain (trip_index 0 is NOT the earliest departure) must still come out
    non-negative: the clip bound is the per-person MINIMUM departure over all rows."""
    unordered = pd.DataFrame({
        "person_id": ["p", "p"], "trip_index": [0, 1],
        "departure_time": [900.0, 120.0],           # 0:15 reported first, a 0:02 row behind it
        "arrival_time": [1500.0, 720.0],
        "preceding_purpose": ["home", "other"], "following_purpose": ["other", "home"],
    })
    persons = pd.DataFrame({"person_id": ["p"], "age": [40], "employed": [True]})
    table, diag = M.apply_departure_time_model(unordered, persons, model=M.MODEL_DEROUNDED,
                                               random_seed=6)      # draws -303 s, below -120
    assert diag["n_clipped_lower"] == 1                     # the -450..+450 draw fell below -120
    assert (table["departure_time"] >= 0).all() and table["departure_time"].min() == 0.0


# --------------------------------------------------------------------------- dispatch
def test_eqasim_uniform_is_byte_identical_to_apply_per_person_jitter():
    from braunschweig.popsim.trips_stage import apply_per_person_jitter
    a = apply_per_person_jitter(_table(), random_seed=5)
    b, diag = M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_EQASIM_UNIFORM,
                                           random_seed=5)
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    assert diag["model"] == M.MODEL_EQASIM_UNIFORM and diag["cells"] == {}


def test_eqasim_uniform_reproduces_the_task_1_golden_fixture():
    """The Task 1 golden fixture (tests/test_popsim_trips_stage.py), routed through the model's
    dispatch, must come out byte-identical to the direct jitter call."""
    from braunschweig.popsim.trips_stage import apply_per_person_jitter
    fixture = pd.DataFrame({
        "person_id":      ["p1", "p1", "p2", "p2", "p2", "p3"],
        "departure_time": [8 * 3600.0, 17 * 3600.0, 7 * 3600.0, 12 * 3600.0, 18 * 3600.0, 600.0],
        "arrival_time":   [8 * 3600.0 + 900.0, 17 * 3600.0 + 900.0,
                           7 * 3600.0 + 600.0, 12 * 3600.0 + 600.0, 18 * 3600.0 + 600.0, 900.0],
    })
    persons = pd.DataFrame({"person_id": ["p1", "p2", "p3"], "age": [40, 40, 40],
                            "employed": [True, True, True]})
    expected = apply_per_person_jitter(fixture.copy(), random_seed=20260910)
    out, _ = M.apply_departure_time_model(fixture.copy(), persons,
                                          model=M.MODEL_EQASIM_UNIFORM, random_seed=20260910)
    pd.testing.assert_frame_equal(expected, out, check_exact=True)
    assert out["departure_time"].tolist() == [27337.0, 59737.0, 26689.0, 44689.0, 66289.0, 622.0]


def test_unknown_model_and_missing_reference_raise():
    with pytest.raises(ValueError, match="departure_time_model"):
        M.apply_departure_time_model(_table(), _persons(), model="magic", random_seed=1)
    with pytest.raises(ValueError, match="reference"):
        M.apply_departure_time_model(_table(), _persons(), model=M.MODEL_SRV_MAPPED,
                                     random_seed=1, reference=None)


def test_the_same_seed_reproduces_the_run_and_a_different_seed_does_not():
    kwargs = dict(model=M.MODEL_SRV_MAPPED, reference=_reference(), min_reference_n=100,
                  min_model_n=10)
    a, _ = M.apply_departure_time_model(_table(), _persons(), random_seed=1, **kwargs)
    b, _ = M.apply_departure_time_model(_table(), _persons(), random_seed=1, **kwargs)
    c, _ = M.apply_departure_time_model(_table(), _persons(), random_seed=2, **kwargs)
    pd.testing.assert_frame_equal(a, b, check_exact=True)
    assert not a["departure_time"].equals(c["departure_time"])


# --------------------------------------------------------------------------- reference loader
def test_load_departure_time_reference_reads_the_committed_table():
    reference = M.load_departure_time_reference(SRV_DIR)
    assert len(reference) > 0 and set(reference["position"]) == {"first"}
    assert reference.groupby(["segment", "purpose"]).size().eq(M.N_BINS).all()
    work = reference[(reference["segment"] == "employed") & (reference["purpose"] == "work")]
    assert int(work["n_unweighted"].iloc[0]) == 4649           # committed table, 2026-09-10 build


def test_committed_reference_maps_onto_the_reference_shape():
    """Map a synthetic (work, employed) population onto the COMMITTED SrV reference and check the
    realised first departures reproduce that cell's cumulative shape: the median must land in the
    reference's median bin and every bin's realised share must match the reference's within the
    1/n granularity of the quantile grid."""
    reference = M.load_departure_time_reference(SRV_DIR)
    n = 201
    rng = np.random.RandomState(0)
    table = pd.DataFrame({
        "person_id": np.repeat(np.arange(n), 2),
        "trip_index": np.tile([0, 1], n),
        # Reported quarter-hour departures spread over the morning; only their ORDER matters.
        "departure_time": np.repeat(6 * 3600 + rng.randint(0, 12, size=n) * 900, 2)
        + np.tile([0, 6 * 3600], n),
        "preceding_purpose": np.tile(["home", "work"], n),
        "following_purpose": np.tile(["work", "home"], n),
    })
    table["arrival_time"] = table["departure_time"] + 900
    persons = pd.DataFrame({"person_id": np.arange(n), "age": 40, "employed": True})

    out, diag = M.apply_departure_time_model(table, persons, model=M.MODEL_SRV_MAPPED,
                                             random_seed=1, reference=reference,
                                             min_reference_n=200, min_model_n=50)
    assert diag["cells"][("work", "employed")]["level"] == "purpose_group"

    cell = reference[(reference["segment"] == "employed")
                     & (reference["purpose"] == "work")].sort_values("bin_15min")
    shares = cell["share_derounded"].to_numpy()
    reference_median_bin = int(np.searchsorted(np.cumsum(shares), 0.5, side="right"))

    first = out[out["trip_index"] == 0]["departure_time"].to_numpy()
    realised_bins = np.floor(first / (M.BIN_MINUTES * 60.0)).astype(int)
    assert int(np.floor(np.median(first) / (M.BIN_MINUTES * 60.0))) == reference_median_bin
    realised_shares = np.bincount(realised_bins, minlength=M.N_BINS)[:M.N_BINS] / n
    assert np.abs(realised_shares - shares).max() < 3.0 / n


def test_load_departure_time_reference_rejects_a_missing_bin(tmp_path):
    table = _reference()
    table.drop(table[(table["segment"] == "all") & (table["bin_15min"] == 5)].index).to_csv(
        tmp_path / "srv2023_departure_time_reference.csv", index=False)
    with pytest.raises(ValueError, match="bin"):
        M.load_departure_time_reference(str(tmp_path))


def test_load_departure_time_reference_rejects_shares_that_do_not_sum_to_one(tmp_path):
    table = _reference()
    broken = (table["segment"] == "all") & (table["bin_15min"] == 32)
    table.loc[broken, "share_derounded"] = 0.9
    table.to_csv(tmp_path / "srv2023_departure_time_reference.csv", index=False)
    with pytest.raises(ValueError, match="sum"):
        M.load_departure_time_reference(str(tmp_path))


def test_load_departure_time_reference_rejects_a_varying_n_unweighted(tmp_path):
    """``n_unweighted`` is a per-cell count repeated on every bin row; a varying value means two
    cells' rows were merged, which would make every threshold decision depend on row order."""
    table = _reference()
    table.loc[(table["segment"] == "all") & (table["bin_15min"] == 7), "n_unweighted"] = 12
    table.to_csv(tmp_path / "srv2023_departure_time_reference.csv", index=False)
    with pytest.raises(ValueError, match="n_unweighted"):
        M.load_departure_time_reference(str(tmp_path))


def test_load_departure_time_reference_reports_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="srv2023_departure_time_reference.csv"):
        M.load_departure_time_reference(str(tmp_path))
