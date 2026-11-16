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
import re

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
    household so "first row in input order" is tested. Some ages are NaN (sorted
    last), car counts include non-integer and negative floats (``int()``
    truncation toward zero), and one person row has no household_id (skipped).
    """
    rng = np.random.default_rng(seed)
    records = []
    for household in range(n_households):
        household_id = int(rng.integers(0, 10 * n_households + 10)) * 7 + household % 7
        size = int(rng.integers(1, 6))
        no_adults = rng.random() < 0.1
        cars = float(rng.choice([0, 0, 1, 1, 2, 3, 3, 4, 1.5, 2.7, -0.5, -1.2]))
        for member in range(size):
            age = int(rng.integers(5, 17)) if no_adults else int(rng.choice(
                [12, 16, 17, 18, 18, 30, 30, 45, 45, 70]))
            if rng.random() < 0.05:
                age = np.nan
            license_draw = rng.random()
            has_license = True if license_draw < 0.55 else (
                False if license_draw < 0.85 else np.nan)
            records.append({
                "household_id": household_id,
                "person_id": int(rng.integers(0, 50 * n_households + 50)),
                "age": age,
                "has_license": has_license,
                "number_of_cars": cars if rng.random() < 0.8 else cars - 1,
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
    # One person without a household id, in the middle of the frame: never visited.
    orphan = persons.iloc[[0]].assign(household_id=np.nan, number_of_cars=3.0)
    middle = len(persons) // 2
    persons = pd.concat([persons.iloc[:middle], orphan, persons.iloc[middle:]],
                        ignore_index=True)
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


def _run_both(persons, homes, regiostar, caplog, *, reference_indexed_lookup=True):
    """Run the reference (in the given lookup mode) and the production function."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=household_module.__name__):
        expected = _reference_build_household_car_frame(
            persons, homes, regiostar, indexed_home_lookup=reference_indexed_lookup)
    expected_logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=household_module.__name__):
        actual = household_module.build_household_car_frame(persons, homes, regiostar)
    actual_logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    # The owner-fallback lines were reworded on purpose (audit H, finding 6): their
    # counts are asserted as a property, every other line must match the reference.
    assert _owner_fallback_counts_logged(actual_logs) == _expected_owner_fallback_counts(
        persons)
    return (expected, actual, _without_owner_lines(expected_logs),
            _without_owner_lines([log for log in actual_logs if log[1] >= logging.WARNING]))


def _without_owner_lines(logs):
    return [log for log in logs
            if "licensed adult" not in log[2] and "[vehicles.household.owner" not in log[2]]


def _expected_owner_fallback_counts(persons: pd.DataFrame) -> dict:
    """Fallback tiers computed directly from the persons (independent of both codes)."""
    persons = persons[persons["household_id"].notna()]
    age = persons["age"].astype("float64")
    adult = (age >= ADULT_AGE).to_numpy()
    licensed_adult = adult & persons["has_license"].fillna(False).astype(bool).to_numpy()
    per_household = pd.DataFrame({
        "household_id": persons["household_id"].to_numpy(),
        "cars": persons["number_of_cars"].astype("float64").to_numpy(),
        "adult": adult, "licensed_adult": licensed_adult,
    }).groupby("household_id").agg(cars=("cars", "max"), adult=("adult", "any"),
                                   licensed_adult=("licensed_adult", "any"))
    cars = np.trunc(per_household["cars"])
    owning = per_household[cars > 0].assign(cars=cars[cars > 0].astype(int))
    adult_tier = owning[~owning["licensed_adult"] & owning["adult"]]
    minor_tier = owning[~owning["adult"]]
    return {
        "car_households": len(owning), "cars": int(owning["cars"].sum()),
        "adult_tier": (len(adult_tier), int(adult_tier["cars"].sum())),
        "minor_tier": (len(minor_tier), int(minor_tier["cars"].sum())),
    }


_RATE_LINE = re.compile(
    r"\[vehicles\.household\.owner_(households|cars)\] primary (\d+)/(\d+) .*fallback (\d+)")
_TIER_COUNTS = re.compile(r"(\d+) household\(s\), (\d+) car\(s\)")


def _owner_fallback_counts_logged(logs) -> dict:
    """Read the counts (not the wording) of the production owner-fallback lines."""
    rates = {}
    tiers = [(0, 0), (0, 0)]
    for _, _, message in logs:
        match = _RATE_LINE.search(message)
        if match:
            rates[match.group(1)] = (int(match.group(3)), int(match.group(4)))
        elif message.startswith("[vehicles.household.owner] fallback tiers"):
            tiers = [(int(h), int(c)) for h, c in _TIER_COUNTS.findall(message)]
    assert rates["households"][1] == tiers[0][0] + tiers[1][0]
    assert rates["cars"][1] == tiers[0][1] + tiers[1][1]
    return {
        "car_households": rates["households"][0], "cars": rates["cars"][0],
        "adult_tier": tiers[0], "minor_tier": tiers[1],
    }


