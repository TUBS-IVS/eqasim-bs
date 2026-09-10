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
    """All reference mass in one 15-min bin: n persons must spread over that bin at their mid-rank
    quantiles in the rung's ranking base, i.e. evenly, not all pile up on the bin's lower edge.

    Here the base is the mapped set itself (no ranking context, ruling A-R20), so the i-th of the
    four persons is at ``(#below + 0.5 * #ties + 0.5) / (n + 1) = i / 5`` -- the person ties with
    their own value in the base. The pre-A-R20 no-context convention was ``(i - 0.5) / n``; the two
    differ by at most half a base step and the unified one is what BOTH call sites now use.
    """
    table, _ = M.apply_departure_time_model(_table(4), _persons(4), model=M.MODEL_SRV_MAPPED,
                                            random_seed=1, reference=_reference(),
                                            min_reference_n=100, min_model_n=1)
    first = np.sort(table[table["trip_index"] == 0]["departure_time"].to_numpy())
    expected = np.array([30.2, 30.4, 30.6, 30.8]) * 900.0
    # 1 s tolerance: the per-person offset is rounded to whole seconds before it is applied, and
    # the de-rounding cancels out of `derounding + mapping` only up to float cancellation.
    assert np.abs(first - expected).max() <= 1.0
    assert np.abs(np.diff(first) - 180.0).max() <= 1.0      # evenly spread, not on the bin edge


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
    # ONE common base at the pooled rung: the 55 education persons (every group of that purpose,
    # ruling A-R20) spread evenly across the target bin at i / 56.
    ordered = np.sort(first["departure_time"].to_numpy())
    assert np.abs(np.diff(ordered) - 900.0 / 56.0).max() <= 1.0


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
    """``derounded`` needs NO group at all (final fix wave item 2 -- it draws only from the
    reporting-precision rule): a persons frame missing rows for half the table must NOT block
    it. Only ``srv_mapped``, the one model that picks a mapping cell, raises."""
    out, diag = M.apply_departure_time_model(_table(4), _persons(2), model=M.MODEL_DEROUNDED,
                                             random_seed=3)
    assert diag["model"] == M.MODEL_DEROUNDED and diag["cells"] == {}
    assert (out[M.OFFSET_COLUMN].abs() <= 450).all()

    with pytest.raises(ValueError, match="attribute"):
        M.apply_departure_time_model(_table(4), _persons(2), model=M.MODEL_SRV_MAPPED,
                                     random_seed=3, reference=_reference(),
                                     min_reference_n=100, min_model_n=1)


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


# --------------------------------------------------------------------------- Task 4 re-review
def test_an_unmapped_cell_reports_the_size_of_its_last_pooled_attempt():
    """Task 3 re-review observation (a): an unmapped cell used to report ``n_model_pooled ==
    n_model`` although it never pooled at all, which reads as "this cell was ranked alone" when
    in fact the LAST rung attempt pooled every still-unplaced person. Reporting that attempt's
    size instead lets a reader see how far the cell actually was from ``min_model_n``."""
    persons = _persons(55)
    persons.loc[persons.index[10:], "age"] = 70        # 10 school-age + 45 senior, both thin
    _table_out, diag = M.apply_departure_time_model(
        _table(55), persons, model=M.MODEL_SRV_MAPPED, random_seed=1,
        reference=_reference_with_pooled_purpose(), min_reference_n=100, min_model_n=100)

    school = diag["cells"][("education", "school_age_6_17_not_employed")]
    senior = diag["cells"][("education", "senior_65plus_not_employed")]
    assert school["level"] == "unmapped" and senior["level"] == "unmapped"
    assert school["n_model"] == 10 and senior["n_model"] == 45
    # Both cells were pooled TOGETHER at the last attempt (the ("all", "all") rung): 55 persons,
    # still below min_model_n = 100 -- that distance is exactly what the diagnostic must show.
    assert school["n_model_pooled"] == 55 and senior["n_model_pooled"] == 55


