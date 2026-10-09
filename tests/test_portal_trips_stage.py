"""The portal stages: declared keys, the OFF pass-through, the execute() wiring and the end-to-end chain
through the pure entry point ``build_portal_trips`` (eqasim-bs#442)."""
import json
import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from braunschweig.synthesis.portal_trips import anchors as anchors_stage
from braunschweig.synthesis.portal_trips import classification
from braunschweig.synthesis.portal_trips import config_keys as keys
from braunschweig.synthesis.portal_trips import stage, trips_final
from braunschweig.synthesis.portal_trips.rewrite import PORTAL_LEG_COLUMN

CRS = "EPSG:25832"


class _RecordingConfigureContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_portal_keys_and_the_inputs():
    context = _RecordingConfigureContext({"cordon_enabled": True})
    stage.configure(context)
    for key in (keys.KEY_ENABLED, keys.KEY_MAX_ROUTABLE_DISTANCE_M, keys.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE,
                keys.KEY_MODE_SUBSTITUTION_WARN_SHARE, keys.KEY_FALLBACK_WARN_SHARE, "random_seed",
                "cordon_network_buffer_fraction"):
        assert key in context.calls
    assert context.calls[keys.KEY_FALLBACK_WARN_SHARE] == keys.DEFAULT_FALLBACK_WARN_SHARE
    for name in (stage.TRIPS_STAGE, "synthesis.population.enriched", "synthesis.population.spatial.home.locations",
                 "synthesis.population.spatial.primary.locations", "braunschweig.synthesis.cordon_gates",
                 "braunschweig.data.cordon_network", "braunschweig.data.external_secondary_points",
                 "data.spatial.municipalities", "matsim.scenario.supply.processed"):
        assert name in context.stages
    # The sampled frame lacks the availability columns the mode check needs.
    assert "synthesis.population.sampled" not in context.stages


def test_configure_rejects_portal_on_without_the_cordon():
    with pytest.raises(ValueError, match="cordon_enabled"):
        stage.configure(_RecordingConfigureContext({"cordon_enabled": False}))


def test_configure_rejects_an_invalid_fallback_share():
    with pytest.raises(ValueError, match=keys.KEY_FALLBACK_WARN_SHARE.replace(".", r"\.")):
        stage.configure(_RecordingConfigureContext({"cordon_enabled": True, keys.KEY_FALLBACK_WARN_SHARE: 0.0}))


def test_configure_off_declares_only_the_flag_and_the_trips_stage():
    context = _RecordingConfigureContext({keys.KEY_ENABLED: False})
    stage.configure(context)
    assert context.stages == [stage.TRIPS_STAGE]
    assert set(context.calls) == {keys.KEY_ENABLED}


def test_thin_stages_return_the_two_frames_of_the_core_stage():
    payload = {"trips": pd.DataFrame({"person_id": [1]}), "anchors": pd.DataFrame({"person_id": []}), "report": {}}

    class _Context:
        def stage(self, name):
            assert name == "braunschweig.synthesis.portal_trips.stage"
            return payload

        def config(self, key):
            raise AssertionError(key)

    assert trips_final.execute(_Context()) is payload["trips"]
    assert anchors_stage.execute(_Context()) is payload["anchors"]


def test_thin_stages_declare_the_core_stage():
    for module in (trips_final, anchors_stage):
        context = _RecordingConfigureContext()
        module.configure(context)
        assert context.stages == ["braunschweig.synthesis.portal_trips.stage"]


def test_validate_token_is_a_stable_hex_digest():
    token = stage.validate(None)
    assert token == stage.validate(None)
    assert len(token) == 32 and int(token, 16) >= 0


def test_execute_off_passes_the_trips_frame_through_unchanged():
    trips = pd.DataFrame({"person_id": [1]})

    class _Context:
        def config(self, key):
            assert key == keys.KEY_ENABLED
            return False

        def stage(self, name):
            assert name == stage.TRIPS_STAGE
            return trips

    result = stage.execute(_Context())
    assert result["trips"] is trips and len(result["anchors"]) == 0 and result["report"] == {"enabled": False}


# --------------------------------------------------------------------------------------------------
# Pure entry point
# --------------------------------------------------------------------------------------------------

