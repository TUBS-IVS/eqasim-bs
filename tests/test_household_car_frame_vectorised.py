"""Output identity of the vectorised ``build_household_car_frame`` (audit H).

The per-household groupby loop that ``build_household_car_frame`` used before
the vectorised rewrite is kept below VERBATIM (function body and its two owner
helpers, renamed with a ``_reference`` prefix) as the reference implementation.
Randomised inputs with a fixed seed must produce a frame identical to the
reference in values, column order, dtypes, index and row order, and the same
warning log lines; error cases must raise the same exception and message.
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import pytest

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.synthesis.vehicles.cars import household as household_module
from braunschweig.synthesis.vehicles.cars.household import (
    ADULT_AGE,
    _kreis_ags5_from_ars,
)

# The reference logs under the production logger name so caplog records compare 1:1.
logger = logging.getLogger(household_module.__name__)


# --------------------------------------------------------------------------- #
# Reference implementation: the pre-vectorisation code, copied verbatim.
# --------------------------------------------------------------------------- #
def _reference_build_household_car_frame(df_persons: pd.DataFrame, df_homes: pd.DataFrame,
                                         df_regiostar: pd.DataFrame, *,
                                         indexed_home_lookup: bool = True) -> pd.DataFrame:
    required = {"household_id", "person_id", "age", "has_license",
                "number_of_cars", "economic_status"}
    missing = required - set(df_persons.columns)
    if missing:
        raise ValueError(
            "build_household_car_frame: df_persons is missing required columns "
            f"{sorted(missing)}"
        )

    # Home commune (12-digit ARS) per household -> Kreis AGS-5 + AGS-8 join key.
    homes = df_homes[["household_id", "commune_id"]].drop_duplicates("household_id").copy()
    homes["commune_id"] = homes["commune_id"].astype(str)
    homes["kreis_ags5"] = homes["commune_id"].map(_kreis_ags5_from_ars)
    homes["ags8"] = homes["commune_id"].map(ars_to_ags8)

    # RegioStaR reference -> Gemeinde name (upper-cased, FZ 27.17 key) + RS7.
    rs = df_regiostar[["commune_id", "name", "regiostar7"]].copy()
    rs["commune_id"] = rs["commune_id"].astype(str)
    rs = rs.rename(columns={"commune_id": "ags8", "name": "gemeinde",
                            "regiostar7": "raumtyp"})
    # A missing name stays missing (``None``): ``astype(str)`` would turn it into the
    # fake label "NAN" (Langelsheim, prod100_20261031). The fleet's Gemeinde tilt
    # counts a missing label as a Gemeinde fallback (``normalize_gemeinde_name``
    # maps it to ""); the count of such cars is logged below.
    has_name = rs["gemeinde"].notna()
    rs["gemeinde"] = rs["gemeinde"].astype(object).where(has_name, None)
    rs.loc[has_name, "gemeinde"] = (
        rs.loc[has_name, "gemeinde"].astype(str).str.strip().str.upper())
    homes = homes.merge(rs[["ags8", "gemeinde", "raumtyp"]], on="ags8", how="left")
    home_rows_by_household = None
    if indexed_home_lookup:
        # ``merge`` may duplicate a home when RegioStaR has duplicate AGS rows.
        # Keep the first row so the indexed path exactly matches the historical
        # ``homes[homes["household_id"] == household_id].iloc[0]`` behaviour.
        home_rows_by_household = (
            homes.drop_duplicates("household_id", keep="first")
            .set_index("household_id", drop=False)
        )

    # One car-need-ordered record list per household.
    rows: list[dict] = []
    n_zero_licensed_households = 0
    n_zero_licensed_cars = 0

    # Group once; iterate households deterministically by household_id.
    for household_id, group in df_persons.groupby("household_id", sort=True):
        number_of_cars = int(group["number_of_cars"].max())
        if number_of_cars <= 0:
            continue

        if indexed_home_lookup:
            try:
                home_row = home_rows_by_household.loc[household_id]
            except KeyError:
                raise ValueError(
                    f"build_household_car_frame: no home commune for household "
                    f"{household_id}"
                ) from None
        else:
            home = homes[homes["household_id"] == household_id]
            if home.empty:
                raise ValueError(
                    f"build_household_car_frame: no home commune for household "
                    f"{household_id}"
                )
            home_row = home.iloc[0]
        economic_status = str(group["economic_status"].iloc[0])

        owners = _reference_assign_owners(group, number_of_cars)
        if owners is None:
            # No licensed adult: assign to the oldest adult (logged fallback).
            n_zero_licensed_households += 1
            n_zero_licensed_cars += number_of_cars
            owners = _reference_fallback_owners(group, number_of_cars)

        age_by_person = dict(zip(group["person_id"], group["age"]))
        for owner_id in owners:
            rows.append({
                "household_id": household_id,
                "owner_id": owner_id,
                "economic_status": economic_status,
                "kreis_ags5": home_row["kreis_ags5"],
                "gemeinde": home_row["gemeinde"],
                "raumtyp": home_row["raumtyp"],
                # Issue #315: the assigned owner's age feeds the wohnmobile
                # holder-age tilt (input-only column, dropped before the writer).
                "owner_age": float(age_by_person[owner_id]),
            })

    if n_zero_licensed_households:
        logger.warning(
            "[vehicles.household] %d household(s) with cars but no licensed "
            "adult (%d car(s)): assigned to the oldest adult as a fallback.",
            n_zero_licensed_households, n_zero_licensed_cars,
        )

    df_cars = pd.DataFrame.from_records(
        rows,
        columns=["household_id", "owner_id", "economic_status",
                 "kreis_ags5", "gemeinde", "raumtyp", "owner_age"],
    )
    # Fallback transparency: cars whose home Gemeinde has no name (home code absent
    # from the RegioStaR reference, or a reference row without a name) get no
    # Gemeinde tilt. Named here because a merger missing from the reference sheet
    # surfaced only as a fake label before (ADR-0180).
    no_name = df_cars["gemeinde"].isna()
    if no_name.any():
        logger.warning(
            "[vehicles.household] %d car(s) in %d household(s) have no Gemeinde "
            "name (%.2f%% of %d cars; home AGS-8 codes: %s): Gemeinde tilt falls "
            "back to the Kreis share.",
            int(no_name.sum()), int(df_cars.loc[no_name, "household_id"].nunique()),
            100.0 * float(no_name.mean()), len(df_cars),
            sorted(homes.loc[homes["household_id"].isin(
                df_cars.loc[no_name, "household_id"]), "ags8"].unique().tolist()),
        )
    return df_cars


def _reference_assign_owners(group: pd.DataFrame, number_of_cars: int):
    licensed = group[group["has_license"].fillna(False) & (group["age"] >= ADULT_AGE)]
    if licensed.empty:
        return None
    ordered = licensed.sort_values(
        ["age", "person_id"], ascending=[False, True])["person_id"].tolist()
    owners = [ordered[i % len(ordered)] for i in range(number_of_cars)]
    return owners


def _reference_fallback_owners(group: pd.DataFrame, number_of_cars: int) -> list:
    adults = group[group["age"] >= ADULT_AGE]
    pool = adults if not adults.empty else group
    oldest = pool.sort_values(
        ["age", "person_id"], ascending=[False, True])["person_id"].iloc[0]
    return [oldest] * number_of_cars


# --------------------------------------------------------------------------- #
# Randomised inputs
# --------------------------------------------------------------------------- #
_COMMUNES = [
    # (12-digit ARS, 8-digit AGS) pairs; the last one is absent from RegioStaR.
    ("031010000000", "03101000"),
    ("031020000000", "03102000"),
    ("031530000000", "03153000"),
    ("031540000000", "03154000"),
    ("031550000000", "03155000"),
]


def _regiostar() -> pd.DataFrame:
    """RegioStaR rows: one duplicate AGS (first row wins) and one missing name."""
    return pd.DataFrame.from_records([
        {"commune_id": "03101000", "name": " Braunschweig, Stadt ", "regiostar7": 71},
        {"commune_id": "03102000", "name": "Salzgitter first", "regiostar7": 72},
        {"commune_id": "03102000", "name": "Salzgitter duplicate", "regiostar7": 77},
        {"commune_id": "03153000", "name": "Goslar", "regiostar7": 73},
        {"commune_id": "03154000", "name": None, "regiostar7": 74},
    ])


def _random_population(n_households: int, seed: int, *, string_ids: bool = False):
    """Persons + homes with every edge case the owner rule distinguishes.

    Ages come from a narrow range (many ties), ``has_license`` is object-typed with
    NaN, minors may be licensed, households may lack adults, car counts exceed the
    licensed adults, 0-car households occur, and person rows are shuffled so a
    household's rows are not contiguous. ``economic_status`` varies within a
    household so "first row in input order" is tested.
    """
    rng = np.random.default_rng(seed)
    records = []
    for household in range(n_households):
        household_id = int(rng.integers(0, 10 * n_households + 10)) * 7 + household % 7
        size = int(rng.integers(1, 6))
        no_adults = rng.random() < 0.1
        cars = int(rng.choice([0, 0, 1, 1, 2, 3, 3, 4]))
        for member in range(size):
            age = int(rng.integers(5, 17)) if no_adults else int(rng.choice(
                [12, 16, 17, 18, 18, 30, 30, 45, 45, 70]))
            license_draw = rng.random()
            has_license = True if license_draw < 0.55 else (
                False if license_draw < 0.85 else np.nan)
            records.append({
                "household_id": household_id,
                "person_id": int(rng.integers(0, 50 * n_households + 50)),
                "age": age,
                "has_license": has_license,
                "number_of_cars": cars if rng.random() < 0.8 else max(cars - 1, 0),
                "economic_status": str(rng.choice(["low", "medium", "high"])),
            })
    persons = pd.DataFrame.from_records(records)
    # Same household drawn twice merges two record blocks: keeps the loop honest
    # about the per-household max of number_of_cars and economic_status order.
    persons = persons.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    persons["has_license"] = persons["has_license"].astype(object)
    if string_ids:
        persons["household_id"] = "hh" + persons["household_id"].astype(str)
        persons["person_id"] = "p" + persons["person_id"].astype(str)
    household_ids = persons["household_id"].drop_duplicates().tolist()
    commune_index = rng.integers(0, len(_COMMUNES), size=len(household_ids))
    homes = pd.DataFrame({
        "household_id": household_ids,
        "commune_id": [_COMMUNES[i][0] for i in commune_index],
    })
    # A duplicate home row for some households: the first one must win.
    duplicate = homes.sample(frac=0.1, random_state=seed).assign(commune_id="031010000000")
    homes = pd.concat([homes, duplicate], ignore_index=True)
    return persons, homes


def _run_both(persons, homes, regiostar, caplog, *, indexed_home_lookup):
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=household_module.__name__):
        expected = _reference_build_household_car_frame(
            persons, homes, regiostar, indexed_home_lookup=indexed_home_lookup)
    expected_logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=household_module.__name__):
        actual = household_module.build_household_car_frame(
            persons, homes, regiostar, indexed_home_lookup=indexed_home_lookup)
    actual_logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return expected, actual, expected_logs, actual_logs


def _assert_identical(expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    assert list(actual.columns) == list(expected.columns)
    assert actual.dtypes.to_dict() == expected.dtypes.to_dict()
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    # Element types too: assert_frame_equal treats e.g. str "1" vs int 1 as different,
    # but this also pins scalar types inside object columns.
    for column in expected.columns:
        assert [type(v) for v in actual[column]] == [type(v) for v in expected[column]]


@pytest.mark.parametrize("indexed_home_lookup", [True, False])
@pytest.mark.parametrize("n_households,seed", [(1, 1), (7, 2), (60, 3), (400, 4)])
def test_vectorised_frame_matches_reference(n_households, seed, indexed_home_lookup,
                                            caplog):
    persons, homes = _random_population(n_households, seed)
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog, indexed_home_lookup=indexed_home_lookup)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


def test_vectorised_frame_matches_reference_with_string_ids(caplog):
    persons, homes = _random_population(150, 11, string_ids=True)
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog, indexed_home_lookup=True)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


def test_random_fixture_exercises_every_edge_case(caplog):
    """Positive control: the identity test above really meets each branch."""
    persons, homes = _random_population(400, 4)
    licensed = persons["has_license"].fillna(False).astype(bool)
    cars = persons.groupby("household_id")["number_of_cars"].max()
    assert persons["has_license"].isna().any()
    assert (licensed & (persons["age"] < ADULT_AGE)).any()  # licensed minors
    assert (persons.groupby("household_id")["age"].max() < ADULT_AGE).any()
    assert (cars == 0).any()
    n_licensed_adults = (licensed & (persons["age"] >= ADULT_AGE)).groupby(
        persons["household_id"]).sum()
    assert ((cars > n_licensed_adults) & (n_licensed_adults > 0)).any()
    # Non-contiguous household rows.
    assert (persons["household_id"] != persons["household_id"].shift()).sum() > \
        persons["household_id"].nunique()
    # Age ties among licensed adults of one household.
    adults = persons[licensed & (persons["age"] >= ADULT_AGE)]
    assert adults.duplicated(["household_id", "age"]).any()
    _, actual, _, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog, indexed_home_lookup=True)
    messages = [message for _, _, message in actual_logs]
    assert any("no licensed adult" in m for m in messages)
    assert any("no Gemeinde name" in m for m in messages)
    assert actual["gemeinde"].isna().any() and actual["gemeinde"].notna().any()


def test_duplicate_person_id_within_household_uses_last_age(caplog):
    """The former dict(zip(person_id, age)) took the LAST row's age for a repeated id."""
    persons = pd.DataFrame.from_records([
        {"household_id": 1, "person_id": 5, "age": 40, "has_license": True,
         "number_of_cars": 2, "economic_status": "low"},
        {"household_id": 1, "person_id": 5, "age": 33, "has_license": False,
         "number_of_cars": 2, "economic_status": "high"},
    ])
    homes = pd.DataFrame({"household_id": [1], "commune_id": ["031010000000"]})
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog, indexed_home_lookup=True)
    _assert_identical(expected, actual)
    assert actual["owner_age"].tolist() == [33.0, 33.0]
    assert actual_logs == expected_logs


