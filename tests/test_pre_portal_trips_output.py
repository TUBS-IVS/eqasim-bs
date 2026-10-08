"""The pre-portal trips file of the synthesis output and the readers that prefer it (eqasim-bs#442, ADR-0141).

With ``braunschweig.portal.enabled`` true the written ``<prefix>trips.csv`` is the POST-portal table (far
destinations carry the purpose ``outside``, the inner legs of a stay are dropped). The validators that compare
the diary with a travel survey must see the donor purposes, so ``braunschweig.synthesis.commute_day.output_day``
additionally writes ``<prefix>trips_pre_portal.csv`` from ``trips_day_stage`` and the file-based readers prefer it.

Covered here:

* the vendored column derivation, extracted into ``synthesis.output.prepare_trip_output_frame`` without a change
  of the legacy bytes;
* ``output_day`` configure/execute: flag declared with the shared default, stage declared only when the flag is
  on, the file written with the vendored column set and the donor purposes, nothing new written when it is off;
* the shared reader resolver, ``population_source`` and ``run_mid_validation`` preferring the file and logging it;
* a regression with the frames of the reproduction: ``trip_coherence.purpose_distribution`` raises on the
  rewritten table and matches the donor values on the pre-portal file; ``realised_participation`` counts a far
  worker as working on the pre-portal file only.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import synthesis.output as vendored  # noqa: E402
from braunschweig.analysis import pipeline_trips_file as PTF  # noqa: E402
from braunschweig.analysis import run_mid_validation as RMV  # noqa: E402
from braunschweig.analysis.population_validation import population_source as ps  # noqa: E402
from braunschweig.analysis.population_validation import participation_fit as pf  # noqa: E402
from braunschweig.analysis.population_validation import trip_coherence as tc  # noqa: E402
from braunschweig.synthesis.commute_day import output_day as OUTPUT  # noqa: E402
from braunschweig.synthesis.portal_trips import config_keys as PORTAL  # noqa: E402
from braunschweig.synthesis.portal_trips import rewrite as rw  # noqa: E402

PRE_PORTAL_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"
DAY_TRIPS_STAGE = "synthesis.population.trips.final"
VENDORED_TRIP_COLUMNS = [
    "person_id", "trip_index", "preceding_activity_index", "following_activity_index",
    "departure_time", "arrival_time", "preceding_purpose", "following_purpose", "is_first", "is_last",
]


def _donor_trips() -> pd.DataFrame:
    """The reporting-day trips as ``trips_day_stage`` hands them on: the eqasim trips schema, donor purposes."""
    return pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "preceding_purpose": ["home", "work", "home", "shop"],
        "following_purpose": ["work", "home", "shop", "home"],
        "mode": ["car", "car", "walk", "walk"],
        "euclidean_distance": [90000.0, 90000.0, 800.0, 800.0],
        "departure_time": [7 * 3600.0, 16 * 3600.0, 10 * 3600.0, 11 * 3600.0],
        "arrival_time": [8 * 3600.0, 17 * 3600.0, 10.2 * 3600.0, 11.2 * 3600.0],
        "is_first_trip": [True, False, True, False],
        "is_last_trip": [False, True, False, True],
    })


def _post_portal_trips() -> pd.DataFrame:
    """The same day after the portal rewrite: the far work leg of person 1 became an outside stay."""
    trips = _donor_trips()
    trips.loc[0, "following_purpose"] = "outside"
    trips.loc[1, "preceding_purpose"] = "outside"
    return trips


# ------------------------------------------------------------------ vendored derivation


def test_prepare_trip_output_frame_keeps_the_legacy_derivation():
    # Pinned independently of the helper: the legacy block of synthesis.output.execute, written out literally.
    legacy = _donor_trips().rename(columns={"is_first_trip": "is_first", "is_last_trip": "is_last"})
    legacy["preceding_activity_index"] = legacy["trip_index"]
    legacy["following_activity_index"] = legacy["trip_index"] + 1
    legacy = legacy[VENDORED_TRIP_COLUMNS]

    frame = vendored.prepare_trip_output_frame(_donor_trips())

    assert list(frame.columns) == VENDORED_TRIP_COLUMNS
    pd.testing.assert_frame_equal(frame, legacy)
    assert frame.to_csv(sep=";", index=None, lineterminator="\n") == legacy.to_csv(
        sep=";", index=None, lineterminator="\n")


def test_prepare_trip_output_frame_does_not_modify_its_input():
    trips = _donor_trips()
    before = trips.copy()
    vendored.prepare_trip_output_frame(trips)
    pd.testing.assert_frame_equal(trips, before)


# ------------------------------------------------------------------ output_day: configure


class _ConfigureRecorder:
    """Records ``configure`` with synpp's two-argument config(); returns the effective value."""

    def __init__(self, config=None):
        self.stages = []
        self.config_keys = {}
        self._config = config or {}

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        if name not in self.config_keys:
            self.config_keys[name] = default
        return self._config.get(name, self.config_keys[name])


