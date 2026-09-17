"""Measure raw MiD passenger availability without exporting respondents.

This is an attribute-wiring A/B measurement for issue #398.  It runs the
production passenger-availability helpers on the raw MiD delivery, then writes
only aggregate counts and rates.  The resulting counts are raw-survey sample
counts, never ZGB population estimates; no MATSim simulation or calibration is
run by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

REPORT_FILENAME = "report.json"
ELIGIBILITY_FILENAME = "eligibility_by_age_and_cars.csv"
VALID_P_VAUTO = {1: "all", 2: "some", 3: "none"}
PASSENGER_AVAILABILITY_SOURCES = (
    "own_response",
    "adult_empirical_imputation",
    "member_completion_borrowed_response",
    "member_completion_borrowed_imputation",
    "child_household_evidence",
    "child_diary_evidence",
    "child_empirical_fallback",
)
AGE_GROUPS = ("under14", "14to17", "18plus")
CAR_GROUPS = ("0", "positive")
REQUIRED_EVALUATION_COLUMNS = {
    "H_ID",
    "P_ID",
    "age",
    "number_of_cars",
    "P_VAUTO",
    "car_availability",
    "_driver_car_availability_before",
    "has_license",
    "_has_license_before",
    "car_passenger_availability",
    "passenger_availability_source",
    "src_has_car_passenger_trip",
}
WEGE_COLUMNS = ("H_ID", "P_ID", "hvm_imp", "W_RBW")


def _age_group(age: pd.Series) -> pd.Series:
    return pd.Series(
        np.select([age < 14, age < 18], ["under14", "14to17"], default="18plus"),
        index=age.index,
    )


def _car_group(number_of_cars: pd.Series) -> pd.Series:
    return pd.Series(
        np.where(number_of_cars > 0, "positive", "0"), index=number_of_cars.index
    )


def _assert_required_columns(persons: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_EVALUATION_COLUMNS - set(persons.columns))
    if missing:
        raise ValueError(
            "Passenger-availability evaluation is missing required person column(s): "
            f"{missing}"
        )


def _assert_invariants(persons: pd.DataFrame) -> None:
    """Stop the measurement if the new wiring changes protected driver values."""
    unknown_sources = set(persons["passenger_availability_source"].dropna()) - set(
        PASSENGER_AVAILABILITY_SOURCES
    )
    if unknown_sources or persons["passenger_availability_source"].isna().any():
        raise ValueError(
            "car_passenger_availability has unknown or missing "
            f"passenger_availability_source values: {sorted(unknown_sources)}"
        )
    valid_answers = persons["age"].ge(14) & persons["P_VAUTO"].isin(VALID_P_VAUTO)
    expected = persons.loc[valid_answers, "P_VAUTO"].map(VALID_P_VAUTO)
    actual = persons.loc[valid_answers, "car_passenger_availability"]
    if (~actual.eq(expected).fillna(False)).any():
        raise ValueError(
            "P_VAUTO responses for valid 14+ respondents did not survive exactly "
            "as car_passenger_availability."
        )

    for column, before_column in (
        ("car_availability", "_driver_car_availability_before"),
        ("has_license", "_has_license_before"),
    ):
        unchanged = persons[column].eq(persons[before_column]).fillna(False)
        if (~unchanged).any():
            raise ValueError(
                f"Passenger-availability measurement changed protected driver attribute {column!r}."
            )


def _eligibility_table(persons: pd.DataFrame) -> pd.DataFrame:
    working = persons.copy()
    working["age_group"] = _age_group(working["age"])
    working["household_cars"] = _car_group(working["number_of_cars"])
    working["old_eligible"] = working["car_availability"].ne("none")
    working["new_eligible"] = working["car_passenger_availability"].ne("none")
    working["newly_eligible"] = ~working["old_eligible"] & working["new_eligible"]
    working["newly_excluded"] = working["old_eligible"] & ~working["new_eligible"]

    rows = []
    for age_group in AGE_GROUPS:
        for car_group in CAR_GROUPS:
            cell = working[
                (working["age_group"] == age_group)
                & (working["household_cars"] == car_group)
            ]
            n_persons = int(len(cell))
            old_eligible = int(cell["old_eligible"].sum())
            new_eligible = int(cell["new_eligible"].sum())
            rows.append(
                {
                    "age_group": age_group,
                    "household_cars": car_group,
                    "n_persons": n_persons,
                    "old_eligible": old_eligible,
                    "new_eligible": new_eligible,
                    "newly_eligible": int(cell["newly_eligible"].sum()),
                    "newly_excluded": int(cell["newly_excluded"].sum()),
                    "old_eligible_rate": old_eligible / n_persons if n_persons else None,
                    "new_eligible_rate": new_eligible / n_persons if n_persons else None,
                }
            )
    return pd.DataFrame(rows)


def evaluate_passenger_availability(
    persons: pd.DataFrame,
    *,
    input_households: int,
    input_persons: int | None = None,
) -> tuple[dict, pd.DataFrame]:
    """Return aggregate passenger-availability outcomes and age/car cells.

    ``persons`` must be the post-production-helper frame, with snapshots of the
    two protected driver attributes from immediately before the helper ran.
    No row-level input is included in the returned report.
    """
    _assert_required_columns(persons)
    _assert_invariants(persons)

    old_eligible = persons["car_availability"].ne("none")
    new_eligible = persons["car_passenger_availability"].ne("none")
    valid_answers = (
        persons["age"].ge(14)
        & persons["P_VAUTO"].isin(VALID_P_VAUTO)
        & persons["passenger_availability_source"].eq("own_response")
    )
    missing_or_proxy = (
        persons["age"].ge(14)
        & ~persons["P_VAUTO"].isin(VALID_P_VAUTO)
        & persons["passenger_availability_source"].eq("adult_empirical_imputation")
    )
    conflict = (
        persons["age"].ge(14)
        & persons["P_VAUTO"].eq(3)
        & persons["src_has_car_passenger_trip"].fillna(False).astype(bool)
    )
    table = _eligibility_table(persons)

    report = {
        "measurement": "raw_mid_passenger_availability_attribute_wiring_ab",
        "scope_note": (
            "Counts are unweighted raw MiD sample counts, not population estimates. "
            "The full raw national data are not the synthetic ZGB population."
        ),
        "counts": {
            "input_persons": int(len(persons) if input_persons is None else input_persons),
            "input_households": int(input_households),
            "evaluated_persons": int(len(persons)),
            "observed_14plus_answers": int(valid_answers.sum()),
            "missing_or_proxy_rows": int(missing_or_proxy.sum()),
            "children_under14": int(persons["age"].lt(14).sum()),
            "old_eligible": int(old_eligible.sum()),
            "new_eligible": int(new_eligible.sum()),
            "newly_eligible": int((~old_eligible & new_eligible).sum()),
            "newly_excluded": int((old_eligible & ~new_eligible).sum()),
            "p_vauto_none_with_passenger_diary_evidence": int(conflict.sum()),
        },
        "source_category_counts": {
            source: int(persons["passenger_availability_source"].eq(source).sum())
            for source in PASSENGER_AVAILABILITY_SOURCES
        },
    }
    return report, table


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, encoding="utf-8"
    ).strip()


def passenger_rng_with_provenance(
    seed: int, passenger_availability_rng_offset: int
) -> tuple[np.random.RandomState, dict]:
    """Create the production-equivalent independent passenger RNG stream."""
    effective_seed = int(seed) + int(passenger_availability_rng_offset)
    return np.random.RandomState(effective_seed), {
        "protocol": "independent_production_passenger_availability_rng",
        "offset": int(passenger_availability_rng_offset),
        "effective_seed": effective_seed,
    }


def _read_required_wege(mid_dir: Path) -> pd.DataFrame:
    from braunschweig.popsim.mid import detect_csv_separator

    path = mid_dir / "MiD2023_Wege.csv"
    if not path.exists():
        raise FileNotFoundError(f"MiD Wege file not found: {path}")
    separator = detect_csv_separator(path)
    header = pd.read_csv(path, sep=separator, nrows=0).columns
    missing = sorted(set(WEGE_COLUMNS) - set(header))
    if missing:
        raise ValueError(f"MiD Wege file {path} is missing required columns: {missing}")
    return pd.read_csv(path, sep=separator, usecols=list(WEGE_COLUMNS), low_memory=False)


def _assert_unique_person_keys(persons: pd.DataFrame) -> None:
    duplicated = persons.duplicated(["H_ID", "P_ID"], keep=False)
    if duplicated.any():
        raise ValueError(
            "MiD persons have non-unique (H_ID, P_ID) keys; cannot safely join "
            "household attributes or diary evidence."
        )


def _run_raw_measurement(mid_dir: Path, seed: int) -> tuple[dict, pd.DataFrame, dict]:
    """Run the production helpers, keeping the raw data local to this process."""
    from braunschweig.popsim import attributes, expand, mid
    from braunschweig.popsim.passenger_availability import (
        PASSENGER_AVAILABILITY_RNG_OFFSET,
        attach_car_passenger_diary_evidence,
        derive_car_passenger_availability,
    )

    households, persons = mid.load_mid_attributes(
        mid_dir, include_passenger_availability=True
    )
    raw_households, raw_persons = len(households), len(persons)
    households, persons, dropped_households, dropped_persons = mid.drop_invalid_households(
        households, persons
    )
    _assert_unique_person_keys(persons)
    if households["H_ID"].duplicated().any():
        raise ValueError("MiD households have non-unique H_ID keys; cannot safely attach car counts.")

    rng = np.random.RandomState(seed)
    persons = expand.map_demographics(persons, rng=rng)
    persons = attributes.map_has_license(persons, rng=rng)
    households = attributes.map_number_of_cars(households, rng=rng)
    persons = persons.merge(
        households[["H_ID", "number_of_cars"]], on="H_ID", how="left", validate="many_to_one"
    )
    if persons["number_of_cars"].isna().any():
        raise ValueError("At least one MiD person has no valid household car-count join.")
    persons["household_id"] = persons["H_ID"]
    adults = persons["age"].ge(18).groupby(persons["H_ID"]).sum()
    persons["car_availability"] = [
        attributes.derive_car_availability(cars, int(adults.loc[household_id]))
        for household_id, cars in zip(persons["H_ID"], persons["number_of_cars"])
    ]
    persons["_driver_car_availability_before"] = persons["car_availability"]
    persons["_has_license_before"] = persons["has_license"]

    wege = _read_required_wege(mid_dir)
    persons = attach_car_passenger_diary_evidence(persons, wege)
    passenger_rng, passenger_rng_provenance = passenger_rng_with_provenance(
        seed, PASSENGER_AVAILABILITY_RNG_OFFSET
    )
    persons = derive_car_passenger_availability(persons, households, rng=passenger_rng)
    report, table = evaluate_passenger_availability(
        persons, input_households=raw_households, input_persons=raw_persons
    )
    report["input_filtering"] = {
        "invalid_households_dropped": int(dropped_households),
        "invalid_persons_dropped": int(dropped_persons),
    }
    return report, table, passenger_rng_provenance


def _prepare_output_dir(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [out_dir / name for name in (REPORT_FILENAME, ELIGIBILITY_FILENAME) if (out_dir / name).exists()]
    if existing:
        raise FileExistsError(f"Refusing to overwrite existing aggregate report file(s): {existing}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mid-dir", required=True, help="raw MiD 2023 delivery directory")
    parser.add_argument("--out-dir", required=True, help="directory for aggregate report outputs")
    parser.add_argument("--seed", type=int, default=1234, help="seed for existing attribute mappers")
    args = parser.parse_args(argv)

    mid_dir = Path(args.mid_dir)
    out_dir = Path(args.out_dir)
    _prepare_output_dir(out_dir)
    report, table, passenger_rng_provenance = _run_raw_measurement(mid_dir, args.seed)
    source_files = [
        mid_dir / "MiD2023_Haushalte.csv",
        mid_dir / "MiD2023_Personen.csv",
        mid_dir / "MiD2023_Wege.csv",
    ]
    report["provenance"] = {
        "seed": args.seed,
        "passenger_rng": passenger_rng_provenance,
        "git_commit": _git_commit(),
        "source_file_sha256": {path.name: _sha256(path) for path in source_files},
        "matsim_simulation_run": False,
        "calibration_run": False,
    }
    (out_dir / REPORT_FILENAME).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    table.to_csv(out_dir / ELIGIBILITY_FILENAME, index=False, lineterminator="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
