"""Tests for braunschweig.analysis.synthesis.work_participation_by_kreis.

Every model-side fixture is a synthetic frame, and the VG250 access
(``load_vg250_layer`` / ``assign_geographies``) is monkeypatched: the implementing worktree
has no ``eqasim-data`` population inputs (no VG250 archive, no synpp cache). The real
measurement runs on the server (Phase A Task 6).

Two levels are covered. The pure helpers are tested directly on synthetic frames; on top of
that, ``test_execute_writes_the_report_against_the_committed_srv_reference`` drives ``execute``
through a stub context that mirrors synpp's ExecuteContext contract (``stage(name)`` and
SINGLE-argument ``config(name)``, both refusing anything ``configure`` did not declare) and
reads the REAL committed SrV reference from ``eqasim-data/data/braunschweig/srv/`` -- so a
schema drift between that committed file and the comparison fails here, not first on the run
server.
"""
import json
import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.analysis.synthesis import work_participation_by_kreis as S
from braunschweig.calibration import commute_day_state_reference as R
from braunschweig.calibration.srv_distance_targets import ZGB_KREISE


# --------------------------------------------------------------------------- fixtures

def _persons():
    """6 persons in 3 households across 2 Kreise; person 4 is not employed."""
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6],
        "household_id": [1, 1, 2, 2, 3, 3],
        "employed": [True, True, True, False, True, True],
    })


def _trips():
    """Persons 1, 3, 5, 6 make a work trip; person 4 (not employed) makes only a shop trip."""
    return pd.DataFrame({
        "person_id": [1, 1, 2, 3, 4, 5, 6],
        "preceding_purpose": ["home", "work", "home", "home", "home", "home", "home"],
        "following_purpose": ["work", "home", "shop", "work", "shop", "work", "work"],
    })


def _homes(ars5_household_3="03151"):
    return pd.DataFrame({
        "household_id": [1, 2, 3],
        "ars5": ["03101", "03101", ars5_household_3],
    })


def _home_points():
    return gpd.GeoDataFrame(
        {"household_id": [1, 2], "ars5": ["03101", "03151"]},
        geometry=[Point(0.0, 0.0), Point(0.0, 0.0)], crs="EPSG:25832")


def _work_points():
    return gpd.GeoDataFrame(
        {"person_id": [1, 3], "location_id": ["work_1", "work_2"]},
        geometry=[Point(100_000.0, 0.0), Point(5_000.0, 0.0)], crs="EPSG:25832")


def _work_locations():
    """One external workplace (EXT + 8-digit AGS 03241001) and one ZGB workplace."""
    return pd.DataFrame({
        "location_id": ["work_1", "work_2"],
        "commune_id": ["EXT03241001", "03101000"],
    })


def _work_persons():
    return pd.DataFrame({"person_id": [1, 3], "household_id": [1, 2]})


def _srv_table():
    """Shape of the committed SrV table, incl. the Wolfsburg row (n_persons 0, NaN shares)."""
    rows = []
    for code in ZGB_KREISE:
        if code == "03103":
            rows.append({"level": "kreis", "code": code, "n_persons": 0,
                         "share_home_office_day": np.nan, "share_work_trip": np.nan,
                         "share_neither": np.nan})
        else:
            rows.append({"level": "kreis", "code": code, "n_persons": 1000,
                         "share_home_office_day": 0.15, "share_work_trip": 0.60,
                         "share_neither": 0.25})
    rows.append({"level": "zgb", "code": "zgb", "n_persons": 7000,
                 "share_home_office_day": 0.14, "share_work_trip": 0.65, "share_neither": 0.21})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- participation