def test_output_day_declares_the_portal_flag_with_the_shared_default():
    recorder = _ConfigureRecorder(config={"mode_choice": False})
    OUTPUT.configure(recorder)
    assert recorder.config_keys[PORTAL.KEY_ENABLED] is PORTAL.DEFAULT_ENABLED


def test_output_day_stages_the_pre_portal_trips_only_when_the_flag_is_on():
    on = _ConfigureRecorder(config={"mode_choice": False, PORTAL.KEY_ENABLED: True})
    OUTPUT.configure(on)
    assert PRE_PORTAL_STAGE in on.stages
    assert PORTAL.PRE_PORTAL_TRIPS_STAGE == PRE_PORTAL_STAGE

    off = _ConfigureRecorder(config={"mode_choice": False, PORTAL.KEY_ENABLED: False})
    OUTPUT.configure(off)
    assert PRE_PORTAL_STAGE not in off.stages


def test_output_day_hashes_the_portal_config_keys_it_reads():
    token = OUTPUT.validate(None)
    assert PORTAL in OUTPUT._HELPER_MODULES
    assert isinstance(token, str) and token


# ------------------------------------------------------------------ output_day: execute


class _ExecuteContext:
    """synpp ExecuteContext contract: stage(name) and SINGLE-argument config(name), failing on anything undeclared."""

    def __init__(self, tmp_path, portal_enabled, output_formats=("csv", "gpkg"), prefix="bs_"):
        self.declared_stages = {DAY_TRIPS_STAGE, "synthesis.population.activities.final",
                                "synthesis.population.enriched"}
        if portal_enabled:
            self.declared_stages.add(PRE_PORTAL_STAGE)
        self._stages = {
            DAY_TRIPS_STAGE: _post_portal_trips() if portal_enabled else _donor_trips(),
            PRE_PORTAL_STAGE: _donor_trips(),
            "synthesis.population.activities.final": pd.DataFrame({"person_id": [1]}),
            "synthesis.population.enriched": pd.DataFrame({"person_id": [1, 2]}),
        }
        self._config = {
            OUTPUT.KEY_ENABLED: False, OUTPUT.KEY_DAY_ABSENCE_ENABLED: False, PORTAL.KEY_ENABLED: portal_enabled,
            "output_path": str(tmp_path), "output_prefix": prefix, "output_formats": list(output_formats),
        }

    def stage(self, name):
        assert name in self.declared_stages, f"stage '{name}' was not declared in configure()"
        return self._stages[name]

    def config(self, name):
        return self._config[name]


def _stub_vendored_writer(monkeypatch, tmp_path, prefix="bs_"):
    """Replace the vendored writer by one that writes the trips.csv of the stage it is handed (post-portal)."""
    def fake_execute(context):
        trips = vendored.prepare_trip_output_frame(context.stage("synthesis.population.trips"))
        trips.to_csv(tmp_path / f"{prefix}trips.csv", sep=";", index=None, lineterminator="\n")
    monkeypatch.setattr(OUTPUT.base, "execute", fake_execute)


def test_output_day_on_writes_the_pre_portal_csv_with_the_vendored_columns_and_donor_purposes(
        monkeypatch, tmp_path, caplog):
    _stub_vendored_writer(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO):
        OUTPUT.execute(_ExecuteContext(tmp_path, portal_enabled=True))

    written = pd.read_csv(tmp_path / "bs_trips_pre_portal.csv", sep=";")
    assert list(written.columns) == VENDORED_TRIP_COLUMNS
    assert list(written["following_purpose"]) == ["work", "home", "shop", "home"]
    assert list(written["preceding_purpose"]) == ["home", "work", "home", "shop"]
    assert len(written) == 4
    # The post-portal file keeps the rewritten purposes: the new file does not replace it.
    post = pd.read_csv(tmp_path / "bs_trips.csv", sep=";")
    assert list(post["following_purpose"]) == ["outside", "home", "shop", "home"]
    # No mode merge: the file carries no mode column even though the stage frame had one.
    assert "mode" not in written.columns
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "bs_trips_pre_portal.csv" in messages
    assert "4 rows" in messages


