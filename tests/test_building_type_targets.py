"""Building-type targets count households, not dwellings (issue #647, WP3 task 3).

The Zensus building-type columns count DWELLINGS; PopulationSim seeds count households, so
the three class targets of a cell must sum to the household total of the cell.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import building_type_targets as btt
from braunschweig.popsim import control_spec, folders, mid
from braunschweig.popsim import stage as popsim_stage

HH = control_spec.HH_TOTAL_CENSUS_COLUMN
BT_COLS = [name for name, _, _ in control_spec._TIER2_BUILDING_TYPE_ENTRIES]
EFH, MFH, OTHER = BT_COLS
LOG_NAME = "building_type_targets"


def _frame(rows):
    """Cells frame from ``(cell, 1km, kreis, households, efh, mfh, other)`` tuples."""
    return pd.DataFrame(
        rows,
        columns=["ZENSUS100m", folders.GEO_1KM, folders.GEO_KREIS, HH, EFH, MFH, OTHER],
    )


def test_rescaled_targets_sum_to_household_total_per_cell():
    cells = _frame([("c1", "k1", "01", 12.0, 10.0, 5.0, 1.0)])
    out = btt.rescale_building_type_to_households(cells, BT_COLS)
    assert out.loc[0, BT_COLS].tolist() == pytest.approx([7.5, 3.75, 0.75])
    assert out[BT_COLS].dtypes.eq("float64").all()


def test_cell_without_dwellings_takes_1km_shares(caplog):
    cells = _frame([
        ("c1", "k1", "01", 4.0, 0.0, 0.0, 0.0),
        ("c2", "k1", "01", 9.0, 6.0, 3.0, 0.0),
        ("c3", "k2", "01", 5.0, 0.0, 10.0, 0.0),
    ])
    with caplog.at_level(logging.INFO):
        out = btt.rescale_building_type_to_households(cells, BT_COLS)
    # c1 takes the 1 km parent shares (2/3 EFH, 1/3 MFH), c2 and c3 their own.
    assert out.loc[0, BT_COLS].tolist() == pytest.approx([4 * 2 / 3, 4 / 3, 0.0])
    assert out.loc[1, BT_COLS].tolist() == pytest.approx([6.0, 3.0, 0.0])
    assert out.loc[2, BT_COLS].tolist() == pytest.approx([0.0, 5.0, 0.0])
    # The fallback is counted: 1 of 3 cells (4 of 18 households).
    text = "\n".join(r.getMessage() for r in caplog.records if LOG_NAME in r.getMessage())
    assert "primary 2/3" in text and "fallback 1" in text
    assert "primary 14/18" in text and "fallback 4" in text


def test_partly_suppressed_class_vector_is_counted(caplog):
    """A cell with some classes NaN and some reported reads NaN as 0 and is counted.

    Defect caught: such a cell takes the own-share (primary) tier silently although one of
    its shares rests on the NaN-as-0 assumption.
    """
    cells = _frame([
        ("c1", "k1", "01", 10.0, 6.0, np.nan, 4.0),       # partly suppressed
        ("c2", "k1", "01", 5.0, 5.0, 5.0, 0.0),           # fully reported
        ("c3", "k1", "01", 3.0, np.nan, np.nan, np.nan),  # fully suppressed: 1 km tier
    ])
    with caplog.at_level(logging.INFO):
        out = btt.rescale_building_type_to_households(cells, BT_COLS)
    assert out.loc[0, BT_COLS].tolist() == pytest.approx([6.0, 0.0, 4.0])
    text = "\n".join(r.getMessage() for r in caplog.records if "partial_nan" in r.getMessage())
    # 1 of the 3 cells with households (10 of 18 households) has a partly NaN class vector.
    assert "primary 2/3" in text and "fallback 1" in text
    assert "primary 8/18" in text and "fallback 10" in text


def test_cell_and_1km_without_dwellings_take_kreis_shares(caplog):
    cells = _frame([
        ("c1", "k1", "01", 6.0, 0.0, 0.0, 0.0),
        ("c2", "k2", "01", 8.0, 3.0, 1.0, 0.0),
        ("c3", "k3", "02", 8.0, 0.0, 4.0, 4.0),
    ])
    with caplog.at_level(logging.INFO):
        out = btt.rescale_building_type_to_households(cells, BT_COLS)
    # c1 and its 1 km parent have no dwellings: Kreis 01 shares are 3/4 EFH and 1/4 MFH.
    assert out.loc[0, BT_COLS].tolist() == pytest.approx([4.5, 1.5, 0.0])
    # The Kreis-tier fallback is counted: 1 of 3 cells (6 of 22 households).
    text = "\n".join(r.getMessage() for r in caplog.records if LOG_NAME in r.getMessage())
    assert "primary 2/3" in text and "fallback 1" in text
    assert "primary 16/22" in text and "fallback 6" in text


def test_validation_and_stage_rescale_the_same_building_type_columns():
    """The validation tool (tier2-only map) and the stage (production tiers) must rescale
    the same building-type columns, or the validation target silently diverges."""
    from braunschweig.popsim.control_spec import (
        build_aggregation_map, controls_for_seed, full_catalog)

    tier2_map = build_aggregation_map(
        controls_for_seed(full_catalog(include_tiers=("tier2",)), "mid"))
    production_map = build_aggregation_map(controls_for_seed(
        full_catalog(include_tiers=("tier0", "tier1", "tier2", "tier3")), "mid"))
    entries = [name for name, _, _ in control_spec._TIER2_BUILDING_TYPE_ENTRIES]
    validation_cols = [n for n in entries if n in tier2_map]
    stage_cols = [n for n in entries if n in production_map]
    assert validation_cols and validation_cols == stage_cols


def test_no_share_at_any_level_raises():
    cells = _frame([
        ("c1", "k1", "01", 6.0, 0.0, 0.0, 0.0),
        ("c2", "k2", "01", 8.0, 0.0, np.nan, 0.0),
    ])
    with pytest.raises(ValueError, match="no dwelling count"):
        btt.rescale_building_type_to_households(cells, BT_COLS)


def test_zero_household_cell_gets_zero():
    cells = _frame([
        ("c1", "k1", "01", 0.0, 10.0, 5.0, 1.0),
        ("c2", "k1", "01", np.nan, 10.0, 5.0, 1.0),
        ("c3", "k1", "01", 3.0, 1.0, 1.0, 1.0),
    ])
    out = btt.rescale_building_type_to_households(cells, BT_COLS)
    assert out.loc[0, BT_COLS].tolist() == [0.0, 0.0, 0.0]
    assert out.loc[1, BT_COLS].tolist() == [0.0, 0.0, 0.0]
    assert out.loc[2, BT_COLS].tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_source_dwelling_columns_unchanged():
    """Through the stage helper: the raw dwelling columns stay, only the derived ones move."""
    cells = _stage_cells()
    before = cells.copy(deep=True)
    out = popsim_stage._add_aggregated_control_columns(
        cells, "catalog", "mid", ("tier2",), building_type_targets_to_households=True)
    source_cols = [c for c in before.columns if c.endswith("_Wohnung_Gebaeudetyp_Groesse_100m_Gitter")]
    assert source_cols
    pd.testing.assert_frame_equal(out[before.columns], before)
    pd.testing.assert_frame_equal(cells, before)
    assert folders.GEO_KREIS not in out.columns


def test_key_off_keeps_dwelling_sums_and_is_silent(caplog):
    cells = _stage_cells()
    with caplog.at_level(logging.DEBUG):
        off = popsim_stage._add_aggregated_control_columns(
            cells, "catalog", "mid", ("tier2",), building_type_targets_to_households=False)
    expected = cells[_EFH_SOURCES].sum(axis=1)
    np.testing.assert_array_equal(off[EFH].to_numpy(), expected.to_numpy())
    # Dwelling sums exceed the household total (the defect this feature repairs).
    assert off[BT_COLS].sum(axis=1).sum() > cells[HH].sum()
    # Silent: no counter line, no WARNING that names the feature.
    assert not [r for r in caplog.records if LOG_NAME in r.getMessage()]
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "building" in r.getMessage().lower()
                and "target" in r.getMessage().lower()]


def test_key_on_through_stage_takes_primary_path(caplog):
    cells = _stage_cells()
    with caplog.at_level(logging.INFO):
        on = popsim_stage._add_aggregated_control_columns(
            cells, "catalog", "mid", ("tier2",), building_type_targets_to_households=True)
    np.testing.assert_allclose(on[BT_COLS].sum(axis=1).to_numpy(), cells[HH].to_numpy())
    text = "\n".join(r.getMessage() for r in caplog.records if LOG_NAME in r.getMessage())
    # PRIMARY: every cell of the fixture has its own dwelling count, so no fallback.
    assert "primary 4/4" in text and "fallback 0" in text
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and LOG_NAME in r.getMessage()]


def test_integerised_targets_sum_to_household_total_per_1km_parent():
    cells = _stage_cells()
    out = popsim_stage._add_aggregated_control_columns(
        cells, "catalog", "mid", ("tier2",), building_type_targets_to_households=True)
    crosswalk = out[["ZENSUS100m", "ZENSUS1km"]]
    totals = mid.build_control_totals(out, crosswalk, BT_COLS + [HH])
    cells_100m = totals["ZENSUS100m"].merge(crosswalk, on="ZENSUS100m")
    for parent, group in cells_100m.groupby("ZENSUS1km"):
        household_parent = int(round(out.loc[out["ZENSUS1km"] == parent, HH].sum()))
        for col in BT_COLS:
            expected = int(round(out.loc[out["ZENSUS1km"] == parent, col].sum()))
            assert int(group[f"{col}_ZENSUS100m"].sum()) == expected
        together = int(sum(group[f"{col}_ZENSUS100m"].sum() for col in BT_COLS))
        # Each column is rounded separately within its parent.
        # Each column equals round(parent sum), so the gap is at most 3 x 0.5 + 0.5 = 2.
        assert abs(together - household_parent) <= 2


def test_key_is_declared_in_configure_with_default_on():
    declared = {}

    class _Recorder:
        def config(self, key, default=None, **kwargs):
            declared.setdefault(key, default)
            return default

        def stage(self, *args, **kwargs):
            return None

    popsim_stage.configure(_Recorder())
    assert declared[popsim_stage.KEY_BUILDING_TYPE_TARGETS_TO_HOUSEHOLDS] is True


def test_validation_target_is_household_based(tmp_path):
    """popsim_validation reads the same household-based building-type shares as the stage."""
    from braunschweig.analysis.popsim_validation import controls as C

    sources = {
        EFH: control_spec._TIER2_BUILDING_TYPE_ENTRIES[0][1][0],
        MFH: control_spec._TIER2_BUILDING_TYPE_ENTRIES[1][1][0],
        OTHER: control_spec._TIER2_BUILDING_TYPE_ENTRIES[2][1][0],
    }
    raw = {name.replace("_100m_Gitter", "_100m-Gitter"): 0.0 for name in
           {c for _, cols, _ in control_spec._TIER2_BUILDING_TYPE_ENTRIES for c in cols}}
    frame = pd.DataFrame({
        "GITTER_ID_100m": ["CRS3035RES100mN2689200E4331000", "CRS3035RES100mN2689300E4331100"],
        HH.replace("_100m_Gitter", "_100m-Gitter"): [100.0, 100.0],
        "RegionalSchlüssel_ARS": ["031010000000", "031010000000"],
    })
    for column, values in raw.items():
        frame[column] = values
    # Cell A: 10 dwellings, all single-family; cell B: 90 dwellings, all multi-family. Both hold
    # 100 households, so the household-based shares are 50 / 50 (dwelling-based: 10 / 90).
    frame.loc[0, sources[EFH].replace("_100m_Gitter", "_100m-Gitter")] = 10.0
    frame.loc[1, sources[MFH].replace("_100m_Gitter", "_100m-Gitter")] = 90.0
    parquet = tmp_path / C._PARQUET_SUBPATH
    parquet.parent.mkdir(parents=True)
    frame.to_parquet(parquet)
    C.clear_cells_cache()
    try:
        out = C.building_type_target(str(tmp_path)).set_index("category")["target_share"]
        # Key off: the dwelling sums are kept (10 / 90), as on the stage's OFF path.
        dwellings = C.building_type_target(
            str(tmp_path), building_type_targets_to_households=False,
        ).set_index("category")["target_share"]
    finally:
        C.clear_cells_cache()
    assert out["ein_zweifamilienhaus"] == pytest.approx(0.5)
    assert out["mehrfamilienhaus"] == pytest.approx(0.5)
    assert out["sonstiges"] == pytest.approx(0.0)
    assert dwellings["ein_zweifamilienhaus"] == pytest.approx(0.1)
    assert dwellings["mehrfamilienhaus"] == pytest.approx(0.9)


# --------------------------------------------------------------------------- fixtures

_EFH_SOURCES = list(control_spec._TIER2_BUILDING_TYPE_ENTRIES[0][1])


def _stage_cells() -> pd.DataFrame:
    """Four 100 m cells in two 1 km parents with every dwelling source column."""
    rng = np.random.default_rng(3)
    frame = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2", "c3", "c4"],
        "ZENSUS1km": ["k1", "k1", "k2", "k2"],
        mid._ARS_COLUMN: ["031010000000"] * 2 + ["031020000000"] * 2,
        HH: [7.0, 11.0, 5.0, 13.0],
    })
    for _, sources, _ in control_spec._TIER2_BUILDING_TYPE_ENTRIES:
        for col in sources:
            frame[col] = rng.integers(1, 9, size=4).astype("float64")
    return frame