def test_a_malformed_arrival_before_the_earliest_departure_raises():
    """The lower clip bounds the offset by the person's earliest DEPARTURE, so a malformed row
    whose ARRIVAL precedes that departure can still be pushed below zero by a large negative
    mapping offset. The post-shift check must RAISE (never an assert -- ``python -O`` strips
    those) rather than let a negative time reach the MATSim plans.

    The fixture maps 60 school-age education persons onto a reference peaking in bin 0 (00:00 -
    00:15), so every offset is about -7 h, and row 0's arrival is set to 100 s after midnight --
    far below its own person's 7:00 first departure, which is what the lower clip protects.
    """
    table = _table(60)
    table.loc[0, "arrival_time"] = 100.0
    with pytest.raises(ValueError, match="negative after the model"):
        M.apply_departure_time_model(table, _persons(60), model=M.MODEL_SRV_MAPPED,
                                     random_seed=1, reference=_reference(peak_bin=0),
                                     min_reference_n=100, min_model_n=1)


# ---------------------------------------------------------------------------
# The ranking context (ruling A-R18, issue #123 cleanup item 7).
# The mapping is a quantile map, so it needs a distribution to rank a person IN. Ranking the
# MAPPED SET is right for the whole-population trip build and wrong for the reporting-day splice,
# whose set is a handful of home-office persons; ``ranking_context`` makes the POPULATION that
# distribution instead. Without a context nothing changes, byte for byte.
# ---------------------------------------------------------------------------

def _ranking_context(values=None, n=2000, purpose="education",
                     group="school_age_6_17_not_employed"):
    """A population ranking base: one row per POPULATION person with that person's RAW first
    departure (``departure_time - OFFSET_COLUMN``) and their mapping cell.

    Defaults to ``n`` persons on the same quarter-hour clock grid as :func:`_table`, so the
    context is de-rounded exactly like the target persons are.
    """
    if values is None:
        values = [7 * 3600 + (i % 4) * 900 for i in range(n)]
    return pd.DataFrame({"person_id": ["pop%05d" % i for i in range(len(values))],
                         "raw_first_departure_seconds": [float(v) for v in values],
                         "purpose": purpose, "group": group})


def _table_with_departures(departures, purpose="education"):
    """:func:`_table`'s two-trip chain per person, with each person's FIRST departure given
    explicitly, so a test can place a reported time exactly where it needs it."""
    rows = []
    for person_id, departure in enumerate(departures):
        departure = float(departure)
        rows += [{"person_id": person_id, "trip_index": 0, "departure_time": departure,
                  "arrival_time": departure + 900,
                  "preceding_purpose": "home", "following_purpose": purpose},
                 {"person_id": person_id, "trip_index": 1, "departure_time": departure + 6 * 3600,
                  "arrival_time": departure + 6 * 3600 + 900,
                  "preceding_purpose": purpose, "following_purpose": "home"}]
    return pd.DataFrame(rows)


def _off_grid_seconds(count, start_minute=361):
    """``count`` ascending, distinct reported times whose MINUTE is off the five-minute grid.

    The reporting-precision rule gives such a report half width 0, so the de-rounding is exactly
    zero and a context-vs-target comparison is arithmetically exact rather than up to +/- 7.5 min.
    All values stay inside one hour, so no chain approaches the plan-time clip.
    """
    values, minute = [], start_minute
    while len(values) < count:
        if minute % 5:
            for second in range(60):
                values.append(minute * 60.0 + second)
                if len(values) == count:
                    break
        minute += 1
    return values