def test_work_participation_counts_only_employed_persons_and_work_trips():
    out = S.work_participation_by_kreis(_persons(), _trips(), _homes()).set_index("code")
    # 03101: persons 1, 2, 3 employed (person 4 is not); 1 and 3 have a work trip.
    assert out.loc["03101", "n_employed"] == 3
    assert out.loc["03101", "n_with_work_trip"] == 2
    assert out.loc["03101", "share_work_trip"] == pytest.approx(2.0 / 3.0)
    assert out.loc["03101", "share_no_work_trip"] == pytest.approx(1.0 / 3.0)
    # 03151: persons 5 and 6, both with a work trip.
    assert out.loc["03151", "n_employed"] == 2
    assert out.loc["03151", "share_work_trip"] == pytest.approx(1.0)
    # zgb row = union of the per-Kreis rows.
    assert out.loc["zgb", "n_employed"] == 5
    assert out.loc["zgb", "n_with_work_trip"] == 4
    assert out.loc["zgb", "share_work_trip"] == pytest.approx(0.8)
    assert int(out["n_employed"].drop("zgb").sum()) == int(out.loc["zgb", "n_employed"])


def test_work_participation_emits_one_row_per_zgb_kreis_plus_zgb():
    out = S.work_participation_by_kreis(_persons(), _trips(), _homes())
    assert list(out["code"]) == list(ZGB_KREISE) + ["zgb"]


def test_work_participation_kreis_without_employed_persons_yields_nan_shares():
    out = S.work_participation_by_kreis(_persons(), _trips(), _homes()).set_index("code")
    assert out.loc["03102", "n_employed"] == 0
    assert out.loc["03102", "n_with_work_trip"] == 0
    assert np.isnan(out.loc["03102", "share_work_trip"])
    assert np.isnan(out.loc["03102", "share_no_work_trip"])


def test_work_participation_raises_above_the_unmatched_home_threshold():
    homes = _homes()
    homes.loc[homes["household_id"] == 3, "ars5"] = np.nan  # 2 of 5 employed persons
    with pytest.raises(ValueError, match="cds_max_unmatched_home_share"):
        S.work_participation_by_kreis(_persons(), _trips(), homes,
                                      max_unmatched_home_share=0.05)


def test_work_participation_tolerates_unmatched_homes_below_the_threshold():
    homes = _homes()
    homes.loc[homes["household_id"] == 3, "ars5"] = np.nan
    stats = {}
    out = S.work_participation_by_kreis(_persons(), _trips(), homes,
                                        max_unmatched_home_share=0.5, stats=stats).set_index("code")
    assert stats["n_home_unmatched"] == 2
    assert out.loc["zgb", "n_employed"] == 3  # the two unmatched persons are excluded


def test_work_participation_requires_the_employed_column():
    persons = _persons().drop(columns=["employed"])
    with pytest.raises(ValueError, match="employed"):
        S.work_participation_by_kreis(persons, _trips(), _homes())


# --------------------------------------------------------------------------- comparison

def _model_participation():
    return S.work_participation_by_kreis(_persons(), _trips(), _homes())


def test_compare_participation_adds_reference_columns_and_delta():
    out = S.compare_participation(_model_participation(), _srv_table()).set_index("code")
    assert list(out.reset_index().columns) == list(S.PARTICIPATION_COLUMNS)
    assert out.loc["03101", "srv_n_persons"] == 1000
    assert out.loc["03101", "srv_share_work_trip"] == pytest.approx(0.60)
    assert out.loc["03101", "srv_share_home_office_day"] == pytest.approx(0.15)
    assert out.loc["03101", "delta_work_trip_pp"] == pytest.approx(100.0 * (2.0 / 3.0 - 0.60))
    assert out.loc["zgb", "delta_work_trip_pp"] == pytest.approx(100.0 * (0.8 - 0.65))


def test_compare_participation_wolfsburg_row_yields_nan_delta_without_error():
    out = S.compare_participation(_model_participation(), _srv_table()).set_index("code")
    assert out.loc["03103", "srv_n_persons"] == 0
    assert np.isnan(out.loc["03103", "srv_share_work_trip"])
    assert np.isnan(out.loc["03103", "delta_work_trip_pp"])