def test_output_day_pre_portal_csv_bytes_equal_the_vendored_writer_bytes_for_the_same_frame(monkeypatch, tmp_path):
    _stub_vendored_writer(monkeypatch, tmp_path)
    OUTPUT.execute(_ExecuteContext(tmp_path, portal_enabled=True))
    expected = vendored.prepare_trip_output_frame(_donor_trips()).to_csv(
        sep=";", index=None, lineterminator="\n")
    assert (tmp_path / "bs_trips_pre_portal.csv").read_bytes() == expected.encode("utf-8")


def test_output_day_on_mirrors_the_output_formats_for_parquet(monkeypatch, tmp_path):
    pytest.importorskip("pyarrow")
    _stub_vendored_writer(monkeypatch, tmp_path)
    OUTPUT.execute(_ExecuteContext(tmp_path, portal_enabled=True, output_formats=("csv", "parquet")))
    assert (tmp_path / "bs_trips_pre_portal.csv").exists()
    parquet = pd.read_parquet(tmp_path / "bs_trips_pre_portal.parquet")
    assert list(parquet.columns) == VENDORED_TRIP_COLUMNS


def test_output_day_on_writes_no_csv_when_csv_is_not_an_output_format(monkeypatch, tmp_path):
    _stub_vendored_writer(monkeypatch, tmp_path)
    OUTPUT.execute(_ExecuteContext(tmp_path, portal_enabled=True, output_formats=("gpkg",)))
    assert not (tmp_path / "bs_trips_pre_portal.csv").exists()
    assert not (tmp_path / "bs_trips_pre_portal.parquet").exists()


def test_output_day_off_writes_nothing_new_and_leaves_trips_csv_byte_identical(monkeypatch, tmp_path):
    _stub_vendored_writer(monkeypatch, tmp_path)
    OUTPUT.execute(_ExecuteContext(tmp_path, portal_enabled=False))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["bs_trips.csv"]
    expected = vendored.prepare_trip_output_frame(_donor_trips()).to_csv(
        sep=";", index=None, lineterminator="\n")
    assert (tmp_path / "bs_trips.csv").read_bytes() == expected.encode("utf-8")


def test_output_day_rejects_a_pre_portal_frame_without_the_vendored_inputs(monkeypatch, tmp_path):
    _stub_vendored_writer(monkeypatch, tmp_path)
    context = _ExecuteContext(tmp_path, portal_enabled=True)
    context._stages[PRE_PORTAL_STAGE] = _donor_trips().drop(columns=["following_purpose"])
    with pytest.raises(ValueError, match="following_purpose"):
        OUTPUT.execute(context)


# ------------------------------------------------------------------ shared reader resolver


def _write_trips(directory: Path, name: str, frame: pd.DataFrame) -> Path:
    path = directory / name
    frame.to_csv(path, sep=";", index=False)
    return path


def test_resolver_prefers_the_pre_portal_file_and_logs_which_file_and_why(tmp_path, caplog):
    _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    pre = _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    with caplog.at_level(logging.INFO):
        resolved = PTF.resolve_pipeline_trips_path(tmp_path, "bs_")
    assert resolved == pre
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "bs_trips_pre_portal.csv" in messages
    assert "pre-portal" in messages and "outside" in messages


def test_resolver_falls_back_to_trips_csv_without_logging_a_preference(tmp_path, caplog):
    plain = _write_trips(tmp_path, "bs_trips.csv", _donor_trips())
    with caplog.at_level(logging.INFO):
        resolved = PTF.resolve_pipeline_trips_path(tmp_path, "bs_")
    assert resolved == plain
    assert not [record for record in caplog.records if "pre-portal" in record.getMessage()]


def test_resolver_returns_none_when_neither_file_exists(tmp_path):
    assert PTF.resolve_pipeline_trips_path(tmp_path, "bs_") is None


