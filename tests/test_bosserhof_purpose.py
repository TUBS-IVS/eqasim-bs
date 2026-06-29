import pandas as pd
import pytest
from braunschweig.data.bosserhof_purpose import load_mapping, OTHER_CLASSES


def test_load_mapping_schema_and_whitelist(tmp_path):
    csv = tmp_path / "m.csv"
    pd.DataFrame({
        "bosserhof_class": ["services", "normal office", "schools"],
        "eqasim_purpose": ["other", "work", "education"],
        "other_destination": [True, False, False],
    }).to_csv(csv, index=False)
    m = load_mapping(str(csv))
    assert list(m.columns) == ["bosserhof_class", "eqasim_purpose", "other_destination"]
    assert m["other_destination"].dtype == bool
    assert set(m.loc[m["other_destination"], "bosserhof_class"]) == {"services"}


def test_load_mapping_rejects_unknown_purpose(tmp_path):
    csv = tmp_path / "m.csv"
    pd.DataFrame({"bosserhof_class": ["x"], "eqasim_purpose": ["nonsense"],
                  "other_destination": [False]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="eqasim_purpose"):
        load_mapping(str(csv))


def test_whitelist_constant_is_the_eleven():
    assert set(OTHER_CLASSES) == {
        "services", "customer oriented services", "customer service",
        "business oriented services", "public facilities", "hospitals",
        "nursing homes", "vehicle electrical repair", "craft businesses",
        "craft courtyards", "transport"}
