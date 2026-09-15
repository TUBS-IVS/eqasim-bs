"""Tests for the raw MiD passenger-availability measurement harness."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scripts import measure_mid_passenger_availability as measurement


def _persons() -> pd.DataFrame:
    """Literal MiD-shaped outcomes spanning each report age/car group."""
    return pd.DataFrame(
        {
            "H_ID": [1, 1, 2, 2],
            "P_ID": [1, 2, 1, 2],
            "age": [10, 16, 28, 45],
            "number_of_cars": [1, 1, 0, 0],
            "P_VAUTO": [403, 1, 2, 3],
            "car_availability": ["none", "none", "all", "some"],
            "_driver_car_availability_before": ["none", "none", "all", "some"],
            "has_license": [False, False, True, True],
            "_has_license_before": [False, False, True, True],
            "car_passenger_availability": ["all", "all", "some", "none"],
            "passenger_availability_source": [
                "child_diary_evidence",
                "own_response",
                "own_response",
                "own_response",
            ],
            "src_has_car_passenger_trip": [True, False, False, True],
        }
    )


def test_evaluate_preserves_each_valid_14plus_own_response_mapping():
    """Changing the P_VAUTO mapping must fail instead of silently reporting it."""
    report, table = measurement.evaluate_passenger_availability(_persons(), input_households=2)

    assert report["counts"]["observed_14plus_answers"] == 3
    assert report["counts"]["old_eligible"] == 2
    assert report["counts"]["new_eligible"] == 3
    assert report["counts"]["newly_eligible"] == 2
    assert report["counts"]["newly_excluded"] == 1
    assert report["source_category_counts"] == {
        "adult_empirical_imputation": 0,
        "child_empirical_fallback": 0,
        "child_diary_evidence": 1,
        "child_household_evidence": 0,
        "own_response": 3,
    }
    assert set(table["age_group"]) == {"under14", "14to17", "18plus"}


def test_evaluate_rejects_a_changed_valid_own_response():
    """A report must stop if a 14+ P_VAUTO=3 answer is recoded to available."""
    persons = _persons()
    persons.loc[3, "car_passenger_availability"] = "some"

    with pytest.raises(ValueError, match="P_VAUTO"):
        measurement.evaluate_passenger_availability(persons, input_households=2)


def test_evaluate_rejects_nullable_derived_valid_own_response():
    """A missing nullable passenger value cannot pass the exact P_VAUTO guard."""
    persons = _persons()
    persons["car_passenger_availability"] = persons[
        "car_passenger_availability"
    ].astype("string")
    persons.loc[1, "car_passenger_availability"] = pd.NA

    with pytest.raises(ValueError, match="P_VAUTO"):
        measurement.evaluate_passenger_availability(persons, input_households=2)


def test_evaluate_rejects_driver_attribute_changes():
    """A passenger measurement may not mutate the already-derived driver attributes."""
    persons = _persons()
    persons.loc[2, "has_license"] = False

    with pytest.raises(ValueError, match="has_license"):
        measurement.evaluate_passenger_availability(persons, input_households=2)


@pytest.mark.parametrize(
    ("column", "dtype"),
    [
        ("car_availability", "string"),
        ("has_license", "boolean"),
    ],
)
def test_evaluate_rejects_nullable_protected_driver_attribute(column, dtype):
    """A nullable protected-driver mismatch must fail closed instead of being skipped."""
    persons = _persons()
    persons[column] = persons[column].astype(dtype)
    persons.loc[2, column] = pd.NA

    with pytest.raises(ValueError, match=column):
        measurement.evaluate_passenger_availability(persons, input_households=2)


def test_evaluate_rejects_an_unrecognised_derivation_source():
    """An unknown production source label must not disappear from the aggregate report."""
    persons = _persons()
    persons.loc[0, "passenger_availability_source"] = "unrecognised_source"

    with pytest.raises(ValueError, match="passenger_availability_source"):
        measurement.evaluate_passenger_availability(persons, input_households=2)


def test_evaluate_reports_age_car_rates_and_conflicting_diary_measure():
    """The aggregate table must retain each age/car cell and P_VAUTO=3 conflict count."""
    report, table = measurement.evaluate_passenger_availability(_persons(), input_households=2)

    assert report["counts"]["children_under14"] == 1
    assert report["counts"]["missing_or_proxy_rows"] == 0
    assert report["counts"]["p_vauto_none_with_passenger_diary_evidence"] == 1
    rows = table.set_index(["age_group", "household_cars"])
    assert rows.loc[("under14", "positive"), "new_eligible_rate"] == 1.0
    assert rows.loc[("14to17", "positive"), "old_eligible_rate"] == 0.0
    assert rows.loc[("18plus", "0"), "n_persons"] == 2
    assert rows.loc[("18plus", "0"), "newly_excluded"] == 1


def test_passenger_rng_uses_production_offset_and_reports_effective_seed():
    """Changing the passenger stream to the shared attribute RNG must fail this protocol check."""
    rng, provenance = measurement.passenger_rng_with_provenance(1234, 74517)

    assert rng.randint(0, 1_000_000) == np.random.RandomState(75751).randint(0, 1_000_000)
    assert provenance == {
        "protocol": "independent_production_passenger_availability_rng",
        "offset": 74517,
        "effective_seed": 75751,
    }


def test_cli_refuses_to_overwrite_an_existing_aggregate_report(tmp_path):
    """The CLI must fail before raw-data loading when its report would be overwritten."""
    (tmp_path / measurement.REPORT_FILENAME).write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        measurement.main(["--mid-dir", str(tmp_path / "missing"), "--out-dir", str(tmp_path)])