def test_a_thin_target_set_is_ranked_in_the_population_instead_of_in_itself():
    """REPRODUCTION of the defect (Task 4 review Minor 4): 30 persons in ONE cell, a fat
    reference (500 unweighted observations) and ``min_model_n=50``.

    Without a context the 30 persons ARE the whole ranking base, so the cell fails every rung and
    stays UNMAPPED -- they keep the donor's own start times although the population has 2,000
    persons in exactly that cell and the whole-population trip build maps it at ``purpose_group``
    (proved by ``test_srv_mapped_moves_every_first_departure_into_the_reference_cell_and_shifts_
    the_chain``, which maps 100 persons against the same reference). The root cause is the RANKING
    BASE, not the threshold: with the population as the base the same 30 persons map at
    ``purpose_group`` and land in the reference's own quarter hour (bin 30), and the gate reads
    the CONTEXT size.
    """
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1, reference=_reference(),
                     min_reference_n=100, min_model_n=50)
    without, diagnostics_without = M.apply_departure_time_model(_table(30), _persons(30),
                                                                **arguments)
    assert diagnostics_without["cells"][("education", "school_age_6_17_not_employed")]["level"] \
        == "unmapped"
    assert diagnostics_without["share_unmapped"] == 1.0
    assert (without[without["trip_index"] == 0]["departure_time"] < 30 * 900).any()

    with_context, diagnostics = M.apply_departure_time_model(
        _table(30), _persons(30), ranking_context=_ranking_context(), **arguments)
    cell = diagnostics["cells"][("education", "school_age_6_17_not_employed")]
    assert cell["level"] == "purpose_group"
    assert cell["n_model"] == 30 and cell["n_context"] == 2000
    assert cell["n_context_pooled"] == 2000 and cell["n_reference"] == 500
    assert diagnostics["share_unmapped"] == 0.0
    assert diagnostics["n_ranking_context"] == 2000
    first = with_context[with_context["trip_index"] == 0]["departure_time"]
    assert ((first >= 30 * 900) & (first < 31 * 900)).all()


def test_a_target_lands_where_an_equally_placed_population_person_lands():
    """Ruling A-R20, measured on the COMMITTED ``(employed, work)`` reference rather than on a
    one-bin fixture: with the same base values, the two call sites place a person IDENTICALLY.

    1,000 population persons on a first work leg are mapped by the whole-population path (their
    own set as the base), and the same 1,000 raw times are then handed to a second run as a
    ranking context whose targets are those same times. Every departure is off the five-minute
    grid, so both sides de-round by exactly 0 and only the BASE RULE and the quantile formula can
    differ.

    Why <= 1 s is a real assertion here and not a property of the fixture: on this committed cell
    the pre-A-R20 convention difference alone -- ``i / (N + 1)`` against ``(i - 0.5) / N`` -- moves
    a person by a median of 3.8 s and by up to 77 min in the sparse late tail (measured with
    ``_inverse_cdf`` on the cell's own ``share_derounded`` column, N = 1,000). A tolerance of one
    second therefore only holds if both call sites compute the SAME quantile in the SAME base.
    """
    values = _off_grid_seconds(1000, start_minute=361)
    trips = _table_with_departures(values, purpose="work")
    persons = pd.DataFrame({"person_id": range(1000), "age": 40, "employed": True})
    reference = M.load_departure_time_reference(SRV_DIR)
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1, reference=reference,
                     min_reference_n=200, min_model_n=1000)

    population, population_diagnostics = M.apply_departure_time_model(trips.copy(), persons,
                                                                      **arguments)
    context = M.build_ranking_context(trips.assign(**{M.OFFSET_COLUMN: 0.0}), persons)
    targets, target_diagnostics = M.apply_departure_time_model(trips.copy(), persons,
                                                               ranking_context=context, **arguments)
    for diagnostics in (population_diagnostics, target_diagnostics):
        assert diagnostics["cells"][("work", "employed")]["level"] == "purpose_group"

    population_first = (population[population["trip_index"] == 0]
                        .set_index("person_id")["departure_time"])
    target_first = targets[targets["trip_index"] == 0].set_index("person_id")["departure_time"]
    difference = (population_first - target_first).abs()
    assert difference.max() <= 1.0
    # Rank preservation over the whole set, not just for a pair: the mapped order is the reported
    # order, at both call sites.
    assert target_first.is_monotonic_increasing and population_first.is_monotonic_increasing