def _fixture():
    extent = box(0.0, 0.0, 50000.0, 50000.0)  # cut extent 50 x 50 km
    persons = pd.DataFrame({"person_id": [1, 2], "household_id": [10, 20], "car_availability": ["all", "none"],
                            "has_license": [True, False], "bicycle_availability": ["all", "all"],
                            "car_passenger_availability": ["some", "none"]})
    homes = gpd.GeoDataFrame({"household_id": [10, 20]}, geometry=[Point(25000.0, 25000.0), Point(25000.0, 25000.0)],
                             crs=CRS)
    work = gpd.GeoDataFrame({"person_id": [1], "location_id": ["EXT1"]}, geometry=[Point(200000.0, 25000.0)], crs=CRS)
    education = gpd.GeoDataFrame({"person_id": [], "location_id": []}, geometry=[], crs=CRS)
    # Person 1: the work leg has no reported distance (the sampler never sees it, the assigned location
    # decides); the return leg reports the 175 km the donor travelled. Person 2: a far leisure trip.
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2], "trip_index": [0, 1, 0, 1],
        "preceding_purpose": ["home", "work", "home", "leisure"], "following_purpose": ["work", "home", "leisure", "home"],
        "mode": ["car", "car", "car", "car"], "euclidean_distance": [np.nan, 175000.0, 120000.0, 120000.0],
        "departure_time": [7 * 3600.0, 16 * 3600.0, 9 * 3600.0, 18 * 3600.0],
        "arrival_time": [9 * 3600.0, 18 * 3600.0, 11 * 3600.0, 20 * 3600.0],
    })
    gates = gpd.GeoDataFrame({"link_id": ["L1", "L2"], "capacity": [6000.0, 6000.0], "road_class": ["motorway", "motorway"],
                              "gate_id": ["gate_e", "gate_w"]},
                             geometry=[Point(50000.0, 25000.0), Point(0.0, 25000.0)], crs=CRS)
    links = gpd.GeoDataFrame({"link_id": ["L1", "L2"], "capacity": [6000.0, 6000.0], "road_class": ["motorway", "motorway"]},
                             geometry=[LineString([(49000.0, 25000.0), (51000.0, 25000.0)]),
                                       LineString([(-1000.0, 25000.0), (1000.0, 25000.0)])], crs=CRS)
    external = gpd.GeoDataFrame({"commune_id": ["EXT1", "EXT2"], "ewz": [100.0, 100.0]},
                                geometry=[Point(200000.0, 25000.0), Point(-100000.0, 25000.0)], crs=CRS)
    stops = {"hbf": (25000.0, 25000.0), "far": (80000.0, 25000.0)}
    routes = [("rail", ["hbf", "far"])]
    return dict(trips=trips, persons=persons, df_home=homes, df_work=work, df_education=education, gates=gates,
                links=links, external_points=external, stops=stops, routes=routes, cordon_polygon=extent,
                threshold_m=45000.0, tolerance=0.2, warn_share=0.1, fallback_warn_share=0.1, seed=7)


def test_build_portal_trips_makes_the_far_worker_and_the_far_leisure_trip_portal_trips():
    fixture = _fixture()
    out = stage.build_portal_trips(**fixture)
    trips, anchors, report = out["trips"], out["anchors"], out["report"]
    assert trips[PORTAL_LEG_COLUMN].tolist() == [True, True, True, True]
    assert trips["following_purpose"].tolist() == ["outside", "home", "outside", "home"]
    assert anchors["gate_id"].tolist() == ["gate_e", "hbf"]            # east towards EXT1, the rail exit for person 2
    assert anchors["mode"].tolist() == ["car", "pt"]                   # person 2 has neither car nor passenger seat
    assert report["n_stays"] == 2 and report["n_mode_substituted"] == 1
    assert report["n_stays_by_kind"] == {"road": 1, "rail": 1}
    # person 1: origin = home (25000, 25000), gate_e inside end (49000, 25000), assigned work (200000, 25000):
    # outside 151000 m, return proxy = origin -> inside 24000 m, so share_out = 151000 / (151000 + 24000) of the
    # 2 h return (the donor reported distance of 175 km plays no part).
    expected_reentry = 16 * 3600.0 + (200000.0 - 49000.0) / 175000.0 * 7200.0
    assert trips.loc[1, "departure_time"] == pytest.approx(expected_reentry)
    # The stage's classification agrees with the module it replaces a second pass of.
    home_xy = classification.person_home_xy(fixture["persons"], fixture["df_home"])
    primary = classification.primary_xy(fixture["df_work"], fixture["df_education"])
    expected_portal = classification.classify_portal_legs(
        stage.rewrite.recompute_chain_columns(fixture["trips"]), home_xy, primary, fixture["threshold_m"])
    assert report["n_portal_legs"] == int(expected_portal.sum()) == 2