def test_resolver_ignores_a_pre_portal_file_older_than_trips_csv_and_warns_with_both_mtimes(tmp_path, caplog):
    pre = _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    plain = _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    old = plain.stat().st_mtime - 3600.0
    os.utime(pre, (old, old))
    with caplog.at_level(logging.INFO):
        resolved = PTF.resolve_pipeline_trips_path(tmp_path, "bs_")
    assert resolved == plain
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "older" in warnings[0] and "ignored" in warnings[0]
    assert "bs_trips_pre_portal.csv" in warnings[0] and "bs_trips.csv" in warnings[0]
    # Both modification times are named, as ISO timestamps.
    assert warnings[0].count("mtime") == 2
    # The preference message is not logged for a file that was not used.
    assert not [record for record in caplog.records if "Reading the pre-portal trips" in record.getMessage()]


def test_resolver_keeps_a_pre_portal_file_written_a_moment_before_trips_csv(tmp_path):
    """A copy that does not preserve times exactly must not flip the choice (slack, not an exact comparison)."""
    pre = _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    plain = _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    near = plain.stat().st_mtime - 2.0
    os.utime(pre, (near, near))
    assert PTF.resolve_pipeline_trips_path(tmp_path, "bs_") == pre


def test_is_pre_portal_trips_path_tells_the_two_files_apart(tmp_path):
    assert PTF.is_pre_portal_trips_path(tmp_path / "bs_trips_pre_portal.csv")
    assert not PTF.is_pre_portal_trips_path(tmp_path / "bs_trips.csv")


def test_the_new_file_is_not_matched_by_the_trips_glob_of_the_dashboard(tmp_path):
    _write_trips(tmp_path, "bs_trips.csv", _donor_trips())
    _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    assert [path.name for path in tmp_path.glob("*_trips.csv")] == ["bs_trips.csv"]


# ------------------------------------------------------------------ population_source


def _write_population_dir(directory: Path, prefix: str = "bs_") -> None:
    pd.DataFrame({"person_id": [1, 2], "household_id": [10, 11], "age": [40, 30],
                  "sex": ["male", "female"]}).to_csv(directory / f"{prefix}persons.csv", sep=";", index=False)
    pd.DataFrame({"household_id": [10, 11], "household_size": [1, 1], "number_of_cars": [1, 0]}).to_csv(
        directory / f"{prefix}households.csv", sep=";", index=False)
    gpd.GeoDataFrame({"household_id": [10, 11]}, geometry=[Point(605000, 5790000), Point(606000, 5790000)],
                     crs="EPSG:25832").to_file(directory / f"{prefix}homes.gpkg", driver="GPKG")


def test_population_source_prefers_the_pre_portal_trips_and_logs_it(tmp_path, caplog):
    _write_population_dir(tmp_path)
    _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    with caplog.at_level(logging.INFO):
        frames = ps.load_population(run_output_dir=str(tmp_path), prefix="bs_")
    assert list(frames.trips["following_purpose"]) == ["work", "home", "shop", "home"]
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "bs_trips_pre_portal.csv" in messages


def test_population_source_reads_trips_csv_when_there_is_no_pre_portal_file(tmp_path):
    _write_population_dir(tmp_path)
    _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    frames = ps.load_population(run_output_dir=str(tmp_path), prefix="bs_")
    assert list(frames.trips["following_purpose"]) == ["outside", "home", "shop", "home"]


def test_population_source_trips_stay_none_without_any_trips_file(tmp_path):
    _write_population_dir(tmp_path)
    assert ps.load_population(run_output_dir=str(tmp_path), prefix="bs_").trips is None


# ------------------------------------------------------------------ run_mid_validation


def test_run_mid_validation_reads_the_pre_portal_trips_when_present(tmp_path, caplog):
    _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    with caplog.at_level(logging.INFO):
        trips, is_pre_portal = RMV._read_pipeline_trips(tmp_path, "bs_")
    assert is_pre_portal is True
    assert list(trips["following_purpose"]) == ["work", "home", "shop", "home"]
    assert any("bs_trips_pre_portal.csv" in record.getMessage() for record in caplog.records)


def test_run_mid_validation_reads_trips_csv_otherwise(tmp_path):
    _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    trips, is_pre_portal = RMV._read_pipeline_trips(tmp_path, "bs_")
    assert is_pre_portal is False
    assert list(trips["following_purpose"]) == ["outside", "home", "shop", "home"]