def test_no_cars_gives_identical_empty_frame(caplog):
    persons, homes = _random_population(30, 5)
    persons["number_of_cars"] = 0
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog, indexed_home_lookup=True)
    assert len(actual) == 0
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs == []


def test_integer_ids_in_object_column_infer_like_reference(caplog):
    """Only car households' ids decide the dtype (object input, int64 output)."""
    persons = pd.DataFrame.from_records([
        {"household_id": 1, "person_id": 11, "age": 44, "has_license": True,
         "number_of_cars": 1, "economic_status": "low"},
        {"household_id": "zero", "person_id": 21, "age": 50, "has_license": True,
         "number_of_cars": 0, "economic_status": "high"},
    ])
    homes = pd.DataFrame({"household_id": [1, "zero"],
                          "commune_id": ["031010000000", "031550000000"]})
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog, indexed_home_lookup=True)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


@pytest.mark.parametrize("indexed_home_lookup", [True, False])
def test_missing_home_raises_same_error(indexed_home_lookup):
    persons, homes = _random_population(80, 6)
    cars = persons.groupby("household_id", sort=True)["number_of_cars"].max()
    car_households = cars[cars > 0].index
    # Drop the homes of the 2nd and last car household: the first in id order is named.
    dropped = [car_households[1], car_households[-1]]
    homes = homes[~homes["household_id"].isin(dropped)]
    with pytest.raises(ValueError) as expected:
        _reference_build_household_car_frame(
            persons, homes, _regiostar(), indexed_home_lookup=indexed_home_lookup)
    with pytest.raises(ValueError) as actual:
        household_module.build_household_car_frame(
            persons, homes, _regiostar(), indexed_home_lookup=indexed_home_lookup)
    assert str(actual.value) == str(expected.value)
    assert str(car_households[1]) in str(actual.value)


