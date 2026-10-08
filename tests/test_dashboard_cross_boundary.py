"""Trips that touch an outside activity form an additive cross-boundary category (#442, ruling R28).

Every pre-#442 key keeps its definition (all trips after the pseudo-mode drop); the new blocks
``cross_boundary`` and ``in_region`` are additive.

The eqasim-trip KPIs live in ``run_metrics.metrics_matsim`` (the block that reads
``simulation_output/eqasim_trips.csv``); ``metrics_eqasim`` only summarises the synthesis CSVs.
"""
import numpy as np
import pandas as pd

from braunschweig.analysis.dashboard import run_metrics

EXISTING_EQASIM_TRIP_KEYS = (
    "all_trip_dist_pct", "mean_trip_km", "median_trip_km", "sim_trip_mode_share_pct",
    "mean_km_by_mode", "dist_pct_by_mode", "commute", "mean_km_by_purpose",
)


def _run(tmp_path, monkeypatch, frame):
    # The per-Kreis block needs the VG250 archive; it is irrelevant for the keys under test.
    monkeypatch.setattr(run_metrics, "_load_zgb_kreise", lambda: None)
    frame.to_csv(tmp_path / "eqasim_trips.csv", sep=";", index=False)
    return run_metrics.metrics_matsim(tmp_path)


def _trips(rows):
    columns = ["person_id", "person_trip_id", "preceding_purpose", "following_purpose", "mode",
               "routed_distance", "travel_time", "departure_time"]
    return pd.DataFrame(rows, columns=columns)


def test_existing_keys_keep_their_all_trips_definition_with_outside_trips(tmp_path, monkeypatch):
    trips = _trips([
        (1, 0, "home", "work", "car", 8000.0, 900.0, 1.0),
        (1, 1, "work", "home", "car", 8000.0, 900.0, 2.0),
        (2, 0, "home", "shop", "walk", 2000.0, 1500.0, 3.0),
        (3, 0, "home", "outside", "car", 40000.0, 2400.0, 4.0),
        (4, 0, "outside", "work", "car", 30000.0, 2000.0, 5.0),
        (5, 0, "outside", "outside", "outside", 10000.0, 900.0, 6.0),  # pseudo-mode leg: dropped as before
    ])
    out = _run(tmp_path, monkeypatch, trips)

    # Values pinned by hand for the five real-mode trips (km 8, 8, 2, 40, 30), outside-touching ones included.
    assert out["sim_trip_mode_share_pct"] == {"car": 80.0, "walk": 20.0}
    assert out["mean_trip_km"] == 17.6
    assert out["median_trip_km"] == 8.0
    assert out["mean_km_by_mode"] == {"car": 21.5, "walk": 2.0}
    assert out["mean_km_by_purpose"] == {"home": 8.0, "outside": 40.0, "shop": 2.0, "work": 19.0}
    assert out["commute"]["n_trips"] == 2
    assert out["commute"]["mean_km"] == 19.0
    assert out["commute"]["median_km"] == 19.0
    assert out["commute"]["p95_km"] == 28.9
    assert out["commute"]["mode_share_pct"] == {"car": 100.0}
    assert out["all_trip_dist_pct"] == run_metrics._to_km_bands(np.array([8.0, 8.0, 2.0, 40.0, 30.0]))
    assert "all_trips_mode_share_pct" not in out


def test_cross_boundary_and_in_region_blocks_are_additive(tmp_path, monkeypatch):
    trips = _trips([
        (1, 0, "home", "work", "car", 8000.0, 900.0, 1.0),
        (1, 1, "work", "home", "car", 8000.0, 900.0, 2.0),
        (2, 0, "home", "shop", "walk", 2000.0, 1500.0, 3.0),
        (3, 0, "home", "outside", "car", 40000.0, 2400.0, 4.0),
        (4, 0, "outside", "work", "car", 30000.0, 2000.0, 5.0),
    ])
    out = _run(tmp_path, monkeypatch, trips)
    assert out["cross_boundary"] == {
        "n_trips": 2, "share_pct": 40.0, "mode_share_pct": {"car": 100.0}, "mean_km": 35.0,
    }
    in_region = out["in_region"]
    assert set(in_region) == {"n_trips", "mode_share_pct", "mean_trip_km", "median_trip_km", "commute"}
    assert in_region["n_trips"] == 3
    assert in_region["mode_share_pct"] == {"car": 66.67, "walk": 33.33}
    assert in_region["mean_trip_km"] == 6.0
    assert in_region["median_trip_km"] == 8.0
    # The in-commuter trip (home outside, destination work) is not an in-region commute.
    assert set(in_region["commute"]) == set(out["commute"])
    assert in_region["commute"]["n_trips"] == 1
    assert in_region["commute"]["mean_km"] == 8.0
    assert in_region["commute"]["mode_share_pct"] == {"car": 100.0}


def test_run_without_outside_activity_has_in_region_equal_to_existing_keys(tmp_path, monkeypatch):
    trips = _trips([
        (1, 0, "home", "work", "car", 8000.0, 900.0, 1.0),
        (1, 1, "work", "home", "car", 8000.0, 900.0, 2.0),
        (2, 0, "home", "shop", "bicycle", 2000.0, 600.0, 3.0),
        (2, 1, "shop", "home", "walk", 2000.0, 1500.0, 4.0),
    ])
    out = _run(tmp_path, monkeypatch, trips)
    assert out["sim_trip_mode_share_pct"] == {"car": 50.0, "bicycle": 25.0, "walk": 25.0}
    assert out["mean_trip_km"] == 5.0
    assert out["in_region"]["mode_share_pct"] == out["sim_trip_mode_share_pct"]
    assert out["in_region"]["mean_trip_km"] == out["mean_trip_km"]
    assert out["in_region"]["commute"] == out["commute"]
    assert out["cross_boundary"] == {"n_trips": 0, "share_pct": 0.0, "mode_share_pct": {}, "mean_km": None}


def test_missing_purpose_column_skips_both_blocks_with_warning(tmp_path, monkeypatch, caplog):
    trips = _trips([(1, 0, "home", "work", "car", 8000.0, 900.0, 1.0)]).drop(columns=["preceding_purpose"])
    with caplog.at_level("WARNING"):
        out = _run(tmp_path, monkeypatch, trips)
    assert "cross_boundary" not in out and "in_region" not in out
    assert out["mean_trip_km"] == 8.0
    assert any("preceding_purpose" in r.getMessage() for r in caplog.records)
