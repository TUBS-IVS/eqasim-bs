"""Tests for Phase 4A: RegioStaR-2 stratum-key plumbing.

Covers:
- ``braunschweig.popsim.stratum``: cell_urban_class_from_rs7 and entd_urban_class.
- ``braunschweig.popsim.mid._EXTRA_CELL_COLUMNS`` carries ``"RegioStaR7"``.
- ``braunschweig.popsim.mid.MID_HOUSEHOLD_ATTR_COLS`` carries ``"RegioStaR7"``.
- ``load_mid_seed`` / ``select_seed_columns`` pass ``RegioStaR7`` onto seed households.
- ENTD: ``urban_type`` is forwarded from the households frame to persons via
  ``_HH_JOIN_COLS``.

No full pipeline execution is needed: all tests use small synthetic fixtures
and column-membership assertions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.popsim.stratum import (  # noqa: E402
    URBAN_CLASSES,
    cell_urban_class_from_rs7,
    entd_urban_class,
)
from braunschweig.popsim import mid  # noqa: E402
from braunschweig.popsim import seed as seedmod  # noqa: E402
from braunschweig.popsim.sources.entd import _HH_JOIN_COLS  # noqa: E402


# ---------------------------------------------------------------------------
# (a) Stratum helper: cell_urban_class_from_rs7
# ---------------------------------------------------------------------------

class TestCellUrbanClassFromRs7:
    """cell_urban_class_from_rs7 reuses regiostar2_label: all RS7 codes covered."""

    def test_urban_codes_return_urban(self):
        for code in (71, 72, 73, 74):
            assert cell_urban_class_from_rs7(code) == "urban", (
                f"RS7 {code} should be 'urban'"
            )

    def test_rural_codes_return_rural(self):
        for code in (75, 76, 77):
            assert cell_urban_class_from_rs7(code) == "rural", (
                f"RS7 {code} should be 'rural'"
            )

    def test_boundary_74_is_urban(self):
        # RS7 74 is the last urban code (Kleinstädtischer, dörflicher Raum
        # in the STADTREGION); 75 is the first rural code.
        assert cell_urban_class_from_rs7(74) == "urban"
        assert cell_urban_class_from_rs7(75) == "rural"

    def test_integer_castable_input(self):
        # regiostar2_label accepts any int-castable value (e.g. numpy int64).
        import numpy as np
        assert cell_urban_class_from_rs7(np.int64(72)) == "urban"
        assert cell_urban_class_from_rs7(np.int64(77)) == "rural"

    def test_invalid_code_raises(self):
        with pytest.raises(ValueError):
            cell_urban_class_from_rs7(70)
        with pytest.raises(ValueError):
            cell_urban_class_from_rs7(78)


# ---------------------------------------------------------------------------
# (a) Stratum helper: entd_urban_class
# ---------------------------------------------------------------------------

class TestEntdUrbanClass:
    """entd_urban_class wraps URBAN_TYPE_TO_URBAN_CLASS lookup."""

    def test_urban_categories(self):
        for cat in ("central_city", "suburb", "isolated_city"):
            assert entd_urban_class(cat) == "urban", (
                f"ENTD urban_type '{cat}' should map to 'urban'"
            )

    def test_none_is_rural(self):
        assert entd_urban_class("none") == "rural"

    def test_unknown_category_raises(self):
        with pytest.raises(KeyError):
            entd_urban_class("unknown_category")


# ---------------------------------------------------------------------------
# Shared: URBAN_CLASSES constant
# ---------------------------------------------------------------------------

def test_urban_classes_constant():
    assert set(URBAN_CLASSES) == {"urban", "rural"}


# ---------------------------------------------------------------------------
# (b) RegioStaR7 in _EXTRA_CELL_COLUMNS
# ---------------------------------------------------------------------------

def test_regiostar7_in_extra_cell_columns():
    """load_control_cells loads RegioStaR7 onto cells because it is in _EXTRA_CELL_COLUMNS."""
    assert "RegioStaR7" in mid._EXTRA_CELL_COLUMNS, (
        "_EXTRA_CELL_COLUMNS must include 'RegioStaR7' so load_control_cells "
        "carries the RS7 code per cell for Phase 4B stratum-key lookup."
    )


# ---------------------------------------------------------------------------
# (c) RegioStaR7 in MID_HOUSEHOLD_ATTR_COLS
# ---------------------------------------------------------------------------

def test_regiostar7_in_mid_household_attr_cols():
    """MID_HOUSEHOLD_ATTR_COLS must carry RegioStaR7 so load_mid_attributes
    can deliver it to Phase 4B donor matching."""
    assert "RegioStaR7" in mid.MID_HOUSEHOLD_ATTR_COLS, (
        "MID_HOUSEHOLD_ATTR_COLS must include 'RegioStaR7' (the MiD Haushalte "
        "RegioStaR-7 code, column H_REGIO7 in the raw MiD delivery which we "
        "rename to RegioStaR7 at load time for consistency)."
    )


# ---------------------------------------------------------------------------
# (c) load_mid_seed forwards RegioStaR7 onto the seed households
# ---------------------------------------------------------------------------

class TestSeedCarriesRegioStar7:
    """After load_mid_seed / select_seed_columns, seed households carry RegioStaR7."""

    def _make_household_csv(self, tmp_path: Path, n: int = 10) -> Path:
        """Write a minimal MiD household CSV with H_ID, H_GEW, and RegioStaR7."""
        df = pd.DataFrame({
            "H_ID": range(1, n + 1),
            "H_GEW": [100.0] * n,
            "RegioStaR7": ([72, 75] * (n // 2))[:n],  # alternating urban/rural
            "H_GR": [2] * n,
            "H_MIETE": [1] * n,
            "haustyp": [1] * n,
        })
        p = tmp_path / "MiD2023_Haushalte.csv"
        df.to_csv(p, index=False)
        return p

    def _make_person_csv(self, tmp_path: Path, n: int = 10) -> Path:
        """Write a minimal MiD person CSV (all pass the day filter)."""
        df = pd.DataFrame({
            "H_ID": range(1, n + 1),
            "P_ID": range(100, 100 + n),
            "P_GEW": [100.0] * n,
            "HP_ALTER": [35] * n,
            "HP_SEX": [1] * n,
            "kernwo": [1] * n,  # all pass the day filter
        })
        p = tmp_path / "MiD2023_Personen.csv"
        df.to_csv(p, index=False)
        return p

    def test_load_mid_seed_households_carry_regiostar7(self, tmp_path):
        """Calling load_mid_seed on a minimal fixture yields households with RegioStaR7."""
        self._make_household_csv(tmp_path)
        self._make_person_csv(tmp_path)

        households, persons, report = mid.load_mid_seed(
            tmp_path,
            day_filter_values=(1, 2, 3),
        )
        assert "RegioStaR7" in households.columns, (
            "seed households must carry RegioStaR7 after load_mid_seed; "
            "check that extra_household_cols=('RegioStaR7',) is passed to "
            "select_seed_columns inside load_mid_seed."
        )

    def test_regiostar7_values_preserved(self, tmp_path):
        """The RegioStaR7 values must not be altered by select_seed_columns."""
        self._make_household_csv(tmp_path)
        self._make_person_csv(tmp_path)

        households, _, _ = mid.load_mid_seed(tmp_path, day_filter_values=(1, 2, 3))
        # The fixture has alternating 72/75 values; both should survive.
        assert set(households["RegioStaR7"].unique()) == {72, 75}

    def test_select_seed_columns_preserves_extra_household_col(self):
        """select_seed_columns carries extra_household_cols through unchanged."""
        hh = pd.DataFrame({
            "H_ID": [1, 2, 3],
            "H_GEW": [100.0, 200.0, 150.0],
            "RegioStaR7": [72, 75, 73],
            "irrelevant_col": [9, 9, 9],
        })
        persons = pd.DataFrame({
            "H_ID": [1, 2, 3],
            "P_ID": [10, 20, 30],
            "P_GEW": [100.0, 200.0, 150.0],
            "HP_ALTER": [30, 40, 50],
            "HP_SEX": [1, 2, 1],
        })
        hh_out, _ = seedmod.select_seed_columns(
            hh, persons, seedmod.MID_SEED_COLUMNS,
            extra_household_cols=("RegioStaR7",),
        )
        assert "RegioStaR7" in hh_out.columns
        assert "irrelevant_col" not in hh_out.columns
        assert list(hh_out["RegioStaR7"]) == [72, 75, 73]


# ---------------------------------------------------------------------------
# (d) ENTD: urban_type in _HH_JOIN_COLS
# ---------------------------------------------------------------------------

def test_urban_type_in_entd_hh_join_cols():
    """urban_type must be in _HH_JOIN_COLS so it is joined onto persons
    inside EntdSource.map_person_attributes (Phase 4B will use it as the
    ENTD-side stratum key)."""
    assert "urban_type" in _HH_JOIN_COLS, (
        "_HH_JOIN_COLS in braunschweig.popsim.sources.entd must include "
        "'urban_type' so it flows from the ENTD households frame onto the "
        "per-person output of map_person_attributes."
    )


def test_entd_map_person_attributes_carries_urban_type():
    """EntdSource.map_person_attributes forwards urban_type from households."""
    from braunschweig.popsim.sources.entd import EntdSource

    households = pd.DataFrame({
        "household_id": [1, 2, 3],
        "household_size": [2, 3, 1],
        "number_of_cars": [1, 0, 1],
        "number_of_bicycles": [1, 2, 0],
        "income_class": [8, 5, 10],
        "urban_type": ["central_city", "none", "suburb"],
    })
    persons = pd.DataFrame({
        "person_id": [10, 11, 12, 13],
        "household_id": [1, 1, 2, 3],
        "age": [35, 8, 42, 29],
        "sex": ["male", "female", "male", "female"],
        "employed": [True, False, True, False],
        "studies": [False, True, False, False],
        "has_license": [True, False, True, True],
        "has_pt_subscription": [False, False, True, False],
        "socioprofessional_class": [4, 0, 3, 2],
        "person_weight": [1.0] * 4,
    })

    src = EntdSource()
    # Unified mapper contract: (persons, pseudonym_map); ENTD's map is empty.
    out, _pseudonym_map = src.map_person_attributes(persons, households)

    assert "urban_type" in out.columns, (
        "map_person_attributes must forward urban_type from households to persons; "
        "check that 'urban_type' is in _HH_JOIN_COLS."
    )
    # Persons in household 1 (central_city) should get urban_type == "central_city"
    hh1_persons = out[out["household_id"] == 1]["urban_type"].unique()
    assert list(hh1_persons) == ["central_city"], (
        f"Persons in household 1 should have urban_type='central_city', got {hh1_persons}"
    )
    # Person in household 2 (none) should get urban_type == "none"
    hh2_urban_type = out[out["household_id"] == 2]["urban_type"].iloc[0]
    assert hh2_urban_type == "none"
