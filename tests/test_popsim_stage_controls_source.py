from __future__ import annotations

import pandas as pd
import pytest
from braunschweig.popsim import stage


def test_controls_source_catalog_renders_same_controls_as_csv() -> None:
    # controls_source == "catalog" must build controls_df from the catalog (mid seed)
    # equal to the production CSV baseline (modulo row order).
    # The committed CSV is the PRE-#320 control set, so this equivalence is a flag-OFF
    # property; the ON path adds the four fine teen-band controls by design.
    rendered = stage.build_controls_df(controls_source="catalog", seed="mid",
                                       fine_teen_age_bands=False)
    baseline = pd.read_csv("tests/fixtures/prep3_controls_baseline.csv", sep=";")
    key = ["target", "geography", "seed_table", "importance", "control_field", "expression"]
    pd.testing.assert_frame_equal(
        rendered[key].sort_values(key).reset_index(drop=True),
        baseline[key].sort_values(key).reset_index(drop=True),
        check_dtype=False,
    )


def test_controls_source_csv_reads_external_file() -> None:
    df = stage.build_controls_df(controls_source="csv",
                                 controls_path="tests/fixtures/prep3_controls_baseline.csv")
    assert list(df.columns)[:2] == ["target", "geography"]


def test_controls_source_catalog_ownership_grid_adds_nine() -> None:
    off = stage.build_controls_df(controls_source="catalog", seed="mid")
    on = stage.build_controls_df(controls_source="catalog", seed="mid", ownership_grid=True)
    assert len(on) == len(off) + 9
    new = set(on["control_field"]) - set(off["control_field"])
    assert {"OWN_CARS_0_agg_ZENSUS1km", "OWN_BIKES_4plus_agg_ZENSUS1km"} <= new
    assert (on[on["control_field"].str.startswith("OWN_")]["geography"] == "ZENSUS1km").all()


def test_controls_source_csv_with_ownership_grid_raises() -> None:
    with pytest.raises(ValueError, match="catalog"):
        stage.build_controls_df(controls_source="csv",
                                controls_path="tests/fixtures/prep3_controls_baseline.csv",
                                ownership_grid=True)


def test_base_cols_union_covers_1km_only_ownership_bases() -> None:
    """Review finding C1: bases existing only at ZENSUS1km must reach base_cols, or
    build_control_totals never writes OWN_*_ZENSUS1km. Also pins OFF byte-identity:
    without the grid, the union adds nothing beyond the ZENSUS100m list."""
    from braunschweig.popsim import mid, ownership_grid as og
    on_df = stage.build_controls_df(controls_source="catalog", seed="mid", ownership_grid=True)
    union = list(dict.fromkeys([*mid.control_base_columns(on_df, "ZENSUS100m"),
                                *mid.control_base_columns(on_df, "ZENSUS1km")]))
    assert set(og.OWNERSHIP_COLUMNS) <= set(union)
    off_df = stage.build_controls_df(controls_source="catalog", seed="mid")
    union_off = list(dict.fromkeys([*mid.control_base_columns(off_df, "ZENSUS100m"),
                                    *mid.control_base_columns(off_df, "ZENSUS1km")]))
    assert union_off == mid.control_base_columns(off_df, "ZENSUS100m")


class _FakeContext:
    """Minimal synpp ExecuteContext stand-in (mirrors test_kreis_control_stage_wiring.py's
    _FakeContext): config(key) takes NO default argument; an unresolved KREIS toggle key
    falls back to stage._KREIS_CONTROL_DEFAULT, exactly as configure() declares it."""

    def __init__(self, values):
        self._values = values

    def config(self, key):
        if key in self._values:
            return self._values[key]
        for name, toggle_key in stage._KREIS_CONTROL_TOGGLE_KEY.items():
            if key == toggle_key:
                return stage._KREIS_CONTROL_DEFAULT[name]
        raise KeyError(f"_FakeContext: no value or declared default for config key {key!r}")


def _control_config_context(**overrides):
    values = {
        stage.KEY_CONTROL_TIERS: "tier0",
        stage.KEY_SEED_DAY_FILTER: "default",
        stage.KEY_CONTROLS_SOURCE: "catalog",
        stage.KEY_EMPLOYMENT_GRID: "off",
        stage.KEY_OWNERSHIP_GRID: "on",
        stage.KEY_FINE_TEEN_AGE_BANDS: "on",
        stage.KEY_STATUS_KREIS_SHRINKAGE_N: 0.0,
        stage.KEY_EBIKE_SEED_COLUMN: "H_ANZPED",
        stage.KEY_IMPORTANCE_PROFILE: "uniform",
    }
    values.update(overrides)
    return _FakeContext(values)


def _ownership_grid_on(source_name, **overrides):
    """The ownership_grid_on element of _read_control_config's return tuple."""
    return _read_control_config_tuple(source_name, **overrides)[4]


def _read_control_config_tuple(source_name, **overrides):
    return stage._read_control_config(_control_config_context(**overrides), source_name)


def test_ownership_grid_flag_is_on_for_the_mid_source_by_default() -> None:
    assert _ownership_grid_on("mid") is True


def test_ownership_grid_flag_off_when_the_key_is_off() -> None:
    assert _ownership_grid_on("mid", **{stage.KEY_OWNERSHIP_GRID: "off"}) is False


def test_ownership_grid_flag_is_skipped_for_a_non_mid_source(caplog) -> None:
    """A default-"on" key on an ENTD run must LOG and skip, never raise.

    The nine catalog controls are ``entd: None`` and the injection guard depends on the
    MiD-only KREIS ownership entries, so the grid is inexpressible for ENTD; crashing an
    otherwise valid popsim_open run on a default-on flag would be wrong.
    """
    with caplog.at_level("INFO", logger="braunschweig.popsim.stage"):
        assert _ownership_grid_on("entd") is False
    assert any("ownership_grid_1km is 'on'" in record.getMessage()
               and "source='entd'" in record.getMessage()
               for record in caplog.records)
