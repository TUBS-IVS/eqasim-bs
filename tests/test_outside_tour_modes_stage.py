"""The outside-tour-modes stage: declared keys, the OFF pass-through (output, counters, no warning), the pure
entry point on a fixture and the report (eqasim-bs#453)."""
import json
import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.calibration import srv_mode_by_distance_band as M
from braunschweig.synthesis.outside_tour_modes import config_keys as keys
from braunschweig.synthesis.outside_tour_modes import stage
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable

CRS = "EPSG:25832"


class _RecordingConfigureContext:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_keys_and_the_inputs():
    context = _RecordingConfigureContext({"cordon_enabled": True})
    stage.configure(context)
    for key in (keys.KEY_ENABLED, keys.KEY_MIN_SHARE, keys.KEY_WARN_SHARE, "random_seed", "data_path",
                "cordon_enabled", "cordon_network_buffer_fraction"):
        assert key in context.calls
    assert context.calls[keys.KEY_MIN_SHARE] == keys.DEFAULT_MIN_SHARE
    assert context.stages == [keys.FINAL_TRIPS_STAGE, keys.LOCATIONS_STAGE, keys.PERSONS_STAGE,
                              keys.MUNICIPALITIES_STAGE]


def test_configure_off_declares_only_the_flag_and_the_trips_stage():
    context = _RecordingConfigureContext({keys.KEY_ENABLED: False})
    stage.configure(context)
    assert context.stages == [keys.FINAL_TRIPS_STAGE]
    assert set(context.calls) == {keys.KEY_ENABLED}


def test_configure_rejects_on_without_the_cordon():
    with pytest.raises(ValueError, match="cordon_enabled"):
        stage.configure(_RecordingConfigureContext({"cordon_enabled": False}))


def test_configure_rejects_an_invalid_min_share():
    with pytest.raises(ValueError, match=keys.KEY_MIN_SHARE.replace(".", r"\.")):
        stage.configure(_RecordingConfigureContext({"cordon_enabled": True, keys.KEY_MIN_SHARE: 1.5}))


class _ValidateContext:
    """Mimics synpp's ValidateContext: config() raises for an option the stage did not declare."""

    def __init__(self, values):
        self._values = values

    def config(self, option):
        if option not in self._values:
            raise KeyError(f"Config option {option} is not requested")
        return self._values[option]


def test_validate_token_is_a_stable_hex_digest_and_needs_only_the_flag_when_off():
    context = _ValidateContext({keys.KEY_ENABLED: False})
    token = stage.validate(context)
    assert token == stage.validate(context) and len(token) == 32 and int(token, 16) >= 0


def test_validate_token_covers_the_committed_table_bytes_when_on(tmp_path):
    srv_dir = tmp_path.joinpath(*stage.SRV_SUBDIR)
    srv_dir.mkdir(parents=True)
    table_path = srv_dir / M.TABLE_FILE_NAME
    context = _ValidateContext({keys.KEY_ENABLED: True, "data_path": str(tmp_path)})
    with pytest.raises(FileNotFoundError, match=M.TABLE_FILE_NAME):
        stage.validate(context)
    table_path.write_text("# a\npurpose_group,band\n", encoding="utf-8")
    first = stage.validate(context)
    table_path.write_text("# b\npurpose_group,band\n", encoding="utf-8")
    second = stage.validate(context)
    assert first != second
    assert first != stage.validate(_ValidateContext({keys.KEY_ENABLED: False}))