def test_the_de_rounding_realisation_is_the_only_residual_difference_between_the_call_sites():
    """ADR-0114 Assumption 10b, MEASURED. The trip build and the splice de-round the same reported
    times from different positions of the model's stream, so a person's rank INSIDE their reported
    quarter hour differs between the two runs. On quarter-hour reports (1,000 employed work
    persons, 17 distinct clock times, the committed ``(employed, work)`` reference) that moves a
    person by a median of 44 s and a 90th percentile of 263 s, but by up to 262 min for the few
    persons who land in the reference's sparse late tail, where a small rank change is a large
    time change.

    The test asserts the BULK (a stated bound on the median, an order of magnitude above the
    measured 44 s) and deliberately does NOT bound the maximum: the tail figure is a measured
    property of this fixture, reported so nobody reads "the same person, the same placement" as an
    exact guarantee -- it is not a scientific bound and no source states one.
    """
    values = [5 * 3600 + (index % 17) * 900.0 for index in range(1000)]
    trips = _table_with_departures(values, purpose="work")
    persons = pd.DataFrame({"person_id": range(1000), "age": 40, "employed": True})
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1,
                     reference=M.load_departure_time_reference(SRV_DIR),
                     min_reference_n=200, min_model_n=1000)

    population, _ = M.apply_departure_time_model(trips.copy(), persons, **arguments)
    context = M.build_ranking_context(trips.assign(**{M.OFFSET_COLUMN: 0.0}), persons)
    targets, _ = M.apply_departure_time_model(trips.copy(), persons, ranking_context=context,
                                              **arguments)
    difference = (population[population["trip_index"] == 0].set_index("person_id")["departure_time"]
                  - targets[targets["trip_index"] == 0].set_index("person_id")["departure_time"]
                  ).abs()
    assert difference.median() <= 600.0            # measured 44 s; the bound is an order above it
    assert difference.max() > 60.0                 # the residual is REAL, not a rounding artefact


def test_the_ranking_context_result_does_not_depend_on_its_row_order():
    """Determinism: the context is sorted by ``person_id`` before its de-rounding draw, so a
    shuffled context frame must produce exactly the same offsets -- otherwise the realised times
    would depend on the row order of a frame nobody guarantees the order of."""
    context = _ranking_context()
    shuffled = context.sample(frac=1.0, random_state=7).reset_index(drop=True)
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1, reference=_reference(),
                     min_reference_n=100, min_model_n=50)
    ordered_out, _ = M.apply_departure_time_model(_table(30), _persons(30),
                                                  ranking_context=context, **arguments)
    shuffled_out, _ = M.apply_departure_time_model(_table(30), _persons(30),
                                                   ranking_context=shuffled, **arguments)
    pd.testing.assert_frame_equal(ordered_out, shuffled_out, check_exact=True)


def test_without_a_ranking_context_the_mapped_set_is_its_own_base():
    """``ranking_context=None`` (what the whole-population trip build passes) must be exactly the
    same call as omitting the argument, and the base is then the MODEL set under the same per-rung
    rule (ruling A-R20): the cell's own 100 persons here, reported as ``n_context`` /
    ``n_context_pooled`` so the cell report always states what a quantile was computed in.
    ``n_ranking_context`` stays ``None`` -- it says where the base came FROM, and 0 there would
    read as "the base was empty"."""
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1, reference=_reference(),
                     min_reference_n=100, min_model_n=10)
    without, diagnostics_without = M.apply_departure_time_model(_table(), _persons(), **arguments)
    explicit, diagnostics_explicit = M.apply_departure_time_model(
        _table(), _persons(), ranking_context=None, **arguments)
    pd.testing.assert_frame_equal(without, explicit, check_exact=True)
    assert diagnostics_without == diagnostics_explicit
    cell = diagnostics_without["cells"][("education", "school_age_6_17_not_employed")]
    assert cell["n_context"] == 100 and cell["n_context_pooled"] == 100
    assert diagnostics_without["n_ranking_context"] is None
    assert "the model set itself" in M.format_ranking_base(diagnostics_without)


def test_a_pooled_rung_is_gated_and_ranked_by_the_pooled_context():
    """The ladder still coarsens with a context, and the pooled rung's gate AND base are the
    pooled CONTEXT: two thin target cells whose own (purpose, group) context is too small map at
    ``purpose_all`` against the population's whole education distribution."""
    persons = _persons(20)
    persons.loc[persons.index[10:], "age"] = 70        # 10 school-age + 10 senior, both thin
    senior_context = _ranking_context(n=400, group="senior_65plus_not_employed")
    senior_context["person_id"] = ["ctx%05d" % i for i in range(len(senior_context))]
    context = pd.concat([_ranking_context(n=600), senior_context], ignore_index=True)

    table, diagnostics = M.apply_departure_time_model(
        _table(20), persons, model=M.MODEL_SRV_MAPPED, random_seed=1,
        reference=_reference_with_pooled_purpose(), min_reference_n=100, min_model_n=700,
        ranking_context=context)

    school = diagnostics["cells"][("education", "school_age_6_17_not_employed")]
    senior = diagnostics["cells"][("education", "senior_65plus_not_employed")]
    # Own cells: 600 / 400 context persons, both below min_model_n = 700 -> climb to (education,
    # "all"), whose context pools all 1,000 and whose reference peaks in bin 20.
    assert school["level"] == "purpose_all" and senior["level"] == "purpose_all"
    assert school["n_context"] == 600 and senior["n_context"] == 400
    assert school["n_context_pooled"] == 1000 and senior["n_context_pooled"] == 1000
    first = table[table["trip_index"] == 0]["departure_time"]
    assert ((first >= 20 * 900) & (first < 21 * 900)).all()