def test_build_portal_trips_classifies_the_work_leg_by_the_assigned_location_not_the_fallback():
    report = stage.build_portal_trips(**_fixture())["report"]
    # The only primary leg (person 1's work leg) has NaN reported distance, so it can only have become a
    # portal leg through the assigned-location join: the PRIMARY path ran.
    assert report["n_primary_legs"] == 1
    assert report["n_primary_legs_assigned"] == 1
    assert report["n_primary_legs_reported_distance_fallback"] == 0
    assert report["n_external_point_drawn"] == 1               # person 2 only; person 1 points at the work location
    assert report["n_external_band_miss"] == 0
    assert report["n_origin_proxied_by_home"] == 0             # both outbound legs leave home


def test_build_portal_trips_leaves_near_trips_untouched():
    fixture = _fixture()
    fixture["trips"]["euclidean_distance"] = [np.nan, np.nan, 10000.0, 10000.0]
    fixture["df_work"] = gpd.GeoDataFrame({"person_id": [1], "location_id": ["w"]}, geometry=[Point(30000.0, 25000.0)],
                                          crs=CRS)
    out = stage.build_portal_trips(**fixture)
    assert not out["trips"][PORTAL_LEG_COLUMN].any() and len(out["anchors"]) == 0
    assert out["report"]["n_stays"] == 0 and out["report"]["n_primary_legs_assigned"] == 1
    pd.testing.assert_frame_equal(out["trips"].drop(columns=[PORTAL_LEG_COLUMN]),
                                  fixture["trips"].pipe(stage.rewrite.recompute_chain_columns))


def test_build_portal_trips_does_not_require_a_trip_key_column():
    fixture = _fixture()
    assert "trip_key" not in fixture["trips"].columns
    assert "trip_key" not in stage.REQUIRED_TRIP_COLUMNS
    stage.build_portal_trips(**fixture)


def test_build_portal_trips_rejects_a_trips_frame_without_a_required_column():
    fixture = _fixture()
    fixture["trips"] = fixture["trips"].drop(columns=["euclidean_distance"])
    with pytest.raises(ValueError, match="euclidean_distance"):
        stage.build_portal_trips(**fixture)


def test_build_portal_trips_raises_when_every_primary_leg_used_the_reported_distance():
    fixture = _fixture()
    # No assigned work location at all: the join is "broken", the single work leg falls back to its
    # reported distance (120 km here so that it would still look plausible).
    fixture["df_work"] = gpd.GeoDataFrame({"person_id": [], "location_id": []}, geometry=[], crs=CRS)
    fixture["trips"].loc[0, "euclidean_distance"] = 120000.0
    with pytest.raises(ValueError, match="reported-distance fallback"):
        stage.build_portal_trips(**fixture)


