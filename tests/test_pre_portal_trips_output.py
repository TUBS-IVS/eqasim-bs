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
        if portal_enabled:
            # The pre-portal commutes read the primary and home locations and the persons' households.
            extra = {"synthesis.population.spatial.primary.locations": _commute_primary_locations(),
                     "synthesis.population.spatial.home.locations": _commute_home_frame()}
            self._stages.update(extra)
            self._stages["synthesis.population.enriched"] = _commute_persons()
            self.declared_stages.update(extra)
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


# ====================================================================================================================
# Pre-portal commutes (eqasim-bs#442): <prefix>commutes_pre_portal.gpkg and the readers that prefer it
# ====================================================================================================================

CRS = "EPSG:25832"
COMMUTE_PERSON_IDS = [1, 2, 3, 4]
HOME_XY = {10: (0.0, 0.0), 11: (1000.0, 0.0), 12: (2000.0, 0.0), 13: (3000.0, 0.0)}
FAR_WORK_XY = (90000.0, 0.0)
SCHOOL_XY = (3000.0, 4000.0)


def _commute_persons() -> pd.DataFrame:
    """The enriched persons frame; person 3 has no trip, person 4 is a pupil."""
    return pd.DataFrame({
        "person_id": COMMUTE_PERSON_IDS, "household_id": [10, 11, 12, 13],
        "age": [40, 30, 50, 12], "employed": [True, True, False, False], "sex": ["male", "female", "male", "female"],
        "socioprofessional_class": [1, 1, 1, 1], "has_license": [True, True, True, False],
        "has_pt_subscription": [False] * 4, "pt_subscription_type": ["none"] * 4, "census_person_id": [1, 2, 3, 4],
        "hts_id": [1, 2, 3, 4], "is_urban_resident": [True] * 4,
        "household_income": [1.0] * 4, "car_availability": ["all"] * 4, "bicycle_availability": ["all"] * 4,
        "number_of_cars": [1] * 4, "number_of_bicycles": [1] * 4, "high_income": [False] * 4,
        "household_size": [1] * 4, "census_household_id": [1, 2, 3, 4],
    })


def _commute_day_trips() -> pd.DataFrame:
    """Donor day: person 1 commutes 90 km, person 2 shops, person 3 stays home, person 4 goes to school."""
    rows = []
    for person, first, second in ((1, "work", "home"), (2, "shop", "home"), (4, "education", "home")):
        for index, (preceding, following) in enumerate((("home", first), (first, second))):
            rows.append({"person_id": person, "trip_index": index, "preceding_purpose": preceding,
                         "following_purpose": following, "mode": "car", "euclidean_distance": 1000.0,
                         "departure_time": (7 + 9 * index) * 3600.0, "arrival_time": (8 + 9 * index) * 3600.0,
                         "is_first_trip": index == 0, "is_last_trip": index == 1})
    return pd.DataFrame(rows)


def _commute_home_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"household_id": list(HOME_XY)}, geometry=[Point(xy) for xy in HOME_XY.values()],
                            crs=CRS)


def _commute_primary_locations():
    work = gpd.GeoDataFrame({"person_id": [1], "location_id": ["w1"]}, geometry=[Point(FAR_WORK_XY)], crs=CRS)
    education = gpd.GeoDataFrame({"person_id": [4], "location_id": ["e4"]}, geometry=[Point(SCHOOL_XY)], crs=CRS)
    return work, education


def _located_activities(activities: pd.DataFrame) -> gpd.GeoDataFrame:
    """The vendored location join, by hand: home -> household home, work/education -> primary location."""
    household = _commute_persons().set_index("person_id")["household_id"]
    work, education = _commute_primary_locations()
    work_xy = {row.person_id: (row.geometry.x, row.geometry.y) for row in work.itertuples()}
    education_xy = {row.person_id: (row.geometry.x, row.geometry.y) for row in education.itertuples()}
    points = []
    for row in activities.itertuples():
        if row.purpose == "home":
            points.append(Point(HOME_XY[household[row.person_id]]))
        elif row.purpose == "work":
            points.append(Point(work_xy[row.person_id]))
        elif row.purpose == "education":
            points.append(Point(education_xy[row.person_id]))
        else:
            points.append(Point(500.0, 500.0))
    return gpd.GeoDataFrame(activities[["person_id", "activity_index"]].copy(), geometry=points, crs=CRS)