def test_a_ranking_context_is_rejected_by_a_model_that_maps_nothing():
    """A context handed to ``derounded`` or ``eqasim_uniform`` would be silently ignored -- and a
    caller believing a base is in effect when it is not is exactly the hidden defect CLAUDE.md's
    fallback-transparency rule exists to stop."""
    for model in (M.MODEL_EQASIM_UNIFORM, M.MODEL_DEROUNDED):
        with pytest.raises(ValueError, match="maps nothing"):
            M.apply_departure_time_model(_table(4), _persons(4), model=model, random_seed=1,
                                         ranking_context=_ranking_context(n=10))


def test_the_ranking_context_is_validated_before_it_is_ranked_against():
    """A malformed base fails loudly: a missing column, a NaN raw time (a person the offset column
    could not decompose) and a negative one (an upstream trip-build defect) each RAISE naming the
    count, rather than silently narrowing the distribution."""
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1, reference=_reference(),
                     min_reference_n=100, min_model_n=10)
    with pytest.raises(ValueError, match="ranking context is missing required column"):
        M.apply_departure_time_model(
            _table(4), _persons(4), ranking_context=_ranking_context(n=10).drop(columns=["group"]),
            **arguments)
    for bad_value, message in ((float("nan"), "are NaN or non-numeric"), (-60.0, "are negative")):
        context = _ranking_context(n=10)
        context.loc[0, "raw_first_departure_seconds"] = bad_value
        with pytest.raises(ValueError, match=message):
            M.apply_departure_time_model(_table(4), _persons(4), ranking_context=context,
                                         **arguments)


def test_build_ranking_context_derives_the_raw_first_departure_and_the_mapping_cell():
    """The builder turns the POPULATION's trips plus its person attributes into the base: one row
    per person, the FIRST trip's ``departure_time`` minus ``OFFSET_COLUMN`` (the reported grid
    time before any model ran), the first trip's destination purpose and the harmonised group."""
    trips = _table(3)
    trips[M.OFFSET_COLUMN] = 120.0                       # a model already shifted every chain
    persons = pd.DataFrame({"person_id": [0, 1, 2], "age": [10, 40, 70],
                            "employed": [False, True, False]})
    context = M.build_ranking_context(trips, persons)

    assert list(context.columns) == list(M.RANKING_CONTEXT_COLUMNS)
    assert context["person_id"].tolist() == [0, 1, 2]
    # _table's first departures are 7:00, 7:15, 7:30 -- minus the 120 s offset already applied.
    assert context["raw_first_departure_seconds"].tolist() == [7 * 3600 - 120.0,
                                                               7 * 3600 + 900 - 120.0,
                                                               7 * 3600 + 1800 - 120.0]
    assert context["purpose"].tolist() == ["education"] * 3
    assert context["group"].tolist() == ["school_age_6_17_not_employed", "employed",
                                         "senior_65plus_not_employed"]


def test_build_ranking_context_raises_on_a_missing_column_or_person():
    """Both inputs are checked: the offset column (without it a raw time cannot be recovered at
    all) and full person coverage (a population person with no attribute row cannot be placed in a
    cell, and dropping them would silently narrow the base)."""
    trips = _table(3)
    persons = pd.DataFrame({"person_id": [0, 1, 2], "age": [10, 40, 70],
                            "employed": [False, True, False]})
    with pytest.raises(ValueError, match=M.OFFSET_COLUMN):
        M.build_ranking_context(trips, persons)

    trips[M.OFFSET_COLUMN] = 0.0
    with pytest.raises(ValueError, match="no attribute row"):
        M.build_ranking_context(trips, persons.iloc[:2])