def test_build_portal_trips_counts_a_partial_fallback_and_keeps_the_drawn_point_for_the_fallback_person(caplog):
    fixture = _fixture()
    # Person 3 works far away per the donor, but has no assigned work location (a fallback person).
    fixture["persons"] = pd.concat([fixture["persons"], pd.DataFrame({
        "person_id": [3], "household_id": [30], "car_availability": ["all"], "has_license": [True],
        "bicycle_availability": ["all"], "car_passenger_availability": ["some"]})], ignore_index=True)
    fixture["df_home"] = gpd.GeoDataFrame({"household_id": [10, 20, 30]},
                                          geometry=[Point(25000.0, 25000.0)] * 3, crs=CRS)
    extra = pd.DataFrame({
        "person_id": [3, 3], "trip_index": [0, 1], "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"], "mode": ["car", "car"], "euclidean_distance": [120000.0, 120000.0],
        "departure_time": [7 * 3600.0, 16 * 3600.0], "arrival_time": [9 * 3600.0, 18 * 3600.0]})
    fixture["trips"] = pd.concat([fixture["trips"], extra], ignore_index=True)
    with caplog.at_level(logging.INFO):
        out = stage.build_portal_trips(**fixture)
    report = out["report"]
    assert report["n_primary_legs"] == 2 and report["n_primary_legs_reported_distance_fallback"] == 1
    assert report["n_stays"] == 3
    # Person 3 keeps a DRAWN point (EXT2 is the only commune in the 96-144 km band around the home).
    assert report["n_external_point_drawn"] == 2
    assert any("reported-distance fallback 1 (50.0%" in message for message in caplog.messages)
    # 50 % exceeds the 10 % warning share, so the rate is surfaced as a WARNING.
    assert any(record.levelno == logging.WARNING and "reported-distance fallback" in record.getMessage()
               for record in caplog.records)


def test_build_portal_trips_warns_when_the_external_band_is_missed_too_often(caplog):
    fixture = _fixture()
    fixture["external_points"] = gpd.GeoDataFrame(
        {"commune_id": ["EXT1", "EXT2"], "ewz": [100.0, 100.0]},
        geometry=[Point(200000.0, 25000.0), Point(-200000.0, 25000.0)], crs=CRS)
    with caplog.at_level(logging.INFO):
        report = stage.build_portal_trips(**fixture)["report"]
    assert report["n_external_band_miss"] == 1 and report["n_external_point_drawn"] == 1
    assert any(record.levelno == logging.WARNING and "outside the distance band" in record.getMessage()
               for record in caplog.records)


def _force_degenerate_shares(monkeypatch):
    """Make the real timing functions flag every stay as degenerate, to exercise the report and log wiring."""
    real_outbound, real_reentry = stage._timing.outbound_arrivals, stage._timing.reentry_times

    def outbound(*args, **kwargs):
        result = real_outbound(*args, **kwargs)
        result["outbound_share_degenerate"] = True
        return result

    def reentry(*args, **kwargs):
        result = real_reentry(*args, **kwargs)
        result["share_degenerate"] = result["has_return"]
        return result

    monkeypatch.setattr(stage._timing, "outbound_arrivals", outbound)
    monkeypatch.setattr(stage._timing, "reentry_times", reentry)


def test_build_portal_trips_times_the_stays_by_the_synthetic_geometry_and_reports_the_shares():
    report = stage.build_portal_trips(**_fixture())["report"]
    # Neither the work leg (no reported distance) nor the 120 km leisure leg (125 km outside) needs a cap any more.
    assert report["n_return_share_degenerate"] == 0 and report["n_outbound_share_degenerate"] == 0
    assert "n_share_out_capped" not in report and "n_outbound_share_capped" not in report
    # person 1: share_out = 151000 / 175000; person 2: the rail exit sits at home (= the return proxy), so the
    # whole return leg is outside (share 1). Median of the two = their mean.
    assert report["return_share_out_median"] == pytest.approx((151000.0 / 175000.0 + 1.0) / 2.0)


def test_build_portal_trips_timing_ignores_the_reported_distance_of_the_return_leg():
    reference = stage.build_portal_trips(**_fixture())["trips"]
    for reported in (np.nan, 0.0, 1000.0):
        fixture = _fixture()
        fixture["trips"].loc[1, "euclidean_distance"] = reported      # person 1's return leg
        trips = stage.build_portal_trips(**fixture)["trips"]
        assert trips.loc[1, "departure_time"] == pytest.approx(reference.loc[1, "departure_time"])


