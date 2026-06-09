import pandas as pd
from braunschweig.popsim import enriched_adapter as ea


def _base_persons(**overrides):
    """Return a minimal one-row persons frame, optionally overriding columns."""
    data = {
        "person_id": ["A_1_0_1"], "household_id": ["c_1_0"],
        "source_person_id": ["1"], "source_household_id": ["1"],
        "age": [40], "sex": ["male"], "employed": [True],
        "household_income": ["3000_3600"], "high_income": [False],
        "car_availability": ["all"], "bicycle_availability": ["all"],
        "has_license": [True], "has_pt_subscription": [False],
        "pt_subscription_type": ["fahre_nie"], "household_income_eur": [3300.0],
        "is_urban_resident": [True], "age_range": ["higher_education"],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_adapter_fills_writer_id_fields_from_source():
    persons = _base_persons()
    out = ea.run(persons)
    for col in ["hts_id", "hts_household_id", "census_person_id", "census_household_id"]:
        assert col in out.columns and out[col].notna().all()
    assert out.loc[0, "hts_id"] == "1"                 # from source_person_id
    assert out.loc[0, "hts_household_id"] == "1"        # from source_household_id
    assert out.loc[0, "census_person_id"] == "A_1_0_1"  # popsim own id
    assert out.loc[0, "census_household_id"] == "c_1_0"


def test_adapter_preserves_existing_census_ids():
    """When synthesis.population.sampled has already set census_*ids, the adapter
    must NOT overwrite them.  Only hts_id / hts_household_id should be (re-)set.

    Scenario: sampled sets census_person_id="orig_p" / census_household_id="orig_h"
    for the ORIGINAL popsim ids, then reassigns person_id to a new integer "99".
    The adapter must leave the provenance ids intact.
    """
    # person_id="99" is the NEW reassigned integer id from sampled;
    # census_person_id="orig_p" is the ORIGINAL popsim id preserved by sampled.
    persons = _base_persons(
        person_id=["99"],
        household_id=["42"],
        census_person_id=["orig_p"],
        census_household_id=["orig_h"],
        source_person_id=["mid_p1"],
        source_household_id=["mid_h1"],
    )
    out = ea.run(persons)

    # census ids must NOT be overwritten (sampled's original provenance is preserved).
    assert out.loc[0, "census_person_id"] == "orig_p", (
        f"census_person_id was overwritten: expected 'orig_p', got {out.loc[0, 'census_person_id']!r}"
    )
    assert out.loc[0, "census_household_id"] == "orig_h", (
        f"census_household_id was overwritten: expected 'orig_h', got {out.loc[0, 'census_household_id']!r}"
    )

    # hts_id / hts_household_id must always be set from source_*.
    assert out.loc[0, "hts_id"] == "mid_p1", (
        f"hts_id not set from source_person_id: {out.loc[0, 'hts_id']!r}"
    )
    assert out.loc[0, "hts_household_id"] == "mid_h1", (
        f"hts_household_id not set from source_household_id: {out.loc[0, 'hts_household_id']!r}"
    )
