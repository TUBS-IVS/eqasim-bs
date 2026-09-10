"""Departure-time decomposition and the de-rounded SrV comparison (issue #123, Task 5).

Covers the pure helpers of :mod:`braunschweig.analysis.departure_time` (the three-way
raw / pre-offset / realised decomposition, the 15-minute bin comparison, the EMD table and
the hold-out activity-duration comparison) and the
:mod:`braunschweig.analysis.synthesis.departure_time_vs_srv` synpp stage.

The stage test runs against the REAL committed references
(``eqasim-data/data/braunschweig/srv/srv2023_departure_time_reference.csv`` and
``...srv2023_activity_duration_reference.csv``) rather than a stub, so a schema drift between
the committed files and the comparison code fails here and not only on the run server. NOTHING
is monkeypatched: unlike ``tests/test_plan_structure_vs_srv.py`` this stage performs no VG250
join (it does not segment by Kreis), so it touches no gitignored input at all.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import departure_time as D
from braunschweig.analysis.synthesis import departure_time_vs_srv as S
from braunschweig.calibration import srv_departure_times as SRVDT
from braunschweig.calibration import srv_plan_structure as SRV
from braunschweig.popsim.departure_time_model import OFFSET_COLUMN, person_groups
from braunschweig.popsim.departure_time_model import persons_from_synthetic_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO_ROOT, "eqasim-data", "data")

HOUR = 3600.0
MINUTE = 60.0


# --------------------------------------------------------------------------- fixtures

def _model_persons():
    """Four synthetic persons in TWO harmonised groups (the ``enriched`` frame's schema).

    Persons 1/2 are ``employed``; persons 3/4 are 10 and 12 years old and not employed, so
    both land in ``school_age_6_17_not_employed`` -- the segment the summary headline reports.
    """
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "household_id": [10, 20, 10, 30],
        "age": [40, 30, 10, 12],
        "sex": ["male", "female", "male", "female"],
        "employed": [True, True, False, False],
    })


def _model_trips():
    """Eight legs: four closed home->X->home days, with the MiD raw time columns and the offset.

    Person 1 carries a real +900 s offset, so ``realised = raw + offset`` is visible in the
    decomposition (raw 7:45 -> realised 8:00). Persons 3 and 4 carry the MiD design code 99 in
    ``W_SZS``/``W_SZM`` on BOTH legs, i.e. no raw time at all: they are the NaN-safe case, and
    they make the (school_age_6_17_not_employed, education, first) cell a cell with ``n_raw ==
    0`` whose ``share_raw`` must be NaN throughout rather than a fabricated zero.
    """
    rows = [
        # pid, seq, departure_time, arrival_time, preceding, following, W_SZS, W_SZM, offset
        (1, 0, 8 * HOUR, 8.5 * HOUR, "home", "work", 7, 45, 900.0),
        (1, 1, 17 * HOUR, 17.5 * HOUR, "work", "home", 16, 45, 900.0),
        (2, 0, 9 * HOUR, 9.25 * HOUR, "home", "shop", 9, 0, 0.0),
        (2, 1, 10 * HOUR, 10.25 * HOUR, "shop", "home", 10, 0, 0.0),
        (3, 0, 7.5 * HOUR, 8 * HOUR, "home", "education", 99, 99, 0.0),
        (3, 1, 13 * HOUR, 13.5 * HOUR, "education", "home", 99, 99, 0.0),
        (4, 0, 7 * HOUR, 7.5 * HOUR, "home", "education", 99, 99, 0.0),
        (4, 1, 14 * HOUR, 14.5 * HOUR, "education", "home", 99, 99, 0.0),
    ]
    return pd.DataFrame(rows, columns=[
        "person_id", "trip_index", "departure_time", "arrival_time",
        "preceding_purpose", "following_purpose", "W_SZS", "W_SZM", OFFSET_COLUMN])


def _model_groups(persons=None):
    return person_groups(persons_from_synthetic_schema(
        _model_persons() if persons is None else persons))


def _harmonised():
    return D.harmonise_model_times(_model_persons(), _model_trips(), _model_groups())


def _dense_reference(cells, n_unweighted=1000):
    """A hand-built departure-time reference in the committed DENSE shape.

    ``cells`` maps ``(segment, purpose, position)`` to a dict ``{bin_15min: share}``; every
    listed cell gets all :data:`SRVDT.N_BINS` rows, unlisted bins 0.0, and the same share in
    both ``share_derounded`` and ``share_as_reported`` unless a two-tuple is given.
    """
    rows = []
    for (segment, purpose, position), shares in cells.items():
        for b in range(SRVDT.N_BINS):
            value = shares.get(b, 0.0)
            derounded, as_reported = value if isinstance(value, tuple) else (value, value)
            rows.append({"universe": SRVDT.UNIVERSE, "segment": segment, "purpose": purpose,
                         "position": position, "bin_15min": b,
                         "share_derounded": derounded, "share_as_reported": as_reported,
                         "n_unweighted": n_unweighted})
    return pd.DataFrame(rows, columns=SRVDT.DEPARTURE_TIME_COLUMNS)


# --------------------------------------------------------------------------- harmonisation

def test_harmonise_model_times_decomposes_raw_pre_offset_and_realised():
    frame = D.harmonise_model_times(_model_persons(), _model_trips(), _model_groups())

    assert list(frame.columns) == D.HARMONISED_COLUMNS
    assert len(frame) == 8

    first_leg = frame[(frame["pid"] == 1) & (frame["seq"] == 0)].iloc[0]
    # realised = raw + offset: 7:45 + 15 min = 8:00.
    assert first_leg["raw_dep_min"] == pytest.approx(7 * 60 + 45)
    assert first_leg["pre_offset_dep_min"] == pytest.approx(7 * 60 + 45)
    assert first_leg["realised_dep_min"] == pytest.approx(8 * 60)
    assert first_leg["arr_min"] == pytest.approx(8.5 * 60)
    assert first_leg["purpose"] == "work"
    assert first_leg["position"] == D.POSITION_FIRST
    assert first_leg["segment_group"] == "employed"

    # The MiD design code 99 invalidates the raw time; the realised one is untouched.
    coded = frame[(frame["pid"] == 3) & (frame["seq"] == 0)].iloc[0]
    assert np.isnan(coded["raw_dep_min"])
    assert coded["realised_dep_min"] == pytest.approx(7.5 * 60)
    assert coded["segment_group"] == "school_age_6_17_not_employed"

    assert set(frame["position"]) == {D.POSITION_FIRST, D.POSITION_LATER}
    assert (frame[frame["seq"] == 1]["position"] == D.POSITION_LATER).all()

    # Raw-time coverage is a first-class diagnostic, not a footnote.
    assert frame.attrs["n_legs"] == 8
    assert frame.attrs["n_legs_with_raw_time"] == 4
    assert frame.attrs["share_legs_with_raw_time"] == pytest.approx(0.5)


def test_harmonise_model_times_refuses_a_trips_frame_without_the_offset_column():
    trips = _model_trips().drop(columns=[OFFSET_COLUMN])
    with pytest.raises(ValueError) as error:
        D.harmonise_model_times(_model_persons(), trips, _model_groups(),
                                trips_view="final")
    message = str(error.value)
    assert OFFSET_COLUMN in message
    assert "final" in message
    assert "departure_time_model" in message


def test_harmonise_model_times_reports_absent_raw_columns_instead_of_failing():
    """A trips view without W_SZS/W_SZM keeps working: raw_dep_min is NaN throughout."""
    trips = _model_trips().drop(columns=list(D.RAW_HOUR_COLUMNS))
    frame = D.harmonise_model_times(_model_persons(), trips, _model_groups())
    assert frame["raw_dep_min"].isna().all()
    assert frame.attrs["n_legs_with_raw_time"] == 0
    assert frame.attrs["raw_columns_present"] is False


# --------------------------------------------------------------------------- decomposition

def test_decomposition_is_dense_over_hours_and_carries_the_three_time_columns():
    table = D.decomposition(_harmonised())

    assert list(table.columns) == D.DECOMPOSITION_COLUMNS
    assert sorted(table["hour"].unique()) == list(D.HOURS)
    n_cells = len(D.SEGMENTS) * len(D.PURPOSES) * len(D.POSITIONS)
    assert len(table) == n_cells * len(D.HOURS)

    # employed / work / first: person 1 only -- raw hour 7, realised hour 8.
    cell = table[(table["segment"] == "employed") & (table["purpose"] == "work")
                 & (table["position"] == D.POSITION_FIRST)].set_index("hour")
    assert cell["n"].iloc[0] == 1
    assert cell["n_raw"].iloc[0] == 1
    assert cell.loc[7, "share_raw"] == pytest.approx(1.0)
    assert cell.loc[7, "share_pre_offset"] == pytest.approx(1.0)
    assert cell.loc[8, "share_realised"] == pytest.approx(1.0)
    assert cell.loc[7, "share_realised"] == pytest.approx(0.0)


def test_decomposition_share_raw_is_nan_when_the_cell_has_no_raw_time():
    """No fabricated zero for a cell whose legs all carry a coded MiD time."""
    table = D.decomposition(_harmonised())
    cell = table[(table["segment"] == "school_age_6_17_not_employed")
                 & (table["purpose"] == "education")
                 & (table["position"] == D.POSITION_FIRST)].set_index("hour")
    assert cell["n"].iloc[0] == 2
    assert cell["n_raw"].iloc[0] == 0
    assert cell["share_raw"].isna().all()
    # The realised side is unaffected: both education legs depart in hour 7.
    assert cell.loc[7, "share_realised"] == pytest.approx(1.0)


def test_decomposition_clips_late_hours_into_the_last_hour_and_counts_the_clip():
    trips = _model_trips()
    trips.loc[trips.index[-1], "departure_time"] = 30 * HOUR      # beyond hour 27
    frame = D.harmonise_model_times(_model_persons(), trips, _model_groups())
    table = D.decomposition(frame)
    assert table.attrs["n_hours_clipped_realised"] == 1
    cell = table[(table["segment"] == "all") & (table["purpose"] == "home")
                 & (table["position"] == D.POSITION_LATER)].set_index("hour")
    assert cell.loc[D.HOURS[-1], "share_realised"] > 0.0


# --------------------------------------------------------------------------- bin comparison

def test_bin_comparison_and_emd_are_zero_when_the_model_reproduces_the_reference():
    frame = _harmonised()
    # The two education first legs both fall in the 7:00-7:15 / 7:30-7:45 bins.
    reference = _dense_reference({
        ("school_age_6_17_not_employed", "education", D.POSITION_FIRST): {28: 0.5, 30: 0.5},
    })
    comparison = D.bin_comparison(frame, reference)

    assert list(comparison.columns) == D.COMPARISON_COLUMNS
    assert len(comparison) == SRVDT.N_BINS
    assert comparison["share_model"].sum() == pytest.approx(1.0)
    assert set(comparison["n_model"]) == {2}
    assert set(comparison["n_srv"]) == {1000}

    emd = D.emd_table(comparison)
    assert list(emd.columns) == D.EMD_COLUMNS
    assert len(emd) == 1
    assert emd["emd_vs_derounded"].iloc[0] == pytest.approx(0.0, abs=1e-12)
    assert emd["emd_vs_as_reported"].iloc[0] == pytest.approx(0.0, abs=1e-12)


def test_emd_table_labels_the_first_position_calibrated_and_the_others_holdout():
    frame = _harmonised()
    reference = _dense_reference({
        ("all", SRVDT.PURPOSE_ALL, D.POSITION_FIRST): {28: 1.0},
        ("all", SRVDT.PURPOSE_ALL, D.POSITION_LATER): {28: 1.0},
        ("all", SRVDT.PURPOSE_ALL, D.POSITION_ALL): {28: 1.0},
    })
    emd = D.emd_table(D.bin_comparison(frame, reference)).set_index("position")
    assert emd.loc[D.POSITION_FIRST, "dimension"] == D.DIMENSION_CALIBRATED
    assert emd.loc[D.POSITION_LATER, "dimension"] == D.DIMENSION_HOLDOUT
    assert emd.loc[D.POSITION_ALL, "dimension"] == D.DIMENSION_HOLDOUT
    # A genuine distance, not a zero: the model's later legs are spread over the afternoon.
    assert emd.loc[D.POSITION_LATER, "emd_vs_derounded"] > 0.0


def test_bin_comparison_skips_cells_where_either_side_is_empty():
    """An empty side is reported as a skipped cell, never compared against fabricated zeros."""
    frame = _harmonised()
    reference = pd.concat([
        # The model has no 'child_0_5' person at all -> model side empty.
        _dense_reference({("child_0_5", "education", D.POSITION_FIRST): {28: 1.0}}),
        # n_unweighted 0 is how the builder records an EMPTY reference cell (NaN shares).
        _dense_reference({("employed", "work", D.POSITION_FIRST): {}}, n_unweighted=0),
    ], ignore_index=True)
    reference.loc[reference["n_unweighted"] == 0,
                  ["share_derounded", "share_as_reported"]] = float("nan")

    comparison = D.bin_comparison(frame, reference)
    assert len(comparison) == 0
    assert comparison.attrs["n_cells_skipped_model_empty"] == 1
    assert comparison.attrs["n_cells_skipped_reference_empty"] == 1
    assert comparison.attrs["n_cells_compared"] == 0


def test_bin_comparison_against_the_committed_reference_covers_every_position():
    frame = _harmonised()
    reference = S.load_departure_time_reference(DATA_PATH)
    comparison = D.bin_comparison(frame, reference)
    emd = D.emd_table(comparison)

    assert set(emd["position"]) == set(D.POSITIONS)
    assert set(emd["dimension"]) == {D.DIMENSION_CALIBRATED, D.DIMENSION_HOLDOUT}
    assert (emd["emd_vs_derounded"] >= 0.0).all()
    assert (emd["emd_vs_derounded"] <= 1.0).all()
    # The pooled cell exists on both sides, so it must be compared.
    pooled = emd[(emd["segment"] == "all") & (emd["purpose"] == SRVDT.PURPOSE_ALL)
                 & (emd["position"] == D.POSITION_FIRST)]
    assert len(pooled) == 1
    assert pooled["n_model"].iloc[0] == 4


# --------------------------------------------------------------------- activity durations

def test_activity_duration_comparison_measures_the_next_departure_minus_arrival():
    frame = _harmonised()
    reference = pd.DataFrame({
        "universe": [SRVDT.UNIVERSE] * len(SRVDT.DURATION_BAND_LABELS),
        "segment": ["employed"] * len(SRVDT.DURATION_BAND_LABELS),
        "purpose": ["work"] * len(SRVDT.DURATION_BAND_LABELS),
        "band": list(SRVDT.DURATION_BAND_LABELS),
        "share": [0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        "n_unweighted": [500] * len(SRVDT.DURATION_BAND_LABELS),
    })
    table = D.activity_duration_comparison(frame, reference)

    assert list(table.columns) == D.DURATION_COLUMNS
    assert len(table) == len(SRVDT.DURATION_BAND_LABELS)
    # Person 1: arrival 8:30, next departure 17:00 -> 8.5 h -> the "8h+" band.
    by_band = table.set_index("band")
    assert by_band.loc["8h+", "share_model"] == pytest.approx(1.0)
    assert by_band.loc["8h+", "share_srv"] == pytest.approx(1.0)
    assert by_band.loc["8h+", "delta_pp"] == pytest.approx(0.0)
    assert by_band.loc["4-8h", "share_model"] == pytest.approx(0.0)
    assert by_band.loc["8h+", "n_model"] == 1
    assert by_band.loc["8h+", "n_srv"] == 500
    assert table.attrs["n_legs_excluded_no_next_or_missing_time"] == 4


def test_activity_duration_comparison_excludes_out_of_range_durations():
    trips = _model_trips()
    # Person 2's shop activity becomes 30 h long: beyond WORK_ACTIVITY_MAX_H, so excluded.
    trips.loc[(trips["person_id"] == 2) & (trips["trip_index"] == 1), "departure_time"] = \
        39.25 * HOUR
    frame = D.harmonise_model_times(_model_persons(), trips, _model_groups())
    reference = pd.DataFrame({
        "universe": [SRVDT.UNIVERSE] * len(SRVDT.DURATION_BAND_LABELS),
        "segment": ["employed"] * len(SRVDT.DURATION_BAND_LABELS),
        "purpose": ["shop"] * len(SRVDT.DURATION_BAND_LABELS),
        "band": list(SRVDT.DURATION_BAND_LABELS),
        "share": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        "n_unweighted": [40] * len(SRVDT.DURATION_BAND_LABELS),
    })
    table = D.activity_duration_comparison(frame, reference)
    assert table.attrs["n_legs_excluded_out_of_range"] == 1
    # The only shop activity was excluded, so the cell has no model leg and is skipped.
    assert len(table) == 0
    assert table.attrs["n_cells_skipped_model_empty"] == 1


def test_activity_duration_comparison_against_the_committed_reference():
    frame = _harmonised()
    reference = S.load_activity_duration_reference(DATA_PATH)
    table = D.activity_duration_comparison(frame, reference)
    assert len(table) > 0
    assert set(table["band"]) <= set(SRVDT.DURATION_BAND_LABELS)
    for (segment, purpose), cell in table.groupby(["segment", "purpose"]):
        assert cell["share_model"].sum() == pytest.approx(1.0), (segment, purpose)


# --------------------------------------------------------------------------- stage: shape

class _ConfigureRecorder:
    """synpp ConfigurationContext stand-in: records declared stages and config defaults."""

    def __init__(self, config=None):
        self.stages = []
        self.config_keys = {}
        self._config = dict(config) if config else {}

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)

    def config(self, key, default=None):
        value = self._config.get(key, default)
        self.config_keys[key] = value
        return value


class _FakeContext:
    """synpp ExecuteContext stand-in; ``config`` takes the key ALONE, as real synpp does."""

    def __init__(self, stages, config, cache_path):
        self._stages = stages
        self._config = config
        self._cache_path = str(cache_path)
        self.info = {}

    def stage(self, name, alias=None):
        return self._stages[name]

    def config(self, key):
        return self._config[key]

    def path(self):
        return self._cache_path

    def set_info(self, key, value):
        self.info[key] = value


def _stage_context(tmp_path, data_path=DATA_PATH, trips=None,
                   subdir="analysis/departure_time_vs_srv"):
    """A stub execute() context keyed by the LOCAL alias names ``configure`` declares."""
    return _FakeContext(
        stages={
            "synthesis.population.enriched": _model_persons(),
            "trips": _model_trips() if trips is None else trips,
        },
        config={
            "output_path": str(tmp_path),
            "data_path": str(data_path),
            "sampling_rate": 1.0,
            S.KEY_SUBDIR: subdir,
            S.KEY_TRIPS_VIEW: S.DEFAULT_TRIPS_VIEW,
            S.KEY_DEPARTURE_TIME_MODEL: S.DEFAULT_DEPARTURE_TIME_MODEL,
        },
        cache_path=tmp_path / "cache",
    )


def test_configure_declares_every_stage_and_config_key_execute_reads():
    recorder = _ConfigureRecorder()
    S.configure(recorder)

    assert "synthesis.population.enriched" in recorder.stages
    assert "synthesis.population.trips.final" in recorder.stages
    assert "synthesis.population.trips" not in recorder.stages
    # The receiving-person group comes from the SYNTHETIC schema on `enriched` (ruling A-R13),
    # so no `sampled` dependency, and no Kreis segmentation, so no home-location dependency.
    assert "synthesis.population.sampled" not in recorder.stages
    assert "synthesis.population.spatial.home.locations" not in recorder.stages

    assert recorder.config_keys[S.KEY_SUBDIR] == S.DEFAULT_SUBDIR
    assert recorder.config_keys[S.KEY_TRIPS_VIEW] == S.DEFAULT_TRIPS_VIEW
    assert (recorder.config_keys[S.KEY_DEPARTURE_TIME_MODEL]
            == S.DEFAULT_DEPARTURE_TIME_MODEL)
    for key in ("output_path", "data_path", "sampling_rate"):
        assert key in recorder.config_keys


def test_configure_declares_the_final_view_by_default_and_the_pre_assignment_view_on_request():
    rec = _ConfigureRecorder(config={S.KEY_TRIPS_VIEW: "pre_assignment"})
    S.configure(rec)
    assert "synthesis.population.trips" in rec.stages
    assert "synthesis.population.trips.final" not in rec.stages


def test_configure_raises_on_an_unknown_trips_view():
    rec = _ConfigureRecorder(config={S.KEY_TRIPS_VIEW: "no_such_view"})
    with pytest.raises(ValueError, match="no_such_view"):
        S.configure(rec)


def test_validate_hashes_the_pure_module_and_the_reference_builders():
    from braunschweig.analysis import departure_time, plan_structure
    from braunschweig.calibration import srv_departure_times, srv_plan_structure
    from braunschweig.popsim import departure_time_model

    assert set(S._HELPER_MODULES) == {departure_time, plan_structure, srv_departure_times,
                                      srv_plan_structure, departure_time_model}
    token = S.validate(None)
    assert len(token) == 32 and int(token, 16) >= 0
    assert token == S.validate(None)


# --------------------------------------------------------------------------- stage: execute

def test_execute_writes_the_six_report_files_against_the_committed_references(tmp_path):
    for name in (SRVDT.DEPARTURE_TIME_TABLE, SRVDT.ACTIVITY_DURATION_TABLE):
        assert os.path.exists(os.path.join(DATA_PATH, "braunschweig", "srv", name)), \
            "the committed SrV reference %s must be present for this test to mean anything" % name

    result = S.execute(_stage_context(tmp_path))

    out_dir = tmp_path / "analysis" / "departure_time_vs_srv"
    for name in ("decomposition.csv", "comparison.csv", "emd.csv", "activity_duration.csv",
                 "summary.md", "provenance.json"):
        assert (out_dir / name).exists(), name

    decomposition = pd.read_csv(out_dir / "decomposition.csv")
    assert list(decomposition.columns) == D.DECOMPOSITION_COLUMNS
    emd = pd.read_csv(out_dir / "emd.csv")
    assert list(emd.columns) == D.EMD_COLUMNS
    assert set(emd["dimension"]) == {D.DIMENSION_CALIBRATED, D.DIMENSION_HOLDOUT}

    provenance = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["parameters"]["trips_view"] == S.DEFAULT_TRIPS_VIEW
    assert provenance["parameters"]["departure_time_model"] == S.DEFAULT_DEPARTURE_TIME_MODEL
    assert provenance["inputs"]["departure_time_reference_path"].endswith(
        SRVDT.DEPARTURE_TIME_TABLE)
    assert provenance["inputs"]["activity_duration_reference_path"].endswith(
        SRVDT.ACTIVITY_DURATION_TABLE)
    # Raw-time coverage is recorded, not only logged: half the fixture legs carry a coded time.
    assert provenance["model"]["n_legs"] == 8
    assert provenance["model"]["n_legs_with_raw_time"] == 4
    assert provenance["model"]["share_legs_with_raw_time"] == pytest.approx(0.5)

    summary = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "measured" in summary
    assert "validated" not in summary
    # The headline SrV numbers are READ from the committed table, never typed into the code.
    assert "9.91" in summary and "82.57" in summary

    assert set(result) == {"decomposition", "comparison", "emd", "activity_duration"}


def test_execute_refuses_a_trips_frame_without_the_offset_column(tmp_path):
    trips = _model_trips().drop(columns=[OFFSET_COLUMN])
    with pytest.raises(ValueError) as error:
        S.execute(_stage_context(tmp_path, trips=trips))
    message = str(error.value)
    assert OFFSET_COLUMN in message
    assert S.DEFAULT_TRIPS_VIEW in message
    assert "departure_time_model" in message


def test_execute_raises_when_a_committed_reference_is_missing(tmp_path):
    empty_data_path = tmp_path / "no_data"
    (empty_data_path / "braunschweig" / "srv").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match=SRVDT.DEPARTURE_TIME_TABLE):
        S.execute(_stage_context(tmp_path, data_path=empty_data_path))


def test_the_committed_reference_carries_the_segments_and_positions_the_stage_compares():
    """Schema contract against the real files: a drift must fail here, not on the run server."""
    reference = S.load_departure_time_reference(DATA_PATH)
    assert set(reference["position"].unique()) == set(D.POSITIONS)
    assert set(reference["segment"].unique()) == set(D.SEGMENTS)
    assert set(reference["purpose"].unique()) == set(D.PURPOSES)
    assert set(reference["universe"].unique()) == {SRVDT.UNIVERSE}

    durations = S.load_activity_duration_reference(DATA_PATH)
    assert set(durations["segment"].unique()) <= set(D.SEGMENTS)
    assert set(durations["purpose"].unique()) <= set(D.PURPOSES)
    assert set(durations["band"].unique()) <= set(SRVDT.DURATION_BAND_LABELS)


def test_the_segments_and_purposes_are_the_reference_builder_s_own():
    """One taxonomy, not a copy: a change on the SrV side must move this stage with it."""
    assert D.SEGMENTS == SRVDT.SEGMENTS
    assert D.PURPOSES == SRVDT.PURPOSES_WITH_ALL
    assert D.POSITIONS == SRVDT.POSITIONS
    assert D.SEGMENTS[0] == "all" and set(D.SEGMENTS[1:]) == set(SRV.GROUPS)
