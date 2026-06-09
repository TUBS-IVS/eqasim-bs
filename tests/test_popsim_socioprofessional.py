import pandas as pd
from braunschweig.popsim import attributes as a


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