def test_execute_off_passes_the_frame_through_without_counters_or_warnings(caplog, tmp_path):
    trips = pd.DataFrame({"person_id": [1], "trip_index": [0], "mode": ["walk"]})

    class _Context:
        def config(self, key):
            assert key == keys.KEY_ENABLED
            return False

        def stage(self, name):
            assert name == keys.FINAL_TRIPS_STAGE
            return trips

        def path(self):
            return str(tmp_path)

    with caplog.at_level(logging.INFO):
        result = stage.execute(_Context())
    assert result is trips
    assert not os.path.exists(os.path.join(str(tmp_path), keys.REPORT_FILE_NAME))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _table():
    rows = []
    cells = {
        ("work", 0.0, 5.0): {"walk": 0.3, "bicycle": 0.3, "car": 0.3, "car_passenger": 0.05, "pt": 0.05},
        ("work", 5.0, float("inf")): {"walk": 0.0, "bicycle": 0.0, "car": 0.9, "car_passenger": 0.05, "pt": 0.05},
        ("education", 0.0, float("inf")): {"walk": 0.3, "bicycle": 0.3, "car": 0.2, "car_passenger": 0.1, "pt": 0.1},
        ("other", 0.0, float("inf")): {"walk": 0.3, "bicycle": 0.1, "car": 0.5, "car_passenger": 0.05, "pt": 0.05},
        ("all", 0.0, 45.0): {"walk": 0.3, "bicycle": 0.2, "car": 0.4, "car_passenger": 0.05, "pt": 0.05},
        ("all", 45.0, float("inf")): {"walk": 0.0, "bicycle": 0.0, "car": 0.7, "car_passenger": 0.05, "pt": 0.25},
    }
    for (group, lower, upper), shares in cells.items():
        for mode in M.MODES:
            rows.append((group, M.band_label(lower, upper), lower, upper, mode, shares[mode], 100,
                         int(shares[mode] * 100)))
    return ModeShareTable.from_frame(pd.DataFrame(rows, columns=M.TABLE_COLUMNS))


def _fixture():
    extent = box(0.0, 0.0, 10000.0, 10000.0)
    persons = pd.DataFrame({"person_id": [1, 2, 3], "car_availability": ["all", "none", "all"],
                            "has_license": [True, False, True], "bicycle_availability": ["all", "all", "all"],
                            "car_passenger_availability": ["some", "none", "some"]})
    # person 1: walks 20 km to a workplace outside the extent (implausible -> re-moded, car only candidate
    #           with share 0.9 besides car_passenger/pt); person 2: walks 1 km inside (untouched);
    # person 3: drives to a portal gate (plausible in all/45_plus; untouched)
    trips = pd.DataFrame({
        "person_id": [1, 1, 2, 2, 3, 3], "trip_index": [0, 1, 0, 1, 0, 1],
        "preceding_purpose": ["home", "work", "home", "shop", "home", "outside"],
        "following_purpose": ["work", "home", "shop", "home", "outside", "home"],
        "mode": ["walk", "walk", "walk", "walk", "car", "car"],
    })
    points = {(1, 0): (5000.0, 5000.0), (1, 1): (25000.0, 5000.0), (1, 2): (5000.0, 5000.0),
              (2, 0): (5000.0, 5000.0), (2, 1): (5000.0, 6000.0), (2, 2): (5000.0, 5000.0),
              (3, 0): (5000.0, 5000.0), (3, 1): (9900.0, 5000.0), (3, 2): (5000.0, 5000.0)}
    locations = gpd.GeoDataFrame({"person_id": [k[0] for k in points], "activity_index": [k[1] for k in points]},
                                 geometry=[Point(*xy) for xy in points.values()], crs=CRS)
    return dict(trips=trips, locations=locations, persons=persons, polygon=extent, table=_table(),
                min_share=0.01, warn_share=0.5, seed=7)


def test_remode_outside_tours_re_modes_the_frozen_walk_tour_only_and_reports_it(caplog):
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**_fixture())
    assert out["mode"].tolist()[2:] == ["walk", "walk", "car", "car"]
    assert out["mode"].tolist()[0] == out["mode"].tolist()[1] and out["mode"].tolist()[0] in {"car", "car_passenger", "pt"}
    assert report["n_outside_tours"] == 2 and report["n_outside_tours_remoded"] == 1
    assert report["n_outside_tours_by_group"] == {"work": 1, "all": 1}
    assert report["n_legs_remoded"] == 2 and report["n_legs_swept"] == 0
    assert report["n_portal_legs_as_all_group"] == 2
    assert list(report["substitution_matrix"]) == [f"walk->{out['mode'].tolist()[0]}"]
    assert report["cells_used"] == {"work/5_plus": 1}
    records = [r for r in caplog.records if "[outside_tour_modes]" in r.getMessage()]
    assert any("re-moded 1/2" in r.getMessage() for r in records)
    assert not [r for r in records if r.levelno >= logging.WARNING]