# ---------------------------------------------------------------------------
# ONE base rule at both call sites (ruling A-R20, fix round 1 of item 7).
# A rung's ranking base is the MODEL POPULATION of that rung's own key -- the cell at rung 1, every
# person of that purpose (thick groups included) at rung 2, everyone at rung 3 -- at BOTH call
# sites: with a ranking_context it is the population's, without one the model set is its own
# context. Ranking the still-pending persons among THEMSELVES at a pooled rung (the pre-A-R20
# no-context path) placed the same person hours away from where the other call site placed them.
# ---------------------------------------------------------------------------

def _work_reference(peak_bin=26, pooled_low=24, pooled_high=48, n_unweighted=500):
    """A three-rung work reference: ``(employed, work)`` with all its mass in ``peak_bin``, the
    pooled ``(all groups, work)`` cell UNIFORM over ``[pooled_low, pooled_high)`` (6:00-12:00 by
    default) and the fully pooled ``("all", "all")`` cell likewise.

    The uniform pooled rung is what makes a rank VISIBLE as a time: a person's quantile maps
    linearly onto the six-hour window, so two call sites that rank the same person differently are
    minutes or hours apart rather than inside one quarter hour.
    """
    pooled = {b: 1.0 / (pooled_high - pooled_low) for b in range(pooled_low, pooled_high)}
    rows = []
    for segment, purpose, shares in (("employed", "work", {peak_bin: 1.0}),
                                     ("all", "work", pooled),
                                     ("all", "all", pooled)):
        for b in range(M.N_BINS):
            rows.append({"universe": "at_home_zero", "segment": segment, "purpose": purpose,
                         "position": "first", "bin_15min": b,
                         "share_derounded": shares.get(b, 0.0),
                         "share_as_reported": shares.get(b, 0.0), "n_unweighted": n_unweighted})
    return pd.DataFrame(rows)


def _mixed_work_population():
    """The reviewer's example: 1,000 employed persons departing 6:01-6:17 and 50 thin-group
    persons (20 seniors, 30 non-employed adults) departing 10:01-10:50, all on a first WORK leg.

    Returns ``(trips, persons, thin_person_ids)``. Every departure time is off the five-minute
    grid, so the reporting-precision rule de-rounds it by exactly 0 and the two call sites can be
    compared to the second (the de-rounding REALISATION is the one thing that legitimately still
    differs between them -- see the consistency test below, which measures it).
    """
    values = _off_grid_seconds(1000, start_minute=361) + _off_grid_seconds(50, start_minute=601)
    trips = _table_with_departures(values, purpose="work")
    persons = pd.DataFrame({
        "person_id": range(1050),
        "age": [40] * 1000 + [70] * 20 + [40] * 30,
        "employed": [True] * 1000 + [False] * 50,
    })
    return trips, persons, list(range(1000, 1050))


def test_both_call_sites_place_a_thin_group_in_the_same_ranking_base():
    """Ruling A-R20 (fix round 1). The 50 thin-group persons climb to the ``(work, all groups)``
    rung at BOTH call sites, and their placement there must be the same person by person: that
    rung's base is every WORK person of the model population (the 1,000 employed included),
    whether the population arrives as the mapped set (the trip build, no context) or as a ranking
    context (the reporting-day splice).

    Before A-R20 the no-context path ranked the 50 among THEMSELVES, so they spread across the
    whole uniform six-hour pooled reference, while the context path -- correctly ranking them
    behind 1,000 earlier persons -- placed them in its last minutes: the two call sites modelled
    the same person's morning hours apart.
    """
    trips, persons, thin_ids = _mixed_work_population()
    arguments = dict(model=M.MODEL_SRV_MAPPED, random_seed=1, reference=_work_reference(),
                     min_reference_n=100, min_model_n=50)

    build, build_diagnostics = M.apply_departure_time_model(trips.copy(), persons, **arguments)

    thin_trips = trips[trips["person_id"].isin(thin_ids)].reset_index(drop=True)
    thin_persons = persons[persons["person_id"].isin(thin_ids)]
    context = M.build_ranking_context(trips.assign(**{M.OFFSET_COLUMN: 0.0}), persons)
    splice, splice_diagnostics = M.apply_departure_time_model(
        thin_trips, thin_persons, ranking_context=context, **arguments)

    for diagnostics in (build_diagnostics, splice_diagnostics):
        for group in ("senior_65plus_not_employed", "adult_18_64_not_employed"):
            assert diagnostics["cells"][("work", group)]["level"] == "purpose_all"

    build_first = (build[build["person_id"].isin(thin_ids) & (build["trip_index"] == 0)]
                   .set_index("person_id")["departure_time"])
    splice_first = splice[splice["trip_index"] == 0].set_index("person_id")["departure_time"]
    difference = (build_first - splice_first).abs()
    assert difference.max() <= 1.0, (
        "the two call sites place the same thin-group person up to %.1f min apart"
        % (difference.max() / 60.0))


