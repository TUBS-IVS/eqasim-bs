"""End-to-end tests for scripts/measure_donor_distance_mismatch.py on tiny CSV fixtures.

The script itself can only run on the run server (the raw MiD microdata and the popsim pseudonym
map live there), so these tests exercise the whole ``main()`` path -- the four reads, the three
joins, the two cross-tabs and both output files -- on synthetic CSVs written into ``tmp_path``,
plus the guards that must fail loudly: the privacy contract, the join-rate floor and the id
normalisation across writers.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_SPEC = importlib.util.spec_from_file_location(
    "measure_donor_distance_mismatch", REPO / "scripts" / "measure_donor_distance_mismatch.py")
measure = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(measure)


def _write_inputs(tmp_path, *, persons=None, assigned=None, pseudonym_map=None, mid_persons=None,
                  mid_trips=None):
    """Write a consistent six-worker fixture, overriding any frame the caller supplies.

    Donor distances (P_ARB_ENTF): a 5 km -> lt10, b 30 km -> 25_50, c 5 km -> lt10, d 12 km ->
    10_25, e 60 km -> 50_100, f 999 (a MiD missing code) -> missing. Against the assigned classes
    below that gives 2 equal, 2 assigned-above, 1 assigned-below and 1 non-comparable worker.
    """
    raw = tmp_path / "mid_raw"
    raw.mkdir(exist_ok=True)
    if persons is None:
        persons = pd.DataFrame({
            "person_id": [1, 2, 3, 4, 5, 6],
            "household_id": [1, 1, 2, 2, 3, 3],
            "hts_id": [101, 102, 103, 104, 105, 106],
        })
    if assigned is None:
        assigned = pd.DataFrame({
            "person_id": [1, 2, 3, 4, 5, 6],
            "assigned_distance_class": ["lt10", "25_50", "25_50", "100_200", "10_25", "50_100"],
            "distance_km": [5.1, 30.0, 31.0, 120.0, 12.0, 60.0],
        })
    if pseudonym_map is None:
        pseudonym_map = pd.DataFrame({
            "source_person_id": [101, 102, 103, 104, 105, 106],
            "source_household_id": [9001, 9001, 9002, 9003, 9004, 9005],
            "H_ID": [1, 1, 2, 3, 4, 5],
            "P_ID": [1, 2, 1, 1, 1, 1],
        })
    if mid_persons is None:
        mid_persons = pd.DataFrame({
            "H_ID": [1, 1, 2, 3, 4, 5],
            "P_ID": [1, 2, 1, 1, 1, 1],
            "P_ARB_ENTF": [5.0, 30.0, 5.0, 12.0, 60.0, 999.0],
            "P_STARB1": [1, 1, 2, 1, 1, 9],
            "starb2": [2, 1, 409, 2, 3, 99],
            "M_HOFF": [1, 1, 1, 1, 1, 0],
            "arbwo": [1, 1, 1, 1, 1, 2],
            "ST_WOTAG": [1, 2, 3, 4, 5, 6],
        })
    if mid_trips is None:
        mid_trips = pd.DataFrame({
            "H_ID": [1, 1, 2, 3, 5, 5],
            "P_ID": [1, 2, 1, 1, 1, 1],
            # Only these persons made a work trip; H4/P1 (hts 105) made none, so the trip-length
            # table has one more non-comparable worker than the P_ARB_ENTF table.
            "W_ZWECK": [1, 1, 1, 1, 7, 1],
            "wegkm": [6.0, 28.0, 40.0, 12.0, 3.0, 15.0],
        })
    persons.to_csv(tmp_path / "persons.csv", sep=";", index=False)
    assigned.to_csv(tmp_path / "assigned.csv", index=False)
    pseudonym_map.to_csv(tmp_path / "pseudonym_map.csv", index=False)
    mid_persons.to_csv(raw / measure.MID_PERSON_FILE, index=False)
    mid_trips.to_csv(raw / measure.MID_TRIP_FILE, index=False)
    return raw


def _argv(tmp_path, raw, out_dir, extra=()):
    return [
        "--persons-csv", str(tmp_path / "persons.csv"),
        "--assigned-class-csv", str(tmp_path / "assigned.csv"),
        "--pseudonym-map", str(tmp_path / "pseudonym_map.csv"),
        "--mid-raw", str(raw),
        "--out-dir", str(out_dir),
        "--source-commit", "deadbeef",
    ] + list(extra)


def _read_cross_tab(path):
    return pd.read_csv(path, comment="#").set_index("donor_distance_class")


def test_main_writes_both_cross_tabs_and_the_diagnostics(tmp_path):
    raw = _write_inputs(tmp_path)
    out_dir = tmp_path / "out"
    assert measure.main(_argv(tmp_path, raw, out_dir)) == 0

    diagnostics = json.loads((out_dir / measure.DIAGNOSTICS_FILE).read_text(encoding="utf-8"))
    assert diagnostics["n_workers"] == 6 and diagnostics["n_matched_donor"] == 6
    assert diagnostics["n_comparable"] == 5 and diagnostics["n_assigned_gt_donor"] == 2
    assert diagnostics["share_assigned_gt_donor"] == pytest.approx(2 / 5)
    assert diagnostics["n_donor_distance_missing"] == 1
    assert diagnostics["donor_distance_source"] == "P_ARB_ENTF"
    for key in ("join_rate_assigned_to_persons", "join_rate_hts_to_pseudonym_map",
                "join_rate_pseudonym_map_to_mid", "join_rate_workers_to_donor_rows"):
        assert diagnostics[key] == pytest.approx(1.0)

    universe = diagnostics["donor_universe"]
    assert universe["n_workers"] == 6
    assert universe["n_in_home_office_module"] == 5 and universe["n_not_in_home_office_module"] == 1
    assert universe["n_distance_valid"] == 5
    assert universe["n_by_reporting_day_weekday"] == {"1": 5, "2": 1}
    assert universe["n_by_reporting_day_of_week"] == {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1}

    # The trip-length table is the spec's second source and covers a DIFFERENT set of workers:
    # hts 105 has a P_ARB_ENTF but made no work trip, while hts 106 has a missing P_ARB_ENTF code
    # and does have a work trip (15 km -> 10_25 against an assigned 50_100). Both tables therefore
    # have one non-comparable worker, but not the same one, and the mismatch counts differ.
    trip = diagnostics["trip_length"]
    assert trip["n_donor_distance_missing"] == 1 and trip["n_comparable"] == 5
    assert trip["n_assigned_gt_donor"] == 2 and trip["n_assigned_eq_donor"] == 3
    assert trip["n_assigned_lt_donor"] == 0 and diagnostics["n_assigned_lt_donor"] == 1
    assert "work-trip length" in trip["donor_distance_source"]

    cross_tab = _read_cross_tab(out_dir / measure.CROSS_TAB_FILE)
    assert cross_tab.loc["all", "n_donor_total"] == 6
    assert cross_tab.loc["lt10", "n_25_50"] == 1
    assert cross_tab.loc["missing", "n_50_100"] == 1
    trip_cross_tab = _read_cross_tab(out_dir / measure.TRIP_LENGTH_CROSS_TAB_FILE)
    assert trip_cross_tab.loc["all", "n_donor_total"] == 6
    assert trip_cross_tab.loc["10_25", "n_50_100"] == 1

    # Privacy contract: neither committed table may carry an identifier column.
    for name in (measure.CROSS_TAB_FILE, measure.TRIP_LENGTH_CROSS_TAB_FILE):
        columns = set(pd.read_csv(out_dir / name, comment="#").columns)
        assert not (columns & set(measure.FORBIDDEN_OUTPUT_COLUMNS))
    assert not (set(diagnostics) & set(measure.FORBIDDEN_OUTPUT_COLUMNS))


def test_trip_length_source_disables_the_200km_topcode(tmp_path):
    """Finding 5 (whole-branch review): wegkm is never MiD top-coded, so an exact 200 km donor
    work-trip length must classify as gt200 in the trip-length cross-tab, never 100_200 -- unlike
    a donor P_ARB_ENTF of exactly 200.0, which IS the legitimate MiD top-code and stays 100_200."""
    persons = pd.DataFrame({"person_id": [1], "household_id": [1], "hts_id": [101]})
    assigned = pd.DataFrame({"person_id": [1], "assigned_distance_class": ["gt200"],
                             "distance_km": [250.0]})
    pseudonym_map = pd.DataFrame({"source_person_id": [101], "source_household_id": [9001],
                                  "H_ID": [1], "P_ID": [1]})
    mid_persons = pd.DataFrame({
        "H_ID": [1], "P_ID": [1], "P_ARB_ENTF": [999.0], "P_STARB1": [1], "starb2": [2],
        "M_HOFF": [1], "arbwo": [1], "ST_WOTAG": [1],
    })
    mid_trips = pd.DataFrame({"H_ID": [1], "P_ID": [1], "W_ZWECK": [1], "wegkm": [200.0]})
    raw = _write_inputs(tmp_path, persons=persons, assigned=assigned, pseudonym_map=pseudonym_map,
                        mid_persons=mid_persons, mid_trips=mid_trips)
    out_dir = tmp_path / "out"
    assert measure.main(_argv(tmp_path, raw, out_dir)) == 0

    diagnostics = json.loads((out_dir / measure.DIAGNOSTICS_FILE).read_text(encoding="utf-8"))
    assert diagnostics["trip_length"]["n_assigned_eq_donor"] == 1  # gt200 (assigned) == gt200 (donor)
    trip_cross_tab = _read_cross_tab(out_dir / measure.TRIP_LENGTH_CROSS_TAB_FILE)
    assert trip_cross_tab.loc["gt200", "n_gt200"] == 1
    assert trip_cross_tab.loc["100_200", "n_donor_total"] == 0

    header_text = "\n".join(line for line in
                            (out_dir / measure.TRIP_LENGTH_CROSS_TAB_FILE).read_text(
                                encoding="utf-8").splitlines() if line.startswith("#"))
    assert "topcode_km=None" in header_text


def test_assert_no_identifiers_rejects_a_raw_mid_id_column():
    frame = pd.DataFrame({"donor_distance_class": ["lt10"], "H_ID": [1]})
    with pytest.raises(ValueError, match="H_ID"):
        measure._assert_no_identifiers(frame, "some_table.csv")


def test_assert_no_identifier_keys_rejects_a_nested_identifier_key():
    with pytest.raises(ValueError, match="donor_universe.P_ID"):
        measure._assert_no_identifier_keys({"donor_universe": {"P_ID": 3}}, "diagnostics.json")


def test_normalise_id_handles_the_float_widening_branch():
    # A left merge that leaves some rows unmatched widens an integer id column to float; the
    # normalisation must bring it back so the next join compares like with like.
    widened = pd.Series([101.0, 102.0, 103.0])
    normalised = measure._normalise_id(widened)
    assert normalised.tolist() == [101, 102, 103] and str(normalised.dtype) == "int64"
    # A genuinely non-numeric id stays a stripped string rather than becoming NaN.
    textual = measure._normalise_id(pd.Series([" a1 ", "b2"]))
    assert textual.tolist() == ["a1", "b2"]


def test_join_rate_guard_fires_below_the_threshold():
    with pytest.raises(ValueError, match="Join rate too low"):
        measure._enforce_join_rate(97, 100, "some join", 0.99)
    assert measure._enforce_join_rate(99, 100, "some join", 0.99) == pytest.approx(0.99)


def test_main_fails_when_the_pseudonym_map_misses_donors(tmp_path):
    # Two of six workers point at a donor the map does not know -> 66.7% < the 99% floor.
    pseudonym_map = pd.DataFrame({
        "source_person_id": [101, 102, 103, 104],
        "source_household_id": [9001, 9001, 9002, 9003],
        "H_ID": [1, 1, 2, 3],
        "P_ID": [1, 2, 1, 1],
    })
    raw = _write_inputs(tmp_path, pseudonym_map=pseudonym_map)
    with pytest.raises(ValueError, match="Join rate too low"):
        measure.main(_argv(tmp_path, raw, tmp_path / "out"))


def test_main_fails_on_a_missing_required_column(tmp_path):
    mid_persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 3, 4, 5],
        "P_ID": [1, 2, 1, 1, 1, 1],
        "P_ARB_ENTF": [5.0, 30.0, 5.0, 12.0, 60.0, 999.0],
        "P_STARB1": [1, 1, 2, 1, 1, 9],
        "starb2": [2, 1, 409, 2, 3, 99],
        "M_HOFF": [1, 1, 1, 1, 1, 0],
        "arbwo": [1, 1, 1, 1, 1, 2],
    })  # ST_WOTAG missing
    raw = _write_inputs(tmp_path, mid_persons=mid_persons)
    with pytest.raises(ValueError, match="ST_WOTAG"):
        measure.main(_argv(tmp_path, raw, tmp_path / "out"))


def test_main_fails_on_a_missing_input_file(tmp_path):
    raw = _write_inputs(tmp_path)
    (raw / measure.MID_TRIP_FILE).unlink()
    with pytest.raises(FileNotFoundError, match=measure.MID_TRIP_FILE):
        measure.main(_argv(tmp_path, raw, tmp_path / "out"))
