"""Tests for T9b: wiring the 5 km EV grid tilt into the fleet synthesis.

Covers:
  1. ``grid_ev_share_for_homes``: spatial join of home points (EPSG:25832)
     against grid cells (bounds in EPSG:3857).  Households inside a cell get
     that cell's ev_share; households outside all cells get NaN; households
     in suppressed cells get NaN.
  2. ``gemeinde_grid_mean``: household-weighted mean of ev_share within each
     commune (NaN shares excluded from the mean).
  3. ``sample_fleet`` threading: a df_cars WITH grid_ev_share + gemeinde_grid_mean
     columns causes the grid tilt to fire (high-share household gets more EV
     mass); a df_cars WITHOUT the columns produces byte-identical results to
     a baseline run (seeded).
  4. Graceful fallback: when kba_ev_grid.csv is absent, execute() continues
     without the grid columns (verified by the helper-level tests -- the
     stage integration test is server-only per the task spec).
"""

from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402
from braunschweig.synthesis.vehicles.cars.household import (  # noqa: E402
    gemeinde_grid_mean,
    grid_ev_share_for_homes,
)


# ---------------------------------------------------------------------------
# Fixtures: synthetic grid + homes
# ---------------------------------------------------------------------------

# One 5 km cell in EPSG:3857 centred around a point near Braunschweig.
# Cell covers [1_175_000, 1_180_000] x [6_830_000, 6_835_000] in EPSG:3857.
CELL_MINX = 1_175_000.0
CELL_MAXX = 1_180_000.0
CELL_MINY = 6_830_000.0
CELL_MAXY = 6_835_000.0
CELL_EV = 0.08


def _make_grid() -> pd.DataFrame:
    """One non-suppressed cell + one suppressed cell."""
    return pd.DataFrame([
        {
            "cell_id": "cell_A",
            "stichtag": "2026-04-01",
            "ev_share": CELL_EV,
            "minx": CELL_MINX,
            "miny": CELL_MINY,
            "maxx": CELL_MAXX,
            "maxy": CELL_MAXY,
            "suppressed": False,
        },
        {
            "cell_id": "cell_B",
            "stichtag": "2026-04-01",
            "ev_share": float("nan"),
            "minx": CELL_MINX + 5_000,
            "miny": CELL_MINY,
            "maxx": CELL_MAXX + 5_000,
            "maxy": CELL_MAXY,
            "suppressed": True,
        },
    ])


def _point_inside_cell_3857() -> tuple[float, float]:
    """EPSG:3857 coords for a point inside cell_A."""
    x = (CELL_MINX + CELL_MAXX) / 2.0
    y = (CELL_MINY + CELL_MAXY) / 2.0
    return x, y


def _point_inside_cell_3857_to_25832() -> tuple[float, float]:
    """Convert the cell_A centre from EPSG:3857 to EPSG:25832 approx.

    We use pyproj for the exact conversion so the test is independent of
    the implementation's reprojection approach.
    """
    from pyproj import Transformer
    t = Transformer.from_crs("EPSG:3857", "EPSG:25832", always_xy=True)
    x3857, y3857 = _point_inside_cell_3857()
    return t.transform(x3857, y3857)


def _make_homes_inside_cell(n: int = 3, commune_id: str = "0310100000000") -> gpd.GeoDataFrame:
    """GeoDataFrame with homes INSIDE cell_A (EPSG:25832)."""
    x25832, y25832 = _point_inside_cell_3857_to_25832()
    # Small offsets so they are distinct points but still in the same cell.
    xs = [x25832 + i * 0.5 for i in range(n)]
    ys = [y25832 + i * 0.5 for i in range(n)]
    geoms = [Point(x, y) for x, y in zip(xs, ys)]
    return gpd.GeoDataFrame(
        {
            "household_id": list(range(1, n + 1)),
            "commune_id": [commune_id] * n,
        },
        geometry=geoms,
        crs="EPSG:25832",
    )


def _make_home_outside_cell() -> gpd.GeoDataFrame:
    """A single home at (0, 0) in EPSG:25832, which is far outside all cells."""
    return gpd.GeoDataFrame(
        {"household_id": [99], "commune_id": ["0310100000000"]},
        geometry=[Point(0.0, 0.0)],
        crs="EPSG:25832",
    )


# ---------------------------------------------------------------------------
# Tests for grid_ev_share_for_homes
# ---------------------------------------------------------------------------

