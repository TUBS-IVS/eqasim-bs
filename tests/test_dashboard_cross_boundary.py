"""Trips that touch an outside activity leave the in-region modal split and form their own category (#442).

The eqasim-trip KPIs live in ``run_metrics.metrics_matsim`` (the block that reads
``simulation_output/eqasim_trips.csv``); ``metrics_eqasim`` only summarises the synthesis CSVs.
"""
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


def test_metrics_matsim_separates_cross_boundary_trips(tmp_path, monkeypatch):
    trips = _trips([
        (1, 0, "home", "shop", "walk", 1000.0, 900.0, 1.0),
        (1, 1, "shop", "home", "walk", 1000.0, 900.0, 2.0),
        (2, 0, "home", "outside", "car", 40000.0, 2400.0, 3.0),
        (2, 1, "outside", "home", "car", 40000.0, 2400.0, 4.0),
    ])
    out = _run(tmp_path, monkeypatch, trips)
    assert out["sim_trip_mode_share_pct"] == {"walk": 100.0}
    assert out["cross_boundary"]["n_trips"] == 2 and out["cross_boundary"]["share_pct"] == 50.0
    assert out["cross_boundary"]["mode_share_pct"] == {"car": 100.0}
    assert out["cross_boundary"]["mean_km"] == 40.0
    # Old definition (mode share over ALL trips) stays available so earlier run manifests remain comparable.
    assert out["all_trips_mode_share_pct"] == {"walk": 50.0, "car": 50.0}
    # In-region distance KPIs no longer see the 40 km portal trips.
    assert out["mean_trip_km"] == 1.0


def test_in_commuter_trip_with_outside_home_is_cross_boundary(tmp_path, monkeypatch):
    trips = _trips([
        (1, 0, "home", "work", "car", 5000.0, 600.0, 1.0),
        (2, 0, "outside", "work", "car", 30000.0, 1800.0, 2.0),
    ])
    out = _run(tmp_path, monkeypatch, trips)
    assert out["cross_boundary"]["n_trips"] == 1
    assert out["commute"]["n_trips"] == 1
    assert out["commute"]["mean_km"] == 5.0


def test_run_without_outside_activity_keeps_existing_keys_identical(tmp_path, monkeypatch):
    trips = _trips([
        (1, 0, "home", "work", "car", 8000.0, 900.0, 1.0),
        (1, 1, "work", "home", "car", 8000.0, 900.0, 2.0),
        (2, 0, "home", "shop", "bicycle", 2000.0, 600.0, 3.0),
        (2, 1, "shop", "home", "walk", 2000.0, 1500.0, 4.0),
    ])
    out = _run(tmp_path, monkeypatch, trips)

    expected_share = {"car": 50.0, "bicycle": 25.0, "walk": 25.0}
    assert out["sim_trip_mode_share_pct"] == expected_share
    assert out["all_trips_mode_share_pct"] == expected_share
    assert out["mean_trip_km"] == 5.0
    assert out["median_trip_km"] == 5.0
    assert out["mean_km_by_mode"] == {"bicycle": 2.0, "car": 8.0, "walk": 2.0}
    assert out["mean_km_by_purpose"] == {"home": 5.0, "shop": 2.0, "work": 8.0}
    assert out["commute"]["n_trips"] == 1 and out["commute"]["mean_km"] == 8.0
    assert set(EXISTING_EQASIM_TRIP_KEYS) <= set(out)
    assert out["cross_boundary"] == {"n_trips": 0, "share_pct": 0.0, "mode_share_pct": {}, "mean_km": None}