def _assert_identical(expected: pd.DataFrame, actual: pd.DataFrame) -> None:
    assert list(actual.columns) == list(expected.columns)
    assert actual.dtypes.to_dict() == expected.dtypes.to_dict()
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)
    # Element types too: assert_frame_equal treats e.g. str "1" vs int 1 as different,
    # but this also pins scalar types inside object columns.
    for column in expected.columns:
        assert [type(v) for v in actual[column]] == [type(v) for v in expected[column]]


@pytest.mark.parametrize("reference_indexed_lookup", [True, False])
@pytest.mark.parametrize("n_households,seed", [(1, 1), (7, 2), (60, 3), (400, 4)])
def test_vectorised_frame_matches_reference(n_households, seed, reference_indexed_lookup,
                                            caplog):
    """Identical to the old loop in both of its former home-lookup modes."""
    persons, homes = _random_population(n_households, seed)
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog,
        reference_indexed_lookup=reference_indexed_lookup)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


def test_vectorised_frame_matches_reference_with_string_ids(caplog):
    persons, homes = _random_population(150, 11, string_ids=True)
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


@pytest.mark.parametrize("reference_indexed_lookup", [True, False])
def test_hand_built_edge_cases_match_reference_and_hand_computed_values(
        reference_indexed_lookup, caplog):
    """Owner tie, no-licence and no-adult fallbacks, duplicate RegioStaR AGS, and a
    mixed int/str household id, against the reference AND hand-computed values."""
    persons = pd.DataFrame.from_records([
        {"household_id": 1, "person_id": 11, "age": 44, "has_license": True,
         "number_of_cars": 0, "economic_status": "low"},
        {"household_id": 2, "person_id": 22, "age": 50, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 2, "person_id": 21, "age": 50, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 3, "person_id": 31, "age": 65, "has_license": False,
         "number_of_cars": 1, "economic_status": "medium"},
        {"household_id": 3, "person_id": 32, "age": 60, "has_license": False,
         "number_of_cars": 1, "economic_status": "medium"},
        {"household_id": 4, "person_id": 42, "age": 16, "has_license": False,
         "number_of_cars": 1, "economic_status": "very_low"},
        {"household_id": 4, "person_id": 41, "age": 17, "has_license": False,
         "number_of_cars": 1, "economic_status": "very_low"},
        {"household_id": "five", "person_id": 51, "age": 30, "has_license": True,
         "number_of_cars": 1, "economic_status": "very_high"},
    ])
    homes = pd.DataFrame({
        "household_id": [1, 2, 3, 4, "five"],
        "commune_id": ["031010000000", "031020000000", "031530000000",
                       "031540000000", "031010000000"],
    })
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog,
        reference_indexed_lookup=reference_indexed_lookup)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs
    by_household = actual.groupby("household_id", sort=False)["owner_id"].apply(list)
    assert 1 not in set(actual["household_id"])  # 0 cars: no row
    assert by_household[2] == [21, 22]  # age tie broken by person_id
    assert by_household[3] == [31]  # no licensed adult: oldest adult
    assert by_household[4] == [41]  # no adult: oldest person
    assert by_household["five"] == [51]
    household_two = actual.loc[actual["household_id"] == 2]
    assert household_two["gemeinde"].tolist() == ["SALZGITTER FIRST"] * 2


def test_random_fixture_exercises_every_edge_case(caplog):
    """Positive control: the identity test above really meets each branch."""
    persons, homes = _random_population(400, 4)
    licensed = persons["has_license"].fillna(False).astype(bool)
    cars = persons.groupby("household_id")["number_of_cars"].max()
    assert persons["has_license"].isna().any()
    assert (licensed & (persons["age"] < ADULT_AGE)).any()  # licensed minors
    assert (persons.groupby("household_id")["age"].max() < ADULT_AGE).any()
    assert (cars == 0).any()
    assert ((cars % 1 != 0) & (cars > 0)).any() and (cars < 0).any()  # int() truncation
    assert persons["age"].isna().any() and persons["household_id"].isna().any()
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
        persons, homes, _regiostar(), caplog)
    messages = [message for _, _, message in actual_logs]
    owner_counts = _expected_owner_fallback_counts(persons)  # _run_both checked the log
    assert owner_counts["adult_tier"][0] > 0 and owner_counts["minor_tier"][0] > 0
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
        persons, homes, _regiostar(), caplog)
    _assert_identical(expected, actual)
    assert actual["owner_age"].tolist() == [33.0, 33.0]
    assert actual_logs == expected_logs


