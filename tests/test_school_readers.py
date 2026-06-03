import numpy as np
import pandas as pd
from braunschweig.data.schools.readers import (
    build_abs_long, build_bbs_long, build_schools_long,
)

SCOPE = ["03101", "03158"]


def _abs_raw():
    return pd.DataFrame({
        "Kreis": [101, 158, 999],
        "AGS": ["101000", "158001", "999000"],
        "SNR": ["05009", "07123", "00001"],
        "Schulname": ["GS Alpha", "KGS Beta", "GS Out"],
        "Strasse": ["Hauptstr 1", "Schulweg 2", "Nowhere 9"],
        "PLZ": ["38100", "38300", "00000"],
        "Ort": ["Braunschweig", "Wolfenbuettel", "Out"],
        "SGL1": ["01", "04", "01"], "Sch1": [200, 100, 50],
        "SGL2": [None, "16", None], "Sch2": [np.nan, 60, np.nan],
        "SGL3": [None, "28", None], "Sch3": [np.nan, 30, np.nan],
        "SGL4": [None, None, None], "Sch4": [np.nan, np.nan, np.nan],
        "SGL5": [None, None, None], "Sch5": [np.nan, np.nan, np.nan],
        "SGL6": [None, None, None], "Sch6": [np.nan, np.nan, np.nan],
    })


def test_build_abs_long_types_and_scopes():
    df = build_abs_long(_abs_raw(), SCOPE)
    assert set(df["school_id"]) == {"abs_05009", "abs_07123"}
    beta = df[df["school_id"] == "abs_07123"].set_index("level")["capacity"].to_dict()
    assert beta == {"grundschule": 100.0, "sekundar_1": 60.0, "oberstufe": 30.0}
    alpha = df[df["school_id"] == "abs_05009"]
    assert list(alpha["level"]) == ["grundschule"]
    assert alpha.iloc[0]["ags8"] == "03101000"
    assert alpha.iloc[0]["kreis5"] == "03101"
    assert alpha.iloc[0]["city"] == "Braunschweig"


def _bbs_raw():
    return pd.DataFrame({
        "Schulnummer": ["70026", "70999"],
        "Schulbezeichnung": ["BBS 1", "BBS Out"],
        "Strasse": ["Lavesallee 14", "Out 1"],
        "PLZ": ["38100", "00000"],
        "Ort": ["Braunschweig", "Out"],
        "AGS": ["101000", "999000"],
        "Kreis": [101, 999],
        "Anzahl_BS": [1450, "[n]"],
        "Anzahl_BI": [231, "[n]"],
        "Anzahl_FO": ["[n]", 5],
    })


def test_build_bbs_long_sums_to_bbs():
    pupil_cols = ["Anzahl_BS", "Anzahl_BI", "Anzahl_FO"]
    df = build_bbs_long(_bbs_raw(), SCOPE, pupil_cols)
    assert set(df["school_id"]) == {"bbs_70026"}
    row = df.iloc[0]
    assert row["level"] == "bbs"
    assert row["capacity"] == 1681.0
    assert row["ags8"] == "03101000"


def test_build_schools_long_concats_abs_and_bbs():
    df = build_schools_long(
        _abs_raw(), _bbs_raw(), SCOPE, bbs_pupil_cols=["Anzahl_BS", "Anzahl_BI", "Anzahl_FO"],
    )
    assert set(df["school_id"]) >= {"abs_05009", "abs_07123", "bbs_70026"}
    assert list(df.columns) == [
        "school_id", "name", "level", "capacity",
        "ags8", "kreis5", "street", "plz", "city",
    ]
    assert (df["capacity"] > 0).all()


def test_ids_with_float_artifacts_are_cleaned():
    # pandas reads SNR/AGS as float when blanks are present, so 158037 -> 158037.0;
    # the trailing '.0' must be stripped so school_id has no '.0' and the derived
    # 8-digit AGS stays a valid id (not '03158037.0'). Leading zeros on genuine
    # string ids are preserved (see the existing scope/typing test).
    raw = _abs_raw().copy()
    raw["SNR"] = [5009.0, 7123.0, 1.0]
    raw["AGS"] = [101000.0, 158001.0, 999000.0]
    df = build_abs_long(raw, SCOPE)
    assert set(df["school_id"]) == {"abs_5009", "abs_7123"}
    assert set(df["ags8"]) == {"03101000", "03158001"}
    assert not any("." in a for a in df["ags8"])
    assert not any("." in s for s in df["school_id"])


def test_plz_float_column_is_cleaned_to_5_digit_string():
    # pandas reads the ABS PLZ column as float when blank rows are present,
    # yielding e.g. 38122.0; str(38122.0) == "38122.0" then breaks geocoding.
    # The reader must normalise PLZ to a clean 5-digit string.
    raw = _abs_raw().copy()
    raw["PLZ"] = [38122.0, 38300.0, 99999.0]
    df = build_abs_long(raw, SCOPE)
    assert set(df["plz"]) == {"38122", "38300"}   # out-of-scope (999) dropped

    bbs = _bbs_raw().copy()
    bbs["PLZ"] = [38100.0, 0.0]
    dfb = build_bbs_long(bbs, SCOPE, ["Anzahl_BS", "Anzahl_BI", "Anzahl_FO"])
    assert dfb.iloc[0]["plz"] == "38100"