def test_run_mid_validation_missing_trips_raises_with_both_names(tmp_path):
    with pytest.raises(FileNotFoundError, match="bs_trips.csv"):
        RMV._read_pipeline_trips(tmp_path, "bs_")


# ------------------------------------------------------------------ run_mid_validation: activity purposes


class _ActivitiesContext:
    """Execute context of the vendored ``synthesis.population.activities`` stage."""

    def __init__(self, trips, person_ids):
        self._trips = trips
        self._persons = pd.DataFrame({"person_id": person_ids})

    def stage(self, name):
        return {"synthesis.population.trips": self._trips.copy(),
                "synthesis.population.enriched": self._persons}[name]


def _vendored_activity_counts(trips, person_ids):
    """The purpose counts the written activities file holds: the vendored activities stage, then value_counts."""
    from synthesis.population import activities as vendored_activities
    frame = vendored_activities.execute(_ActivitiesContext(trips, person_ids))
    return {str(purpose): int(count) for purpose, count in frame["purpose"].value_counts().items()}


def _counts(series):
    return {str(purpose): int(count) for purpose, count in series.items()}


PERSON_IDS = [1, 2, 3]  # person 3 has no trip: the vendored stage writes one "home" activity for them


def test_activity_counts_from_trips_equal_the_activities_file_definition_without_portal_stays():
    day = _donor_trips()
    written = vendored.prepare_trip_output_frame(day)
    derived = RMV._activity_purpose_counts_from_trips(written, PERSON_IDS)
    assert _counts(derived) == _vendored_activity_counts(day, PERSON_IDS)
    assert _counts(derived) == {"home": 5, "work": 1, "shop": 1}


def test_activity_counts_from_trips_ignore_persons_that_are_not_in_the_persons_file():
    day = _donor_trips()
    written = vendored.prepare_trip_output_frame(day)
    # The output stage inner-joins the activities with persons.csv, so person 2 vanishes from the file.
    derived = RMV._activity_purpose_counts_from_trips(written, [1, 3])
    assert _counts(derived) == {"home": 3, "work": 1}


def test_activity_counts_from_pre_portal_trips_keep_the_work_activity_the_written_activities_lose():
    donor = _donor_trips()
    rewritten = _rewritten_from_the_reproduction()
    from_post_portal_file = _vendored_activity_counts(rewritten, PERSON_IDS)
    assert from_post_portal_file.get("outside", 0) >= 1
    assert from_post_portal_file.get("work", 0) == 0
    derived = RMV._activity_purpose_counts_from_trips(vendored.prepare_trip_output_frame(donor), PERSON_IDS)
    assert _counts(derived)["work"] == 1
    assert "outside" not in _counts(derived)


def test_activity_purpose_table_uses_the_pre_portal_trips_and_logs_its_source(caplog):
    activities = pd.DataFrame({"person_id": [1, 1], "purpose": ["home", "outside"]})
    trips = vendored.prepare_trip_output_frame(_donor_trips())
    with caplog.at_level(logging.INFO):
        counts, source = RMV._activity_purpose_counts(activities, trips, True, PERSON_IDS)
    assert source == "pre_portal_trips"
    assert _counts(counts) == {"home": 5, "work": 1, "shop": 1}
    assert any("pre-portal" in record.getMessage() and "03_activity_purposes" in record.getMessage()
               for record in caplog.records)


def test_activity_purpose_table_reads_the_activities_file_without_the_pre_portal_trips(caplog):
    activities = pd.DataFrame({"person_id": [1, 1, 2], "purpose": ["home", "work", "home"]})
    with caplog.at_level(logging.INFO):
        counts, source = RMV._activity_purpose_counts(activities, pd.DataFrame(), False, PERSON_IDS)
    assert source == "activities_gpkg"
    assert _counts(counts) == {"home": 2, "work": 1}


def test_plot_purposes_draws_a_counts_series(tmp_path):
    RMV._plot_purposes(pd.Series({"home": 5, "work": 1}), tmp_path / "03_activity_purposes.png")
    assert (tmp_path / "03_activity_purposes.png").stat().st_size > 0


# ------------------------------------------------------------------ run_mid_validation: commute table scope


