import pandas as pd
from braunschweig.popsim import attributes as a
from braunschweig.popsim.mid import MID_PERSON_ATTR_COLS


def test_socioprofessional_from_occupation_with_fallback():
    persons = pd.DataFrame({
        "P_BKAT": [1, 7, 95],          # occupation code, not-employed, nicht zuzuordnen
        "employed": [True, False, True],
        "age": [40, 70, 30],
        "studies": [False, False, False],
    })
    out = a.map_socioprofessional_class(persons)
    assert "socioprofessional_class" in out.columns
    assert out["socioprofessional_class"].isna().sum() == 0
    assert out["socioprofessional_class"].dtype.kind in ("i", "O")


def test_socioprofessional_falls_back_when_no_p_bkat():
    persons = pd.DataFrame({"employed": [True, False], "age": [40, 70], "studies": [False, False]})
    out = a.map_socioprofessional_class(persons)
    assert out["socioprofessional_class"].isna().sum() == 0


def test_p_bkat_in_mid_person_attr_cols():
    """P_BKAT must be in MID_PERSON_ATTR_COLS so it is loaded for attribute mapping.

    Bug D4: P_BKAT was absent from this list, so map_socioprofessional_class
    always used the broad-activity fallback (100% fallback rate).
    """
    assert "P_BKAT" in MID_PERSON_ATTR_COLS, (
        "P_BKAT (MiD Berufskategorie) must be included in MID_PERSON_ATTR_COLS "
        "so that map_socioprofessional_class can use the real occupation crosswalk "
        "(SPC_BY_P_BKAT) instead of the broad-activity fallback for every person."
    )


def test_socioprofessional_uses_p_bkat_when_present():
    """When P_BKAT is present, the P_BKAT crosswalk must be used, not the fallback.

    SPC_BY_P_BKAT mapping:
      P_BKAT=1 (Angestellte) -> CS1 6
      P_BKAT=2 (Beamte)      -> CS1 5
      P_BKAT=3 (Selbststaendig) -> CS1 3
    The fallback derive_socioprofessional_class would produce different CS1 codes
    for the same employed/age combination, so we can assert the exact P_BKAT value.
    """
    persons = pd.DataFrame({
        "P_BKAT":    [1,  2,  3],
        "employed":  [True, True, True],
        "age":       [40,  45,  50],
        "studies":   [False, False, False],
    })
    out = a.map_socioprofessional_class(persons)
    # P_BKAT=1 -> CS1 6 (Angestellte -> Worker)
    assert out.loc[0, "socioprofessional_class"] == 6, (
        f"P_BKAT=1 should map to CS1 6 (Worker/Angestellte), "
        f"got {out.loc[0, 'socioprofessional_class']}"
    )
    # P_BKAT=2 -> CS1 5 (Beamte -> Employee)
    assert out.loc[1, "socioprofessional_class"] == 5, (
        f"P_BKAT=2 should map to CS1 5 (Employee/Beamte), "
        f"got {out.loc[1, 'socioprofessional_class']}"
    )
    # P_BKAT=3 -> CS1 3 (Selbststaendig -> Science/professionals)
    assert out.loc[2, "socioprofessional_class"] == 3, (
        f"P_BKAT=3 should map to CS1 3 (Science/Selbststaendig), "
        f"got {out.loc[2, 'socioprofessional_class']}"
    )


def test_studies_from_p_taet():
    """map_studies must derive the boolean studies flag from MiD P_TAET.

    MiD codebook: codes 8 (Ausbildung), 9 (Schueler), 10 (Student) -> True.
    All other codes (1-7 employment, 11+ Rentner/arbeitslos, 99 k.A.) -> False.
    """
    persons = pd.DataFrame({
        "P_TAET": [1, 9, 10, 11, 99],
    })
    out = a.map_studies(persons)
    assert "studies" in out.columns, "map_studies must add a 'studies' column"
    assert out["studies"].dtype == bool, (
        f"studies must be bool, got {out['studies'].dtype}"
    )
    expected = [False, True, True, False, False]
    for i, (got, exp) in enumerate(zip(out["studies"].tolist(), expected)):
        assert got == exp, (
            f"Row {i}: P_TAET={persons.loc[i, 'P_TAET']} -> studies expected "
            f"{exp}, got {got}"
        )
