import pandas as pd
from braunschweig.popsim import enriched_adapter as ea


def test_adapter_fills_writer_id_fields_from_source():
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1"], "household_id": ["c_1_0"],
        "source_person_id": ["1"], "source_household_id": ["1"],
        "age": [40], "sex": ["male"], "employed": [True],
        "household_income": ["3000_3600"], "high_income": [False],
        "car_availability": ["all"], "bicycle_availability": ["all"],
        "has_license": [True], "has_pt_subscription": [False],
        "pt_subscription_type": ["fahre_nie"], "household_income_eur": [3300.0],
        "is_urban_resident": [True], "age_range": ["higher_education"],
    })
    out = ea.run(persons)
    for col in ["hts_id", "hts_household_id", "census_person_id", "census_household_id"]:
        assert col in out.columns and out[col].notna().all()
    assert out.loc[0, "hts_id"] == "1"                 # from source_person_id
    assert out.loc[0, "hts_household_id"] == "1"        # from source_household_id
    assert out.loc[0, "census_person_id"] == "A_1_0_1"  # popsim own id
    assert out.loc[0, "census_household_id"] == "c_1_0"