def test_build_portal_trips_shortens_the_outbound_leg_to_the_inside_share():
    # Person 1's outbound (work) leg departs 07:00 and arrives 09:00 (7200 s) at the far workplace. Derivation:
    # origin = home (25000, 25000), gate_e inside end (49000, 25000), work (200000, 25000) ->
    # share_in = 24000 / (24000 + 151000) = 24 / 175, arrival = 25200 + 7200 * 24 / 175 s. Its reported distance is
    # NaN in the fixture and plays no part. Person 2's gate is the rail exit at home -> inside distance 0 ->
    # share 0 -> the leg arrives at its departure 09:00 (zero duration).
    out = stage.build_portal_trips(**_fixture())
    assert out["trips"].loc[0, "arrival_time"] == pytest.approx(25200.0 + 7200.0 * 24000.0 / 175000.0)
    assert out["trips"].loc[2, "arrival_time"] == 9 * 3600.0
    # plan times stay monotonic: every leg arrives after it departs and before the next one departs
    trips = out["trips"]
    assert (trips["arrival_time"] >= trips["departure_time"]).all()
    assert (trips["activity_duration"].dropna() >= 0).all()


def test_build_portal_trips_warns_when_the_share_splits_are_degenerate_too_often(monkeypatch, caplog):
    _force_degenerate_shares(monkeypatch)
    with caplog.at_level(logging.INFO):
        report = stage.build_portal_trips(**_fixture())["report"]
    assert report["n_return_share_degenerate"] == 2 and report["n_outbound_share_degenerate"] == 2
    for label in ("return share degenerate", "outbound share degenerate"):
        records = [record for record in caplog.records if label in record.getMessage()]
        assert len(records) == 1 and records[0].levelno == logging.WARNING     # 100 % above the 10 % warn share
        assert "2/2 (100.00%; warn above 10%)" in records[0].getMessage()
    caplog.clear()
    fixture = _fixture()
    fixture["fallback_warn_share"] = 1.0                                       # not exceeded: INFO only
    with caplog.at_level(logging.INFO):
        stage.build_portal_trips(**fixture)
    records = [record for record in caplog.records if "share degenerate" in record.getMessage()]
    assert len(records) == 2 and all(record.levelno == logging.INFO for record in records)


def test_build_portal_trips_logs_the_median_return_share(caplog):
    with caplog.at_level(logging.INFO):
        stage.build_portal_trips(**_fixture())
    records = [record for record in caplog.records if "return share degenerate" in record.getMessage()]
    assert len(records) == 1 and "0/2 (0.00%" in records[0].getMessage()
    assert "median share outside 0.93" in records[0].getMessage()


def test_build_portal_trips_warns_when_the_origin_is_proxied_by_home_too_often(caplog):
    fixture = _fixture()
    # Person 2 now leaves a shop (a secondary activity) for the far leisure trip: home stands in for it.
    extra = pd.DataFrame({
        "person_id": [2], "trip_index": [0], "preceding_purpose": ["home"], "following_purpose": ["shop"],
        "mode": ["car"], "euclidean_distance": [1000.0], "departure_time": [7 * 3600.0],
        "arrival_time": [7.5 * 3600.0]})
    trips = fixture["trips"].copy()
    trips.loc[trips["person_id"] == 2, "trip_index"] += 1
    trips.loc[(trips["person_id"] == 2) & (trips["trip_index"] == 1), "preceding_purpose"] = "shop"
    fixture["trips"] = pd.concat([trips, extra], ignore_index=True)
    with caplog.at_level(logging.INFO):
        report = stage.build_portal_trips(**fixture)["report"]
    assert report["n_origin_proxied_by_home"] == 1
    proxied = [record for record in caplog.records if "origin proxied by home" in record.getMessage()]
    assert len(proxied) == 1 and proxied[0].levelno == logging.WARNING
    assert "1/2 (50.00%" in proxied[0].getMessage()


def test_build_portal_trips_reports_missing_licences_and_unknown_donor_modes():
    fixture = _fixture()
    fixture["persons"].loc[0, "has_license"] = np.nan
    fixture["trips"].loc[2, "mode"] = "ride_hailing"
    report = stage.build_portal_trips(**fixture)["report"]
    assert report["n_stay_persons_missing_license"] == 1
    assert report["n_unknown_donor_mode"] == 1
    assert report["unknown_donor_modes"] == {"ride_hailing": 1}


# --------------------------------------------------------------------------------------------------
# Origin of the outbound leg
# --------------------------------------------------------------------------------------------------

