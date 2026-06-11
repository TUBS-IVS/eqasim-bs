"""Unit tests for braunschweig.analysis.simwrapper.commuters.

Synthetic data only; no large external datasets required.
All geometry is EPSG:25832 (nominal unit: metre) but the numbers
are tiny (unit squares) so only topology matters, not accuracy.
"""
from __future__ import annotations

import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon, Point

from braunschweig.analysis.simwrapper.commuters import (
    commute_matrix_from_record,
    commute_matrix_from_synthesis,
    commuter_balance,
    top_relations,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

ZONES_3 = ["03101", "03102", "external"]

# Matrix: rows = origin, cols = destination
# 03101 -> 03101: 10, 03101 -> 03102: 2, 03101 -> external: 1
# 03102 -> 03101: 3, 03102 -> 03102: 8, 03102 -> external: 0
# external -> 03101: 1, external -> 03102: 0, external -> external: 0
MATRIX_3 = [
    [10, 2, 1],
    [3,  8, 0],
    [1,  0, 0],
]


def _make_record(zones=ZONES_3, matrix=MATRIX_3, purpose="work", od_key="od_matrix"):
    """Build a minimal run record with the given od_matrix structure."""
    return {
        "matsim": {
            od_key: {
                "zones": zones,
                "zone_names": ["Braunschweig", "Salzgitter", "Outside ZGB"],
                "purposes": [purpose],
                "matrices": {purpose: matrix},
            }
        }
    }


# ---------------------------------------------------------------------------
# commute_matrix_from_record
# ---------------------------------------------------------------------------

class TestCommuteMatrixFromRecord:
    def test_returns_zones_and_matrix_for_work(self):
        record = _make_record()
        result = commute_matrix_from_record(record, purpose="work")
        assert result is not None
        zones, matrix = result
        assert zones == ZONES_3
        assert matrix == MATRIX_3

    def test_returns_none_when_od_matrix_absent(self):
        record = {"matsim": {}}
        assert commute_matrix_from_record(record, purpose="work") is None

    def test_returns_none_when_matsim_key_absent(self):
        assert commute_matrix_from_record({}, purpose="work") is None

    def test_returns_none_when_purpose_absent(self):
        record = _make_record(purpose="work")
        # ask for "education" which is not in matrices
        assert commute_matrix_from_record(record, purpose="education") is None

    def test_returns_none_when_matrices_key_absent(self):
        record = {"matsim": {"od_matrix": {"zones": [], "purposes": [], "matrices": {}}}}
        assert commute_matrix_from_record(record, purpose="work") is None

    def test_default_purpose_is_work(self):
        record = _make_record()
        result_default = commute_matrix_from_record(record)
        result_explicit = commute_matrix_from_record(record, purpose="work")
        assert result_default == result_explicit

    def test_returns_copy_not_reference(self):
        """Mutating the returned list must not affect the original record."""
        record = _make_record()
        zones, matrix = commute_matrix_from_record(record)
        matrix[0][0] = 999
        original = record["matsim"]["od_matrix"]["matrices"]["work"]
        assert original[0][0] == 10


# ---------------------------------------------------------------------------
# commuter_balance
# ---------------------------------------------------------------------------

class TestCommuterBalance:
    """
    Reference calculations for ZONES_3 / MATRIX_3:

    Zones: 03101 (idx 0), 03102 (idx 1), external (idx 2, external_index=2)
    Real Kreise: 03101, 03102

    For 03101 (j=0):
        binnen           = matrix[0][0] = 10
        einpendler_intern = matrix[1][0] = 3        (from real i != 0)
        einpendler_extern = matrix[2][0] = 1        (external row)
        einpendler_gesamt = 3 + 1 = 4
        auspendler       = matrix[0][1] + matrix[0][2] = 2 + 1 = 3
        netto            = 4 - 3 = 1

    For 03102 (j=1):
        binnen           = matrix[1][1] = 8
        einpendler_intern = matrix[0][1] = 2        (from real i != 1)
        einpendler_extern = matrix[2][1] = 0
        einpendler_gesamt = 2 + 0 = 2
        auspendler       = matrix[1][0] + matrix[1][2] = 3 + 0 = 3
        netto            = 2 - 3 = -1
    """

    def setup_method(self):
        self.df = commuter_balance(ZONES_3, MATRIX_3)

    def test_output_has_expected_columns(self):
        expected = {"ars5", "binnen", "einpendler_intern", "einpendler_extern",
                    "einpendler_gesamt", "auspendler", "netto"}
        assert set(self.df.columns) == expected

    def test_only_real_kreise_in_output(self):
        """The 'external' pseudo-zone must not appear as its own row."""
        assert "external" not in self.df["ars5"].values
        assert len(self.df) == 2

    def test_sorted_by_ars5(self):
        assert list(self.df["ars5"]) == ["03101", "03102"]

    def test_binnen_03101(self):
        row = self.df[self.df["ars5"] == "03101"].iloc[0]
        assert row["binnen"] == 10

    def test_einpendler_intern_03101(self):
        row = self.df[self.df["ars5"] == "03101"].iloc[0]
        assert row["einpendler_intern"] == 3

    def test_einpendler_extern_03101(self):
        row = self.df[self.df["ars5"] == "03101"].iloc[0]
        assert row["einpendler_extern"] == 1

    def test_einpendler_gesamt_03101(self):
        row = self.df[self.df["ars5"] == "03101"].iloc[0]
        assert row["einpendler_gesamt"] == 4

    def test_auspendler_03101(self):
        row = self.df[self.df["ars5"] == "03101"].iloc[0]
        assert row["auspendler"] == 3

    def test_netto_03101(self):
        row = self.df[self.df["ars5"] == "03101"].iloc[0]
        assert row["netto"] == 1

    def test_binnen_03102(self):
        row = self.df[self.df["ars5"] == "03102"].iloc[0]
        assert row["binnen"] == 8

    def test_einpendler_intern_03102(self):
        row = self.df[self.df["ars5"] == "03102"].iloc[0]
        assert row["einpendler_intern"] == 2

    def test_einpendler_extern_03102(self):
        row = self.df[self.df["ars5"] == "03102"].iloc[0]
        assert row["einpendler_extern"] == 0

    def test_einpendler_gesamt_03102(self):
        row = self.df[self.df["ars5"] == "03102"].iloc[0]
        assert row["einpendler_gesamt"] == 2

    def test_auspendler_03102(self):
        row = self.df[self.df["ars5"] == "03102"].iloc[0]
        assert row["auspendler"] == 3

    def test_netto_03102(self):
        row = self.df[self.df["ars5"] == "03102"].iloc[0]
        assert row["netto"] == -1

    def test_no_external_row(self):
        """Explicit guard: external pseudo-zone never appears as its own row."""
        assert "external" not in self.df["ars5"].tolist()

    def test_result_is_dataframe(self):
        assert isinstance(self.df, pd.DataFrame)

    def test_dtype_is_integer_for_counts(self):
        for col in ["binnen", "einpendler_intern", "einpendler_extern",
                    "einpendler_gesamt", "auspendler"]:
            assert pd.api.types.is_integer_dtype(self.df[col]), f"{col} should be int"


# ---------------------------------------------------------------------------
# top_relations
# ---------------------------------------------------------------------------

class TestTopRelations:
    def setup_method(self):
        self.df = top_relations(ZONES_3, MATRIX_3, n=3)

    def test_output_has_expected_columns(self):
        assert set(self.df.columns) == {"from", "to", "value"}

    def test_diagonal_excluded(self):
        """No within-zone flow (from == to) in output."""
        assert (self.df["from"] != self.df["to"]).all()

    def test_sorted_descending(self):
        values = list(self.df["value"])
        assert values == sorted(values, reverse=True), "rows must be sorted desc by value"

    def test_top_flow_is_03102_to_03101(self):
        """The highest off-diagonal flow is matrix[1][0] = 3 (03102 -> 03101)."""
        top = self.df.iloc[0]
        assert top["from"] == "03102"
        assert top["to"] == "03101"
        assert top["value"] == 3

    def test_n_limits_rows(self):
        df_1 = top_relations(ZONES_3, MATRIX_3, n=1)
        assert len(df_1) == 1

    def test_n_larger_than_available_relations(self):
        """When n > available off-diagonal flows, return all available."""
        df_all = top_relations(ZONES_3, MATRIX_3, n=100)
        # 3x3 matrix has 9 cells, 3 diagonal -> at most 6 off-diagonal;
        # but some are zero; non-zero off-diagonal: (03101,03102)=2,(03101,ext)=1,
        # (03102,03101)=3,(ext,03101)=1 -> 4 non-zero off-diagonal cells
        # Depending on whether zeros are included, at most 6 rows, at least 4.
        assert len(df_all) <= 6
        assert len(df_all) >= 1

    def test_values_are_positive_integers(self):
        """Returned values must be positive integers (zero-value rows excluded or included, but all >= 0)."""
        assert (self.df["value"] >= 0).all()

    def test_result_is_dataframe(self):
        assert isinstance(self.df, pd.DataFrame)


# ---------------------------------------------------------------------------
# commute_matrix_from_synthesis
# ---------------------------------------------------------------------------

def _make_kreise_gdf():
    """Two unit-square Kreise in EPSG:25832 (nominal coords, topology matters)."""
    # Kreis A: x in [0,1], y in [0,1]  -> ars5 = "03101"
    # Kreis B: x in [1,2], y in [0,1]  -> ars5 = "03102"
    poly_a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    poly_b = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
    return gpd.GeoDataFrame(
        {"ars5": ["03101", "03102"]},
        geometry=[poly_a, poly_b],
        crs="EPSG:25832",
    )


def _make_commutes_gdf(lines: list[LineString]) -> gpd.GeoDataFrame:
    """Wrap a list of LineStrings in a GeoDataFrame with CRS EPSG:25832."""
    return gpd.GeoDataFrame(geometry=lines, crs="EPSG:25832")


class TestCommuteMatrixFromSynthesis:
    """
    Three LineStrings:
      L1: (0.2,0.2) -> (0.8,0.8)  home=03101, work=03101  -> within 03101
      L2: (0.2,0.2) -> (1.5,0.5)  home=03101, work=03102  -> 03101 -> 03102
      L3: (0.2,0.2) -> (5.0,5.0)  home=03101, work=outside -> 03101 -> external

    Expected matrix (zones sorted ars5 + "external"):
       zones = ["03101", "03102", "external"]
       03101->03101: 1
       03101->03102: 1
       03101->external: 1
       all other cells: 0
    """

    def setup_method(self):
        l1 = LineString([(0.2, 0.2), (0.8, 0.8)])
        l2 = LineString([(0.2, 0.2), (1.5, 0.5)])
        l3 = LineString([(0.2, 0.2), (5.0, 5.0)])
        self.kreise = _make_kreise_gdf()
        self.commutes = _make_commutes_gdf([l1, l2, l3])
        zones, matrix = commute_matrix_from_synthesis(self.commutes, self.kreise)
        self.zones = zones
        self.matrix = matrix

    def test_zones_sorted_real_kreise_then_external(self):
        assert self.zones == ["03101", "03102", "external"]

    def test_matrix_shape(self):
        n = len(self.zones)
        assert len(self.matrix) == n
        assert all(len(row) == n for row in self.matrix)

    def test_within_03101_count(self):
        idx = self.zones.index("03101")
        assert self.matrix[idx][idx] == 1

    def test_03101_to_03102_count(self):
        i = self.zones.index("03101")
        j = self.zones.index("03102")
        assert self.matrix[i][j] == 1

    def test_03101_to_external_count(self):
        i = self.zones.index("03101")
        j = self.zones.index("external")
        assert self.matrix[i][j] == 1

    def test_other_cells_zero(self):
        """All cells not set above must be 0."""
        idx_a = self.zones.index("03101")
        idx_b = self.zones.index("03102")
        idx_e = self.zones.index("external")
        for i, row in enumerate(self.matrix):
            for j, val in enumerate(row):
                if (i, j) in {(idx_a, idx_a), (idx_a, idx_b), (idx_a, idx_e)}:
                    continue
                assert val == 0, f"Expected 0 at ({i},{j}), got {val}"

    def test_matrix_values_are_integers(self):
        for row in self.matrix:
            for val in row:
                assert isinstance(val, int), f"Expected int, got {type(val)}"

    def test_empty_commutes_returns_zero_matrix(self):
        empty = _make_commutes_gdf([])
        zones, matrix = commute_matrix_from_synthesis(empty, self.kreise)
        assert zones == ["03101", "03102", "external"]
        for row in matrix:
            assert all(v == 0 for v in row)

    def test_home_endpoint_classified_as_start(self):
        """Only the START of the LineString is the home; a reversed line gives different result."""
        # L_rev: home=03102, work=03101  -> 03102->03101 = 1
        l_rev = LineString([(1.5, 0.5), (0.2, 0.2)])
        commutes = _make_commutes_gdf([l_rev])
        zones, matrix = commute_matrix_from_synthesis(commutes, self.kreise)
        i = zones.index("03102")
        j = zones.index("03101")
        assert matrix[i][j] == 1
        # and 03101->03102 must be 0
        assert matrix[zones.index("03101")][zones.index("03102")] == 0
