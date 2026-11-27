"""The dashboard's MATSim km metrics use the survey diary length of loop trips instead of 0 m (issue #679).

Hand-computed: walk trips are a stroll loop (diary 3.0 km), the trip back to the loop start (diary 0.8 km) and a
normal 1.0 km walk, so the mean walk trip is (3.0 + 0.8 + 1.0) / 3 = 1.6 km instead of 1.0 / 3 without the correction.
"""
import pandas as pd
import pytest

from braunschweig.analysis import loop_trip_distances as ltd
from braunschweig.analysis.dashboard import run_metrics


def _write_eqasim_trips(tmp_path):
    columns = ["person_id", "person_trip_id", "preceding_purpose", "following_purpose", "mode",
               "routed_distance", "euclidean_distance", "travel_time", "departure_time"]
    rows = [
        (1, 0, "work", "leisure", "walk", 0.0, 0.0, 600.0, 36000.0),
        (1, 1, "leisure", "work", "walk", 0.0, 0.0, 600.0, 39600.0),
        (2, 0, "home", "work", "car", 10000.0, 8000.0, 900.0, 28800.0),
        (3, 0, "home", "leisure", "walk", 1000.0, 800.0, 900.0, 50000.0),
    ]
    pd.DataFrame(rows, columns=columns).to_csv(tmp_path / "eqasim_trips.csv", sep=";", index=False)


def test_mean_km_by_mode_and_loop_block_reflect_the_diary_length(tmp_path, monkeypatch):
    monkeypatch.setattr(run_metrics, "_load_zgb_kreise", lambda: None)  # the VG250 archive is irrelevant here
    _write_eqasim_trips(tmp_path)
    loop_trips = pd.DataFrame(
        [(1, 0, True, 3.0), (1, 1, False, 0.8)],
        columns=["person_id", "trip_index", ltd.LOOP_FLAG_COLUMN, ltd.LOOP_DIARY_DISTANCE_COLUMN])

    corrected = run_metrics.metrics_matsim(tmp_path, loop_trips=loop_trips)
    assert corrected["mean_km_by_mode"]["walk"] == pytest.approx(1.6)
    assert corrected["mean_km_by_mode"]["car"] == pytest.approx(10.0)
    assert corrected["loop_trips"]["into_loop"]["corrected"] == 1
    assert corrected["loop_trips"]["back_to_loop_start"]["corrected"] == 1
    assert corrected["loop_trips"]["km_after_by_mode"] == {"walk": pytest.approx(3.8)}

    uncorrected = run_metrics.metrics_matsim(tmp_path)
    assert uncorrected["mean_km_by_mode"]["walk"] == pytest.approx(1.0 / 3.0, abs=0.01)
    assert uncorrected["loop_trips"]["available"] is False
