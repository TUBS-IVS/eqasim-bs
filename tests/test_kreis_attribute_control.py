import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    KreisAttributeControl, REGISTRY, control_columns, attribute_kreis_count_table,
)


def _econ_entry():
    return next(c for c in REGISTRY if c.name == "economic_status")


def test_registry_has_economic_status_only():
    # Superseded by test_registry_has_four_entries_with_expected_tiers (Task 2 extends the
    # REGISTRY with number_of_cars/number_of_bicycles/has_ebike); economic_status itself is
    # unchanged and must still be the first entry.
    assert [c.name for c in REGISTRY][0] == "economic_status"


def test_control_columns_follow_name_category():
    c = _econ_entry()
    assert control_columns(c) == tuple(
        f"economic_status_{k}" for k in ("very_low", "low", "medium", "high", "very_high"))


def test_count_table_rows_sum_to_hh_total_integer():
    c = _econ_entry()
    tgt = pd.DataFrame([
        {"ars5": "03ZGB", "very_low": 9, "low": 12, "medium": 31, "high": 36, "very_high": 12},
        {"ars5": "03102", "very_low": 5, "low": 10, "medium": 29, "high": 42, "very_high": 13},
    ])
    out = attribute_kreis_count_table(c, tgt, {"03102": 50000.4}, prior_n=0.0)
    cols = list(control_columns(c))
    assert list(out.columns) == ["ARS_kreis", *cols]
    row = out[out.ARS_kreis == "03102"][cols].to_numpy().ravel()
    assert row.sum() == 50000 and (row == np.floor(row)).all()


def test_count_table_missing_kreis_raises():
    c = _econ_entry()
    tgt = pd.DataFrame([{"ars5": "03ZGB", "very_low": 9, "low": 12, "medium": 31, "high": 36, "very_high": 12}])
    with pytest.raises(ValueError):
        attribute_kreis_count_table(c, tgt, {"09999": 100.0}, prior_n=0.0)


# --- Task 2: generic catalog factory (economic_status via generic == the L1 controls) ---
from braunschweig.popsim import control_spec as cs  # noqa: E402


def test_generic_factory_reproduces_L1_economic_status_controls():
    econ = [c for c in REGISTRY if c.name == "economic_status"]
    generic = cs.attribute_kreis_controls(econ)
    assert [c.name for c in generic] == list(control_columns(econ[0]))
    for c in generic:
        assert c.geography == cs.GEO_KREIS and c.seed_table == cs.SEED_TABLE_HOUSEHOLDS
        assert c.census_source == (c.name,) and c.seed_expressions["entd"] is None
    exprs = {c.name: c.seed_expressions["mid"] for c in generic}
    assert exprs["economic_status_very_low"] == "(households.oek_status == 1)"
    assert exprs["economic_status_very_high"] == "(households.oek_status == 5)"


def test_status_kreis_controls_still_returns_five_identical():
    # The L1 public factory now delegates to the generic one; output must be unchanged.
    s = cs.status_kreis_controls()
    assert [c.name for c in s] == list(control_columns(_econ_entry()))
    assert all(c.geography == cs.GEO_KREIS and c.seed_table == cs.SEED_TABLE_HOUSEHOLDS for c in s)


# --- Task 1: generic per-Kreis target loader ---
from braunschweig.popsim.kreis_attribute_control import load_kreis_target  # noqa: E402


def _write_target_csv(tmp_path: Path) -> Path:
    root = tmp_path / "braunschweig" / "targets"
    root.mkdir(parents=True)
    p = root / "target2026_toy_by_kreis.csv"
    p.write_text(
        "# comment line one\n"
        "# CONSUMER NOTE: FINAL target - use with prior_n = 0.\n"
        "ars5,source,n_effective,a,b\n"
        "Gesamt,mid,0,0.6,0.4\n"
        "03101,blend,1000,0.7,0.3\n",
        encoding="utf-8",
    )
    return p


_TOY = KreisAttributeControl(
    name="toy", seed_column="toy_col", level="household",
    categories=(("a", "== 0"), ("b", ">= 1")),
    target_csv_relpath="braunschweig/targets/target2026_toy_by_kreis.csv",
    target_columns=("a", "b"), tier="soft",
)


def test_load_kreis_target_drops_comments_and_meta_columns(tmp_path):
    _write_target_csv(tmp_path)
    df = load_kreis_target(tmp_path, _TOY)
    assert list(df.columns) == ["ars5", "a", "b"]
    assert set(df["ars5"]) == {"Gesamt", "03101"}
    row = df[df["ars5"] == "03101"].iloc[0]
    assert row["a"] == pytest.approx(0.7)


def test_load_kreis_target_requires_aggregate_row(tmp_path):
    p = _write_target_csv(tmp_path)
    # strip the Gesamt row
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if not l.startswith("Gesamt")]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="aggregate row"):
        load_kreis_target(tmp_path, _TOY)


def test_load_kreis_target_fails_on_missing_expected_kreis(tmp_path):
    _write_target_csv(tmp_path)
    with pytest.raises(ValueError, match="missing Kreis"):
        load_kreis_target(tmp_path, _TOY, expected_ars5=("03101", "03102"))


def test_load_kreis_target_fails_when_shares_do_not_sum_to_one(tmp_path):
    p = _write_target_csv(tmp_path)
    p.write_text(p.read_text(encoding="utf-8").replace("0.7,0.3", "0.7,0.9"), encoding="utf-8")
    with pytest.raises(ValueError, match="sum to 1"):
        load_kreis_target(tmp_path, _TOY)


