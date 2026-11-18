"""Synthetic two-batch PopulationSim folders with a KREIS control table (#658, Task A0g).

Importable helper (not a test file) behind ``tests/test_popsim_kreis_batch_golden.py``. It runs
``braunschweig.popsim.mid.assemble_batch_folder`` for two batches over two Kreise (one Kreis is
split over both batches, the other lies in one batch only) with every KREIS control family
active that the folder assembly can carry, and freezes each batch's ``control_totals_*.csv``
as golden CSVs in ``tests/data/popsim_input_consistency/kreis_batch/<batch>/``. Part A of #658
changes how the KREIS table of a batch is built; the golden proves that at the legacy setting
the tables stay value for value the same (what the golden holds: that README).

All values are SYNTHETIC test data, not Zensus or MiD figures. The cell frame reuses the
synthetic cells of ``popsim_control_build_fixture`` (never modified here) and adds what the
KREIS path needs on top: single-year age columns, the employment and ownership grid columns,
and per-Kreis census/target shares. The KREIS count tables come from the production function
(``kreis_attribute_control.attribute_kreis_count_table``) with the person and household totals
the stage uses (``stage.controls_builder``).

Regenerate the golden CSVs (only on a deliberate, documented change of the expected tables)::

    python -m tests.popsim_kreis_batch_fixture
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_control_build_fixture as base_fixture  # noqa: E402

from braunschweig.popsim import control_spec, folders, mid, stage  # noqa: E402
from braunschweig.popsim import kreis_attribute_control as kac  # noqa: E402
from braunschweig.popsim.stage import controls_builder  # noqa: E402

GOLDEN_DIR = base_fixture.GOLDEN_DIR / "kreis_batch"
BATCH_NAMES = ("batch_000", "batch_001")
CONTROL_TIERS = ("tier0", "tier2", "tier3")
ID_COLUMNS = ("ZENSUS100m", "ZENSUS1km", "KREIS")

# 1 km parents of the base fixture cells (indices into its _CELLS): batch_000 holds the parent
# of cells 0-2 (Kreis 03101) and of cells 5-7 (Kreis 03158); batch_001 holds the parent of
# cells 3-4 (Kreis 03101). So Kreis 03101 is split over both batches, 03158 is in one only.
_BATCH_CELL_INDICES = {"batch_000": (0, 1, 2, 5, 6, 7), "batch_001": (3, 4)}
_CHILD_RICH_CELLS = (3, 4)
_CHILD_FACTOR = 2.5  # children (age 0-19) of the child-rich batch, relative to batch_000

_EMPLOYED_RATES = {"16_29": 0.55, "30_39": 0.85, "40_49": 0.88, "50_59": 0.82, "60plus": 0.20}
_EMPLOYED_SOURCE_BANDS = {
    "16_29": ("20_29",), "30_39": ("30_39",), "40_49": ("40_49",), "50_59": ("50_59",),
    "60plus": ("60_69", "70_79", "80_plus"),
}
_OWN_CARS_SHARES = {"0": 0.20, "1": 0.50, "2": 0.25, "3plus": 0.05}
_OWN_BIKES_SHARES = {"0": 0.15, "1": 0.25, "2": 0.30, "3": 0.15, "4plus": 0.15}
SINGLE_YEAR_MAX_AGE = 100


def _child_columns() -> list[str]:
    return [f"{sex}_AGE_{label}" for sex in ("M", "F")
            for label in ("0_9_agg", *[str(a) for a in range(10, 20)])]


def make_cells() -> pd.DataFrame:
    """The synthetic prepared-cell frame of the two batches (before the aggregated columns)."""
    cells = base_fixture.make_cells()
    # Child-rich batch: more children, so the two batches differ in age structure.
    cells.loc[list(_CHILD_RICH_CELLS), _child_columns()] *= _CHILD_FACTOR
    # Single-year columns M_AGE_<y> / F_AGE_<y> for the age-range universes (education 0-5 /
    # 6-17 / 18+, 14+): the cell frame carries 10-19 already; the other years are the band
    # aggregate spread evenly over its years.
    spans = {"0_9_agg": range(0, 10), "20_29_agg": range(20, 30), "30_39_agg": range(30, 40),
             "40_49_agg": range(40, 50), "50_59_agg": range(50, 60), "60_69_agg": range(60, 70),
             "70_79_agg": range(70, 80), "80_plus_agg": range(80, SINGLE_YEAR_MAX_AGE + 1)}
    new_columns: dict[str, pd.Series] = {}
    for sex in ("M", "F"):
        for label, years in spans.items():
            for year in years:
                new_columns[f"{sex}_AGE_{year}"] = cells[f"{sex}_AGE_{label}"] / len(years)
    # Employment grid columns (ten sex x age classes) and ownership grid columns, synthetic.
    for sex in ("M", "F"):
        for cls, rate in _EMPLOYED_RATES.items():
            new_columns[f"EMPLOYED_{sex}_{cls}_agg"] = rate * sum(
                cells[f"{sex}_AGE_{band}_agg"] for band in _EMPLOYED_SOURCE_BANDS[cls])
    households = cells[control_spec.HH_TOTAL_CENSUS_COLUMN]
    for cls, share in _OWN_CARS_SHARES.items():
        new_columns[f"OWN_CARS_{cls}_agg"] = share * households
    for cls, share in _OWN_BIKES_SHARES.items():
        new_columns[f"OWN_BIKES_{cls}_agg"] = share * households
    return pd.concat([cells, pd.DataFrame(new_columns)], axis=1)


def _controls_and_base_columns() -> tuple[pd.DataFrame, list[str], list]:
    """The controls frame and base columns with every KREIS attribute control, grids on."""
    names = [ctl.name for ctl in kac.REGISTRY]
    controls_df, base_cols = stage._build_control_frame(
        base_fixture.CONTROL_SOURCE, None, base_fixture.SEED_NAME, CONTROL_TIERS, True, names,
        base_fixture.IMPORTANCE_PROFILE, fine_teen_age_bands=base_fixture.FINE_TEEN_AGE_BANDS,
        ownership_grid_on=True)
    return controls_df, base_cols, list(kac.REGISTRY)


def _kreis_tables(cells: pd.DataFrame, active_entries: list) -> tuple[pd.DataFrame, dict, set]:
    """Per-Kreis census table, controls map and household control names (as the stage builds)."""
    kreise = sorted({str(a)[:5] for a in cells[mid._ARS_COLUMN]})
    # Tier-3 census table: synthetic source counts per Kreis (the columns the tier3 map names).
    tier3 = [c for c in control_spec.tier3_controls()
             if c.expression_for(base_fixture.SEED_NAME) is not None]
    kreis_controls_map = controls_builder._kreis_controls_map(tier3)
    sources = sorted({col for cols in kreis_controls_map.values() for col in cols})
    kreis_table = pd.DataFrame({"ARS_kreis": kreise})
    for i, col in enumerate(sources):
        kreis_table[col] = [1000.0 * (i + 1) + 137.0 * k for k in range(len(kreise))]
    household_control_names: set[str] = set()

    # The totals the category counts partition, per resolved Kreis (stage logic).
    kreis_by_row = mid.resolved_kreis_per_cell(cells)
    hh_total = cells.groupby(kreis_by_row)[control_spec.HH_TOTAL_CENSUS_COLUMN].sum().to_dict()
    persons_total = controls_builder.person_total_by_kreis(cells, kreis_by_row)
    bands = controls_builder.age_band_by_entry_name(active_entries)
    for ctl in active_entries:
        if ctl.level == "household":
            total = hh_total
        elif bands.get(ctl.name) is None:
            total = persons_total
        else:
            lo, hi = bands[ctl.name]
            total = controls_builder.person_total_by_kreis_age_range(cells, kreis_by_row, lo, hi)
        # Synthetic target shares: a deterministic rotation of integer weights per Kreis,
        # plus the region-aggregate row the loader requires.
        n = len(ctl.target_columns)
        rows = []
        for k, ars5 in enumerate([*kreise, "Gesamt"]):
            weights = [1 + (j + k) % n + j for j in range(n)]
            rows.append({"ars5": ars5, **{c: w / sum(weights)
                                          for c, w in zip(ctl.target_columns, weights)}})
        table = kac.attribute_kreis_count_table(ctl, pd.DataFrame(rows), total, prior_n=0.0)
        kreis_table = kreis_table.merge(table, on="ARS_kreis", how="left", validate="one_to_one")
        kreis_controls_map.update(
            controls_builder._kreis_controls_map(control_spec.attribute_kreis_controls([ctl])))
        if ctl.level == "household":
            household_control_names.update(
                controls_builder._kreis_controls_map(
                    control_spec.attribute_kreis_controls([ctl])).keys())
    return kreis_table, kreis_controls_map, household_control_names


def build_batch_folders(work_dir: Path) -> dict[str, Path]:
    """Assemble the two batch folders under ``work_dir`` and return ``{batch: data directory}``."""
    work_dir = Path(work_dir)
    cells = make_cells()
    controls_df, base_cols, active_entries = _controls_and_base_columns()
    cells = stage._add_aggregated_control_columns(
        cells, base_fixture.CONTROL_SOURCE, base_fixture.SEED_NAME, CONTROL_TIERS,
        **base_fixture.AGGREGATE_KWARGS)
    kreis_table, kreis_controls_map, household_control_names = _kreis_tables(
        cells, active_entries)

    # Region-wide apportionment bases per resolved Kreis (as run_popsim_mid computes them).
    full_xwalk = folders.build_geo_crosswalk(
        cells, id_col_100m="ZENSUS100m", parent_col="ZENSUS1km", ars_col=mid._ARS_COLUMN,
        resolve_parent_kreis=True, kreis_weight_col="POP_TOTAL_100m_adj")
    kreis_total_pop = mid._kreis_pop_from_crosswalk(cells, full_xwalk)
    kreis_total_hh = mid._kreis_pop_from_crosswalk(
        cells, full_xwalk, weight_col=control_spec.HH_TOTAL_CENSUS_COLUMN)

    seed_households = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_persons = pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]})
    data_dirs: dict[str, Path] = {}
    for name in BATCH_NAMES:
        subset = cells.iloc[list(_BATCH_CELL_INDICES[name])].copy()
        folder = work_dir / name
        mid.assemble_batch_folder(
            folder, subset, base_cols, controls_df, seed_households, seed_persons,
            settings_yaml="x: 1\n", logging_yaml="version: 1\n",
            kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
            kreis_total_pop=kreis_total_pop, kreis_total_hh=kreis_total_hh,
            household_control_names=household_control_names)
        data_dirs[name] = folder / "data"
    return data_dirs


def table_names(data_dir: Path) -> list[str]:
    """Names (``ZENSUS100m`` ...) of the ``control_totals_*.csv`` files of a data directory."""
    return sorted(p.stem[len("control_totals_"):] for p in Path(data_dir).glob("control_totals_*.csv"))


def read_table(data_dir: Path, name: str) -> pd.DataFrame:
    """Read one control table; grid ids and the Kreis key as str (zero-padded keys survive)."""
    path = Path(data_dir) / f"control_totals_{name}.csv"
    header = pd.read_csv(path, nrows=0).columns
    return pd.read_csv(path, dtype={c: "object" for c in header if c in ID_COLUMNS})


def write_golden(out_dir: Path = GOLDEN_DIR, work_dir: Path | None = None) -> list[Path]:
    """Build the folders (in a temporary directory) and copy their control tables to ``out_dir``."""
    import tempfile
    written: list[Path] = []
    with tempfile.TemporaryDirectory() as tmp:
        data_dirs = build_batch_folders(Path(work_dir) if work_dir else Path(tmp))
        for batch, data_dir in data_dirs.items():
            target = Path(out_dir) / batch
            target.mkdir(parents=True, exist_ok=True)
            for name in table_names(data_dir):
                source = Path(data_dir) / f"control_totals_{name}.csv"
                (target / source.name).write_bytes(source.read_bytes())
                written.append(target / source.name)
    return written


if __name__ == "__main__":
    for path in write_golden():
        print(path)
