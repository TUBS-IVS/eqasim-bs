"""The 10 % validation package reads loop trips with their diary length, not 0 m (issue #679).

Every distance consumer of the package reads ``distance_km`` from ``io.trips_full()`` (directly or through
``metrics.trip_summary``), so the correction is tested there. Hand-computed crow-fly values: a stroll loop with diary
3.0 km is 3.0 / 1.3 km, the trip back to the loop start 0.8 / 1.3 km, a normal trip keeps its gpkg length.
"""
import pandas as pd
import pytest

from scripts.validate_bs_10pct import io


def test_trips_full_replaces_the_zero_crow_fly_length_of_loop_trips(tmp_path, monkeypatch):
    prefix = "t_"
    (tmp_path / f"{prefix}trips.csv").write_text(
        "person_id;trip_index;is_loop_stroll;loop_trip_diary_distance_km\n"
        "1;0;True;3.0\n1;1;False;0.8\n2;0;False;\n")
    monkeypatch.setattr(io, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(io, "PREFIX", prefix)
    keys = {"person_id": [1, 1, 2], "trip_index": [0, 1, 0]}
    monkeypatch.setattr(io, "load_trips", lambda: pd.DataFrame({**keys, "following_purpose": ["leisure", "work", "work"]}))
    monkeypatch.setattr(io, "load_trip_modes", lambda: pd.DataFrame({**keys, "mode": ["walk", "walk", "car"]}))
    monkeypatch.setattr(io, "persons_with_kreis", lambda: pd.DataFrame({"person_id": [1, 2], "ars5": ["03101"] * 2}))
    monkeypatch.setattr(io, "load_trips_gdf", lambda: pd.DataFrame({**keys, "distance_km": [0.0, 0.0, 9.0]}))

    full = io.trips_full.__wrapped__()  # bypass the lru_cache so the monkeypatched inputs are used

    assert full["distance_km"].tolist() == pytest.approx([3.0 / 1.3, 0.8 / 1.3, 9.0])
    assert full["mode"].tolist() == ["walk", "walk", "car"]