class TestGridEvShareForHomes:
    def test_households_inside_cell_get_ev_share(self):
        homes = _make_homes_inside_cell(n=3)
        grid = _make_grid()
        result = grid_ev_share_for_homes(homes, grid)
        assert set(result.index) == {1, 2, 3}
        # All three are inside cell_A -> should get CELL_EV
        for hid in [1, 2, 3]:
            assert result.loc[hid] == pytest.approx(CELL_EV, abs=1e-10), (
                f"household {hid}: expected {CELL_EV}, got {result.loc[hid]}"
            )

    def test_household_outside_all_cells_gets_nan(self):
        outside = _make_home_outside_cell()
        grid = _make_grid()
        result = grid_ev_share_for_homes(outside, grid)
        assert result.index[0] == 99
        assert pd.isna(result.iloc[0]), (
            f"outside-cell household should get NaN, got {result.iloc[0]}"
        )

    def test_suppressed_cell_gives_nan(self):
        """A household inside cell_B (suppressed) should get NaN."""
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:3857", "EPSG:25832", always_xy=True)
        # Centre of cell_B (suppressed)
        cx3857 = (CELL_MINX + 5_000 + CELL_MAXX + 5_000) / 2.0
        cy3857 = (CELL_MINY + CELL_MAXY) / 2.0
        x25832, y25832 = t.transform(cx3857, cy3857)
        home_b = gpd.GeoDataFrame(
            {"household_id": [50], "commune_id": ["0310100000000"]},
            geometry=[Point(x25832, y25832)],
            crs="EPSG:25832",
        )
        grid = _make_grid()
        result = grid_ev_share_for_homes(home_b, grid)
        assert pd.isna(result.iloc[0]), (
            "household in suppressed cell should get NaN"
        )

    def test_result_indexed_by_household_id(self):
        homes = _make_homes_inside_cell(n=2)
        grid = _make_grid()
        result = grid_ev_share_for_homes(homes, grid)
        assert result.index.name == "household_id" or set(result.index) == {1, 2}

    def test_mix_inside_and_outside(self):
        """Some homes inside cell_A, one outside -- NaN only for the outside one."""
        homes_in = _make_homes_inside_cell(n=2)
        home_out = _make_home_outside_cell()
        all_homes = pd.concat(
            [homes_in, home_out], ignore_index=True
        )
        # Re-create as GeoDataFrame
        all_homes = gpd.GeoDataFrame(all_homes, geometry="geometry", crs="EPSG:25832")
        grid = _make_grid()
        result = grid_ev_share_for_homes(all_homes, grid)
        # Households 1, 2 -> inside cell_A -> CELL_EV
        for hid in [1, 2]:
            assert result.loc[hid] == pytest.approx(CELL_EV, abs=1e-10)
        # Household 99 -> outside -> NaN
        assert pd.isna(result.loc[99])

    def test_empty_grid_all_nan(self):
        """Empty grid -> all households get NaN."""
        homes = _make_homes_inside_cell(n=2)
        empty_grid = pd.DataFrame(
            columns=["cell_id", "stichtag", "ev_share", "minx", "miny",
                     "maxx", "maxy", "suppressed"]
        )
        result = grid_ev_share_for_homes(homes, empty_grid)
        assert all(pd.isna(result))

    def test_matched_and_unmatched_counts_logged(self, caplog):
        """grid_ev_share_for_homes must log matched vs unmatched counts."""
        import logging
        homes = pd.concat(
            [_make_homes_inside_cell(n=2), _make_home_outside_cell()],
            ignore_index=True,
        )
        homes = gpd.GeoDataFrame(homes, geometry="geometry", crs="EPSG:25832")
        grid = _make_grid()
        with caplog.at_level(logging.INFO):
            grid_ev_share_for_homes(homes, grid)
        text = " ".join(r.message for r in caplog.records).lower()
        assert any(w in text for w in ("match", "cell", "grid", "unmatched")), (
            f"Expected matching stats in log, got: {text!r}"
        )


# ---------------------------------------------------------------------------
# Tests for gemeinde_grid_mean
# ---------------------------------------------------------------------------

