"""Phase 1 contract tests for the population.method switch.

Covers the fail-fast config validator, the producer selector, and the shared
output-schema contract. All are pure (no data, no pipeline run), so they run in
the default unit-test gate.
"""

from __future__ import annotations

import pytest

from braunschweig.population import config as cfg
from braunschweig.population import schema
from braunschweig.population import selector
from braunschweig.population.methods import (
    DEFAULT_POPULATION_METHOD,
    POPSIM_MID,
    POPSIM_OPEN,
    POPULATION_METHODS,
    SIMPLE_IPF_OPEN,
)


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def test_default_method_is_simple_ipf_open():
    """An empty config validates to the behaviour-preserving default."""
    result = cfg.validate_population_config({})
    assert result.method == SIMPLE_IPF_OPEN
    assert DEFAULT_POPULATION_METHOD == SIMPLE_IPF_OPEN
    assert result.mid_raw_path is None


@pytest.mark.parametrize("method", POPULATION_METHODS)
def test_valid_methods_accepted(method):
    config = {cfg.KEY_METHOD: method}
    if method in (POPSIM_OPEN, POPSIM_MID):
        config[cfg.KEY_CELLS_1KM] = "/data/grid_1km.parquet"
        config[cfg.KEY_CELLS_100M] = "/data/grid_100m.parquet"
    if method == POPSIM_MID:
        config[cfg.KEY_MID_RAW] = "/data/mid2023_raw"
    result = cfg.validate_population_config(config)
    assert result.method == method


def test_invalid_method_raises_no_silent_fallback():
    """A present-but-invalid method must raise, never fall back to the default."""
    with pytest.raises(cfg.PopulationConfigError) as exc:
        cfg.validate_population_config({cfg.KEY_METHOD: "ipf"})
    assert "not a valid population method" in str(exc.value)
    # The valid options are named so the user can fix it.
    assert "simple_ipf_open" in str(exc.value)


def test_simple_ipf_open_does_not_require_mid():
    """The open IPF baseline validates with no MiD path."""
    result = cfg.validate_population_config({cfg.KEY_METHOD: SIMPLE_IPF_OPEN})
    assert result.method == SIMPLE_IPF_OPEN
    assert result.mid_raw_path is None
    assert result.uses_mid_raw is False


def test_popsim_open_does_not_require_mid():
    """popsim_open must validate without MiD and stay provably MiD-free."""
    result = cfg.validate_population_config(
        {
            cfg.KEY_METHOD: POPSIM_OPEN,
            cfg.KEY_CELLS_1KM: "/data/grid_1km.parquet",
            cfg.KEY_CELLS_100M: "/data/grid_100m.parquet",
        }
    )
    assert result.method == POPSIM_OPEN
    assert result.mid_raw_path is None


def test_popsim_open_ignores_any_supplied_mid_path():
    """Even if a MiD path is present, popsim_open must not carry it (no leak)."""
    result = cfg.validate_population_config(
        {
            cfg.KEY_METHOD: POPSIM_OPEN,
            cfg.KEY_CELLS_1KM: "/data/grid_1km.parquet",
            cfg.KEY_CELLS_100M: "/data/grid_100m.parquet",
            cfg.KEY_MID_RAW: "/data/mid2023_raw",
        }
    )
    assert result.mid_raw_path is None


def test_popsim_mid_requires_mid_path():
    """popsim_mid without a MiD path is a hard, clear error."""
    with pytest.raises(cfg.PopulationConfigError) as exc:
        cfg.validate_population_config(
            {
                cfg.KEY_METHOD: POPSIM_MID,
                cfg.KEY_CELLS_1KM: "/data/grid_1km.parquet",
                cfg.KEY_CELLS_100M: "/data/grid_100m.parquet",
            }
        )
    assert "requires the restricted MiD 2023 raw path" in str(exc.value)


def test_popsim_methods_require_cell_paths():
    with pytest.raises(cfg.PopulationConfigError) as exc:
        cfg.validate_population_config({cfg.KEY_METHOD: POPSIM_OPEN})
    assert "Zensus grid paths" in str(exc.value)


