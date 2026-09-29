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


def test_person_columns_append_every_optional_attribute_after_the_legacy_block():
    # Base columns keep their exact order; each optional attribute (license type, economic
    # status, the P9 employment status of popsim_mid, the passenger-availability facts) is
    # appended in its contract order when the frame carries it.
    optionals = ["license_type", "economic_status", "employment_status",
                 "car_passenger_availability", "passenger_availability_source"]
    cols = select_person_output_columns(set(BASE_PERSON) | set(optionals), "is_urban_resident")
    assert cols == BASE_PERSON + optionals


def test_passenger_attribute_origin_ids_stay_out_of_public_person_csv():
    """Protected raw MiD identities are internal even when passenger facts are public."""
    private = {"attribute_source_H_ID", "attribute_source_P_ID"}
    available = set(BASE_PERSON) | private | {
        "car_passenger_availability", "passenger_availability_source",
    }

    columns = select_person_output_columns(available, "is_urban_resident")

    assert private.isdisjoint(columns)
    assert "car_passenger_availability" in columns
    assert "passenger_availability_source" in columns


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


def test_household_columns_place_every_optional_attribute():
    # household_income_eur and hh_type are INSERTED next to their related legacy columns;
    # housing_tenure is appended after the legacy block.
    available = set(BASE_HOUSEHOLD) | {"household_income_eur", "hh_type", "housing_tenure"}
    assert select_household_output_columns(available) == [
        "household_id", "car_availability", "bicycle_availability", "number_of_cars",
        "number_of_bicycles", "income", "household_income_eur", "high_income",
        "household_size", "hh_type", "census_household_id", "housing_tenure",
    ]
