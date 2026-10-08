"""The comparison of model arms with the committed SrV parking references (``scripts/parking/compare_parking_targets.py``).

Parking cost zones v2, Task 5 of issue #436. The script reads, per arm, the outcome report of the Java side
(``ITERS/it.N/N.parking_outcomes.csv``, v3 or v2), the run's ``eqasim_trips`` and the two committed SrV tables, and writes a
table of metrics with the model value, the reference READ from the SrV table, the difference in percentage points and the
universe of every row. Covered on small synthetic runs whose every expected number is derived in a comment: the report
reader (version by header, rows by name, strict checks), the metric definitions and their universes (paid share, garage
share, commuter garage share, free shares per workplace class, car mode shares in and out of the zones), the arms without
a report, the per-arm deltas, determinism, the command line and the rule that the script reads committed inputs only.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "parking"
COMMITTED = REPO / "eqasim-data" / "data"
SCRIPT = REPO / "scripts" / "parking" / "compare_parking_targets.py"

V3_HEADER = "outcome,zone_id,purpose,count,share,garage_probability_sum,stays_with_garages_in_range"
V2_HEADER = "outcome,zone_id,purpose,count,share"

#: The synthetic report (outcome, zone, purpose, count, garage probability sum, stays with garages in range). The numbers
#: of the expected metrics below are sums over these rows.
ROWS = [
    ("PAID_EXPECTED", "bs_zone_ia", "shop", 40, 12.0, 40),
    ("PAID_METERED", "bs_zone_ia", "shop", 10, 0.0, 10),
    ("OUTSIDE_FEE_HOURS", "bs_zone_ia", "shop", 20, 0.0, 0),
    ("FREE_WITHIN_LIMIT", "bs_zone_ia", "leisure", 10, 0.0, 10),
    ("HOME", "bs_zone_ia", "home", 20, 0.0, 0),
    ("EMPLOYER_FREE", "bs_zone_ia", "work", 30, 0.0, 0),
    ("PAID_EXPECTED", "bs_zone_ia", "work", 20, 4.0, 20),
    ("PAID_COMMUTER", "bs_zone_ia", "work", 10, 0.0, 10),
    ("RESIDENT_FREE", "bs_zone_ia", "work", 5, 0.0, 0),
    ("PAID_EXPECTED", "bs_zone_ib", "other", 30, 9.0, 30),
    ("PAID_LONG_STAY", "bs_zone_ib", "education", 10, 0.0, 10),
    ("EMPLOYER_FREE", "bs_zone_ib", "education", 10, 0.0, 0),
    ("EMPLOYER_FREE", "fx_sz", "work", 90, 0.0, 0),
    ("FREE_WITHIN_LIMIT", "fx_sz", "work", 5, 0.0, 0),
    ("PAID_METERED", "fx_sz", "work", 5, 0.0, 0),
    ("PAID_CAMPUS_MEMBER", "fx_campus", "work", 7, 0.0, 0),
    ("NO_ZONE", "", "shop", 100, 0.0, 0),
    ("NO_ZONE", "", "home", 50, 0.0, 0),
]
TOTAL_CALLS = sum(row[3] for row in ROWS)   # 215 in Ia and Ib + 100 in fx_sz + 7 campus + 150 outside = 472

#: The classes of the synthetic SrV commuter table: (free share, street share, garage share).
COMMUTE_CLASSES = {"bs_zentrum": (0.5, 0.2, 0.2), "bs_innenbereich": (0.9, 0.3, 0.1), "bs_outer": (0.9, 0.3, 0.1),
                   "03102": (0.9, 0.3, 0.1), "03103": (0.95, 0.3, 0.1), "03157": (0.96, 0.3, 0.1)}


@pytest.fixture(scope="module")
def cmp():
    spec = importlib.util.spec_from_file_location("compare_parking_targets_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_outcomes(path: Path, rows, *, version: int = 3, reverse: bool = False) -> Path:
    """Write an outcome report; ``reverse`` lists the rows in another order (rows are keyed by name, never by position)."""
    rows = list(reversed(rows)) if reverse else list(rows)
    total = sum(row[3] for row in rows)
    lines = [V3_HEADER if version == 3 else V2_HEADER]
    for outcome, zone_id, purpose, count, probability_sum, in_range in rows:
        line = f"{outcome},{zone_id},{purpose},{count},{count / total:.6f}"
        lines.append(line + (f",{probability_sum:.6f},{in_range}" if version == 3 else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_run(root: Path, name: str, trips, rows=None, *, version: int = 3) -> Path:
    """A run output directory: ``eqasim_trips.csv.gz`` and, for ``rows``, the reports of iterations 0 (no priced stay) and 2."""
    run = root / name
    run.mkdir(parents=True)
    frame = pd.DataFrame(trips, columns=["mode", "destination_x", "destination_y"])
    frame.insert(0, "person_id", range(len(frame)))
    frame.insert(1, "person_trip_id", 0)
    with gzip.open(run / "eqasim_trips.csv.gz", "wt", encoding="utf-8") as stream:
        frame.to_csv(stream, sep=";", index=False)
    if rows is not None:
        write_outcomes(run / "ITERS" / "it.0" / "0.parking_outcomes.csv", [], version=version)
        write_outcomes(run / "ITERS" / "it.2" / "2.parking_outcomes.csv", rows, version=version)
    return run


def _trips(spec):
    """``spec``: {(x, y): {mode: n}} -> a list of (mode, x, y)."""
    return [(mode, x, y) for (x, y), modes in spec.items() for mode, n in modes.items() for _ in range(n)]


#: Destinations: Ia (603100, 5790100), Ib (603500, 5790100), the zone fx_sz (610100, 5790100) and outside every zone.
IA, IB, SZ, OUT = (603100.0, 5790100.0), (603500.0, 5790100.0), (610100.0, 5790100.0), (600000.0, 5780000.0)
ZONES_TRIPS = _trips({IA: {"car": 3, "pt": 1}, IB: {"car": 1, "walk": 1}, SZ: {"car": 2, "pt": 2},
                      OUT: {"car": 4, "walk": 3, "bike": 3}})
OFF_TRIPS = _trips({IA: {"car": 4}, IB: {"car": 1, "walk": 1}, SZ: {"car": 3, "pt": 1}, OUT: {"car": 6, "walk": 2, "bike": 2}})


@pytest.fixture
def data(tmp_path):
    """A data directory with the synthetic SrV tables, the fixture tariffs (zones renamed to the calibration zones) and zones."""
    root = tmp_path / "data"
    srv = root / "braunschweig" / "srv"
    srv.mkdir(parents=True)
    (srv / "srv2023_city_center_parking.csv").write_text(
        "# synthetic\nparking_type,share,n_unweighted\nemployer_lot,0.02,5\nstreet,0.3,5\ngarage_large_lot,0.6,5\n"
        "other,0.08,5\npaid_share_overall,0.8,5\n", encoding="utf-8")
    lines = ["# synthetic", "workplace_class,level,n_unweighted,n_eff,share_employer_lot,share_street,share_garage_large_lot,"
             "share_other,share_paid_total,share_free_total"]
    for name, (free, street, garage) in COMMUTE_CLASSES.items():
        lines.append(f"{name},class,200,100.0,0.3,{street},{garage},0.1,{1 - free},{free}")
    lines.append("total,total,1200,600.0,0.3,0.3,0.1,0.1,0.1,0.9")
    (srv / "srv2023_commute_parking_by_workplace_class.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    parking = root / "braunschweig" / "parking"
    parking.mkdir()
    text = (FIXTURES / "parking_tariffs_fixture.csv").read_text(encoding="utf-8")
    (parking / "parking_tariffs_2026.csv").write_text(
        text.replace("\nfx_bs_ia,", "\nbs_zone_ia,").replace("\nfx_bs_ib,", "\nbs_zone_ib,"), encoding="utf-8")
    zones = gpd.GeoDataFrame(
        {"zone_id": ["bs_zone_ia", "bs_zone_ib", "fx_sz"], "geometry_source": "centre_approximation",
         "source_url": "https://example.org/zones", "source_date": "2026-10-07", "digitised_on": "2026-10-07",
         "digitising_note": "synthetic zone of the comparison test"}, crs="EPSG:25832",
        geometry=[box(603000, 5790000, 603200, 5790200), box(603400, 5790000, 603600, 5790200),
                  box(610000, 5790000, 610200, 5790200)])
    zones.to_crs("EPSG:4326").to_file(parking / "parking_zones_2026.geojson", driver="GeoJSON")
    return root


def row(table: pd.DataFrame, arm: str, metric: str) -> pd.Series:
    found = table[(table["arm"] == arm) & (table["metric"] == metric)]
    assert len(found) == 1, f"{arm}/{metric}: {len(found)} rows"
    return found.iloc[0]


# ------------------------------------------------------------------------------------------- the outcome report reader


def test_a_v3_report_is_read_by_column_name_whatever_the_row_order(cmp, tmp_path):
    forward, version = cmp.read_parking_outcomes(write_outcomes(tmp_path / "a.csv", ROWS))
    backward, _ = cmp.read_parking_outcomes(write_outcomes(tmp_path / "b.csv", ROWS, reverse=True))
    assert version == 3
    pd.testing.assert_frame_equal(forward.sort_values(["outcome", "zone_id", "purpose"]).reset_index(drop=True),
                                  backward.sort_values(["outcome", "zone_id", "purpose"]).reset_index(drop=True))
    assert forward["count"].sum() == TOTAL_CALLS and forward["garage_probability_sum"].sum() == pytest.approx(25.0)
    assert (forward.loc[forward["outcome"] == "NO_ZONE", "zone_id"] == "").all()   # an empty zone id stays text, never NaN


def test_a_v2_report_is_read_and_has_no_garage_figures(cmp, tmp_path):
    frame, version = cmp.read_parking_outcomes(write_outcomes(tmp_path / "v2.csv", ROWS, version=2))
    assert version == 2 and frame["count"].sum() == TOTAL_CALLS
    assert frame["garage_probability_sum"].isna().all() and frame["stays_with_garages_in_range"].isna().all()


def test_the_report_reader_is_strict(cmp, tmp_path):
    def write(text):
        path = tmp_path / "bad.csv"
        path.write_text(text, encoding="utf-8")
        return path

    good = "PAID_METERED,bs_zone_ia,shop,10,1.000000,0.000000,0\n"
    with pytest.raises(ValueError, match="version 1"):
        cmp.read_parking_outcomes(write("outcome,count,share\nPAID_METERED,10,1.0\n"))
    with pytest.raises(ValueError, match="header"):
        cmp.read_parking_outcomes(write("outcome,zone_id,count\nPAID_METERED,bs_zone_ia,10\n"))
    with pytest.raises(ValueError, match="no priced stay"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\n"))
    with pytest.raises(ValueError, match="unknown outcome 'PAID_MAGIC'"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nPAID_MAGIC,bs_zone_ia,shop,10,1.000000,0.000000,0\n"))
    with pytest.raises(ValueError, match="duplicate"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\n" + good + good))
    with pytest.raises(ValueError, match="NO_ZONE"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nNO_ZONE,bs_zone_ia,shop,10,1.000000,0.000000,0\n"))
    with pytest.raises(ValueError, match="needs a zone id"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nPAID_METERED,,shop,10,1.000000,0.000000,0\n"))
    with pytest.raises(ValueError, match="share"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nPAID_METERED,bs_zone_ia,shop,10,0.500000,0.000000,0\n"))
    with pytest.raises(ValueError, match="garage_probability_sum"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nPAID_METERED,bs_zone_ia,shop,10,1.000000,10.500000,0\n"))
    with pytest.raises(ValueError, match="stays_with_garages_in_range"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nPAID_METERED,bs_zone_ia,shop,10,1.000000,0.000000,11\n"))
    with pytest.raises(ValueError, match="count"):
        cmp.read_parking_outcomes(write(V3_HEADER + "\nPAID_METERED,bs_zone_ia,shop,-1,1.000000,0.000000,0\n"))
    with pytest.raises(FileNotFoundError):
        cmp.read_parking_outcomes(tmp_path / "missing.csv")


def test_the_outcome_names_are_the_python_reference_names(cmp):
    from braunschweig.parking import cost
    # every outcome of the reference is classified exactly once (paid, free or other): a new pricing branch must be classified
    classified = [*cmp.PAID_OUTCOMES, *cmp.FREE_OUTCOMES, *cmp.OTHER_OUTCOMES]
    assert sorted(classified) == sorted(cost.OUTCOMES)


# ------------------------------------------------------------------------------------------------------- the references


def test_the_references_are_read_from_the_srv_tables(cmp, data):
    references = cmp.read_references(data)
    assert references.paid_share == 0.8
    assert references.garage_target.value == pytest.approx(0.6 / 0.9, abs=1e-12)   # garage 0.6 / (0.6 + street 0.3)
    assert references.commuter_garage.value == pytest.approx(0.2 / 0.4, abs=1e-12)  # class bs_zentrum 0.2 / (0.2 + 0.2)
    assert references.free_share_by_class["03102"] == 0.9 and references.free_share_by_class["bs_zentrum"] == 0.5
    assert "total" not in references.free_share_by_class.index
    # change the table, the reference follows: nothing is typed in the script
    path = data / "braunschweig" / "srv" / "srv2023_city_center_parking.csv"
    path.write_text(path.read_text(encoding="utf-8").replace("paid_share_overall,0.8", "paid_share_overall,0.55"), encoding="utf-8")
    assert cmp.read_references(data).paid_share == 0.55


def test_the_committed_tables_give_the_references_of_the_calibration_script(cmp):
    references = cmp.read_references(COMMITTED)
    assert 0.0 < references.paid_share < 1.0 and 0.0 < references.garage_target.value < 1.0
    assert references.commuter_garage.table == "srv2023_commute_parking_by_workplace_class.csv"
    assert {"bs_zentrum", "03103"} <= set(references.free_share_by_class.index)
    assert references.free_share_by_class.index.is_unique


def test_compare_uses_only_committed_inputs():
    text = SCRIPT.read_text(encoding="utf-8")
    # no raw-data argument and no import of the raw readers: the references are the committed aggregates
    for forbidden in ("--raw", "srv2023_raw", "extract_srv_commute_parking", "braunschweig.calibration.srv_parking",
                      "SrV2023_Personen", "SciUse"):
        assert forbidden not in text, forbidden
    # no reference number typed into the code: every number of the two committed tables is absent from it
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    numbers = set()
    for name in ("srv2023_city_center_parking.csv", "srv2023_commute_parking_by_workplace_class.csv"):
        table = pd.read_csv(COMMITTED / "braunschweig" / "srv" / name, comment="#", dtype=str)
        for column in table.columns:
            numbers.update(value for value in table[column].dropna() if re.fullmatch(r"0\.\d{3,}", value))
    assert numbers, "the committed tables hold no reference number to look for"
    for number in sorted(numbers):
        assert number not in code, number


# ------------------------------------------------------------------------------------------------------------- metrics


@pytest.fixture
def two_arms(tmp_path):
    """The run directories of an arm without a report (off) and of the zones arm."""
    runs = tmp_path / "runs"
    return write_run(runs, "off", OFF_TRIPS), write_run(runs, "zones", ZONES_TRIPS, ROWS)


def compare_two(cmp, data, off, zones, **kwargs):
    arms = [cmp.ArmSpec("off", off, False), cmp.ArmSpec("zones_v2", zones, True)]
    return cmp.compare(arms, data_path=data, zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson",
                       **kwargs)


def test_paid_share_in_the_city_centre_zones_is_counted_over_pricing_calls(cmp, data, two_arms):
    result = compare_two(cmp, data, *two_arms)
    table = result.table
    # Ia and Ib hold 165 + 50 = 215 calls; the paid ones (PAID_*): Ia 40 + 10 + 20 + 10 = 80, Ib 30 + 10 = 40
    paid = row(table, "zones_v2", "paid_share_ia_ib_all_purposes")
    assert paid["model"] == pytest.approx(120 / 215, abs=1e-6) and paid["reference"] == 0.8
    assert paid["delta_pp"] == pytest.approx(100 * (120 / 215 - 0.8), abs=1e-3)
    # without the 20 home calls: 120 / 195; without home, work and education: Ia shop 70 + leisure 10 and Ib other 30 = 110
    # calls, 40 + 10 + 30 = 80 paid
    assert row(table, "zones_v2", "paid_share_ia_ib_non_home")["model"] == pytest.approx(120 / 195, abs=1e-6)
    assert row(table, "zones_v2", "paid_share_ia_ib_other_purposes")["model"] == pytest.approx(80 / 110, abs=1e-6)
    assert "pricing calls" in paid["universe_note"] and "not trips" in paid["universe_note"]
    assert paid["reference_source"] == "srv2023_city_center_parking.csv row paid_share_overall"
    assert paid["unit"] == "share"
    # M1: an upper bound of the chosen-trip paid share, said in the note
    for needle in ("UPPER BOUND", "PAID_EXPECTED", "zero-cent", "less likely chosen", "non-chosen"):
        assert needle in paid["universe_note"], needle
    # M3: the size of the universe of every row (calls of the zones Ia and Ib: 215, 195, 110)
    assert paid["universe_size"] == 215 and row(table, "zones_v2", "paid_share_ia_ib_other_purposes")["universe_size"] == 110
    assert row(table, "zones_v2", "free_share_work_education_class_bs_zentrum")["universe_size"] == 85
    assert row(table, "zones_v2", "car_trip_share_ending_in_ia_ib")["universe_size"] == 6
    assert row(table, "zones_v2", "car_trip_share_all_trips")["universe_size"] == 20
    assert math.isnan(row(table, "off", "paid_share_ia_ib_all_purposes")["universe_size"])
    assert row(table, "off", "car_trip_share_all_trips")["universe_size"] == 20


def test_garage_share_is_the_target_row_and_labelled_a_calibration_target(cmp, data, two_arms):
    table = compare_two(cmp, data, *two_arms).table
    # other purposes in Ia and Ib: 110 calls, garage probability 12 (Ia shop) + 9 (Ib other) = 21; 90 of them have a garage in
    # range (the 20 OUTSIDE_FEE_HOURS calls have none); 70 are priced as an expectation
    garage = row(table, "zones_v2", "garage_share_ia_ib_other_purposes")
    assert garage["model"] == pytest.approx(21 / 110, abs=1e-6)
    assert garage["reference"] == pytest.approx(0.6 / 0.9, abs=1e-6)
    assert garage["delta_pp"] == pytest.approx(100 * (21 / 110 - 0.6 / 0.9), abs=1e-3)
    assert "calibration target, not validation" in garage["universe_note"]
    assert "NOT the calibration universe" in garage["universe_note"] and "pricing calls" in garage["universe_note"]
    assert row(table, "zones_v2", "garage_in_range_share_ia_ib_other_purposes")["model"] == pytest.approx(90 / 110, abs=1e-6)
    assert row(table, "zones_v2", "paid_expected_share_ia_ib_other_purposes")["model"] == pytest.approx(70 / 110, abs=1e-6)
    assert math.isnan(row(table, "zones_v2", "paid_expected_share_ia_ib_other_purposes")["reference"])   # no reference: model only


def test_commuter_garage_share_is_an_independent_check(cmp, data, two_arms):
    table = compare_two(cmp, data, *two_arms).table
    # work and education in Ia and Ib: Ia work 30 + 20 + 10 + 5 = 65, Ib education 10 + 10 = 20 -> 85 calls, garage probability 4;
    # the employer-free calls (30 + 10 = 40) leave the second denominator: 45
    everything = row(table, "zones_v2", "garage_share_ia_ib_work_education_all_calls")
    without = row(table, "zones_v2", "garage_share_ia_ib_work_education_without_employer_free")
    assert everything["model"] == pytest.approx(4 / 85, abs=1e-6) and without["model"] == pytest.approx(4 / 45, abs=1e-6)
    assert without["reference"] == pytest.approx(0.5, abs=1e-12)   # class bs_zentrum: garage 0.2 / (garage 0.2 + street 0.2)
    assert "independent check" in without["universe_note"] and "not used in the calibration" in without["universe_note"]
    assert "street and garage users" in without["universe_note"]


def test_free_shares_per_workplace_class_use_the_zones_of_the_class(cmp, data, two_arms):
    table = compare_two(cmp, data, *two_arms).table
    # class bs_zentrum (zones Ia and Ib), work and education: 85 calls; no-charge outcomes EMPLOYER_FREE 30 + 10 and
    # RESIDENT_FREE 5 = 45; employer-free alone 40
    zentrum = row(table, "zones_v2", "free_share_work_education_class_bs_zentrum")
    assert zentrum["model"] == pytest.approx(45 / 85, abs=1e-6) and zentrum["reference"] == 0.5
    assert zentrum["delta_pp"] == pytest.approx(100 * (45 / 85 - 0.5), abs=1e-3)
    assert row(table, "zones_v2", "employer_free_share_work_education_class_bs_zentrum")["model"] == pytest.approx(40 / 85)
    # class 03102 (zone fx_sz): 100 work calls, EMPLOYER_FREE 90 + FREE_WITHIN_LIMIT 5 = 95; the campus zone adds nothing
    assert row(table, "zones_v2", "free_share_work_education_class_03102")["model"] == pytest.approx(0.95)
    assert row(table, "zones_v2", "employer_free_share_work_education_class_03102")["model"] == pytest.approx(0.90)
    assert row(table, "zones_v2", "employer_free_share_work_education_class_03102")["delta_pp"] == pytest.approx(0.0, abs=1e-6)
    # a class without a work or education call in this run is a row without a model value, never a zero
    empty = row(table, "zones_v2", "free_share_work_education_class_03157")
    assert math.isnan(empty["model"]) and "No pricing call of this universe" in empty["universe_note"]
    assert "work_education" in empty["universe"]
    assert "parking_free_share_proxy_classes" in zentrum["universe_note"]
    # one row per class of the street and resident zones of the tariff table (the campus zone has its own share, ASSUMPTION C2)
    classes = table.loc[(table["arm"] == "zones_v2") & table["metric"].str.startswith("free_share_work_education_class_"), "metric"]
    assert {metric.removeprefix("free_share_work_education_class_") for metric in classes} == set(COMMUTE_CLASSES)


def test_counts_and_the_share_of_calls_outside_every_zone_are_reported(cmp, data, two_arms):
    table = compare_two(cmp, data, *two_arms).table
    assert row(table, "zones_v2", "pricing_calls_total")["model"] == TOTAL_CALLS
    assert row(table, "zones_v2", "pricing_calls_total")["unit"] == "count"
    assert row(table, "zones_v2", "pricing_calls_ia_ib")["model"] == 215
    assert row(table, "zones_v2", "no_zone_share_of_pricing_calls")["model"] == pytest.approx(150 / TOTAL_CALLS, abs=1e-6)


def test_a_v2_report_gives_no_garage_figure_instead_of_a_zero(cmp, data, tmp_path):
    zones = write_run(tmp_path / "runs", "zones_v2_report", ZONES_TRIPS, ROWS, version=2)
    table = cmp.compare([cmp.ArmSpec("old", zones, True)], data_path=data,
                        zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson").table
    garage = row(table, "old", "garage_share_ia_ib_other_purposes")
    assert math.isnan(garage["model"]) and "v2" in garage["universe_note"] and "no garage" in garage["universe_note"]
    assert row(table, "old", "paid_share_ia_ib_all_purposes")["model"] == pytest.approx(120 / 215, abs=1e-6)


def test_car_mode_shares_in_and_out_of_the_zones_are_model_only(cmp, data, two_arms):
    table = compare_two(cmp, data, *two_arms).table
    # zones arm: 20 trips; Ia and Ib hold 4 + 2 = 6 trips of which 3 + 1 = 4 by car; with fx_sz (4 trips, 2 car) 10 and 6; outside
    # 10 trips, 4 car; overall 10 car of 20
    expected = {"car_trip_share_ending_in_ia_ib": 4 / 6, "car_trip_share_ending_in_zones": 6 / 10,
                "car_trip_share_ending_outside_zones": 4 / 10, "car_trip_share_all_trips": 10 / 20}
    for metric, value in expected.items():
        found = row(table, "zones_v2", metric)
        assert found["model"] == pytest.approx(value, abs=1e-6) and math.isnan(found["reference"])
        assert math.isnan(found["delta_pp"]) and "model only" in found["reference_source"]
        assert "chosen" in found["universe_note"] and "final iteration" in found["universe_note"]
    assert row(table, "zones_v2", "trips_ending_in_zones")["model"] == 10
    assert row(table, "zones_v2", "car_trips_ending_in_ia_ib")["model"] == 4
    # off arm: 4 car trips at Ia and 1 at Ib of 6 trips in Ia and Ib; outside every zone 10 trips, 6 by car
    assert row(table, "off", "car_trip_share_ending_in_ia_ib")["model"] == pytest.approx(5 / 6, abs=1e-6)
    assert row(table, "off", "car_trip_share_ending_outside_zones")["model"] == pytest.approx(6 / 10, abs=1e-6)


def test_an_arm_without_a_report_has_the_outcome_rows_empty_and_the_trip_rows_filled(cmp, data, two_arms):
    table = compare_two(cmp, data, *two_arms).table
    off_paid = row(table, "off", "paid_share_ia_ib_all_purposes")
    assert math.isnan(off_paid["model"]) and math.isnan(off_paid["delta_pp"]) and off_paid["reference"] == 0.8
    assert "no parking outcome report" in off_paid["universe_note"]
    assert row(table, "off", "car_trip_share_all_trips")["model"] == pytest.approx(14 / 20)   # 4 + 1 + 3 + 6 car trips of 20
    # every arm has every metric: the table is rectangular and nothing is missing silently
    per_arm = table.groupby("arm")["metric"].apply(lambda metrics: tuple(metrics))
    assert per_arm["off"] == per_arm["zones_v2"]


def test_an_arm_that_must_have_a_report_but_has_none_fails(cmp, data, tmp_path):
    run = write_run(tmp_path / "runs", "broken", ZONES_TRIPS)   # no ITERS directory
    with pytest.raises(FileNotFoundError, match="parking outcome report"):
        cmp.compare([cmp.ArmSpec("broken", run, True)], data_path=data,
                    zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson")
    (run / "ITERS" / "it.3").mkdir(parents=True)   # an iteration directory without its report
    with pytest.raises(FileNotFoundError, match="3.parking_outcomes.csv"):
        cmp.compare([cmp.ArmSpec("broken", run, True)], data_path=data,
                    zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson")


def test_an_outcome_zone_without_a_tariff_row_fails(cmp, data, tmp_path):
    rows = ROWS + [("PAID_METERED", "nowhere", "shop", 1, 0.0, 0)]
    run = write_run(tmp_path / "runs", "unknown", ZONES_TRIPS, rows)
    with pytest.raises(ValueError, match="nowhere"):
        cmp.compare([cmp.ArmSpec("unknown", run, True)], data_path=data,
                    zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson")


def test_the_iteration_defaults_to_the_last_one_and_a_chosen_earlier_one_is_flagged(cmp, data, tmp_path, caplog):
    run = write_run(tmp_path / "runs", "iter", ZONES_TRIPS, ROWS)
    arms = [cmp.ArmSpec("iter", run, True)]
    zones = data / "braunschweig" / "parking" / "parking_zones_2026.geojson"
    result = cmp.compare(arms, data_path=data, zones_geojson=zones)
    assert result.provenance["arms"]["iter"]["iterations"] == [2] and result.provenance["arms"]["iter"]["last_iteration"] == 2
    with pytest.raises(ValueError, match="no priced stay"):   # iteration 0 is written before any replanning: no priced stay
        cmp.compare(arms, data_path=data, zones_geojson=zones, iterations=[0])
    write_outcomes(run / "ITERS" / "it.1" / "1.parking_outcomes.csv", ROWS)
    assert not any("eqasim_trips" in message for message in caplog.messages)
    with caplog.at_level("WARNING"):
        cmp.compare(arms, data_path=data, zones_geojson=zones, iterations=[1])
    assert any("not the last iteration" in message and "eqasim_trips" in message for message in caplog.messages)
    with pytest.raises(FileNotFoundError, match="7.parking_outcomes.csv"):
        cmp.compare(arms, data_path=data, zones_geojson=zones, iterations=[1, 7])


def test_a_range_of_iterations_is_pooled_cell_by_cell(cmp, data, tmp_path):
    # iteration 1 reports the ROWS, iteration 2 the same rows with every count doubled: the pooled count of a cell is 3x
    # its first count, the garage probability sum 3x, and every share of a ratio metric equals that of one iteration
    run = write_run(tmp_path / "runs", "pooled", ZONES_TRIPS, [(o, z, p, 2 * c, 2 * g, 2 * r) for o, z, p, c, g, r in ROWS])
    write_outcomes(run / "ITERS" / "it.1" / "1.parking_outcomes.csv", ROWS)
    zones = data / "braunschweig" / "parking" / "parking_zones_2026.geojson"
    arms = [cmp.ArmSpec("pooled", run, True)]
    result = cmp.compare(arms, data_path=data, zones_geojson=zones, iterations=[1, 2])
    table = result.table
    assert row(table, "pooled", "pricing_calls_total")["model"] == 3 * TOTAL_CALLS
    assert row(table, "pooled", "pricing_calls_ia_ib")["model"] == 3 * 215
    assert row(table, "pooled", "garage_share_ia_ib_other_purposes")["model"] == pytest.approx(21 / 110, abs=1e-6)
    assert row(table, "pooled", "paid_share_ia_ib_all_purposes")["model"] == pytest.approx(120 / 215, abs=1e-6)
    assert result.provenance["arms"]["pooled"]["iterations"] == [1, 2]
    # M2: the iteration list is in the note of every outcome row, with the pooling caveat
    note = row(table, "pooled", "paid_share_ia_ib_all_purposes")["universe_note"]
    assert "iterations 1-2" in note and "not independent" in note and "convergence, not validation" in note
    assert "pre-equilibrium" in note
    single = cmp.compare(arms, data_path=data, zones_geojson=zones, iterations=[2]).table
    single_note = row(single, "pooled", "paid_share_ia_ib_all_purposes")["universe_note"]
    assert "iteration 2" in single_note and "not independent" not in single_note
    assert len(result.provenance["arms"]["pooled"]["outcome_reports"]) == 2
    # the pooling function itself: shares are recomputed, a mix of versions is refused
    first, _ = cmp.read_parking_outcomes(run / "ITERS" / "it.1" / "1.parking_outcomes.csv")
    second, _ = cmp.read_parking_outcomes(run / "ITERS" / "it.2" / "2.parking_outcomes.csv")
    pooled, version = cmp.pool_outcome_reports([(first, 3), (second, 3)])
    assert version == 3 and pooled["share"].sum() == pytest.approx(1.0) and len(pooled) == len(first)
    assert pooled["count"].sum() == 3 * TOTAL_CALLS and pooled["garage_probability_sum"].sum() == pytest.approx(75.0)
    with pytest.raises(ValueError, match="different versions"):
        cmp.pool_outcome_reports([(first, 3), (second, 2)])


def test_the_iteration_option_parses_a_number_or_an_inclusive_range(cmp):
    import argparse
    assert cmp.parse_iterations("7") == [7] and cmp.parse_iterations("3-6") == [3, 4, 5, 6] and cmp.parse_iterations("4-4") == [4]
    for bad in ("", "a", "5-3", "-2", "1-2-3"):
        with pytest.raises(argparse.ArgumentTypeError):
            cmp.parse_iterations(bad)


# ------------------------------------------------------------------------------------------------------- deltas, files


def test_the_delta_table_lists_each_arm_against_every_earlier_arm_for_share_metrics(cmp, data, tmp_path):
    runs = tmp_path / "runs"
    off = write_run(runs, "off", OFF_TRIPS)
    legacy = write_run(runs, "legacy", OFF_TRIPS)
    zones = write_run(runs, "zones", ZONES_TRIPS, ROWS)
    arms = [cmp.ArmSpec("off", off, False), cmp.ArmSpec("legacy", legacy, False), cmp.ArmSpec("zones_v2", zones, True)]
    result = cmp.compare(arms, data_path=data, zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson")
    deltas = result.deltas
    pairs = deltas[["arm", "baseline_arm"]].drop_duplicates().apply(tuple, axis=1).tolist()
    assert pairs == [("legacy", "off"), ("zones_v2", "off"), ("zones_v2", "legacy")]
    found = deltas[(deltas["arm"] == "zones_v2") & (deltas["baseline_arm"] == "off")
                   & (deltas["metric"] == "car_trip_share_ending_in_ia_ib")].iloc[0]
    assert found["delta_pp"] == pytest.approx(100 * (4 / 6 - 5 / 6), abs=1e-3)
    assert found["model"] == pytest.approx(4 / 6, abs=1e-6) and found["baseline_model"] == pytest.approx(5 / 6, abs=1e-6)
    assert found["n_model"] == 6 and found["n_baseline"] == 6   # M3: the universe size of each side (trips ending in Ia, Ib)
    assert list(deltas.columns) == ["arm", "baseline_arm", "metric", "universe", "n_model", "n_baseline", "model",
                                    "baseline_model", "delta_pp"]
    assert set(deltas["metric"]) <= set(result.table.loc[result.table["unit"] == "share", "metric"])
    # the outcome metrics exist for the zones arm only: no delta against an arm without a value
    assert not deltas["metric"].str.startswith("paid_share").any()
    assert (deltas.loc[(deltas["arm"] == "legacy") & (deltas["baseline_arm"] == "off"), "delta_pp"] == 0).all()


def test_arm_labels_must_be_unique_text(cmp, data, two_arms):
    off, zones = two_arms
    zones_geojson = data / "braunschweig" / "parking" / "parking_zones_2026.geojson"
    with pytest.raises(ValueError, match="duplicate arm label"):
        cmp.compare([cmp.ArmSpec("a", off, False), cmp.ArmSpec("a", zones, True)], data_path=data, zones_geojson=zones_geojson)
    with pytest.raises(ValueError, match="arm label"):
        cmp.compare([cmp.ArmSpec("a b", off, False)], data_path=data, zones_geojson=zones_geojson)
    with pytest.raises(ValueError, match="at least one arm"):
        cmp.compare([], data_path=data, zones_geojson=zones_geojson)


def test_a_trips_file_with_a_missing_column_or_nothing_inside_a_zone_fails(cmp, data, tmp_path):
    zones_geojson = data / "braunschweig" / "parking" / "parking_zones_2026.geojson"
    run = tmp_path / "runs" / "bad"
    run.mkdir(parents=True)
    pd.DataFrame({"person_id": [1], "mode": ["car"]}).to_csv(run / "eqasim_trips.csv", sep=";", index=False)
    with pytest.raises(ValueError, match="destination_x"):
        cmp.compare([cmp.ArmSpec("bad", run, False)], data_path=data, zones_geojson=zones_geojson)
    far = write_run(tmp_path / "runs", "far", [("car", 1.0, 1.0)])
    with pytest.raises(ValueError, match="not one of the"):   # the guard of attach_parking_zones: broken join or CRS
        cmp.compare([cmp.ArmSpec("far", far, False)], data_path=data, zones_geojson=zones_geojson)
    with pytest.raises(FileNotFoundError, match="eqasim_trips"):
        cmp.compare([cmp.ArmSpec("none", tmp_path, False)], data_path=data, zones_geojson=zones_geojson)


def test_the_main_names_the_data_record_and_the_request_route_when_the_zone_polygons_are_absent(cmp, data, tmp_path):
    # the zone polygons are a local restricted file that is not in the repository (issue #436)
    off = write_run(tmp_path / "runs", "off", OFF_TRIPS)
    (data / "braunschweig" / "parking" / "parking_zones_2026.geojson").unlink()
    argv = ["--arm-without-outcomes", f"off={off}", "--data-path", str(data), "--out", str(tmp_path / "out" / "x.csv")]
    with pytest.raises(SystemExit) as error:
        cmp.main(argv)
    message = str(error.value)
    assert "zone polygons does not exist" in message and "docs/registry/data/parking_zones_2026.yml" in message
    assert "not distributed in the repository" in message and "available on request" in message
    assert "TUBS-IVS/eqasim-bs" in message
    assert not (tmp_path / "out").exists()


def test_the_main_writes_the_tables_and_the_provenance_deterministically(cmp, data, tmp_path):
    off = write_run(tmp_path / "runs", "off", OFF_TRIPS)
    zones = write_run(tmp_path / "runs", "zones", ZONES_TRIPS, ROWS)
    out = tmp_path / "out" / "parking_targets_comparison_test.csv"
    argv = ["--arm-without-outcomes", f"off={off}", "--arm", f"zones_v2={zones}", "--data-path", str(data),
            "--zones-geojson", str(data / "braunschweig" / "parking" / "parking_zones_2026.geojson"), "--out", str(out)]
    assert cmp.main(argv) == 0
    delta_path = out.with_name(out.stem + "_arm_deltas.csv")
    provenance_path = out.with_name(out.stem + "_provenance.json")
    first = (out.read_bytes(), delta_path.read_bytes())
    table = pd.read_csv(out, keep_default_na=False)
    assert list(table.columns) == ["arm", "metric", "unit", "universe", "universe_size", "model", "reference", "delta_pp",
                                   "reference_source", "universe_note"]
    assert table["arm"].drop_duplicates().tolist() == ["off", "zones_v2"]   # arm order as given, not alphabetical
    text = first[0].decode("ascii")
    assert "\r" not in text and all(len(line) > 0 for line in text.splitlines())
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["arms"]["zones_v2"]["outcome_report_version"] == 3 and provenance["arms"]["off"]["outcome_reports"] is None
    assert len(provenance["arms"]["zones_v2"]["outcome_reports"][0]["sha256"]) == 64 and "code_state" in provenance
    assert set(provenance["inputs"]) >= {"srv2023_city_center_parking", "srv2023_commute_parking_by_workplace_class", "zones",
                                         "tariffs"}
    # a second run refuses to overwrite, and with --overwrite writes the same bytes
    with pytest.raises(SystemExit, match="--overwrite"):
        cmp.main(argv)
    assert cmp.main(argv + ["--overwrite"]) == 0
    assert (out.read_bytes(), delta_path.read_bytes()) == first


def test_a_missing_input_is_refused_before_anything_is_written(cmp, data, tmp_path):
    out = tmp_path / "out" / "x.csv"
    with pytest.raises(SystemExit, match="not found"):
        cmp.main(["--arm", f"a={tmp_path / 'nowhere'}", "--data-path", str(data), "--out", str(out)])
    assert not out.parent.exists() or not any(out.parent.iterdir())


def test_a_report_without_any_garage_figure_is_flagged_on_the_garage_rows_only(cmp, data, tmp_path, caplog):
    # L2: a v3 report whose garage columns are 0 in every row (options off, or no garage in range anywhere)
    zero_rows = [(o, z, p, c, 0.0, 0) for o, z, p, c, _, _ in ROWS]
    run = write_run(tmp_path / "runs", "nogarage", ZONES_TRIPS, zero_rows)
    with caplog.at_level("WARNING"):
        table = cmp.compare([cmp.ArmSpec("nogarage", run, True)], data_path=data,
                            zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson").table
    expected = "no garage option acted in this run (options off or no garage in range)"
    assert expected in row(table, "nogarage", "garage_share_ia_ib_other_purposes")["universe_note"]
    assert expected in row(table, "nogarage", "garage_share_ia_ib_work_education_all_calls")["universe_note"]
    assert expected not in row(table, "nogarage", "paid_share_ia_ib_all_purposes")["universe_note"]
    assert row(table, "nogarage", "garage_share_ia_ib_other_purposes")["model"] == 0.0   # a measured 0, with the flag
    assert any("no garage option acted" in message for message in caplog.messages)
    # the zones arm of the other tests has garage figures: no flag
    ordinary = write_run(tmp_path / "runs", "garage", ZONES_TRIPS, ROWS)
    table = cmp.compare([cmp.ArmSpec("garage", ordinary, True)], data_path=data,
                        zones_geojson=data / "braunschweig" / "parking" / "parking_zones_2026.geojson").table
    assert expected not in row(table, "garage", "garage_share_ia_ib_other_purposes")["universe_note"]


def test_the_free_share_notes_name_why_the_model_share_is_lower_by_construction(cmp, data, two_arms):
    # L4
    table = compare_two(cmp, data, *two_arms).table
    note = row(table, "zones_v2", "free_share_work_education_class_bs_zentrum")["universe_note"]
    for needle in ("zero cents", "outside every zone", "free by assumption Z1", "whole Oberbezirk",
                   "wider than the zones Ia and Ib", "negative difference is expected by construction"):
        assert needle in note, needle
    employer = row(table, "zones_v2", "employer_free_share_work_education_class_bs_zentrum")["universe_note"]
    # the draw targets the class share of persons, so a negative difference is NOT expected here (it signals a broken draw)
    assert "negative difference is expected" not in employer and "a difference near zero is the mechanism working" in employer
    assert "whole Oberbezirk" in employer and "FIRST paid-zone" in employer


#: The literal reports of the Java ``ParkingOutcomeReportListenerTest`` (eqasim-java-bs, ``braunschweig/src/test/java/org/eqasim/
#: braunschweig/parking/``), copied line by line: the writer's own expectations are the reader's fixture, so a change of the
#: Java format is caught when this copy is updated together with the Java writer (L5); the copy is not read from the Java repo.
JAVA_V3_HEADER = "outcome,zone_id,purpose,count,share,garage_probability_sum,stays_with_garages_in_range"
JAVA_REPORTS = {
    "it3_no_garage_options": [
        JAVA_V3_HEADER,
        "NO_ZONE,,shop,2,0.250000,0.000000,0",
        "NO_ZONE,,work,3,0.375000,0.000000,0",
        "EMPLOYER_FREE,fx_bs_ib,work,1,0.125000,0.000000,0",
        "PAID_METERED,fx_bs_ia,shop,1,0.125000,0.000000,0",
        "PAID_METERED,fx_bs_ib,work,1,0.125000,0.000000,0"],
    "it1_v2_outcomes": [
        JAVA_V3_HEADER,
        "PAID_CAMPUS_GUEST,fx_campus_v2,shop,1,0.250000,0.000000,0",
        "PAID_GARAGE,fx_wob_v2,shop,2,0.500000,0.000000,0",
        "PAID_COMMUTER,fx_bs_ib_v2,work,1,0.250000,0.000000,0"],
    "it4_garage_options": [
        JAVA_V3_HEADER,
        "NO_ZONE,,work,1,0.200000,0.000000,0",
        "FREE_WITHIN_LIMIT,fx_bs_ib,shop,1,0.200000,0.000000,1",
        "PAID_METERED,fx_bs_ib,shop,1,0.200000,0.000000,0",
        "PAID_EXPECTED,fx_bs_ib,shop,2,0.400000,0.750000,2"],
    "it2_quoted_zone_id": [JAVA_V3_HEADER, 'PAID_METERED,"fx,zone",work,1,1.000000,0.000000,0'],
}


@pytest.mark.parametrize("name", sorted(JAVA_REPORTS))
def test_the_reader_parses_the_literal_reports_of_the_java_listener_test(cmp, tmp_path, name):
    lines = JAVA_REPORTS[name]
    path = tmp_path / f"{name}.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    frame, version = cmp.read_parking_outcomes(path)
    assert version == 3 and len(frame) == len(lines) - 1
    assert lines[0] == ",".join(cmp.REPORT_V3_COLUMNS)   # the Java header is the reader's v3 header, in order
    if name == "it3_no_garage_options":
        assert frame["count"].tolist() == [2, 3, 1, 1, 1] and frame.loc[frame["outcome"] == "NO_ZONE", "zone_id"].eq("").all()
    if name == "it4_garage_options":
        expected = frame[frame["outcome"] == "PAID_EXPECTED"].iloc[0]
        assert (expected["count"], expected["garage_probability_sum"], expected["stays_with_garages_in_range"]) == (2, 0.75, 2)
    if name == "it2_quoted_zone_id":
        assert frame["zone_id"].tolist() == ["fx,zone"]
