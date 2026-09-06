"""Model side of the SrV 2023 plan-structure comparison (issue #369, Task 10).

Covers the pure helpers of ``braunschweig.analysis.plan_structure`` (harmonisation to the
SrV comparison schema, the long comparison table, the synthetic-closure rates) and the
``braunschweig.analysis.synthesis.plan_structure_vs_srv`` synpp stage.

The stage test uses the real COMMITTED reference table
(``eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv``) rather than a
stub, so a schema drift between the committed file and the comparison code fails here and
not only on the run server; only the VG250 access (``spatial.assign_geographies``) is
monkeypatched, because a unit test must not depend on the gitignored VG250 archive.
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis import plan_structure as P
from braunschweig.analysis.synthesis import plan_structure_vs_srv as S
from braunschweig.calibration import srv_plan_structure as SRV

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO_ROOT, "eqasim-data", "data")


# --------------------------------------------------------------------------- fixtures

def _model_persons():
    """Four synthetic persons: two in Braunschweig (03101), one in Salzgitter (03102) and
    one in Wolfsburg (03103), which SrV does not survey (model-only segment)."""
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "household_id": [10, 20, 10, 30],
        "age": [40, 30, 10, 70],
        "sex": ["male", "female", "male", "female"],
        "employed": [True, False, False, False],
        "employment_status": ["employed", "unemployed", "student", "retired"],
    })


def _model_trips():
    """Six trips: three closed home->X->home days plus one person without any trip.

    Person 4 (Wolfsburg) is immobile -- the case the plan-structure fix is about (a donor
    without a diary leaves an empty plan). Person 1's return leg carries the synthetic
    home-closure flag.
    """
    rows = [
        # person_id, trip_index, departure_time, arrival_time, preceding, following, closure
        (1, 0, 8 * 3600.0, 8.5 * 3600.0, "home", "work", False),
        (1, 1, 17 * 3600.0, 17.5 * 3600.0, "work", "home", True),
        (2, 0, 9 * 3600.0, 9.25 * 3600.0, "home", "shop", False),
        (2, 1, 10 * 3600.0, 10.25 * 3600.0, "shop", "home", False),
        (3, 0, 7.5 * 3600.0, 8 * 3600.0, "home", "education", False),
        (3, 1, 13 * 3600.0, 13.5 * 3600.0, "education", "home", False),
    ]
    return pd.DataFrame(rows, columns=[
        "person_id", "trip_index", "departure_time", "arrival_time",
        "preceding_purpose", "following_purpose", "is_synthetic_closure"])


def _model_homes():
    """``assign_geographies`` output shape: one row per household_id carrying ars5."""
    return pd.DataFrame({"household_id": [10, 20, 30],
                         "ars5": ["03101", "03102", "03103"]})


def _model_homes_mostly_unmatched():
    """``assign_geographies`` output for a broken VG250 join: only one household resolves."""
    return pd.DataFrame({"household_id": [10, 20, 30],
                         "ars5": ["03101", None, None]})


def _reference_two_rows(universe=SRV.UNIVERSE_AT_HOME_ZERO):
    """Minimal reference frame in the committed long format."""
    return pd.DataFrame({
        "universe": [universe, universe],
        "segment": ["all", "all"],
        "metric": ["mobility_rate", "trips_per_person"],
        "value": [0.8, 3.0],
        "n_unweighted": [100, 100],
    })


# --------------------------------------------------------------------------- harmonise_model

def test_harmonise_model_yields_the_srv_comparison_schema():
    persons, trips = P.harmonise_model(_model_persons(), _model_trips(), _model_homes())

    assert list(persons.columns) == SRV.HARMONISED_PERSON_COLUMNS
    assert list(trips.columns) == SRV.HARMONISED_TRIP_COLUMNS

    # Every synthetic person exists and starts the day at home -- the at_home_zero universe.
    assert (persons["weight"] == 1.0).all()
    assert (trips["weight"] == 1.0).all()
    assert not persons["away_from_home"].any()
    assert persons["reported_at_home"].all()

    by_pid = persons.set_index("pid")
    assert by_pid.loc[1, "kreis"] == "03101"
    assert by_pid.loc[4, "kreis"] == "03103"
    assert by_pid.loc[1, "group"] == "employed"
    assert by_pid.loc[3, "group"] == "school_age_6_17_not_employed"
    assert by_pid.loc[4, "group"] == "senior_65plus_not_employed"
    assert by_pid.loc[1, "age_band"] == "25-44"
    # The immobile person keeps a row with zero trips (never dropped).
    assert by_pid.loc[4, "n_trips"] == 0
    assert by_pid.loc[1, "n_trips"] == 2

    # Trip purposes come from following_purpose, origins from preceding_purpose; the
    # eqasim seconds become the SrV-side minutes.
    first = trips.iloc[0]
    assert first["pid"] == 1 and first["seq"] == 0
    assert first["purpose"] == "work" and first["prev_purpose"] == "home"
    assert first["dep_min"] == pytest.approx(480.0)
    assert first["arr_min"] == pytest.approx(510.0)


def test_harmonise_model_output_passes_through_the_srv_metric_code():
    """The harmonised frames must be accepted by the SrV metric functions unchanged."""
    persons, trips = P.harmonise_model(_model_persons(), _model_trips(), _model_homes())
    per = SRV.person_level(persons, trips)
    metrics = SRV.segment_metrics(per, trips, "all")

    assert metrics["n_persons_unweighted"] == 4
    assert metrics["mobility_rate"] == pytest.approx(0.75)      # 3 of 4 persons move
    assert metrics["trips_per_person"] == pytest.approx(1.5)    # 6 trips / 4 persons
    assert metrics["share_mobile_last_to_home"] == pytest.approx(1.0)
    assert metrics["purpose_share_home"] == pytest.approx(0.5)


def test_harmonise_model_maps_an_unmapped_purpose_to_unknown(caplog):
    trips = _model_trips()
    trips.loc[0, "following_purpose"] = "business"
    with caplog.at_level("WARNING"):
        _, harmonised = P.harmonise_model(_model_persons(), trips, _model_homes())
    assert harmonised.iloc[0]["purpose"] == SRV.UNKNOWN_PURPOSE
    assert any("business" in record.getMessage() for record in caplog.records)


def test_harmonise_model_keeps_a_person_without_a_home_kreis_and_logs_the_rate(caplog):
    """An unresolvable home Kreis leaves the person in the model frame with kreis = NaN.

    The person must NOT be dropped here (that would shrink the model side invisibly); the
    stage is what excludes it from the head-to-head universe, after guarding the rate.
    """
    with caplog.at_level("INFO"):
        persons, _ = P.harmonise_model(_model_persons(), _model_trips(),
                                       _model_homes_mostly_unmatched())
    assert len(persons) == 4
    by_pid = persons.set_index("pid")
    assert by_pid.loc[1, "kreis"] == "03101"
    assert pd.isna(by_pid.loc[2, "kreis"]) and pd.isna(by_pid.loc[4, "kreis"])
    messages = " ".join(record.getMessage() for record in caplog.records)
    # Households 20 and 30 do not resolve, so persons 2 and 4 have no Kreis.
    assert "resolved for 2/4 persons" in messages and "unresolved 2" in messages


def test_harmonise_model_raises_on_a_trip_without_a_person():
    trips = _model_trips()
    trips.loc[0, "person_id"] = 99
    with pytest.raises(ValueError, match="99"):
        P.harmonise_model(_model_persons(), trips, _model_homes())


# --------------------------------------------------------------------------- compare

def test_compare_scales_share_metrics_to_percentage_points_and_keeps_counts_plain():
    model_long = pd.DataFrame({
        "segment": ["all", "all"],
        "metric": ["mobility_rate", "trips_per_person"],
        "value": [1.0, 2.0],
        "n_unweighted": [3, 3],
    })
    comparison = P.compare(model_long, _reference_two_rows())

    assert list(comparison.columns) == P.COMPARISON_COLUMNS
    mobility = comparison[comparison["metric"] == "mobility_rate"].iloc[0]
    assert mobility["model"] == pytest.approx(1.0)
    assert mobility["srv"] == pytest.approx(0.8)
    assert mobility["delta_pp"] == pytest.approx(20.0)          # share -> percentage points
    assert mobility["n_srv"] == 100
    rate = comparison[comparison["metric"] == "trips_per_person"].iloc[0]
    assert rate["delta_pp"] == pytest.approx(-1.0)              # plain difference


def test_compare_keeps_model_only_and_reference_only_rows_without_a_delta():
    model_long = pd.DataFrame({
        "segment": ["all", "kreis_03103"],
        "metric": ["mobility_rate", "mobility_rate"],
        "value": [1.0, 0.5],
        "n_unweighted": [3, 1],
    })
    reference = pd.concat([_reference_two_rows()], ignore_index=True)
    comparison = P.compare(model_long, reference)

    wolfsburg = comparison[comparison["segment"] == "kreis_03103"].iloc[0]
    assert wolfsburg["model"] == pytest.approx(0.5)
    assert np.isnan(wolfsburg["srv"]) and np.isnan(wolfsburg["delta_pp"])
    reference_only = comparison[comparison["metric"] == "trips_per_person"].iloc[0]
    assert np.isnan(reference_only["model"]) and np.isnan(reference_only["delta_pp"])


def test_compare_raises_when_the_reference_mixes_universes():
    model_long = pd.DataFrame({"segment": ["all"], "metric": ["mobility_rate"],
                               "value": [1.0], "n_unweighted": [3]})
    mixed = pd.concat([_reference_two_rows(SRV.UNIVERSE_AT_HOME_ZERO),
                       _reference_two_rows(SRV.UNIVERSE_AT_HOME_ONLY)], ignore_index=True)
    with pytest.raises(ValueError, match="universe"):
        P.compare(model_long, mixed)


# --------------------------------------------------------------------------- closure

def test_closure_metrics_counts_the_flagged_row():
    metrics = P.closure_metrics(_model_trips())

    assert metrics["closure_column_present"] is True
    assert metrics["n_trips"] == 6 and metrics["n_trips_synthetic_closure"] == 1
    assert metrics["share_trips_synthetic_closure"] == pytest.approx(1.0 / 6.0)
    # Persons WITH a trip are the denominator (same convention as trips_stage).
    assert metrics["n_persons"] == 3 and metrics["n_persons_closed"] == 1
    assert metrics["share_persons_closed"] == pytest.approx(1.0 / 3.0)
    # Three home trips, one of them synthesised.
    assert metrics["n_home_trips"] == 3
    assert metrics["share_home_trips_synthetic"] == pytest.approx(1.0 / 3.0)


def test_closure_metrics_reports_zero_and_flags_an_absent_column(caplog):
    trips = _model_trips().drop(columns=["is_synthetic_closure"])
    with caplog.at_level("WARNING"):
        metrics = P.closure_metrics(trips)
    assert metrics["closure_column_present"] is False
    assert metrics["share_trips_synthetic_closure"] == 0.0
    assert metrics["share_persons_closed"] == 0.0
    assert metrics["share_home_trips_synthetic"] == 0.0
    assert any("is_synthetic_closure" in record.getMessage() for record in caplog.records)


# --------------------------------------------------------------------------- accepted deviations

def test_accepted_deviations_reports_the_three_closed_plan_consequences():
    comparison = pd.DataFrame({
        "segment": ["all"] * 3,
        "metric": ["share_mobile_last_to_home", "share_mobile_first_from_home",
                   "share_trips_1"],
        "model": [1.0, 1.0, 0.0],
        "srv": [0.9776, 0.9781, 0.0058],
        "delta_pp": [2.24, 2.19, -0.58],
        "n_srv": [18223] * 3,
    })
    deviations = P.accepted_deviations(comparison)

    assert list(deviations.columns) == P.ACCEPTED_DEVIATION_COLUMNS
    assert set(deviations["deviation"]) == {"open_end_days", "open_start_days",
                                            "single_trip_days"}
    open_end = deviations[deviations["deviation"] == "open_end_days"].iloc[0]
    assert open_end["model"] == pytest.approx(0.0)
    assert open_end["srv"] == pytest.approx(1.0 - 0.9776)
    assert open_end["note"] == P.ACCEPTED_DEVIATION_NOTE


def test_accepted_deviation_note_names_the_issue_not_a_placeholder_adr_id():
    """The ADR id lives ONLY in the stage record's ``decisions:`` list (one fact, one file).

    An inlined placeholder such as "ADR E" would be a second, always-wrong home for it.
    """
    assert P.ACCEPTED_DEVIATION_NOTE == (
        "closed plans by decision (issue #367; ADR in the stage record's decisions)")
    assert "ADR E" not in P.ACCEPTED_DEVIATION_NOTE


# --------------------------------------------------------------------------- stage

class _ConfigureRecorder:
    """synpp ConfigurationContext stand-in: records declared stages and config defaults."""

    def __init__(self):
        self.stages = []
        self.config_keys = {}

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)

    def config(self, key, default=None):
        self.config_keys[key] = default
        return default


class _FakeContext:
    """synpp ExecuteContext stand-in (pattern of tests/test_completed_donor_stage.py).

    ``config`` deliberately takes the key ALONE: passing a default in ``execute`` is a
    runtime error in real synpp (tests/test_execute_context_config_contract.py), so the
    stand-in must not tolerate it either.
    """

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


def _stage_context(tmp_path, data_path=DATA_PATH, subdir="analysis/plan_structure_vs_srv"):
    return _FakeContext(
        stages={
            "synthesis.population.enriched": _model_persons(),
            "synthesis.population.trips": _model_trips(),
            "synthesis.population.spatial.home.locations": _model_homes().assign(
                geometry=[None, None, None]),
        },
        config={
            "output_path": str(tmp_path),
            "data_path": str(data_path),
            "sampling_rate": 1.0,
            S.KEY_SUBDIR: subdir,
            S.KEY_UNIVERSE: SRV.UNIVERSE_AT_HOME_ZERO,
            S.KEY_MAX_UNMATCHED_HOME_SHARE: S.DEFAULT_MAX_UNMATCHED_HOME_SHARE,
        },
        cache_path=tmp_path / "cache",
    )


def test_validate_hashes_the_metric_helpers_and_the_spatial_module():
    """The cache token must cover every module that shapes the comparison.

    synpp hashes only the stage module's own source; ``plan_structure`` and
    ``srv_plan_structure`` define the harmonisation and every metric, and
    ``braunschweig.analysis.spatial.assign_geographies`` decides every person's home Kreis and
    therefore the whole head-to-head universe.
    """
    from braunschweig.analysis import plan_structure, spatial
    from braunschweig.calibration import srv_plan_structure

    assert set(S._HELPER_MODULES) == {plan_structure, srv_plan_structure}
    assert S._DEFERRED_HELPER_MODULE_NAMES == ("braunschweig.analysis.spatial",)

    token = S.validate(None)
    assert len(token) == 32 and int(token, 16) >= 0        # md5 hex digest
    assert token == S.validate(None)                       # deterministic
    # The token really depends on the deferred module's source, not only on the two direct
    # helpers: hashing the three sources by hand must reproduce it.
    import hashlib
    import inspect
    expected = hashlib.md5()
    for module in (plan_structure, srv_plan_structure, spatial):
        expected.update(inspect.getsource(module).encode("utf-8"))
    assert token == expected.hexdigest()


def test_configure_declares_every_stage_and_config_key_execute_reads():
    recorder = _ConfigureRecorder()
    S.configure(recorder)

    assert "synthesis.population.enriched" in recorder.stages
    assert "synthesis.population.trips" in recorder.stages
    assert "synthesis.population.spatial.home.locations" in recorder.stages
    assert recorder.config_keys[S.KEY_SUBDIR] == S.DEFAULT_SUBDIR
    assert recorder.config_keys[S.KEY_UNIVERSE] == S.DEFAULT_UNIVERSE
    assert (recorder.config_keys[S.KEY_MAX_UNMATCHED_HOME_SHARE]
            == S.DEFAULT_MAX_UNMATCHED_HOME_SHARE)
    for key in ("output_path", "data_path", "sampling_rate"):
        assert key in recorder.config_keys


def test_execute_writes_the_comparison_against_the_committed_reference(tmp_path, monkeypatch):
    from braunschweig.analysis import spatial

    reference_path = os.path.join(DATA_PATH, "braunschweig", "srv",
                                  SRV.PLAN_STRUCTURE_TABLE)
    assert os.path.exists(reference_path), \
        "the committed SrV plan-structure reference must be present for this test to mean anything"
    monkeypatch.setattr(spatial, "assign_geographies",
                        lambda homes, kreise=None: _model_homes())

    result = S.execute(_stage_context(tmp_path))

    out_dir = tmp_path / "analysis" / "plan_structure_vs_srv"
    for name in ("comparison.csv", "headline.csv", "by_kreis.csv", "closure.csv",
                 "accepted_deviations.csv", "summary.md", "provenance.json"):
        assert (out_dir / name).exists(), name

    comparison = pd.read_csv(out_dir / "comparison.csv")
    assert list(comparison.columns) == P.COMPARISON_COLUMNS
    headline = pd.read_csv(out_dir / "headline.csv")
    assert set(headline["segment"]) == {"all"}

    # Head-to-head universe: only the three persons living in the seven SrV Kreise.
    mobility = headline[headline["metric"] == "mobility_rate"].iloc[0]
    assert mobility["model"] == pytest.approx(1.0)
    assert mobility["srv"] == pytest.approx(0.843720, abs=1e-6)
    assert mobility["delta_pp"] == pytest.approx((1.0 - 0.843720) * 100.0, abs=1e-4)
    assert mobility["n_srv"] == 18223

    # Wolfsburg is not surveyed by SrV: model-only row, never a substituted zero.
    by_kreis = pd.read_csv(out_dir / "by_kreis.csv")
    wolfsburg = by_kreis[(by_kreis["segment"] == "kreis_03103")
                         & (by_kreis["metric"] == "mobility_rate")].iloc[0]
    assert wolfsburg["model"] == pytest.approx(0.0)      # the single Wolfsburg person is immobile
    assert np.isnan(wolfsburg["srv"]) and np.isnan(wolfsburg["delta_pp"])
    assert "kreis_03101" in set(by_kreis["segment"])

    closure = pd.read_csv(out_dir / "closure.csv")
    share = dict(zip(closure["metric"], closure["value"]))
    assert share["share_trips_synthetic_closure"] == pytest.approx(1.0 / 6.0)

    deviations = pd.read_csv(out_dir / "accepted_deviations.csv")
    assert set(deviations["deviation"]) == {"open_end_days", "open_start_days",
                                            "single_trip_days"}

    provenance = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["parameters"]["srv_universe"] == SRV.UNIVERSE_AT_HOME_ZERO
    assert provenance["inputs"]["reference_path"] == reference_path
    assert provenance["model"]["kreise_model_only"] == ["03103"]
    assert provenance["model"]["n_persons_in_scope"] == 3

    assert set(result) == {"comparison", "headline", "by_kreis", "closure",
                           "accepted_deviations"}


def test_execute_raises_when_the_committed_reference_is_missing(tmp_path):
    missing_data_path = tmp_path / "no_data"
    missing_data_path.mkdir()
    expected = os.path.join(str(missing_data_path), "braunschweig", "srv",
                            SRV.PLAN_STRUCTURE_TABLE)
    with pytest.raises(FileNotFoundError, match=SRV.PLAN_STRUCTURE_TABLE):
        S.execute(_stage_context(tmp_path, data_path=missing_data_path))
    assert not os.path.exists(expected)


def test_execute_raises_when_too_many_homes_have_no_kreis(tmp_path, monkeypatch):
    """A broken VG250 / household join must abort, not produce a well-formed report of NaNs."""
    from braunschweig.analysis import spatial

    monkeypatch.setattr(spatial, "assign_geographies",
                        lambda homes, kreise=None: _model_homes_mostly_unmatched())
    with pytest.raises(ValueError,
                       match=S.KEY_MAX_UNMATCHED_HOME_SHARE):
        S.execute(_stage_context(tmp_path))
    assert not (tmp_path / "analysis").exists(),         "the stage must abort before writing any report file"


def test_execute_excludes_a_person_without_a_home_kreis_from_the_head_to_head_universe(
        tmp_path, monkeypatch):
    """One unresolvable home stays under the guard threshold but leaves the 'all' segment.

    With the threshold raised for this test the run completes, and the excluded person must be
    visible in provenance.json (counted, not silently absorbed): the head-to-head 'all'
    segment covers 2 of the 4 persons -- the Wolfsburg one is out of scope and the
    Kreis-less one is unmatched.
    """
    from braunschweig.analysis import spatial

    monkeypatch.setattr(
        spatial, "assign_geographies",
        lambda homes, kreise=None: pd.DataFrame({"household_id": [10, 20, 30],
                                                 "ars5": ["03101", None, "03103"]}))
    context = _stage_context(tmp_path)
    context._config[S.KEY_MAX_UNMATCHED_HOME_SHARE] = 0.5
    S.execute(context)

    out_dir = tmp_path / "analysis" / "plan_structure_vs_srv"
    provenance = json.loads((out_dir / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["model"]["n_persons_total"] == 4
    assert provenance["model"]["n_persons_in_scope"] == 2      # persons 1 and 3, both in 03101
    assert provenance["model"]["n_persons_no_kreis"] == 1      # person 2, household 20
    assert provenance["model"]["share_persons_no_kreis"] == pytest.approx(0.25)
    assert provenance["model"]["n_persons_out_of_scope"] == 1  # person 4, Wolfsburg
    assert provenance["parameters"]["max_unmatched_home_share"] == 0.5

    headline = pd.read_csv(out_dir / "headline.csv")
    n_persons = headline[headline["metric"] == "n_persons_unweighted"].iloc[0]
    assert n_persons["model"] == 2


def test_execute_raises_on_an_unknown_universe(tmp_path):
    context = _stage_context(tmp_path)
    context._config[S.KEY_UNIVERSE] = "no_such_universe"
    with pytest.raises(ValueError, match="no_such_universe"):
        S.execute(context)