def test_origin_is_the_assigned_primary_location_when_the_outbound_leg_leaves_work_or_education():
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2, 3, 3, 4, 4, 5],
        "trip_index": [0, 1, 0, 1, 0, 1, 0, 1, 0],
        "preceding_purpose": ["home", "work", "home", "shop", "home", "work", "home", "education", "home"],
        "following_purpose": ["work", "leisure", "shop", "leisure", "work", "leisure", "education", "leisure", "leisure"]})
    stays = pd.DataFrame({"person_id": [1, 2, 3, 4, 5], "outbound_trip_index": [1, 1, 1, 1, 0],
                          "return_trip_index": [np.nan] * 5, "n_removed_legs": [0] * 5})
    home_xy = pd.DataFrame({"x": [0.0] * 5, "y": [0.0] * 5}, index=pd.Index([1, 2, 3, 4, 5], name="person_id"))
    primary = {"work": pd.DataFrame({"x": [1000.0, np.nan], "y": [2000.0, np.nan]},
                                    index=pd.Index([1, 3], name="person_id")),
               "education": pd.DataFrame({"x": [3000.0], "y": [4000.0]}, index=pd.Index([4], name="person_id"))}
    outbound_positions = stage._trip_row_positions(trips, stays["person_id"], stays["outbound_trip_index"])
    origins, n_proxied = stage._origin_xy_for_stays(trips, stays, home_xy, primary, outbound_positions)
    assert origins.tolist() == [[1000.0, 2000.0],    # leaves an assigned work location
                                [0.0, 0.0],          # leaves a shop: home proxy
                                [0.0, 0.0],          # leaves work without a finite location: home proxy
                                [3000.0, 4000.0],    # leaves an assigned education location
                                [0.0, 0.0]]          # leaves home: the true origin
    assert n_proxied == 2                            # shop and the work leg without a location


# --------------------------------------------------------------------------------------------------
# execute() wiring
# --------------------------------------------------------------------------------------------------

def test_execute_on_wires_the_inputs_into_the_pure_entry_point(tmp_path, monkeypatch):
    fixture = _fixture()
    schedule_dir = tmp_path / "supply"
    schedule_dir.mkdir()
    report_dir = tmp_path / "stage_cache"
    report_dir.mkdir()
    seen = {}

    def fake_read_transit_stops_routes(path):
        seen["schedule_path"] = path
        return fixture["stops"], fixture["routes"]

    monkeypatch.setattr("braunschweig.data.cordon.network.read_transit_stops_routes", fake_read_transit_stops_routes)
    monkeypatch.setattr("braunschweig.data.spatial.cordon.buffer_m_from_fraction", lambda df, fraction: 123.0)
    monkeypatch.setattr("braunschweig.data.spatial.cordon.build_cordon_polygon",
                        lambda df, buffer_m: fixture["cordon_polygon"])
    stages = {
        stage.TRIPS_STAGE: fixture["trips"],
        "synthesis.population.enriched": fixture["persons"],
        "synthesis.population.spatial.home.locations": fixture["df_home"],
        "synthesis.population.spatial.primary.locations": (fixture["df_work"], fixture["df_education"]),
        "braunschweig.synthesis.cordon_gates": {"gates": fixture["gates"], "assignment": None},
        "braunschweig.data.cordon_network": fixture["links"],
        "braunschweig.data.external_secondary_points": fixture["external_points"],
        "data.spatial.municipalities": gpd.GeoDataFrame({"name": ["muni"]}, geometry=[Point(0.0, 0.0)], crs=CRS),
        "matsim.scenario.supply.processed": {"schedule_path": "schedule.xml.gz"},
    }
    values = {keys.KEY_ENABLED: True, keys.KEY_MAX_ROUTABLE_DISTANCE_M: 45000, keys.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE: 0.2,
              keys.KEY_MODE_SUBSTITUTION_WARN_SHARE: 0.1, keys.KEY_FALLBACK_WARN_SHARE: 0.1, "random_seed": 7,
              "cordon_network_buffer_fraction": 0.1}

    class _Context:
        def config(self, key):
            return values[key]

        def stage(self, name):
            return stages[name]

        def path(self, name=None):
            if name is None:
                return str(report_dir)
            assert name == "matsim.scenario.supply.processed"
            return str(schedule_dir)

    out = stage.execute(_Context())
    assert seen["schedule_path"] == os.path.join(str(schedule_dir), "schedule.xml.gz")
    assert out["report"]["enabled"] is True and out["report"]["n_stays"] == 2
    assert out["anchors"]["gate_id"].tolist() == ["gate_e", "hbf"]
    # R34: the report is persisted in the stage directory for the A/B manifest.
    with open(report_dir / "portal_trips_report.json", encoding="utf-8") as handle:
        assert json.load(handle) == out["report"]