def test_load_kreis_target_fails_on_missing_file(tmp_path):
    # No CSV written under tmp_path: the relpath resolves to a nonexistent file.
    with pytest.raises(FileNotFoundError):
        load_kreis_target(tmp_path, _TOY)


def test_load_kreis_target_fails_on_missing_target_column(tmp_path):
    p = _write_target_csv(tmp_path)
    # drop the "b" target column from header and rows
    p.write_text(
        "# comment line one\n"
        "# CONSUMER NOTE: FINAL target - use with prior_n = 0.\n"
        "ars5,source,n_effective,a\n"
        "Gesamt,mid,0,0.6\n"
        "03101,blend,1000,0.7\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing columns"):
        load_kreis_target(tmp_path, _TOY)


# --- Task 2: cars (hard) + bicycles/has_ebike (soft) registry entries ---


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


def test_registry_has_four_entries_with_expected_tiers():
    # Superseded by test_registry_has_five_entries_with_expected_tiers (Task 1 of the
    # 2026-07-08 trip-class-kreis-control plan adds the person-level trip_class entry);
    # kept (renamed in spirit, not name) so the historical four remain covered too.
    by_name = {c.name: c for c in REGISTRY}
    assert {"economic_status", "number_of_cars", "number_of_bicycles", "has_ebike"} <= set(by_name)
    assert by_name["number_of_cars"].tier == "hard"
    assert by_name["number_of_bicycles"].tier == "soft"
    assert by_name["has_ebike"].tier == "soft"


def test_registry_has_five_entries_with_expected_tiers():
    by_name = {c.name: c for c in REGISTRY}
    assert set(by_name) == {
        "economic_status", "number_of_cars", "number_of_bicycles", "has_ebike", "trip_class",
    }
    assert by_name["trip_class"].tier == "soft"
    assert by_name["trip_class"].level == "person"
    assert by_name["trip_class"].seed_column == "trip_class"


def test_cars_control_columns_and_predicates():
    cars = _entry("number_of_cars")
    assert control_columns(cars) == (
        "number_of_cars_0", "number_of_cars_1", "number_of_cars_2", "number_of_cars_3plus")
    preds = [p for _, p in cars.categories]
    assert preds == ["== 0", "== 1", "== 2", ">= 3"]
    assert cars.target_columns == ("cars_0", "cars_1", "cars_2", "cars_3plus")
    assert cars.seed_column == "number_of_cars"
    assert cars.level == "household"


def test_bicycles_and_ebike_shapes():
    bikes = _entry("number_of_bicycles")
    assert control_columns(bikes) == (
        "number_of_bicycles_0", "number_of_bicycles_1", "number_of_bicycles_2",
        "number_of_bicycles_3", "number_of_bicycles_4plus")
    assert bikes.target_columns == ("bikes_0", "bikes_1", "bikes_2", "bikes_3", "bikes_4plus")
    ebike = _entry("has_ebike")
    assert control_columns(ebike) == ("has_ebike_yes", "has_ebike_no")
    assert ebike.target_columns == ("ebike_yes", "ebike_no")
    assert [p for _, p in ebike.categories] == ["== 1", "== 0"]


def test_cars_count_table_partitions_household_total():
    cars = _entry("number_of_cars")
    tgt = pd.DataFrame({
        "ars5": ["Gesamt", "03101"],
        "cars_0": [0.2, 0.25], "cars_1": [0.5, 0.5],
        "cars_2": [0.2, 0.2], "cars_3plus": [0.1, 0.05],
    })
    tbl = attribute_kreis_count_table(cars, tgt, {"03101": 1000}, prior_n=0.0)
    cols = list(control_columns(cars))
    assert int(tbl[cols].sum(axis=1).iloc[0]) == 1000


# --- Task 1 (2026-07-08 plan): trip_class, the first PERSON-level registry entry ---


def test_trip_class_control_columns_and_predicates():
    trip_class = _entry("trip_class")
    assert control_columns(trip_class) == (
        "trip_class_0", "trip_class_1_2", "trip_class_3_4", "trip_class_5plus")
    preds = [p for _, p in trip_class.categories]
    assert preds == ["== 0", "== 1", "== 2", "== 3"]
    assert trip_class.target_columns == ("trips_0", "trips_1_2", "trips_3_4", "trips_5plus")
    assert trip_class.target_csv_relpath == "braunschweig/targets/target2026_trip_class_by_kreis.csv"


def test_trip_class_count_table_partitions_a_person_total():
    # Person-level entries partition a PERSON total, not a household total; the
    # count-table machinery is level-agnostic (the mapping key is just "the totals to
    # partition"), so this proves the generic helper already supports the new level.
    trip_class = _entry("trip_class")
    tgt = pd.DataFrame({
        "ars5": ["Gesamt", "03101"],
        "trips_0": [0.11, 0.10], "trips_1_2": [0.34, 0.31],
        "trips_3_4": [0.32, 0.34], "trips_5plus": [0.23, 0.25],
    })
    person_total_by_kreis = {"03101": 20000}  # a PERSON total, unlike the household totals above
    tbl = attribute_kreis_count_table(trip_class, tgt, person_total_by_kreis, prior_n=0.0)
    cols = list(control_columns(trip_class))
    row = tbl[tbl.ARS_kreis == "03101"][cols].to_numpy().ravel()
    assert row.sum() == 20000 and (row == np.floor(row)).all()
