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
    assert corrected["loop_trips"]["out_of_loop_at_its_place"]["corrected"] == 1
    assert corrected["loop_trips"]["km_after_by_mode"] == {"walk": pytest.approx(3.8)}

    uncorrected = run_metrics.metrics_matsim(tmp_path)
    assert uncorrected["mean_km_by_mode"]["walk"] == pytest.approx(1.0 / 3.0, abs=0.01)
    assert uncorrected["loop_trips"]["available"] is False


@pytest.mark.parametrize("with_companion, expected_trip_km, expected_source", [
    (True, [2.0, 2.5], run_metrics.LOOP_COUNTED_TRAVEL_DISTANCE_FILE),
    (False, [1.0, 1.5], "traveldistancestats.csv"),
])
def test_the_distance_evolution_reads_the_loop_counted_companion_when_the_run_has_it(
        tmp_path, with_companion, expected_trip_km, expected_source):
    """eqasim-java-bs writes MATSim's traveldistancestats.csv columns with the loop diary lengths next to MATSim's
    file; the dashboard prefers it and names the file it read."""
    header = "ITERATION;avg. Average Leg distance;avg. Average Trip distance\n"
    (tmp_path / "traveldistancestats.csv").write_text(header + "0;900.0;1000.0\n1;1400.0;1500.0\n")
    if with_companion:
        (tmp_path / run_metrics.LOOP_COUNTED_TRAVEL_DISTANCE_FILE).write_text(
            header + "0;1900.0;2000.0\n1;2400.0;2500.0\n")

    out = run_metrics.metrics_matsim(tmp_path)

    assert out["distance_evolution_source"] == expected_source
    assert out["distance_evolution"]["avg_trip_km"] == expected_trip_km
    assert out["avg_trip_km_final"] == expected_trip_km[-1]


def test_the_run_record_reads_the_loop_table_from_the_synthesis_trips_file(tmp_path, monkeypatch):
    """The whole dashboard path: assemble_run_record finds <prefix>trips.csv in the output folder, reads its loop
    columns and hands them to the MATSim metrics (the hand-computed walk mean of the first test)."""
    from braunschweig.analysis.dashboard import run_records

    output_dir, sim_output = tmp_path / "out", tmp_path / "matsim_output"
    output_dir.mkdir()
    sim_output.mkdir()
    _write_eqasim_trips(sim_output)
    pd.DataFrame({"person_id": [1, 1, 2, 3], "trip_index": [0, 1, 0, 0],
                  ltd.LOOP_FLAG_COLUMN: [True, False, False, False],
                  ltd.LOOP_DIARY_DISTANCE_COLUMN: [3.0, 0.8, None, None]}).to_csv(
        output_dir / "x_trips.csv", sep=";", index=False)
    monkeypatch.setattr(run_metrics, "_load_zgb_kreise", lambda: None)
    monkeypatch.setattr(run_records, "metrics_eqasim", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_records, "load_mid_reference", lambda: {})
    monkeypatch.setattr(run_records, "build_comparisons", lambda *args, **kwargs: {})
    monkeypatch.setattr(run_records, "_find_sim_output", lambda sim_cache: sim_cache)

    record = run_records.assemble_run_record("label", output_dir, sim_output, None)

    assert record["matsim"]["mean_km_by_mode"]["walk"] == pytest.approx(1.6)
    assert record["matsim"]["loop_trips"]["into_loop"]["corrected"] == 1