def test_no_cars_gives_identical_empty_frame(caplog):
    persons, homes = _random_population(30, 5)
    persons["number_of_cars"] = 0
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog)
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
        persons, homes, _regiostar(), caplog)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


@pytest.mark.parametrize("reference_indexed_lookup", [True, False])
def test_missing_home_raises_same_error(reference_indexed_lookup):
    persons, homes = _random_population(80, 6)
    cars = persons.groupby("household_id", sort=True)["number_of_cars"].max()
    car_households = cars[cars > 0].index
    # Drop the homes of the 2nd and last car household: the first in id order is named.
    dropped = [car_households[1], car_households[-1]]
    homes = homes[~homes["household_id"].isin(dropped)]
    with pytest.raises(ValueError) as expected:
        _reference_build_household_car_frame(
            persons, homes, _regiostar(), indexed_home_lookup=reference_indexed_lookup)
    with pytest.raises(ValueError) as actual:
        household_module.build_household_car_frame(persons, homes, _regiostar())
    assert str(actual.value) == str(expected.value)
    assert str(car_households[1]) in str(actual.value)


def test_missing_person_id_is_refused_with_a_clear_message():
    """ANY NaN person_id is refused, even one that would own no car.

    Deliberately stricter than the former loop, which raised a bare KeyError only
    when an id-less person became an owner: here the id-less person lives in a
    0-car household, where the old loop would have returned a frame.
    """
    persons = pd.DataFrame.from_records([
        {"household_id": 1, "person_id": 11.0, "age": 44, "has_license": True,
         "number_of_cars": 1, "economic_status": "low"},
        {"household_id": 2, "person_id": np.nan, "age": 30, "has_license": True,
         "number_of_cars": 0, "economic_status": "high"},
    ])
    homes = pd.DataFrame({"household_id": [1, 2],
                          "commune_id": ["031010000000", "031010000000"]})
    assert len(_reference_build_household_car_frame(persons, homes, _regiostar())) == 1
    with pytest.raises(ValueError, match="1 person row\\(s\\) have no person_id"):
        household_module.build_household_car_frame(persons, homes, _regiostar())


def test_missing_household_id_rows_are_skipped_like_reference(caplog):
    """Persons without a household_id are never visited (float ids [1, 1, 2, NaN])."""
    persons = pd.DataFrame({
        "household_id": [1.0, 1.0, 2.0, np.nan],
        "person_id": [11, 12, 21, 99],
        "age": [44, 40, 30, 50],
        "has_license": [True, True, True, True],
        "number_of_cars": [2, 2, 1, 4],
        "economic_status": ["low", "low", "high", "medium"],
    })
    homes = pd.DataFrame({"household_id": [1.0, 2.0],
                          "commune_id": ["031010000000", "031020000000"]})
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog)
    assert len(expected) == 3
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


def test_nullable_age_with_missing_values_matches_reference(caplog):
    """Int64 ages with pd.NA: NA is neither adult nor licensed and sorts last."""
    persons = pd.DataFrame({
        "household_id": [1, 1, 1, 2, 2, 3, 3],
        "person_id": [11, 12, 13, 21, 22, 31, 32],
        "age": pd.array([pd.NA, 45, 45, pd.NA, 16, 30, pd.NA], dtype="Int64"),
        "has_license": [True, True, True, True, False, False, True],
        "number_of_cars": [3, 3, 3, 1, 1, 2, 2],
        "economic_status": ["low"] * 7,
    })
    homes = pd.DataFrame({"household_id": [1, 2, 3],
                          "commune_id": ["031010000000"] * 3})
    expected, actual, expected_logs, actual_logs = _run_both(
        persons, homes, _regiostar(), caplog)
    _assert_identical(expected, actual)
    assert actual_logs == expected_logs


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


def test_no_licence_anywhere_reports_full_fallback_rate_at_warning(caplog):
    """A broken licence column (all False) puts every car on the fallback: both rate
    lines must say 100 % and be WARNINGs, not INFO (CLAUDE.md "Fallback transparency")."""
    persons, homes = _random_population(60, 8)
    persons["has_license"] = False
    expected, actual, _, _ = _run_both(persons, homes, _regiostar(), caplog)
    _assert_identical(expected, actual)
    rate_records = [r for r in caplog.records
                    if _RATE_LINE.search(r.getMessage())]
    assert len(rate_records) == 2
    for record in rate_records:
        match = _RATE_LINE.search(record.getMessage())
        n_total, n_fallback = int(match.group(3)), int(match.group(4))
        assert n_total > 0 and n_fallback == n_total
        assert record.levelno == logging.WARNING
