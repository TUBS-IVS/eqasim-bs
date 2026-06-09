import pandas as pd
from braunschweig.popsim import enriched_adapter as ea


def _base_persons(**overrides):
    """Return a minimal one-row persons frame, optionally overriding columns.

    source_person_id / source_household_id are integer surrogates (not raw MiD ids)
    after the data-protection fix in braunschweig.popsim.assembly.assign_donor_surrogates.
    person_id / household_id are the synthetic integer ids assigned by sampled.
    """
    data = {
        "person_id": [42], "household_id": [7],
        "source_person_id": [1], "source_household_id": [1],
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
    """hts_* must be set from source_* (surrogate integers); census_* must be the
    synthetic integer person_id / household_id (NOT the popsim embedding strings).

    Design change (data-protection): census_* are ALWAYS overwritten with the
    current integer person_id / household_id to prevent the leaking embedding strings
    from synthesis.population.sampled from reaching the output.
    """
    persons = _base_persons()
    out = ea.run(persons)
    for col in ["hts_id", "hts_household_id", "census_person_id", "census_household_id"]:
        assert col in out.columns and out[col].notna().all()
    assert out.loc[0, "hts_id"] == 1              # from source_person_id (surrogate int)
    assert out.loc[0, "hts_household_id"] == 1    # from source_household_id (surrogate int)
    assert out.loc[0, "census_person_id"] == 42   # synthetic integer person_id
    assert out.loc[0, "census_household_id"] == 7  # synthetic integer household_id


def test_adapter_overwrites_leaking_census_ids():
    """census_* must be OVERWRITTEN with the integer person_id / household_id even
    when census_* were already set by synthesis.population.sampled.

    Rationale: sampled copies the popsim string ids (format ``<cell>_<H_ID>_<occ>[_<P_ID>]``)
    to census_* BEFORE reassigning integer ids.  Those strings embed the raw MiD
    H_ID / P_ID and must not reach the output.  The adapter overwrites census_*
    with the current integer ids so no leaking string survives.

    Note: this replaces the former 'only-if-absent' guard which was correct before
    the pseudonymisation design change but would have preserved the leaking strings.
    """
    # Simulate post-sampled state: person_id / household_id are the new integers;
    # census_* carry the leaking popsim embedding strings set by sampled.
    persons = _base_persons(
        person_id=[99],
        household_id=[42],
        census_person_id=["A_12345_0_678"],    # embedding string with H_ID=12345, P_ID=678
        census_household_id=["A_12345_0"],     # embedding string with H_ID=12345
        source_person_id=[3],                  # already-pseudonymised surrogate
        source_household_id=[2],
    )
    out = ea.run(persons)

    # census ids MUST be overwritten (the embedding strings must not reach the output).
    assert out.loc[0, "census_person_id"] == 99, (
        f"census_person_id should be overwritten with person_id=99, "
        f"got {out.loc[0, 'census_person_id']!r}"
    )
    assert out.loc[0, "census_household_id"] == 42, (
        f"census_household_id should be overwritten with household_id=42, "
        f"got {out.loc[0, 'census_household_id']!r}"
    )
    # Verify the leaking strings are gone.
    assert out.loc[0, "census_person_id"] != "A_12345_0_678", (
        "census_person_id must not carry the leaking popsim embedding string"
    )
    assert out.loc[0, "census_household_id"] != "A_12345_0", (
        "census_household_id must not carry the leaking popsim embedding string"
    )

    # hts_id / hts_household_id must always be set from source_*.
    assert out.loc[0, "hts_id"] == 3, (
        f"hts_id not set from source_person_id: {out.loc[0, 'hts_id']!r}"
    )
    assert out.loc[0, "hts_household_id"] == 2, (
        f"hts_household_id not set from source_household_id: {out.loc[0, 'hts_household_id']!r}"
    )
