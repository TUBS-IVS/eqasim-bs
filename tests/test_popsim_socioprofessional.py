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

    P_BKAT ("Umfang der Erwerbstaetigkeit") is the donor column for the
    categorical ``employment_status`` attribute (EMPLOYMENT_STATUS_BY_P_BKAT). It
    is NOT an occupation variable and must NOT be used for socioprofessional_class
    (issue #167 -- the former SPC_BY_P_BKAT crosswalk was removed).
    """
    assert "P_BKAT" in MID_PERSON_ATTR_COLS, (
        "P_BKAT (MiD Umfang der Erwerbstaetigkeit) must be included in "
        "MID_PERSON_ATTR_COLS so that map_employment_status can derive the "
        "employment_status attribute from it."
    )


def test_socioprofessional_does_not_use_p_bkat_crosswalk():
    """#167: P_BKAT is employment EXTENT (Umfang der Erwerbstaetigkeit), NOT an
    occupation "Berufskategorie". socioprofessional_class must therefore be the
    broad-activity derivation ``derive_socioprofessional_class(employed, age,
    studies)`` -- identical whether or not P_BKAT is present -- and must NOT apply
    the removed SPC_BY_P_BKAT occupation crosswalk.
    """
    from braunschweig.ipf.attributed import derive_socioprofessional_class

    persons = pd.DataFrame({
        "P_BKAT":    [1,  2,  3],
        "employed":  [True, True, True],
        "age":       [40,  45,  50],
        "studies":   [False, False, False],
    })
    out = a.map_socioprofessional_class(persons)
    expected = derive_socioprofessional_class(
        persons["employed"], persons["age"], persons["studies"]
    ).astype(int)
    assert list(out["socioprofessional_class"]) == list(expected), (
        "socioprofessional_class must equal the broad-activity derivation, not the "
        f"(removed) P_BKAT crosswalk. got {list(out['socioprofessional_class'])}, "
        f"expected {list(expected)}"
    )


def test_socioprofessional_invariant_to_p_bkat():
    """P_BKAT must have NO effect on socioprofessional_class (it is not occupation):
    the same (employed, age, studies) yields the same SPC with or without P_BKAT."""
    base = pd.DataFrame({
        "employed": [True, False, True],
        "age":      [40, 70, 20],
        "studies":  [False, False, True],
    })
    with_bkat = base.assign(P_BKAT=[1, 7, 6])
    out_with = a.map_socioprofessional_class(with_bkat)
    out_without = a.map_socioprofessional_class(base)
    assert list(out_with["socioprofessional_class"]) == list(
        out_without["socioprofessional_class"]
    ), "P_BKAT presence must not change socioprofessional_class (issue #167)."


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