class _VendoredOutputContext:
    def __init__(self, tmp_path, trips, prefix):
        from synthesis.population import activities as vendored_activities
        activities = vendored_activities.execute(_ActivitiesContext(trips, COMMUTE_PERSON_IDS))
        self._stages = {
            "synthesis.population.enriched": _commute_persons(),
            "synthesis.population.activities": activities,
            "synthesis.population.trips": trips.copy(),
            "synthesis.population.spatial.locations": _located_activities(activities),
            "synthesis.vehicles.vehicles": (pd.DataFrame({"type_id": []}), pd.DataFrame({"vehicle_id": []})),
        }
        self._config = {"output_path": str(tmp_path), "output_prefix": prefix, "output_formats": ["gpkg"],
                        "mode_choice": False}

    def stage(self, name):
        return self._stages[name]

    def config(self, name):
        return self._config[name]


def _vendored_commutes(tmp_path, trips, prefix="v_") -> gpd.GeoDataFrame:
    """``<prefix>commutes.gpkg`` as the vendored writer produces it for ``trips``."""
    vendored.execute(_VendoredOutputContext(tmp_path, trips, prefix))
    return gpd.read_file(tmp_path / f"{prefix}commutes.gpkg")


def test_build_commute_frame_keeps_the_legacy_selection():
    from shapely.geometry import LineString
    purposes = ["home", "work", "home", "home", "shop"]
    activities = _located_activities(pd.DataFrame({
        "person_id": [1, 1, 1, 2, 2], "activity_index": [0, 1, 2, 0, 1], "purpose": purposes}))
    activities["purpose"] = purposes
    frame = vendored.build_commute_frame(activities)
    assert list(frame.columns) == ["person_id", "geometry"]
    assert list(frame["person_id"]) == [1]  # person 2 has no work activity
    assert len(vendored.build_commute_frame(activities, "education")) == 0
    assert frame.geometry.iloc[0].geom_type == LineString([(0, 0), (1, 1)]).geom_type


def test_pre_portal_commutes_equal_the_vendored_commutes_on_a_run_without_portal_stays(tmp_path):
    trips = _commute_day_trips()
    expected = _vendored_commutes(tmp_path, trips).sort_values("person_id").reset_index(drop=True)
    work, education = _commute_primary_locations()
    commutes, education_commutes = OUTPUT.build_pre_portal_commutes(
        vendored.prepare_trip_output_frame(trips), _commute_persons(), _commute_home_frame(), work, education)
    paths = OUTPUT.write_pre_portal_commutes(commutes, education_commutes, tmp_path, "p_", ["gpkg"])
    actual = gpd.read_file(tmp_path / "p_commutes_pre_portal.gpkg", layer="p_commutes_pre_portal").sort_values("person_id").reset_index(drop=True)
    assert tmp_path / "p_commutes_pre_portal.gpkg" in paths
    assert list(actual.columns) == list(expected.columns)
    assert list(actual["person_id"]) == list(expected["person_id"]) == [1]
    assert actual.crs == expected.crs
    assert all(a.equals_exact(b, 1e-9) for a, b in zip(actual.geometry, expected.geometry))


def test_pre_portal_commutes_keep_the_far_worker_the_vendored_commutes_lose():
    work, education = _commute_primary_locations()
    donor = vendored.prepare_trip_output_frame(_commute_day_trips())
    commutes, _ = OUTPUT.build_pre_portal_commutes(donor, _commute_persons(), _commute_home_frame(), work, education)
    assert list(commutes["person_id"]) == [1]
    assert commutes.geometry.iloc[0].length == pytest.approx(90000.0)

    # The post-portal day: person 1's work activity became an outside stay, so the vendored writer has no work row.
    rewritten = _commute_day_trips()
    rewritten.loc[(rewritten["person_id"] == 1) & (rewritten["trip_index"] == 0), "following_purpose"] = "outside"
    rewritten.loc[(rewritten["person_id"] == 1) & (rewritten["trip_index"] == 1), "preceding_purpose"] = "outside"
    from synthesis.population import activities as vendored_activities
    activities = vendored_activities.execute(_ActivitiesContext(rewritten, COMMUTE_PERSON_IDS))
    located = _located_activities(activities)
    located["purpose"] = activities["purpose"].to_numpy()
    assert "outside" in set(located["purpose"])
    assert 1 not in set(vendored.build_commute_frame(located)["person_id"])