def test_require_paths_exist_checks_mid_dir(tmp_path):
    """With require_paths_exist, a non-existent MiD dir fails fast."""
    grid_1km = tmp_path / "grid_1km.parquet"
    grid_100m = tmp_path / "grid_100m.parquet"
    grid_1km.write_bytes(b"")
    grid_100m.write_bytes(b"")
    config = {
        cfg.KEY_METHOD: POPSIM_MID,
        cfg.KEY_CELLS_1KM: str(grid_1km),
        cfg.KEY_CELLS_100M: str(grid_100m),
        cfg.KEY_MID_RAW: str(tmp_path / "does_not_exist"),
    }
    with pytest.raises(cfg.PopulationConfigError) as exc:
        cfg.validate_population_config(config, require_paths_exist=True)
    assert "mid_raw_path is not a directory" in str(exc.value)


def test_require_paths_exist_passes_when_present(tmp_path):
    grid_1km = tmp_path / "grid_1km.parquet"
    grid_100m = tmp_path / "grid_100m.parquet"
    mid_dir = tmp_path / "mid2023_raw"
    grid_1km.write_bytes(b"")
    grid_100m.write_bytes(b"")
    mid_dir.mkdir()
    config = {
        cfg.KEY_METHOD: POPSIM_MID,
        cfg.KEY_CELLS_1KM: str(grid_1km),
        cfg.KEY_CELLS_100M: str(grid_100m),
        cfg.KEY_MID_RAW: str(mid_dir),
    }
    result = cfg.validate_population_config(config, require_paths_exist=True)
    assert result.mid_raw_path == str(mid_dir)


# ---------------------------------------------------------------------------
# Producer selector
# ---------------------------------------------------------------------------

def test_simple_ipf_open_resolves_to_existing_producer():
    assert selector.resolve_population_producer(SIMPLE_IPF_OPEN) == (
        "braunschweig.ipf.attributed"
    )


def test_popsim_mid_resolves_to_popsim_stage():
    assert selector.resolve_population_producer(POPSIM_MID) == "braunschweig.popsim.stage"


def test_popsim_open_resolves_to_popsim_stage():
    """Phase 3: popsim_open is now wired and must resolve to braunschweig.popsim.stage.

    Before Phase 3 this test expected PopulationMethodNotImplemented.  Now that
    popsim_open is wired (same stage as popsim_mid, source="entd" config key), the
    selector must return the stage name without raising.
    """
    producer = selector.resolve_population_producer(POPSIM_OPEN)
    assert producer == "braunschweig.popsim.stage", (
        f"popsim_open must resolve to 'braunschweig.popsim.stage', got {producer!r}"
    )


def test_selector_rejects_unknown_method():
    with pytest.raises(ValueError):
        selector.resolve_population_producer("nonsense")


# ---------------------------------------------------------------------------
# Output-schema contract
# ---------------------------------------------------------------------------

def test_required_person_columns_include_simulation_inputs():
    required = set(schema.required_person_columns())
    for col in ("person_id", "household_id", "age", "sex", "employed",
                "household_income", "car_availability", "bicycle_availability",
                "has_license", "has_pt_subscription"):
        assert col in required


def test_income_elastic_feature_promotes_income_eur():
    base = set(schema.required_person_columns())
    assert "household_income_eur" not in base
    promoted = set(schema.required_person_columns(["income_elastic_mode_choice"]))
    assert "household_income_eur" in promoted


def test_validate_person_columns_passes_on_full_superset():
    columns = list(schema.REQUIRED_PERSON_COLUMNS) + list(schema.OPTIONAL_PERSON_COLUMNS)
    schema.validate_person_columns(columns)  # must not raise


def test_validate_person_columns_fails_on_missing_required():
    columns = [c for c in schema.REQUIRED_PERSON_COLUMNS if c != "car_availability"]
    with pytest.raises(schema.PopulationSchemaError) as exc:
        schema.validate_person_columns(columns)
    assert "car_availability" in str(exc.value)


def test_validate_household_columns():
    schema.validate_household_columns(schema.REQUIRED_HOUSEHOLD_COLUMNS)
    with pytest.raises(schema.PopulationSchemaError):
        schema.validate_household_columns(["household_id"])