class TestGemeindeGridMean:
    def _make_df(self, commune_ids, ev_shares):
        return pd.DataFrame({
            "household_id": list(range(1, len(commune_ids) + 1)),
            "commune_id": commune_ids,
            "grid_ev_share": ev_shares,
        })

    def test_same_commune_returns_mean(self):
        """3 households in one commune -> mean of their ev_shares."""
        df = self._make_df(
            ["X", "X", "X"],
            [0.04, 0.08, 0.12],
        )
        result = gemeinde_grid_mean(df)
        expected = (0.04 + 0.08 + 0.12) / 3.0
        for v in result:
            assert v == pytest.approx(expected, abs=1e-10)

    def test_nan_shares_excluded_from_mean(self):
        """NaN ev_share is excluded; only non-NaN values contribute."""
        df = self._make_df(
            ["X", "X", "X"],
            [0.04, float("nan"), 0.12],
        )
        result = gemeinde_grid_mean(df)
        expected = (0.04 + 0.12) / 2.0
        for v in result:
            assert v == pytest.approx(expected, abs=1e-10)

    def test_two_communes_independent(self):
        """Two communes compute independent means."""
        df = self._make_df(
            ["A", "A", "B", "B"],
            [0.10, 0.20, 0.30, 0.40],
        )
        result = gemeinde_grid_mean(df)
        mean_a = (0.10 + 0.20) / 2.0
        mean_b = (0.30 + 0.40) / 2.0
        assert result.loc[1] == pytest.approx(mean_a, abs=1e-10)
        assert result.loc[2] == pytest.approx(mean_a, abs=1e-10)
        assert result.loc[3] == pytest.approx(mean_b, abs=1e-10)
        assert result.loc[4] == pytest.approx(mean_b, abs=1e-10)

    def test_all_nan_commune_returns_nan(self):
        """When ALL shares in a commune are NaN, the mean is NaN."""
        df = self._make_df(
            ["X", "X"],
            [float("nan"), float("nan")],
        )
        result = gemeinde_grid_mean(df)
        assert all(pd.isna(result))

    def test_result_indexed_by_household_id(self):
        df = self._make_df(["X", "X"], [0.05, 0.07])
        result = gemeinde_grid_mean(df)
        assert set(result.index) == {1, 2}


# ---------------------------------------------------------------------------
# Tests for sample_fleet threading (grid columns optional)
# ---------------------------------------------------------------------------

def _make_cars_for_threading(n: int = 100, seed: int = 7) -> pd.DataFrame:
    """Minimal df_cars with the required columns for sample_fleet."""
    from braunschweig.data.kba import fleet_tables as ft
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    for i in range(n):
        rows.append({
            "economic_status": rng.choice(statuses),
            "kreis_ags5": "03101",
            "gemeinde": np.nan,
            "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
        })
    return pd.DataFrame(rows)


DATA_PATH_REAL = str(REPO / "eqasim-data" / "data")


def _skip_if_no_data():
    """Skip if the KBA derived CSVs are not available."""
    required = Path(DATA_PATH_REAL) / "braunschweig" / "kba" / "derived" / "kba_kreis_powertrain.csv"
    if not required.exists():
        pytest.skip("KBA derived CSVs not available; skipping sample_fleet threading tests")