def test_compare_participation_warns_when_a_code_has_no_reference_row(caplog):
    srv = _srv_table()
    srv = srv[srv["code"] != "03157"]
    with caplog.at_level(logging.WARNING, logger=S.LOGGER.name):
        out = S.compare_participation(_model_participation(), srv).set_index("code")
    assert np.isnan(out.loc["03157", "srv_share_work_trip"])
    assert any("no SrV reference row" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- realised work frame

def test_realised_work_frame_flags_external_destinations_and_applies_the_detour_factor():
    out = S.realised_work_frame(_home_points(), _work_points(), _work_locations(),
                                _work_persons(), detour_factor=1.3).set_index("person_id")
    # person 1: 100 km euclidean x 1.3 = 130 km, external destination in Kreis 03241.
    assert out.loc[1, "distance_km"] == pytest.approx(130.0)
    assert bool(out.loc[1, "destination_is_external"]) is True
    assert out.loc[1, "destination_ars5"] == "03241"
    assert out.loc[1, "distance_class"] == "100_200"
    assert out.loc[1, "home_ars5"] == "03101"
    # person 3: 5 km euclidean x 1.3 = 6.5 km, internal destination in Kreis 03101.
    assert out.loc[3, "distance_km"] == pytest.approx(6.5)
    assert bool(out.loc[3, "destination_is_external"]) is False
    assert out.loc[3, "destination_ars5"] == "03101"
    assert out.loc[3, "distance_class"] == "lt10"


def test_realised_work_frame_raises_on_a_crs_mismatch():
    work = _work_points().set_crs("EPSG:3035", allow_override=True)
    with pytest.raises(ValueError, match="CRS mismatch"):
        S.realised_work_frame(_home_points(), work, _work_locations(), _work_persons())


def test_realised_work_frame_raises_on_a_geographic_crs():
    homes = _home_points().set_crs("EPSG:4326", allow_override=True)
    work = _work_points().set_crs("EPSG:4326", allow_override=True)
    with pytest.raises(ValueError, match="projected"):
        S.realised_work_frame(homes, work, _work_locations(), _work_persons())


def test_realised_work_frame_raises_above_the_unmatched_home_threshold():
    homes = _home_points()
    homes.loc[homes["household_id"] == 1, "ars5"] = np.nan
    with pytest.raises(ValueError, match="cds_max_unmatched_home_share"):
        S.realised_work_frame(homes, _work_points(), _work_locations(), _work_persons(),
                              max_unmatched_home_share=0.05)


def test_realised_work_frame_counts_destinations_outside_the_zgb_commune_universe(caplog):
    stats = {}
    with caplog.at_level(logging.INFO, logger=S.LOGGER.name):
        S.realised_work_frame(_home_points(), _work_points(), _work_locations(),
                              _work_persons(), known_commune_ids={"03101000"}, stats=stats)
    # Only the EXT destination is outside the ZGB Gemeinde universe; it is expected to be
    # (an external workplace has no ZGB commune) and is reported separately from a real gap.
    assert stats["n_external"] == 1
    assert stats["n_internal_commune_unknown"] == 0


def test_realised_work_frame_warns_on_an_internal_commune_outside_the_municipality_universe(caplog):
    stats = {}
    with caplog.at_level(logging.WARNING, logger=S.LOGGER.name):
        S.realised_work_frame(_home_points(), _work_points(), _work_locations(),
                              _work_persons(), known_commune_ids={"03151005"}, stats=stats)
    assert stats["n_internal_commune_unknown"] == 1
    assert any("outside the data.spatial.municipalities universe" in record.message
               for record in caplog.records)


def test_realised_work_frame_counts_workers_without_a_workplace_row(caplog):
    locations = _work_locations()
    locations = locations[locations["location_id"] != "work_1"]
    stats = {}
    with caplog.at_level(logging.WARNING, logger=S.LOGGER.name):
        out = S.realised_work_frame(_home_points(), _work_points(), locations,
                                    _work_persons(), stats=stats).set_index("person_id")
    assert stats["n_no_workplace_row"] == 1
    assert out.loc[1, "destination_ars5"] == ""
    assert bool(out.loc[1, "destination_is_external"]) is False
    assert any("no row in" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- distance classes

def _realised():
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5],
        "home_ars5": ["03101", "03101", "03101", "03151", "03151"],
        "distance_km": [5.0, 30.0, 130.0, 8.0, 12.0],
        "distance_class": ["lt10", "25_50", "100_200", "lt10", "10_25"],
        "destination_is_external": [False, False, True, False, True],
    })