def test_a_pooled_rung_ranks_the_thin_persons_among_every_person_of_that_purpose():
    """The base rule stated directly on the no-context path: at the ``(purpose, all)`` rung the
    thin persons' quantiles come from a base of 1,050 -- every work person of the model set -- not
    from the 50 pending ones. Since those 50 are the LATEST departures of that base, the uniform
    pooled reference must place every one of them in the last 5 % of its 6:00-12:00 window, never
    spread across the whole window.
    """
    trips, persons, thin_ids = _mixed_work_population()
    table, diagnostics = M.apply_departure_time_model(
        trips, persons, model=M.MODEL_SRV_MAPPED, random_seed=1, reference=_work_reference(),
        min_reference_n=100, min_model_n=50)

    cell = diagnostics["cells"][("work", "senior_65plus_not_employed")]
    assert cell["level"] == "purpose_all"
    assert cell["n_model"] == 20                    # its own persons: still the thin group
    assert cell["n_model_pooled"] == 50             # the pending persons mapped together
    assert cell["n_context"] == 20                  # the cell's own base
    assert cell["n_context_pooled"] == 1050         # the rung's base: every work person
    thin_first = table[table["person_id"].isin(thin_ids) & (table["trip_index"] == 0)]
    window_start, window_end = 24 * 900.0, 48 * 900.0
    assert (thin_first["departure_time"] > window_end - 0.05 * (window_end - window_start)).all()


def test_min_model_n_below_one_still_needs_a_non_empty_base():
    """Review Minor 2: the model side clamps ``min_model_n`` to >= 1 exactly as the reference side
    clamps ``min_reference_n``. Without the clamp, ``min_model_n=0`` would make a rung whose base
    is EMPTY "usable", and every person of that cell would be handed the quantile 0.5/(0+1) --
    a silent collapse of the whole cell onto the reference's median.
    """
    context = _ranking_context(n=600, purpose="work", group="employed")
    table, diagnostics = M.apply_departure_time_model(
        _table(30), _persons(30), model=M.MODEL_SRV_MAPPED, random_seed=1,
        reference=_reference(), min_reference_n=100, min_model_n=0, ranking_context=context)

    # The persons' own cell (education, school_age...) has NO context person at all, and neither
    # has the (education, all) rung; only the fully pooled rung has a base, so that is where they
    # map -- bin 32, not the bin-30 cell whose empty base a min_model_n of 0 would have accepted.
    cell = diagnostics["cells"][("education", "school_age_6_17_not_employed")]
    assert cell["level"] == "all_all" and cell["n_context"] == 0
    first = table[table["trip_index"] == 0]["departure_time"]
    assert ((first >= 32 * 900) & (first < 33 * 900)).all()


def test_a_ranking_context_with_a_duplicate_person_is_rejected():
    """Review Minor 3: one row per person, or the base double-counts that person and the
    de-rounding draw is not the one the person's own row deserves. Duplicates come from a broken
    join, which must fail loudly rather than quietly reweight the distribution."""
    context = _ranking_context(n=10)
    duplicated = pd.concat([context, context.iloc[:3]], ignore_index=True)
    with pytest.raises(ValueError, match="3 duplicate person_id"):
        M.apply_departure_time_model(_table(4), _persons(4), model=M.MODEL_SRV_MAPPED,
                                     random_seed=1, reference=_reference(), min_reference_n=100,
                                     min_model_n=1, ranking_context=duplicated)