def test_nan_car_count_raises_same_error():
    persons, homes = _random_population(20, 7)
    persons["number_of_cars"] = persons["number_of_cars"].astype(float)
    persons.loc[persons["household_id"] == persons["household_id"].max(),
                "number_of_cars"] = np.nan
    with pytest.raises(ValueError) as expected:
        _reference_build_household_car_frame(persons, homes, _regiostar())
    with pytest.raises(ValueError) as actual:
        household_module.build_household_car_frame(persons, homes, _regiostar())
    assert str(actual.value) == str(expected.value)


def test_timing_against_reference(capsys):
    """Report old vs new seconds on a mid-size input; no assertion on absolute time.

    The production-size check (about 300k persons) is reported in the audit-H
    hand-back (the reference loop needs minutes there); here about 3k persons keep
    the test near five seconds while still printing both
    timings and proving identity at scale.
    """
    persons, homes = _random_population(1_000, 99)
    regiostar = _regiostar()
    start = time.perf_counter()
    expected = _reference_build_household_car_frame(persons, homes, regiostar)
    reference_seconds = time.perf_counter() - start
    start = time.perf_counter()
    actual = household_module.build_household_car_frame(persons, homes, regiostar)
    vectorised_seconds = time.perf_counter() - start
    _assert_identical(expected, actual)
    with capsys.disabled():
        print(f"\n[timing] build_household_car_frame on {len(persons)} persons: "
              f"reference {reference_seconds:.2f} s, vectorised {vectorised_seconds:.2f} s")