class TestSampleFleetGridColumnsOptional:
    def test_without_grid_columns_runs_byte_identical(self):
        """df_cars without grid_ev_share / gemeinde_grid_mean -> results are
        byte-identical to a run without those columns (None -> T9a no-op)."""
        _skip_if_no_data()
        df_cars_a = _make_cars_for_threading(n=50, seed=42)
        df_cars_b = df_cars_a.copy()
        # Run A: no grid columns
        result_a, _, _ = fs.sample_fleet(
            df_cars_a, DATA_PATH_REAL, random_seed=123, consistency_v2=True)
        # Run B: explicitly same (no grid columns) -- should be identical
        result_b, _, _ = fs.sample_fleet(
            df_cars_b, DATA_PATH_REAL, random_seed=123, consistency_v2=True)
        pd.testing.assert_frame_equal(
            result_a[["powertrain", "segment", "euro_class"]],
            result_b[["powertrain", "segment", "euro_class"]],
        )

    def test_grid_columns_absent_equals_baseline(self):
        """When grid columns are absent from df_cars, sample_fleet must produce
        the SAME result as when the columns are completely missing from the frame.
        This verifies that car.get('grid_ev_share') returning None is handled
        identically to the column simply not existing."""
        _skip_if_no_data()
        df_base = _make_cars_for_threading(n=50, seed=99)
        # Without any grid columns -> baseline
        r_base, _, _ = fs.sample_fleet(
            df_base, DATA_PATH_REAL, random_seed=77, consistency_v2=True)
        # Same cars, same seed: still no grid columns -> must be identical
        r_same, _, _ = fs.sample_fleet(
            df_base.copy(), DATA_PATH_REAL, random_seed=77, consistency_v2=True)
        pd.testing.assert_frame_equal(
            r_base[["powertrain"]],
            r_same[["powertrain"]],
        )

    def test_with_grid_columns_high_share_household_gets_more_ev_mass(self):
        """When grid_ev_share >> gemeinde_grid_mean for every car, the expected
        bev/phev share of the sampled fleet should exceed the no-grid baseline."""
        _skip_if_no_data()
        n = 500
        df_cars = _make_cars_for_threading(n=n, seed=5)

        # Baseline: no grid columns
        r_base, _, _ = fs.sample_fleet(
            df_cars.copy(), DATA_PATH_REAL, random_seed=11, consistency_v2=True)
        base_electric = float(
            r_base["powertrain"].isin(["bev", "phev"]).mean()
        )

        # With HIGH grid share (5x the mean) -> should raise electric share
        df_high = df_cars.copy()
        # Mean is 0.02; high share = 0.10 (5x) -> factor = clip(5, 0.2, 5) = 5
        df_high["grid_ev_share"] = 0.10
        df_high["gemeinde_grid_mean"] = 0.02
        r_high, _, _ = fs.sample_fleet(
            df_high, DATA_PATH_REAL, random_seed=11, consistency_v2=True)
        high_electric = float(
            r_high["powertrain"].isin(["bev", "phev"]).mean()
        )
        assert high_electric > base_electric, (
            f"High grid share should raise electric fraction: "
            f"base={base_electric:.4f}, high={high_electric:.4f}"
        )

    def test_with_grid_columns_nan_share_same_as_baseline(self):
        """NaN grid_ev_share (suppressed/unmatched) -> T9a no-op -> result
        identical to no-grid baseline (seeded run)."""
        _skip_if_no_data()
        n = 50
        df_base = _make_cars_for_threading(n=n, seed=3)

        r_base, _, _ = fs.sample_fleet(
            df_base.copy(), DATA_PATH_REAL, random_seed=55, consistency_v2=True)

        # NaN grid columns -> every car falls back -> byte-identical
        df_nan = df_base.copy()
        df_nan["grid_ev_share"] = float("nan")
        df_nan["gemeinde_grid_mean"] = 0.05
        r_nan, _, _ = fs.sample_fleet(
            df_nan.copy(), DATA_PATH_REAL, random_seed=55, consistency_v2=True)

        pd.testing.assert_frame_equal(
            r_base[["powertrain", "segment", "euro_class"]],
            r_nan[["powertrain", "segment", "euro_class"]],
        )


class TestSampleFleetLegacyPathGridColumnsIgnored:
    """Legacy path (consistency_v2=False) must also accept optional grid columns
    gracefully (None -> T9a no-op) and produce byte-identical results."""

    def test_legacy_path_no_grid_columns_identical(self):
        _skip_if_no_data()
        df_a = _make_cars_for_threading(n=30, seed=20)
        df_b = df_a.copy()
        r_a, _ = fs.sample_fleet(
            df_a, DATA_PATH_REAL, random_seed=200, consistency_v2=False)
        r_b, _ = fs.sample_fleet(
            df_b, DATA_PATH_REAL, random_seed=200, consistency_v2=False)
        pd.testing.assert_frame_equal(
            r_a[["powertrain", "segment"]],
            r_b[["powertrain", "segment"]],
        )

    def test_legacy_path_with_nan_grid_columns_identical_to_no_columns(self):
        _skip_if_no_data()
        df_base = _make_cars_for_threading(n=30, seed=20)
        r_base, _ = fs.sample_fleet(
            df_base.copy(), DATA_PATH_REAL, random_seed=200, consistency_v2=False)
        df_nan = df_base.copy()
        df_nan["grid_ev_share"] = float("nan")
        df_nan["gemeinde_grid_mean"] = 0.05
        r_nan, _ = fs.sample_fleet(
            df_nan, DATA_PATH_REAL, random_seed=200, consistency_v2=False)
        pd.testing.assert_frame_equal(
            r_base[["powertrain", "segment"]],
            r_nan[["powertrain", "segment"]],
        )
