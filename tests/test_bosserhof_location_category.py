import pandas as pd
import pytest

from braunschweig.data.bosserhof_location_category import (
    BUILDING_CATEGORIES, load_category_mapping)


def _write(tmp_path, rows, columns=("bosserhof_class", "location_category")):
    p = tmp_path / "map.csv"
    pd.DataFrame(rows, columns=list(columns)).to_csv(p, index=False)
    return str(p)


def test_load_valid_mapping(tmp_path):
    p = _write(tmp_path, [("large cinemas", "leisure_culture")])
    df = load_category_mapping(p)
    assert list(df.columns) == ["bosserhof_class", "location_category"]


def test_unknown_category_raises(tmp_path):
    p = _write(tmp_path, [("large cinemas", "leisure_bogus")])
    with pytest.raises(ValueError, match="location_category"):
        load_category_mapping(p)


def test_duplicate_class_raises(tmp_path):
    p = _write(tmp_path, [("services", "errand_service"), ("services", "errand_authority_medical")])
    with pytest.raises(ValueError, match="duplicate"):
        load_category_mapping(p)


def test_missing_column_raises(tmp_path):
    p = _write(tmp_path, [("x",)], columns=("bosserhof_class",))
    with pytest.raises(ValueError, match="missing"):
        load_category_mapping(p)