def test_pre_portal_education_layer_holds_the_pupils(tmp_path):
    work, education = _commute_primary_locations()
    donor = vendored.prepare_trip_output_frame(_commute_day_trips())
    commutes, education_commutes = OUTPUT.build_pre_portal_commutes(
        donor, _commute_persons(), _commute_home_frame(), work, education)
    OUTPUT.write_pre_portal_commutes(commutes, education_commutes, tmp_path, "p_", ["gpkg"])
    layer = gpd.read_file(tmp_path / "p_commutes_pre_portal.gpkg", layer=PORTAL.PRE_PORTAL_EDUCATION_LAYER)
    assert list(layer["person_id"]) == [4]
    assert layer.geometry.iloc[0].length == pytest.approx(4000.0)
    # The default (first) layer stays the work commutes with the vendored schema.
    assert list(gpd.read_file(tmp_path / "p_commutes_pre_portal.gpkg", layer="p_commutes_pre_portal")["person_id"]) == [1]


def test_pre_portal_commutes_write_and_read_back_an_empty_education_layer(tmp_path):
    work, education = _commute_primary_locations()
    trips = vendored.prepare_trip_output_frame(_commute_day_trips())
    trips = trips[trips["person_id"] != 4]
    commutes, education_commutes = OUTPUT.build_pre_portal_commutes(
        trips, _commute_persons(), _commute_home_frame(), work, education)
    assert len(education_commutes) == 0
    OUTPUT.write_pre_portal_commutes(commutes, education_commutes, tmp_path, "p_", ["gpkg"])
    path = tmp_path / "p_commutes_pre_portal.gpkg"
    assert list(gpd.read_file(path, layer="p_commutes_pre_portal")["person_id"]) == [1]
    assert len(gpd.read_file(path, layer=PORTAL.PRE_PORTAL_EDUCATION_LAYER)) == 0


def test_pre_portal_commutes_skip_a_person_without_a_primary_location_and_say_so(caplog):
    work, education = _commute_primary_locations()
    work = work.iloc[0:0]
    donor = vendored.prepare_trip_output_frame(_commute_day_trips())
    with caplog.at_level(logging.WARNING):
        commutes, _ = OUTPUT.build_pre_portal_commutes(
            donor, _commute_persons(), _commute_home_frame(), work, education)
    assert len(commutes) == 0
    assert any("no primary location" in record.getMessage() for record in caplog.records)


class _CommuteExecuteContext(_ExecuteContext):
    """The execute context of the trips test with the donor day of the commute fixture."""

    def __init__(self, tmp_path, portal_enabled, output_formats=("csv", "gpkg"), prefix="bs_"):
        super().__init__(tmp_path, portal_enabled, output_formats, prefix)
        self._stages[PRE_PORTAL_STAGE] = _commute_day_trips()
        self._stages[DAY_TRIPS_STAGE] = _commute_day_trips()


def test_output_day_on_stages_the_primary_and_home_locations_only_with_the_flag():
    on = _ConfigureRecorder(config={"mode_choice": False, PORTAL.KEY_ENABLED: True})
    OUTPUT.configure(on)
    assert "synthesis.population.spatial.primary.locations" in on.stages
    assert "synthesis.population.spatial.home.locations" in on.stages
    off = _ConfigureRecorder(config={"mode_choice": False, PORTAL.KEY_ENABLED: False})
    OUTPUT.configure(off)
    assert "synthesis.population.spatial.primary.locations" not in off.stages
    assert "synthesis.population.spatial.home.locations" not in off.stages


def test_output_day_on_writes_the_pre_portal_commutes_and_logs_them(monkeypatch, tmp_path, caplog):
    _stub_vendored_writer(monkeypatch, tmp_path)
    with caplog.at_level(logging.INFO):
        OUTPUT.execute(_CommuteExecuteContext(tmp_path, portal_enabled=True))
    path = tmp_path / "bs_commutes_pre_portal.gpkg"
    written = gpd.read_file(path, layer="bs_commutes_pre_portal")
    assert list(written["person_id"]) == [1]
    assert written.geometry.iloc[0].length == pytest.approx(90000.0)
    assert "bs_commutes_pre_portal.gpkg" in " ".join(record.getMessage() for record in caplog.records)


def test_output_day_off_writes_no_commutes_file(monkeypatch, tmp_path):
    _stub_vendored_writer(monkeypatch, tmp_path)
    OUTPUT.execute(_CommuteExecuteContext(tmp_path, portal_enabled=False))
    assert sorted(path.name for path in tmp_path.iterdir()) == ["bs_trips.csv"]


def test_output_day_writes_no_commutes_file_without_a_spatial_output_format(monkeypatch, tmp_path, caplog):
    _stub_vendored_writer(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING):
        OUTPUT.execute(_CommuteExecuteContext(tmp_path, portal_enabled=True, output_formats=("csv",)))
    assert not list(tmp_path.glob("*commutes_pre_portal*"))
    assert any("commutes" in record.getMessage() for record in caplog.records)


# ------------------------------------------------------------------ resolver for the commutes file


