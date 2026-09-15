"""Output-CSV column selection.

The eqasim output persons/households CSVs must carry the optional fork attributes
(``license_type``, ``economic_status``, ``housing_tenure``) WHEN the synthesis
produced them, so the population-validation tool can validate them directly
instead of deriving from a boolean / only via the geo export. When the attributes
are absent (feature OFF) the column set must stay byte-identical to the legacy
output. Tested via two pure helpers so no synpp context / full pipeline is needed.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from synthesis.output import (  # noqa: E402
    select_person_output_columns,
    select_household_output_columns,
)

BASE_PERSON = [
    "person_id", "household_id", "age", "employed", "sex",
    "socioprofessional_class", "has_driving_license", "has_pt_subscription",
    "pt_subscription_type", "census_person_id", "hts_id", "is_urban_resident",
]
BASE_HOUSEHOLD = [
    "household_id", "car_availability", "bicycle_availability",
    "number_of_cars", "number_of_bicycles", "income",
    "high_income", "household_size", "census_household_id",
]


def test_person_columns_legacy_byte_identical_when_optionals_absent():
    cols = select_person_output_columns(set(BASE_PERSON), "is_urban_resident")
    assert cols == BASE_PERSON


def test_person_columns_append_license_type_and_economic_status_when_present():
    available = set(BASE_PERSON) | {"license_type", "economic_status"}
    cols = select_person_output_columns(available, "is_urban_resident")
    # Base columns keep their exact order; the new attributes are appended.
    assert cols[:len(BASE_PERSON)] == BASE_PERSON
    assert "license_type" in cols
    assert "economic_status" in cols


def test_person_columns_append_employment_status_when_present():
    # employment_status (P9 taxonomy, MiD P_BKAT; popsim_mid only) follows the same
    # additive-optional-attribute contract as license_type / economic_status.
    available = set(BASE_PERSON) | {"employment_status"}
    cols = select_person_output_columns(available, "is_urban_resident")
    assert cols[:len(BASE_PERSON)] == BASE_PERSON
    assert "employment_status" in cols


def test_person_columns_legacy_byte_identical_when_employment_status_absent():
    # Non-MiD sources (ENTD / simple_ipf_open) never produce employment_status;
    # its absence must not perturb the legacy column set (byte-identical output).
    cols = select_person_output_columns(set(BASE_PERSON), "is_urban_resident")
    assert "employment_status" not in cols
    assert cols == BASE_PERSON


def test_person_columns_append_passenger_availability_facts_when_present():
    available = set(BASE_PERSON) | {
        "car_passenger_availability", "passenger_availability_source",
    }
    cols = select_person_output_columns(available, "is_urban_resident")
    assert cols[:len(BASE_PERSON)] == BASE_PERSON
    assert cols[-2:] == [
        "car_passenger_availability", "passenger_availability_source",
    ]


def test_person_columns_legacy_byte_identical_when_passenger_facts_absent():
    cols = select_person_output_columns(set(BASE_PERSON), "is_urban_resident")
    assert cols == BASE_PERSON


def test_person_csv_bytes_legacy_when_passenger_facts_absent():
    frame = pd.DataFrame([{
        "person_id": 1, "household_id": 10, "age": 35, "employed": "yes",
        "sex": "female", "socioprofessional_class": "employed",
        "has_driving_license": True, "has_pt_subscription": False,
        "pt_subscription_type": "never_pt", "census_person_id": 44,
        "hts_id": 55, "is_urban_resident": True,
    }])
    output = io.BytesIO()
    frame[select_person_output_columns(frame.columns, "is_urban_resident")].to_csv(
        output, sep=";", index=False, lineterminator="\n")
    assert output.getvalue() == (
        b"person_id;household_id;age;employed;sex;socioprofessional_class;"
        b"has_driving_license;has_pt_subscription;pt_subscription_type;"
        b"census_person_id;hts_id;is_urban_resident\n"
        b"1;10;35;yes;female;employed;True;False;never_pt;44;55;True\n"
    )


def test_household_columns_legacy_byte_identical_when_optionals_absent():
    cols = select_household_output_columns(set(BASE_HOUSEHOLD))
    assert cols == BASE_HOUSEHOLD


def test_household_columns_existing_optional_inserts_preserved():
    available = set(BASE_HOUSEHOLD) | {"household_income_eur", "hh_type"}
    cols = select_household_output_columns(available)
    assert cols.index("household_income_eur") == cols.index("high_income") - 1
    assert cols.index("hh_type") == cols.index("household_size") + 1


def test_household_columns_append_housing_tenure_when_present():
    available = set(BASE_HOUSEHOLD) | {"housing_tenure"}
    cols = select_household_output_columns(available)
    assert "housing_tenure" in cols
    # Legacy columns unchanged in order.
    assert cols[:len(BASE_HOUSEHOLD)] == BASE_HOUSEHOLD
