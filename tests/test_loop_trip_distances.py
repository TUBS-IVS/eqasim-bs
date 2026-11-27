"""Survey diary length of loop trips replaces their 0 m in the analyses (issue #679).

The rule lives in ``braunschweig/analysis/loop_trip_distances.py``. Fixture, one person each:
p1 walks a stroll loop (diary 3.0 km) and returns to the loop start (diary 0.8 km); p2 is a normal car trip; p3 is a
zero-length trip the table does not mark (the plan repair's invented home trip); p4 is marked as a loop but has no
diary length; p9 is in the loop table only (e.g. a stuck agent absent from the analysed trips).
"""
import logging

import pandas as pd
import pytest

from braunschweig.analysis import loop_trip_distances as ltd
from braunschweig.constants import ROUTED_DETOUR_FACTOR

KEYS = ("person_id", "trip_index")


def _trips():
    return pd.DataFrame(
        [
            ("p1", 0, "walk", "leisure", 0.0, 0.0),
            ("p1", 1, "walk", "work", 0.0, 0.0),
            ("p2", 0, "car", "work", 12.0, 9000.0),
            ("p3", 0, "walk", "home", 0.0, 0.0),
            ("p4", 0, "walk", "leisure", 0.0, 0.0),
        ],
        columns=["person_id", "trip_index", "mode", "following_purpose", "distance_km", "crow_fly_m"])


def _loop_table():
    return pd.DataFrame(
        [("p1", 0, True, 3.0), ("p1", 1, False, 0.8), ("p4", 0, True, float("nan")), ("p9", 0, True, 2.0)],
        columns=["person_id", "trip_index", ltd.LOOP_FLAG_COLUMN, ltd.LOOP_DIARY_DISTANCE_COLUMN])


def _apply(trips, loop_trips, metric_kind):
    return ltd.apply_loop_trip_distances(
        trips, loop_trips, key_columns=KEYS, distance_column="distance_km", distance_unit="km",
        metric_kind=metric_kind, crow_fly_column="crow_fly_m", crow_fly_unit="m")


@pytest.mark.parametrize("metric_kind, loop_km, back_km", [
    ("path", 3.0, 0.8),
    # Hand-computed: the diary length is a path length; the straight line is shorter by the detour factor.
    ("crow_fly", 3.0 / 1.3, 0.8 / 1.3),
])
def test_diary_length_replaces_zero_for_both_metric_kinds(metric_kind, loop_km, back_km):
    assert ROUTED_DETOUR_FACTOR == 1.3  # the hand-computed divisors above assume the project constant
    original = _trips()
    corrected, report = _apply(original, _loop_table(), metric_kind)

    assert corrected["distance_km"].tolist() == pytest.approx([loop_km, back_km, 12.0, 0.0, 0.0])
    assert corrected["mode"].tolist() == original["mode"].tolist()
    assert original["distance_km"].tolist() == [0.0, 0.0, 12.0, 0.0, 0.0]  # the input frame is not modified
    assert report["into_loop"] == {"matched": 2, "corrected": 1, "without_diary_length": 1}
    assert report["back_to_loop_start"] == {"matched": 1, "corrected": 1, "without_diary_length": 0}
    assert (report["unmatched"], report["unmatched_share"]) == (1, 0.25)
    assert report["km_before_by_mode"] == {"walk": 0.0}
    assert report["km_after_by_mode"] == {"walk": pytest.approx(loop_km + back_km, abs=1e-3)}


def test_marked_trip_with_a_real_model_distance_means_misaligned_indices_and_raises():
    trips = _trips()
    trips.loc[0, "crow_fly_m"] = 250.0  # p1 trip 0 is marked as zero length, but the model has 250 m
    with pytest.raises(ValueError, match=r"1 of 3 .*\('p1', 0\)"):
        _apply(trips, _loop_table(), "path")


def test_unmatched_share_above_the_threshold_warns(caplog):
    with caplog.at_level(logging.WARNING, logger=ltd.logger.name):
        _apply(_trips(), _loop_table(), "path")  # 1 of 4 loop-table trips is absent: 25 % > threshold
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_without_loop_table_the_frame_is_unchanged_and_zero_length_leisure_walks_are_warned(caplog):
    trips = _trips()
    with caplog.at_level(logging.WARNING, logger=ltd.logger.name):
        out, report = _apply(trips, None, "path")
    pd.testing.assert_frame_equal(out, trips)
    # p1 trip 0 and p4 trip 0: zero length, leisure, on foot (p1 trip 1 ends at work, p3 at home)
    assert report == {"available": False, "zero_length_leisure_walk_bicycle_trips": 2}
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def _write_trips_csv(tmp_path, header, rows):
    path = tmp_path / "x_trips.csv"
    path.write_text("\n".join([";".join(header)] + [";".join(str(c) for c in r) for r in rows]) + "\n")
    return path


def test_read_loop_trips_keeps_marked_rows_and_rows_with_a_diary_length(tmp_path):
    header = ["person_id", "trip_index", "mode", ltd.LOOP_FLAG_COLUMN, ltd.LOOP_DIARY_DISTANCE_COLUMN]
    path = _write_trips_csv(tmp_path, header, [
        (1, 0, "walk", True, 3.0), (1, 1, "walk", False, 0.8), (1, 2, "car", False, ""),
        (2, 0, "car", False, ""), (3, 0, "walk", True, "")])
    table = ltd.read_loop_trips(path)
    assert list(table.columns) == ["person_id", "trip_index", ltd.LOOP_FLAG_COLUMN, ltd.LOOP_DIARY_DISTANCE_COLUMN]
    assert table[["person_id", "trip_index"]].values.tolist() == [[1, 0], [1, 1], [3, 0]]
    assert table[ltd.LOOP_FLAG_COLUMN].tolist() == [True, False, True]
    assert table[ltd.LOOP_DIARY_DISTANCE_COLUMN].tolist()[:2] == [3.0, 0.8]
    assert pd.isna(table[ltd.LOOP_DIARY_DISTANCE_COLUMN].iloc[2])


def test_read_loop_trips_without_the_columns_is_none_and_with_one_column_raises(tmp_path, caplog):
    off = _write_trips_csv(tmp_path, ["person_id", "trip_index", "mode"], [(1, 0, "walk")])
    with caplog.at_level(logging.INFO, logger=ltd.logger.name):
        assert ltd.read_loop_trips(off) is None
    assert any(r.levelno == logging.INFO for r in caplog.records)

    half = _write_trips_csv(tmp_path, ["person_id", "trip_index", ltd.LOOP_FLAG_COLUMN], [(1, 0, True)])
    with pytest.raises(ValueError, match="only the column"):
        ltd.read_loop_trips(half)