def _touch_gpkg(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"gpkg")
    return path


def test_commutes_resolver_prefers_the_current_pre_portal_file_and_logs_it(tmp_path, caplog):
    _touch_gpkg(tmp_path, "bs_commutes.gpkg")
    pre = _touch_gpkg(tmp_path, "bs_commutes_pre_portal.gpkg")
    with caplog.at_level(logging.INFO):
        resolved = PTF.resolve_pre_portal_commutes_path(tmp_path, "bs_")
    assert resolved == pre
    assert any("bs_commutes_pre_portal.gpkg" in record.getMessage() for record in caplog.records)


def test_commutes_resolver_ignores_a_stale_pre_portal_file_and_warns(tmp_path, caplog):
    pre = _touch_gpkg(tmp_path, "bs_commutes_pre_portal.gpkg")
    plain = _touch_gpkg(tmp_path, "bs_commutes.gpkg")
    old = plain.stat().st_mtime - 3600.0
    os.utime(pre, (old, old))
    with caplog.at_level(logging.WARNING):
        assert PTF.resolve_pre_portal_commutes_path(tmp_path, "bs_") is None
    assert any("older" in record.getMessage() and "ignored" in record.getMessage() for record in caplog.records)


def test_commutes_resolver_returns_none_without_the_file(tmp_path):
    _touch_gpkg(tmp_path, "bs_commutes.gpkg")
    assert PTF.resolve_pre_portal_commutes_path(tmp_path, "bs_") is None


# ------------------------------------------------------------------ run_mid_validation commute tables


def _persons_kreis() -> pd.DataFrame:
    return pd.DataFrame({"person_id": COMMUTE_PERSON_IDS, "ars5": ["03101"] * 4, "kreis_name": ["BS"] * 4,
                         "age": [40, 30, 50, 12], "regiostar7": [71] * 4})


def test_commute_distances_from_lines_equal_the_activities_derivation_on_a_run_without_portal_stays():
    trips = _commute_day_trips()
    from synthesis.population import activities as vendored_activities
    activities = vendored_activities.execute(_ActivitiesContext(trips, COMMUTE_PERSON_IDS))
    located = _located_activities(activities)
    located["purpose"] = activities["purpose"].to_numpy()
    located["household_id"] = located["person_id"].map(_commute_persons().set_index("person_id")["household_id"])
    from_activities = RMV._commute_distances(located, _commute_home_frame(), _persons_kreis())

    work, education = _commute_primary_locations()
    lines, _ = OUTPUT.build_pre_portal_commutes(
        vendored.prepare_trip_output_frame(trips), _commute_persons(), _commute_home_frame(), work, education)
    from_lines = RMV._commute_distances_from_lines(lines, _persons_kreis())

    assert list(from_lines["person_id"]) == list(from_activities["person_id"])
    assert from_lines["distance_km"].to_numpy() == pytest.approx(from_activities["distance_km"].to_numpy())
    assert list(from_lines["ars5"]) == list(from_activities["ars5"])


def test_education_distances_from_lines_carry_age_and_level():
    work, education = _commute_primary_locations()
    _commutes, education_lines = OUTPUT.build_pre_portal_commutes(
        vendored.prepare_trip_output_frame(_commute_day_trips()), _commute_persons(), _commute_home_frame(),
        work, education)
    table = RMV._education_distances_from_lines(education_lines, _persons_kreis())
    assert list(table["person_id"]) == [4]
    assert table["distance_km"].iloc[0] == pytest.approx(4.0)
    assert {"age", "regiostar7", "level"} <= set(table.columns)


def test_read_commute_lines_prefers_the_pre_portal_file_and_returns_both_layers(tmp_path):
    work, education = _commute_primary_locations()
    commutes, education_commutes = OUTPUT.build_pre_portal_commutes(
        vendored.prepare_trip_output_frame(_commute_day_trips()), _commute_persons(), _commute_home_frame(),
        work, education)
    _touch_gpkg(tmp_path, "bs_commutes.gpkg")
    OUTPUT.write_pre_portal_commutes(commutes, education_commutes, tmp_path, "bs_", ["gpkg"])
    work_lines, education_lines = RMV._read_pre_portal_commute_lines(tmp_path, "bs_")
    assert list(work_lines["person_id"]) == [1]
    assert list(education_lines["person_id"]) == [4]


def test_read_commute_lines_is_none_without_the_pre_portal_file(tmp_path):
    assert RMV._read_pre_portal_commute_lines(tmp_path, "bs_") is None