def test_assigned_distance_classes_shares_per_kreis_and_scope():
    out = S.assigned_distance_classes(_realised())
    assert list(out.columns) == list(S.DISTANCE_CLASS_COLUMNS)
    all_03101 = out[(out["code"] == "03101") & (out["scope"] == "all")].set_index("distance_class")
    assert all_03101.loc["lt10", "n_workers"] == 1
    assert all_03101.loc["lt10", "share"] == pytest.approx(1.0 / 3.0)
    assert all_03101.loc["50_100", "n_workers"] == 0
    assert all_03101["n_workers"].sum() == 3
    external_zgb = out[(out["code"] == "zgb") & (out["scope"] == "external")].set_index("distance_class")
    assert external_zgb.loc["100_200", "n_workers"] == 1
    assert external_zgb.loc["10_25", "n_workers"] == 1
    assert external_zgb["share"].sum() == pytest.approx(1.0)
    internal_zgb = out[(out["code"] == "zgb") & (out["scope"] == "internal")]
    assert internal_zgb["n_workers"].sum() == 3


def test_assigned_distance_classes_emits_every_class_for_every_code_and_scope():
    out = S.assigned_distance_classes(_realised())
    expected = (len(ZGB_KREISE) + 1) * len(S.SCOPES) * len(R.COMMUTE_CLASS_LABELS)
    assert len(out) == expected
    empty = out[(out["code"] == "03102") & (out["scope"] == "all")]
    assert empty["n_workers"].sum() == 0
    assert empty["share"].isna().all()