def _execute_context_with_municipality_crs(tmp_path, monkeypatch, municipality_crs):
    """A stub execute context whose municipalities carry ``municipality_crs`` (all other frames EPSG:25832)."""
    fixture = _fixture()
    calls = []
    monkeypatch.setattr("braunschweig.data.cordon.network.read_transit_stops_routes",
                        lambda path: calls.append("read_transit_stops_routes") or (fixture["stops"], fixture["routes"]))
    monkeypatch.setattr("braunschweig.data.spatial.cordon.buffer_m_from_fraction",
                        lambda df, fraction: calls.append("buffer") or 123.0)
    monkeypatch.setattr("braunschweig.data.spatial.cordon.build_cordon_polygon",
                        lambda df, buffer_m: calls.append("build_cordon_polygon") or fixture["cordon_polygon"])
    stages = {
        stage.TRIPS_STAGE: fixture["trips"], "synthesis.population.enriched": fixture["persons"],
        "synthesis.population.spatial.home.locations": fixture["df_home"],
        "synthesis.population.spatial.primary.locations": (fixture["df_work"], fixture["df_education"]),
        "braunschweig.synthesis.cordon_gates": {"gates": fixture["gates"], "assignment": None},
        "braunschweig.data.cordon_network": fixture["links"],
        "braunschweig.data.external_secondary_points": fixture["external_points"],
        "data.spatial.municipalities": gpd.GeoDataFrame({"name": ["muni"]}, geometry=[Point(0.0, 0.0)],
                                                        crs=municipality_crs),
        "matsim.scenario.supply.processed": {"schedule_path": "schedule.xml.gz"},
    }
    values = {keys.KEY_ENABLED: True, keys.KEY_MAX_ROUTABLE_DISTANCE_M: 45000,
              keys.KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE: 0.2, keys.KEY_MODE_SUBSTITUTION_WARN_SHARE: 0.1,
              keys.KEY_FALLBACK_WARN_SHARE: 0.1, "random_seed": 7, "cordon_network_buffer_fraction": 0.1}

    class _Context:
        def config(self, key):
            return values[key]

        def stage(self, name):
            return stages[name]

        def path(self, name=None):
            return str(tmp_path)

    return _Context(), calls


def test_execute_rejects_municipalities_in_another_crs_before_any_spatial_step(tmp_path, monkeypatch):
    context, calls = _execute_context_with_municipality_crs(tmp_path, monkeypatch, "EPSG:4326")
    with pytest.raises(ValueError, match=r"municipalities.*EPSG:4326.*EPSG:25832|EPSG:25832.*municipalities.*EPSG:4326"):
        stage.execute(context)
    assert calls == []                                    # the cordon polygon was never built
    assert not (tmp_path / "portal_trips_report.json").exists()


@pytest.mark.parametrize("frame_name", ["gates", "links", "external_points", "df_home"])
def test_build_portal_trips_rejects_a_frame_in_another_crs(frame_name):
    fixture = _fixture()
    fixture[frame_name] = fixture[frame_name].to_crs("EPSG:4326")
    with pytest.raises(ValueError) as error:
        stage.build_portal_trips(**fixture)
    message = str(error.value)
    assert frame_name in message and "EPSG:4326" in message and "EPSG:25832" in message


def test_build_portal_trips_rejects_a_frame_without_a_crs():
    fixture = _fixture()
    fixture["links"] = fixture["links"].set_crs(None, allow_override=True)
    with pytest.raises(ValueError, match="links.*None"):
        stage.build_portal_trips(**fixture)


def test_assert_consistent_crs_accepts_equal_crs_given_in_different_spellings():
    frames = {"a": gpd.GeoDataFrame(geometry=[], crs="EPSG:25832"), "b": gpd.GeoDataFrame(geometry=[], crs=25832)}
    stage.assert_consistent_crs(frames)