def test_remode_outside_tours_warns_above_the_warn_share(caplog):
    fixture = _fixture()
    fixture["warn_share"] = 0.4
    with caplog.at_level(logging.INFO):
        stage.remode_outside_tours(**fixture)
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "re-moded 1/2" in r.getMessage()]


def test_remode_outside_tours_raises_on_a_crs_mismatch():
    fixture = _fixture()
    fixture["locations"] = fixture["locations"].to_crs("EPSG:4326")
    fixture["municipalities_crs"] = CRS
    with pytest.raises(ValueError, match="CRS"):
        stage.remode_outside_tours(**fixture)


def test_execute_on_writes_the_report_and_returns_the_re_moded_frame(tmp_path, monkeypatch):
    fixture = _fixture()
    municipalities = gpd.GeoDataFrame({"id": [1]}, geometry=[box(0.0, 0.0, 10000.0, 10000.0)], crs=CRS)
    values = {keys.KEY_ENABLED: True, keys.KEY_MIN_SHARE: 0.01, keys.KEY_WARN_SHARE: 0.5, "random_seed": 1,
              "data_path": str(tmp_path), "cordon_network_buffer_fraction": 0.0}
    stages = {keys.FINAL_TRIPS_STAGE: fixture["trips"], keys.LOCATIONS_STAGE: fixture["locations"],
              keys.PERSONS_STAGE: fixture["persons"], keys.MUNICIPALITIES_STAGE: municipalities}
    monkeypatch.setattr(stage, "_load_table", lambda srv_dir: fixture["table"])

    class _Context:
        def config(self, key):
            return values[key]

        def stage(self, name):
            return stages[name]

        def path(self):
            return str(tmp_path)

    out = stage.execute(_Context())
    assert out["mode"].tolist()[0] in {"car", "car_passenger", "pt"}
    with open(os.path.join(str(tmp_path), keys.REPORT_FILE_NAME), encoding="utf-8") as handle:
        report = json.load(handle)
    assert report["enabled"] is True and report["n_outside_tours_remoded"] == 1


def test_remode_outside_tours_keeps_order_index_and_columns_of_a_shuffled_input():
    fixture = _fixture()
    shuffled = fixture["trips"].sample(frac=1.0, random_state=3)
    fixture["trips"] = shuffled
    out, report = stage.remode_outside_tours(**fixture)
    assert out.index.tolist() == shuffled.index.tolist() and list(out.columns) == list(shuffled.columns)
    by_person = out.groupby("person_id")["mode"].agg(lambda modes: sorted(set(modes)))
    assert by_person[2] == ["walk"] and by_person[3] == ["car"]
    assert len(by_person[1]) == 1 and by_person[1][0] != "walk"
    assert shuffled["mode"].tolist() == fixture["trips"]["mode"].tolist()
    assert report["n_outside_tours_remoded"] == 1


def test_remode_outside_tours_without_an_implausible_leg_changes_nothing_and_reports_zero(caplog):
    fixture = _fixture()
    fixture["trips"] = fixture["trips"][fixture["trips"]["person_id"] != 1]
    with caplog.at_level(logging.INFO):
        out, report = stage.remode_outside_tours(**fixture)
    assert out["mode"].tolist() == fixture["trips"]["mode"].tolist()
    assert report["n_outside_tours"] == 1 and report["n_outside_tours_remoded"] == 0
    assert report["share_outside_tours_remoded"] == 0.0 and report["substitution_matrix"] == {}
    assert report["cells_used"] == {} and report["n_legs_remoded"] == 0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