def test_assigned_distance_classes_counts_unclassified_distances(caplog):
    realised = _realised()
    realised.loc[0, "distance_class"] = None
    stats = {}
    with caplog.at_level(logging.WARNING, logger=S.LOGGER.name):
        out = S.assigned_distance_classes(realised, stats=stats)
    assert stats["n_unclassified"] == 1
    assert out[(out["code"] == "03101") & (out["scope"] == "all")]["n_workers"].sum() == 2
    assert any("no distance class" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- EXT distances

def _ext_workers():
    return pd.DataFrame({
        "person_id": [1, 2, 3],
        "home_ars5": ["03101", "03101", "03151"],
        "dest_ars5": ["03241", "03241", "03241"],
        "distance_km": [120.0, 122.0, 40.0],
    })


def _centroids():
    return pd.DataFrame({
        "ars5": ["03101", "03151", "03241"],
        "centroid_x": [0.0, 60_000.0, 95_000.0],
        "centroid_y": [0.0, 0.0, 0.0],
    })


def _ba_flows():
    return pd.DataFrame({"orig_ars": ["03101"], "dest_ars": ["03241"], "flow": [500]})


def test_ext_destination_distances_detects_a_class_mismatch():
    out = S.ext_destination_distances(_ext_workers(), _centroids(), _ba_flows(),
                                      detour_factor=1.0).set_index(["home_ars5", "dest_ars5"])
    assert list(out.reset_index().columns) == list(S.EXT_DISTANCE_COLUMNS)
    row = out.loc[("03101", "03241")]
    assert row["n_model"] == 2
    assert row["model_km_median"] == pytest.approx(121.0)
    assert row["centroid_km"] == pytest.approx(95.0)
    assert row["class_model_median"] == "100_200"
    assert row["class_centroid"] == "50_100"
    assert bool(row["same_class"]) is False
    assert row["ba_flow"] == 500


def test_ext_destination_distances_matching_class_and_quantiles():
    out = S.ext_destination_distances(_ext_workers(), _centroids(), _ba_flows(),
                                      detour_factor=1.0).set_index(["home_ars5", "dest_ars5"])
    row = out.loc[("03151", "03241")]
    assert row["n_model"] == 1
    assert row["model_km_p10"] == pytest.approx(40.0)
    assert row["model_km_p90"] == pytest.approx(40.0)
    assert row["centroid_km"] == pytest.approx(35.0)
    assert row["class_model_median"] == "25_50" and row["class_centroid"] == "25_50"
    assert bool(row["same_class"]) is True


def test_ext_destination_distances_applies_the_detour_factor_to_the_centroid_distance():
    out = S.ext_destination_distances(_ext_workers(), _centroids(), _ba_flows(),
                                      detour_factor=1.3).set_index(["home_ars5", "dest_ars5"])
    assert out.loc[("03101", "03241"), "centroid_km"] == pytest.approx(95.0 * 1.3)


def test_ext_destination_distances_missing_ba_flow_stays_nan(caplog):
    with caplog.at_level(logging.INFO, logger=S.LOGGER.name):
        out = S.ext_destination_distances(_ext_workers(), _centroids(), _ba_flows(),
                                          detour_factor=1.0).set_index(["home_ars5", "dest_ars5"])
    assert np.isnan(out.loc[("03151", "03241"), "ba_flow"])
    assert any("no BA Pendler flow" in record.message for record in caplog.records)


def test_ext_destination_distances_missing_centroid_is_counted_not_guessed(caplog):
    centroids = _centroids()
    centroids = centroids[centroids["ars5"] != "03241"]
    stats = {}
    with caplog.at_level(logging.WARNING, logger=S.LOGGER.name):
        out = S.ext_destination_distances(_ext_workers(), centroids, _ba_flows(),
                                          detour_factor=1.0, stats=stats)
    assert out["centroid_km"].isna().all()
    assert out["same_class"].isna().all()
    assert stats["n_pairs_without_centroid"] == 2
    assert any("no Kreis centroid" in record.message for record in caplog.records)


def test_ext_destination_distances_on_an_empty_cohort_returns_the_schema():
    empty = _ext_workers().iloc[0:0]
    out = S.ext_destination_distances(empty, _centroids(), _ba_flows())
    assert list(out.columns) == list(S.EXT_DISTANCE_COLUMNS)
    assert len(out) == 0


# --------------------------------------------------------------------------- class edges

def test_near_class_edge_share_is_inclusive_at_the_tolerance():
    # 1.0 -> 9 km from the nearest edge (10); 8.0 -> 2 km; 30.0 -> exactly 5 km from 25;
    # 150.0 -> 50 km from the nearest edge (100/200).
    share = S.near_class_edge_share([1.0, 8.0, 30.0, 150.0], R.COMMUTE_CLASS_EDGES_KM, 5.0)
    assert share == pytest.approx(0.5)


def test_near_class_edge_share_ignores_missing_and_non_positive_distances():
    share = S.near_class_edge_share([1.0, 8.0, 30.0, 150.0, np.nan, -3.0, 0.0],
                                    R.COMMUTE_CLASS_EDGES_KM, 5.0)
    assert share == pytest.approx(0.5)


def test_near_class_edge_share_is_nan_without_any_valid_distance():
    assert np.isnan(S.near_class_edge_share([np.nan, -1.0], R.COMMUTE_CLASS_EDGES_KM, 5.0))


def test_near_class_edge_share_rejects_a_negative_tolerance():
    with pytest.raises(ValueError, match="cds_edge_tolerance_km"):
        S.near_class_edge_share([1.0], R.COMMUTE_CLASS_EDGES_KM, -1.0)


# --------------------------------------------------------------------------- outputs

def test_write_outputs_writes_the_documented_file_set(tmp_path):
    participation = S.compare_participation(_model_participation(), _srv_table())
    classes = S.assigned_distance_classes(_realised())
    ext = S.ext_destination_distances(_ext_workers(), _centroids(), _ba_flows(), detour_factor=1.0)
    per_person = S.per_person_frame(_realised().assign(destination_ars5="03241"))
    provenance = {"generated_at": "2026-09-05T00:00:00+00:00", "parameters": {"detour_factor": 1.3}}
    directory = str(tmp_path / "out")

    S.write_outputs(directory, participation, classes, ext, per_person, provenance)

    for name in ("work_participation_by_kreis.csv", "assigned_distance_classes.csv",
                 "ext_destination_distances.csv", "assigned_class_by_person.csv",
                 "summary.md", "provenance.json"):
        assert os.path.exists(os.path.join(directory, name)), name
    with open(os.path.join(directory, "provenance.json"), encoding="utf-8") as handle:
        assert json.load(handle)["parameters"]["detour_factor"] == 1.3
    written = pd.read_csv(os.path.join(directory, "assigned_class_by_person.csv"))
    assert list(written.columns) == list(S.PER_PERSON_COLUMNS)
    summary = open(os.path.join(directory, "summary.md"), encoding="utf-8").read()
    assert "work participation" in summary.lower()
    assert "SrV" in summary


def test_summary_markdown_reports_the_headline_numbers():
    participation = S.compare_participation(_model_participation(), _srv_table())
    classes = S.assigned_distance_classes(_realised())
    ext = S.ext_destination_distances(_ext_workers(), _centroids(), _ba_flows(), detour_factor=1.0)
    text = S.summary_markdown(participation, classes, ext, near_edge_share=0.25,
                              provenance={"parameters": {"detour_factor": 1.3}})
    assert "0.250" in text or "25.0%" in text
    # The ZGB model share (0.800) and the SrV reference share (0.650) must both be visible.
    assert "0.800" in text and "0.650" in text


# --------------------------------------------------------------------------- stage wiring

class _ConfigureRecorder:
    """Records what ``configure`` declares, with synpp's two-argument config() signature."""

    def __init__(self):
        self.stages = []
        self.config_keys = {}

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        self.config_keys[name] = default


class _StubExecuteContext:
    """synpp's ExecuteContext contract: stage(name) and SINGLE-argument config(name).

    Both accessors fail loudly on anything ``configure`` did not declare, so a stage that reads
    an undeclared key or an undeclared stage cannot pass this test the way a permissive stub
    would (the failure mode tests/test_execute_context_config_contract.py guards statically).
    """

    def __init__(self, declared, stages, config):
        self._declared = declared
        self._stages = stages
        self._config = config

    def stage(self, name):
        assert name in self._declared.stages, f"stage '{name}' was not declared in configure()"
        assert name in self._stages, f"no stub output for stage '{name}'"
        return self._stages[name]

    def config(self, name):
        assert name in self._declared.config_keys, f"config '{name}' was not declared in configure()"
        return self._config[name]


def _kreis_polygons():
    from shapely.geometry import box
    return gpd.GeoDataFrame(
        {"ARS": ["03101", "03151", "03241"]},
        geometry=[box(-5_000, -5_000, 5_000, 5_000),
                  box(55_000, -5_000, 65_000, 5_000),
                  box(90_000, -5_000, 100_000, 5_000)],
        crs="EPSG:25832")


def test_configure_declares_every_stage_and_config_key_execute_reads():
    recorder = _ConfigureRecorder()
    S.configure(recorder)
    assert "braunschweig.locations.work" in recorder.stages
    assert "braunschweig.data.census.pendler" in recorder.stages
    assert recorder.config_keys[S.KEY_DETOUR] == S.DEFAULT_DETOUR_FACTOR
    assert recorder.config_keys[S.KEY_SUBDIR] == S.DEFAULT_SUBDIR
    assert recorder.config_keys[S.KEY_MAX_UNMATCHED_HOME_SHARE] == S.DEFAULT_MAX_UNMATCHED_HOME_SHARE
    assert recorder.config_keys[S.KEY_EDGE_TOLERANCE_KM] == S.DEFAULT_EDGE_TOLERANCE_KM


def test_execute_writes_the_report_against_the_committed_srv_reference(tmp_path, monkeypatch):
    """End-to-end wiring proof with stubbed VG250 access.

    The reference side is NOT stubbed: it reads the real committed SrV table from
    ``eqasim-data/data/braunschweig/srv/``, so a schema drift between the committed file and
    :func:`compare_participation` fails here rather than only on the run server.
    """
    from braunschweig.analysis import spatial

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # <root>/tests -> <root>
    data_path = os.path.join(repo_root, "eqasim-data", "data")
    assert os.path.exists(os.path.join(data_path, "braunschweig", "srv",
                                       "srv2023_work_participation_by_kreis.csv")), \
        "the committed SrV work-participation table must be present for this test to mean anything"

    monkeypatch.setattr(spatial, "load_vg250_layer",
                        lambda layer, strict=True: _kreis_polygons())
    monkeypatch.setattr(spatial, "assign_geographies",
                        lambda homes, kreise=None: homes.assign(ars5=["03101", "03151"]))

    home_locations = gpd.GeoDataFrame(
        {"household_id": [1, 2]},
        geometry=[Point(0.0, 0.0), Point(60_000.0, 0.0)], crs="EPSG:25832")
    persons = pd.DataFrame({"person_id": [1, 3], "household_id": [1, 2],
                            "employed": [True, True]})
    recorder = _ConfigureRecorder()
    S.configure(recorder)
    context = _StubExecuteContext(
        recorder,
        stages={
            "synthesis.population.spatial.home.locations": home_locations,
            "synthesis.population.spatial.primary.locations": (_work_points(), None),
            "synthesis.population.enriched": persons,
            "synthesis.population.trips": _trips(),
            "braunschweig.locations.work": _work_locations(),
            "data.spatial.municipalities": pd.DataFrame({"commune_id": ["03101000"]}),
            "braunschweig.data.census.pendler": _ba_flows(),
        },
        config={
            "output_path": str(tmp_path), "data_path": data_path, "sampling_rate": 1.0,
            S.KEY_DETOUR: 1.3, S.KEY_SUBDIR: "analysis/cds", S.KEY_MAX_UNMATCHED_HOME_SHARE: 0.05,
            S.KEY_EDGE_TOLERANCE_KM: 5.0,
        })

    result = S.execute(context)

    out_dir = tmp_path / "analysis" / "cds"
    for name in ("work_participation_by_kreis.csv", "assigned_distance_classes.csv",
                 "ext_destination_distances.csv", "assigned_class_by_person.csv",
                 "summary.md", "provenance.json"):
        assert (out_dir / name).exists(), name

    participation = pd.read_csv(out_dir / "work_participation_by_kreis.csv", dtype={"code": str})
    assert list(participation.columns) == list(S.PARTICIPATION_COLUMNS)
    # Both persons are employed and both make a work trip; the committed reference is joined in.
    zgb = participation[participation["code"] == "zgb"].iloc[0]
    assert zgb["n_employed"] == 2 and zgb["share_work_trip"] == pytest.approx(1.0)
    assert zgb["srv_n_persons"] == 8016
    assert not np.isnan(zgb["delta_work_trip_pp"])
    # Wolfsburg is not surveyed by SrV: its delta must stay NaN, never a substituted zero.
    wolfsburg = participation[participation["code"] == "03103"].iloc[0]
    assert np.isnan(wolfsburg["delta_work_trip_pp"])
    # Person 1 works at the EXT location 100 km east -> 130 km routed, Kreis 03241.
    per_person = pd.read_csv(out_dir / "assigned_class_by_person.csv",
                             dtype={"destination_ars5": str})
    row = per_person[per_person["person_id"] == 1].iloc[0]
    assert row["assigned_distance_class"] == "100_200"
    assert row["destination_ars5"] == "03241"
    assert bool(row["destination_is_external"]) is True
    assert set(result) == {"participation", "distance_classes", "ext_destinations",
                           "near_class_edge_share", "counts"}
