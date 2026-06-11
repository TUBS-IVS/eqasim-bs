"""Tests for the shared population schema contract.

Verifies that REQUIRED_PERSON_COLUMNS contains the gap + provenance columns
added in the popsim workflow (Tasks 1-5) and that validate_person_columns
enforces their presence.
"""

import pytest

from braunschweig.population import schema


# ---------------------------------------------------------------------------
# Gap and provenance columns that build_persons must emit.
# ---------------------------------------------------------------------------

GAP_COLUMNS = [
    "age_range",
    "high_income",
    "household_size",
    "is_urban_resident",
    "pt_subscription_type",
    "socioprofessional_class",
]

PROVENANCE_COLUMNS = [
    "source_person_id",
    "source_household_id",
]


def test_schema_requires_gap_and_provenance_columns():
    """All 8 gap + provenance columns must appear in REQUIRED_PERSON_COLUMNS."""
    for col in GAP_COLUMNS + PROVENANCE_COLUMNS:
        assert col in schema.REQUIRED_PERSON_COLUMNS, (
            f"Column '{col}' missing from REQUIRED_PERSON_COLUMNS"
        )


def test_validate_rejects_frame_missing_source_person_id():
    """validate_person_columns must raise when source_person_id is absent."""
    cols = [c for c in schema.REQUIRED_PERSON_COLUMNS if c != "source_person_id"]
    with pytest.raises(schema.PopulationSchemaError) as exc:
        schema.validate_person_columns(cols)
    assert "source_person_id" in str(exc.value)


def test_validate_rejects_frame_missing_source_household_id():
    """validate_person_columns must raise when source_household_id is absent."""
    cols = [c for c in schema.REQUIRED_PERSON_COLUMNS if c != "source_household_id"]
    with pytest.raises(schema.PopulationSchemaError) as exc:
        schema.validate_person_columns(cols)
    assert "source_household_id" in str(exc.value)


def test_validate_rejects_frame_missing_gap_column():
    """validate_person_columns must raise when any gap column is absent."""
    for gap_col in GAP_COLUMNS:
        cols = [c for c in schema.REQUIRED_PERSON_COLUMNS if c != gap_col]
        with pytest.raises(schema.PopulationSchemaError) as exc:
            schema.validate_person_columns(cols)
        assert gap_col in str(exc.value), (
            f"Error message should name the missing column '{gap_col}'"
        )


def test_validate_passes_with_all_required_columns_present():
    """validate_person_columns must not raise when all required columns are present."""
    schema.validate_person_columns(schema.REQUIRED_PERSON_COLUMNS)  # must not raise