def test_commute_scope_is_zero_missing_when_the_pre_portal_commutes_are_used(caplog):
    trips = vendored.prepare_trip_output_frame(_commute_day_trips())
    commute = pd.DataFrame({"person_id": [1], "distance_km": [117.0]})
    with caplog.at_level(logging.INFO):
        scope = RMV._commute_table_scope(commute, trips, True, "pre_portal_commutes")
    assert scope["source"] == "pre_portal_commutes"
    assert scope["n_work_persons_pre_portal_trips"] == 1
    assert scope["n_work_persons_without_commute_row"] == 0
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


# ------------------------------------------------------------------ review follow-ups: layer names, CRS, missing reference


WORK_LAYER = "bs_commutes_pre_portal"


def _write_pre_portal_commutes_file(tmp_path, prefix="bs_"):
    work, education = _commute_primary_locations()
    commutes, education_commutes = OUTPUT.build_pre_portal_commutes(
        vendored.prepare_trip_output_frame(_commute_day_trips()), _commute_persons(), _commute_home_frame(),
        work, education)
    OUTPUT.write_pre_portal_commutes(commutes, education_commutes, tmp_path, prefix, ["gpkg"])
    return tmp_path / f"{prefix}commutes_pre_portal.gpkg"


def test_the_work_layer_of_the_pre_portal_commutes_is_named_like_the_file_stem(tmp_path):
    path = _write_pre_portal_commutes_file(tmp_path)
    assert PTF.pre_portal_commutes_work_layer(path) == WORK_LAYER
    layers = [str(row[0]) for row in __import__("pyogrio").list_layers(path)]
    assert layers == [WORK_LAYER, PORTAL.PRE_PORTAL_EDUCATION_LAYER]


def test_reading_the_multi_layer_file_names_the_layer_and_raises_no_warning(tmp_path):
    import warnings
    _touch_gpkg(tmp_path, "bs_commutes.gpkg")
    _write_pre_portal_commutes_file(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        work_lines, education_lines = RMV._read_pre_portal_commute_lines(tmp_path, "bs_")
    assert list(work_lines["person_id"]) == [1]
    assert list(education_lines["person_id"]) == [4]


def test_the_pre_portal_layers_are_crs_less_like_the_vendored_commutes(tmp_path):
    path = _write_pre_portal_commutes_file(tmp_path)
    assert gpd.read_file(path, layer=WORK_LAYER).crs is None
    assert gpd.read_file(path, layer=PORTAL.PRE_PORTAL_EDUCATION_LAYER).crs is None
    vendored_commutes = _vendored_commutes(tmp_path, _commute_day_trips())
    assert vendored_commutes.crs is None


def _line(x0, y0, x1, y1, crs=None):
    return gpd.GeoDataFrame({"person_id": [1]}, geometry=[__import__("shapely.geometry", fromlist=["LineString"])
                                                         .LineString([(x0, y0), (x1, y1)])], crs=crs)


def test_line_lengths_read_a_crs_less_file_as_the_pipeline_crs_in_metres():
    lengths = RMV._line_lengths_km(_line(0.0, 0.0, 3000.0, 4000.0))
    assert lengths.tolist() == pytest.approx([5.0])
    assert RMV._line_lengths_km(_line(0.0, 0.0, 3000.0, 4000.0, crs="EPSG:25832")).tolist() == pytest.approx([5.0])


def test_line_lengths_convert_a_file_that_carries_another_crs():
    # 0.01 degrees of longitude at 52.3 N is about 0.68 km: a geographic file must not be read as metres.
    lengths = RMV._line_lengths_km(_line(10.50, 52.3, 10.51, 52.3, crs="EPSG:4326"))
    assert 0.6 < lengths.iloc[0] < 0.8


def test_resolver_warns_that_the_trips_are_used_unchecked_when_trips_csv_is_missing(tmp_path, caplog):
    pre = _write_trips(tmp_path, "bs_trips_pre_portal.csv", _donor_trips())
    with caplog.at_level(logging.INFO):
        resolved = PTF.resolve_pipeline_trips_path(tmp_path, "bs_")
    assert resolved == pre
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "bs_trips.csv" in warnings[0].getMessage() and "staleness" in warnings[0].getMessage()


def test_resolver_warns_that_the_commutes_are_used_unchecked_when_commutes_gpkg_is_missing(tmp_path, caplog):
    pre = _touch_gpkg(tmp_path, "bs_commutes_pre_portal.gpkg")
    with caplog.at_level(logging.INFO):
        resolved = PTF.resolve_pre_portal_commutes_path(tmp_path, "bs_")
    assert resolved == pre
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "bs_commutes.gpkg" in warnings[0].getMessage() and "staleness" in warnings[0].getMessage()