def test_commute_scope_counts_the_workers_the_activities_file_lost_and_warns(caplog):
    # Person 1 works 90 km away: a pre-portal work trip exists, but the written activities hold no work activity.
    trips = vendored.prepare_trip_output_frame(_donor_trips())
    commute = pd.DataFrame({"person_id": [5], "distance_km": [3.0]})
    with caplog.at_level(logging.INFO):
        scope = RMV._commute_table_scope(commute, trips, True)
    assert scope["source"] == "activities_gpkg"
    assert scope["n_work_persons_pre_portal_trips"] == 1
    assert scope["n_commute_rows"] == 1
    assert scope["n_work_persons_without_commute_row"] == 1
    assert any(record.levelno == logging.WARNING and "commute" in record.getMessage().lower() for record in caplog.records)


def test_commute_scope_without_the_pre_portal_file_only_names_the_source(caplog):
    commute = pd.DataFrame({"person_id": [5], "distance_km": [3.0]})
    with caplog.at_level(logging.INFO):
        scope = RMV._commute_table_scope(commute, pd.DataFrame(), False)
    assert scope == {"source": "activities_gpkg", "n_commute_rows": 1}
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


# ------------------------------------------------------------------ measure_trip_coherence script


def test_measure_trip_coherence_script_reads_the_trips_through_the_resolver(tmp_path):
    from scripts import measure_trip_coherence as script
    _write_trips(tmp_path, "bs_trips.csv", _post_portal_trips())
    _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    trips = script._read_trips(str(tmp_path), "bs_")
    assert list(trips["following_purpose"]) == ["work", "home", "shop", "home"]


# ------------------------------------------------------------------ regression with the reproduction frames


def _rewritten_from_the_reproduction() -> pd.DataFrame:
    """The frames of the reproduction: person 1 commutes 90 km and takes one portal stay at gate_e."""
    trips = _donor_trips()
    stays = pd.DataFrame({"person_id": [1], "outbound_trip_index": [0], "return_trip_index": [1.0],
                          "n_removed_legs": [0]})
    gate_rows = pd.DataFrame({"gate_id": ["gate_e"], "kind": ["road"], "x": [40000.0], "y": [0.0]})
    times = pd.DataFrame({"outbound_arrival_time": [7.5 * 3600.0], "t_reentry": [16.5 * 3600.0],
                          "share_out": [0.5], "inside_return_duration": [1800.0], "clamped": [False],
                          "share_capped": [False], "has_return": [True]})
    modes = pd.DataFrame({"mode": ["car"], "outbound_mode": ["car"], "return_mode": ["car"],
                          "substituted_from": [None], "substitution_reason": [None],
                          "return_mode_differs": [False]})
    out, _anchors = rw.rewrite_trips(trips, stays, gate_rows, times, modes, np.array([[0.0, 0.0]]),
                                     crs="EPSG:25832")
    return out


def test_the_rewritten_table_breaks_the_validators_and_the_pre_portal_file_does_not(tmp_path):
    rewritten = _rewritten_from_the_reproduction()
    assert "outside" in set(rewritten["following_purpose"])
    with pytest.raises(ValueError, match="outside"):
        tc.purpose_distribution(rewritten)

    # The same donor day written as the pre-portal file and read back through the loader.
    pre = vendored.prepare_trip_output_frame(_donor_trips())
    pre.to_csv(tmp_path / "bs_trips_pre_portal.csv", sep=";", index=None, lineterminator="\n")
    _write_trips(tmp_path, "bs_trips.csv", vendored.prepare_trip_output_frame(rewritten))
    _write_population_dir(tmp_path)
    trips = ps.load_population(run_output_dir=str(tmp_path), prefix="bs_").trips

    expected = tc.purpose_distribution(_donor_trips())
    assert tc.purpose_distribution(trips) == pytest.approx(expected)
    assert expected["arbeit"] == pytest.approx(0.5)


def test_realised_participation_counts_the_far_worker_on_the_pre_portal_file_only(tmp_path):
    persons_kreis = pd.DataFrame({"person_id": [1, 2], "ars5": ["03101", "03101"]})
    rewritten = vendored.prepare_trip_output_frame(_rewritten_from_the_reproduction())
    donor = vendored.prepare_trip_output_frame(_donor_trips())

    def work_rate(trips):
        table = pf.realised_participation(trips, persons_kreis).set_index("purpose")
        return table.loc["work", "realised_rate"], table.loc["mobility", "realised_rate"]

    assert work_rate(donor) == (0.5, 1.0)
    # Silent on the rewritten table: the far worker counts as not working.
    assert work_rate(rewritten) == (0.0, 1.0)
