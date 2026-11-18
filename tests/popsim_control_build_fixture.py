"""Synthetic cell frame and control-table builder for the PopulationSim control build (#657).

Importable helper (not a test file) behind ``tests/test_popsim_control_build_golden.py``. It
runs the control-total path of the ``data.census.filtered`` stage (``mid`` seed, catalog
controls, tiers 0 to 2) on a small synthetic cell frame, so the four control-total tables of
that path can be frozen as golden CSVs (``tests/data/popsim_input_consistency/``) and any
later change to the path can be proven to reproduce them at its legacy setting.

The cell values are SYNTHETIC test data, not Zensus figures: they only have to be internally
plausible and to exercise the cases the control build distinguishes (float targets that need
integerisation, a cell without a dwelling count that falls back to its 1 km parent's shares,
a dormitory-like cell with far more households than dwellings, owner-only cells).

Regenerate the golden CSVs (only on a deliberate, documented change of the expected tables)::

    python -m tests.popsim_control_build_fixture
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from braunschweig.popsim import cells as cellmod
from braunschweig.popsim import control_spec, mid, stage
from braunschweig.popsim.stage import controls_builder

GOLDEN_DIR = Path(__file__).resolve().parent / "data" / "popsim_input_consistency"
GOLDEN_NAMES = ("ZENSUS100m", "ZENSUS1km", "STAAT", "WELT")

# The stage-call arguments, in ONE place. They are the production values of the keys that
# shape the control columns (``configs/base_bs.yml``: ``fine_teen_age_bands``,
# ``building_type_targets_to_households``); the control tiers are the production tiers whose
# controls reach the 100 m and 1 km tables (tier3 only builds KREIS controls, which
# ``build_control_totals`` does not write). A later task that adds a keyword to ``_add_aggregated_control_columns`` adds it here.
CONTROL_SOURCE = "catalog"
SEED_NAME = "mid"
CONTROL_TIERS = ("tier0", "tier1", "tier2")
FINE_TEEN_AGE_BANDS = True
AGGREGATE_KWARGS: dict[str, Any] = {
    "fine_teen_age_bands": FINE_TEEN_AGE_BANDS,
    "building_type_targets_to_households": True,
}

# Arguments of ``stage._build_control_frame`` besides the shared ones: employment grid and
# ownership grid off, no Kreis attribute control (empty active-entry names). The importance
# profile only weights the controls and does not change the base columns.
IMPORTANCE_PROFILE = "optimized_2026_06_30"

ROOMS_DWELLINGS_COLUMN = "Insgesamt_Wohnungen_Wohnungen_nach_Zahl_der_Raeume_100m_Gitter_adj"
OWNER_COLUMN = "EigentuemerHH_Tenure_100m_Gitter"
RENTER_COLUMN = "MieterHH_Tenure_100m_Gitter"

# Dwelling counts of a building-type class are split over its census source columns by these
# fixed shares (sum 1 per class); the source columns are those of the control spec.
_CLASS_SHARES = (
    (0, (0.4, 0.2, 0.1, 0.15, 0.1, 0.05)),   # one-/two-family houses (6 source columns)
    (1, (0.5, 0.3, 0.2)),                    # multi-family houses (3 source columns)
    (2, (1.0,)),                             # other building type (1 source column)
)

# Age shares of the persons of one sex: 0-9 (one aggregate), ages 10-19 single years, then the
# seven aggregates 20-29 ... 80+. Sum 1.
_AGE_SHARES = {
    "0_9": 0.09,
    **{str(age): share for age, share in zip(
        range(10, 20), (0.011, 0.011, 0.011, 0.010, 0.010, 0.010, 0.010, 0.010, 0.010, 0.010))},
    "20_29": 0.12, "30_39": 0.13, "40_49": 0.13, "50_59": 0.14, "60_69": 0.12, "70_79": 0.10,
    "80_plus": 0.0,
}
# The 80+ share is the remainder so the shares sum to 1 exactly.
_AGE_SHARES["80_plus"] = 1.0 - sum(v for k, v in _AGE_SHARES.items() if k != "80_plus")
_FEMALE_FACTOR = 1.04  # slightly more women than men in the cell population

# Tier-1 household census columns (household size, family type): counts of the cell households
# split by these fixed shares (each set sums to 1).
_HOUSEHOLD_SIZE_SHARES = {
    "1_Person_Groesse_des_privaten_Haushalts_100m_Gitter": 0.35,
    "2_Personen_Groesse_des_privaten_Haushalts_100m_Gitter": 0.30,
    "3_Personen_Groesse_des_privaten_Haushalts_100m_Gitter": 0.15,
    "4_Personen_Groesse_des_privaten_Haushalts_100m_Gitter": 0.12,
    "5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter": 0.06,
    "6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter": 0.02,
}
_FAMILY_TYPE_SHARES = {
    "Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter": 0.25,
    "Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter": 0.30,
    "Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter": 0.10,
    "MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter": 0.35,
}

# (north, east, ARS, households, owner households, renter households,
#  dwellings by building class (one-/two-family, multi-family, other), rooms-table dwellings,
#  persons). Parents (1 km): cells 0-2 -> N2900000E4400000, 3-4 -> N2901000E4400000,
#  5-7 -> N2900000E4401000.
_CELLS = (
    # ordinary cells: households about equal to dwellings, owners and renters both > 0
    (2900100, 4400200, "031010000000", 24.6, 11.3, 13.3, (6.2, 18.4, 0.9), 25.5, 51.7),
    (2900500, 4400700, "031010000000", 17.3, 12.1, 5.2, (12.4, 5.6, 0.4), 18.4, 38.1),
    # dormitory-like cell: 30 owner households, 2 dwellings
    (2900300, 4400900, "031010000000", 30.0, 30.0, 0.0, (0.0, 2.0, 0.0), 2.0, 105.0),
    # 118 households, all owners, no dwelling count (takes the shares of its 1 km parent)
    (2901200, 4400300, "031010000000", 118.0, 118.0, 0.0, (0.0, 0.0, 0.0), 0.0, 236.0),
    (2901600, 4400800, "031010000000", 41.8, 20.9, 20.9, (9.7, 31.4, 1.2), 42.3, 88.4),
    (2900200, 4401100, "031580000000", 33.4, 8.4, 25.0, (3.1, 30.9, 0.8), 34.8, 70.2),
    # small cell: 3 households, 3 owners, no dwelling count
    (2900700, 4401400, "031580000000", 3.0, 3.0, 0.0, (0.0, 0.0, 0.0), 0.0, 6.0),
    (2900900, 4401700, "031580000000", 12.6, 9.8, 2.8, (9.5, 3.7, 0.2), 13.4, 27.3),
)


def _cell_id(north: int, east: int) -> str:
    return f"CRS3035RES100mN{north}E{east}"


def required_cell_columns() -> list[str]:
    """The census columns the stage loads for these tiers and the ``mid`` seed.

    Derived from the stage itself (``controls_builder.build_source_columns``), so a control
    column a later change adds to the build cannot be missing silently from the fixture:
    ``make_cells`` raises on any column it does not provide.
    """
    columns = controls_builder.build_source_columns(
        controls_source=CONTROL_SOURCE, seed=SEED_NAME, tiers=CONTROL_TIERS,
        fine_teen_age_bands=FINE_TEEN_AGE_BANDS)
    return list(columns)


def base_columns() -> list[str]:
    """The control base columns of ``stage._build_control_frame`` (100 m and 1 km geographies)."""
    _, base_cols = stage._build_control_frame(
        CONTROL_SOURCE, None, SEED_NAME, CONTROL_TIERS, False, [], IMPORTANCE_PROFILE,
        fine_teen_age_bands=FINE_TEEN_AGE_BANDS, ownership_grid_on=False)
    return base_cols


def make_cells() -> pd.DataFrame:
    """Return the synthetic prepared-cell frame (cleaned names, as after ``load_control_cells``).

    Eight 100 m cells in three 1 km parents and two Kreise, with float counts.
    """
    building_type_sources = [
        entry[1] for entry in control_spec._TIER2_BUILDING_TYPE_ENTRIES]
    rows = []
    for (north, east, ars, households, owners, renters, dwellings, rooms, persons) in _CELLS:
        row: dict[str, Any] = {
            "ZENSUS100m": _cell_id(north, east),
            mid._ARS_COLUMN: ars,
            "POP_TOTAL_100m_adj": persons,
            "RegioStaR7": 73,
            control_spec.HH_TOTAL_CENSUS_COLUMN: households,
            OWNER_COLUMN: owners,
            RENTER_COLUMN: renters,
            ROOMS_DWELLINGS_COLUMN: rooms,
        }
        for (class_index, shares), columns in zip(_CLASS_SHARES, building_type_sources):
            for share, column in zip(shares, columns):
                row[column] = dwellings[class_index] * share
        for column, share in {**_HOUSEHOLD_SIZE_SHARES, **_FAMILY_TYPE_SHARES}.items():
            row[column] = households * share
        per_sex = persons / (1.0 + _FEMALE_FACTOR)
        for sex, factor in (("M", 1.0), ("F", _FEMALE_FACTOR)):
            for label, share in _AGE_SHARES.items():
                suffix = f"{label}_agg" if "_" in label else label
                row[f"{sex}_AGE_{suffix}"] = per_sex * factor * share
        rows.append(row)
    cells = pd.DataFrame(rows)
    cells["ZENSUS1km"] = cells["ZENSUS100m"].map(cellmod.derive_1km_parent_id)
    cells["STAAT"] = 1
    cells["WELT"] = 1
    missing = [c for c in required_cell_columns() if c not in cells.columns]
    if missing:
        raise KeyError(
            f"the synthetic cell frame lacks the column(s) {missing} that the control build "
            "loads; add them to tests/popsim_control_build_fixture.py")
    return cells


def build_control_tables(
        cells: pd.DataFrame | None = None, **extra_aggregate_kwargs: Any,
) -> dict[str, pd.DataFrame]:
    """Run the control-total path of the stage on ``cells`` and return its four tables.

    The same calls, in the same order, as the stage: derive the aggregated control columns
    (``_add_aggregated_control_columns``), then ``mid.build_control_totals`` with the base
    columns of ``_build_control_frame`` and the 100 m -> 1 km crosswalk.

    ``extra_aggregate_kwargs`` extend or override ``AGGREGATE_KWARGS`` (for a later keyword).
    """
    if cells is None:
        cells = make_cells()
    aggregate_kwargs = {**AGGREGATE_KWARGS, **extra_aggregate_kwargs}
    prepared = stage._add_aggregated_control_columns(
        cells, CONTROL_SOURCE, SEED_NAME, CONTROL_TIERS, **aggregate_kwargs)
    crosswalk = prepared[["ZENSUS100m", "ZENSUS1km"]]
    return mid.build_control_totals(prepared, crosswalk, base_columns())


def golden_path(name: str, directory: Path = GOLDEN_DIR) -> Path:
    return Path(directory) / f"control_totals_{name}.csv"


def write_golden(out_dir: Path = GOLDEN_DIR) -> list[Path]:
    """Write the four tables as CSV (``index=False``, no float format) into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, table in build_control_tables().items():
        path = golden_path(name, out_dir)
        table.to_csv(path, index=False)
        paths.append(path)
    return paths


def read_golden(name: str, directory: Path = GOLDEN_DIR) -> pd.DataFrame:
    """Read one golden table with the dtypes the builder produces (ids as str, counts as int)."""
    path = golden_path(name, directory)
    header = pd.read_csv(path, nrows=0).columns
    id_columns = {c for c in header if c in ("ZENSUS100m", "ZENSUS1km")}
    return pd.read_csv(path, dtype={c: "object" for c in id_columns})


if __name__ == "__main__":
    for written in write_golden():
        print(written)
