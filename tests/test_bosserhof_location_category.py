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


def test_drift_detected_errand_inconsistency(tmp_path):
    """Drift detection: errand_* class with other_destination=False in purpose mapping raises."""
    # Write location mapping CSV.
    cat_rows = [("services", "errand_service")]
    cat_path = tmp_path / "map.csv"
    pd.DataFrame(cat_rows, columns=["bosserhof_class", "location_category"]).to_csv(cat_path, index=False)

    # Write purpose mapping CSV with other_destination=False for this errand class (leisure instead of other).
    purpose_rows = [
        {"bosserhof_class": "services", "eqasim_purpose": "leisure", "other_destination": False}
    ]
    purpose_path = tmp_path / "purpose.csv"
    pd.DataFrame(purpose_rows).to_csv(purpose_path, index=False)

    # Load purpose mapping.
    from braunschweig.data.bosserhof_purpose import load_mapping as load_purpose_mapping
    purpose_df = load_purpose_mapping(str(purpose_path))

    # Loading location mapping with drift should raise, naming the class.
    with pytest.raises(ValueError, match="services.*other_destination"):
        load_category_mapping(str(cat_path), purpose_df=purpose_df)


def test_class_missing_from_purpose_mapping_raises(tmp_path):
    """A class in location mapping but absent from purpose mapping raises."""
    # Write location mapping CSV.
    cat_rows = [("large cinemas", "leisure_culture")]
    cat_path = tmp_path / "map.csv"
    pd.DataFrame(cat_rows, columns=["bosserhof_class", "location_category"]).to_csv(cat_path, index=False)

    # Write purpose mapping CSV without the "large cinemas" class.
    purpose_rows = [
        {"bosserhof_class": "hotels", "eqasim_purpose": "leisure", "other_destination": False}
    ]
    purpose_path = tmp_path / "purpose.csv"
    pd.DataFrame(purpose_rows).to_csv(purpose_path, index=False)

    # Load purpose mapping.
    from braunschweig.data.bosserhof_purpose import load_mapping as load_purpose_mapping
    purpose_df = load_purpose_mapping(str(purpose_path))

    # Loading location mapping with missing class should raise.
    with pytest.raises(ValueError, match="large cinemas.*not found"):
        load_category_mapping(str(cat_path), purpose_df=purpose_df)
