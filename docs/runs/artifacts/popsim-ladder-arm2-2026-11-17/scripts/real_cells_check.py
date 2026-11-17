"""Per-Kreis household-total check of the WP3 contract on the real prepared 100 m cells (#647).

Loads the prepared Zensus 2022 100 m cells, keeps the ZGB Kreise, derives the aggregated control
columns with the building-type rescale ON (the stage's ``_add_aggregated_control_columns``), groups the
cells by the resolved Kreis (``mid.resolved_kreis_per_cell``, as the stage does) and runs
``seed_contract.check_household_group_totals`` in warn mode on the production control specification.
The ownership-grid columns (own_cars, own_bikes) need the raked 1 km grid and are not injected here;
the check skips them and says so. Writes the per-group, per-Kreis table as CSV.

Usage (eqasim-bs code on PYTHONPATH, eqasim env):
  python real_cells_check.py --cells <prepared 100 m parquet> --kreise 03101 ... --out <csv>
"""
import argparse
import logging
import sys
from pathlib import Path

from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim import mid
from braunschweig.popsim import seed_contract as sc
from braunschweig.popsim import stage as st

TIERS = ("tier0", "tier1", "tier2", "tier3")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cells", required=True, type=Path)
    parser.add_argument("--kreise", nargs="+", required=True)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    catalog = cs.full_catalog(include_tiers=TIERS, include_employment_grid=True,
                              include_ownership_grid=True,
                              kreis_control_names=[c.name for c in kac.REGISTRY],
                              fine_teen_age_bands=True)
    spec = cs.render_catalog_csv(cs.controls_for_seed(catalog, "mid"), "mid")
    load_cols = sorted(set(cs.source_columns_union(cs.controls_for_seed(catalog, "mid")))
                       | {"POP_TOTAL_100m_adj", cs.HH_TOTAL_CENSUS_COLUMN})
    cells = mid.load_control_cells(args.cells, load_cols)
    cells = mid.filter_zgb_cells(cells, args.kreise)
    cells = st._add_aggregated_control_columns(cells, "catalog", "mid", TIERS,
                                               fine_teen_age_bands=True,
                                               building_type_targets_to_households=True)
    cells["KREIS_RESOLVED"] = mid.resolved_kreis_per_cell(cells).to_numpy()
    total = f"{cs.HH_TOTAL_CENSUS_COLUMN}_ZENSUS100m_target"
    table = sc.check_household_group_totals(cells, spec, total, kreis_col="KREIS_RESOLVED",
                                            mode="warn")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    outside = table[(table["ratio"] - 1).abs() > sc.UNIVERSE_RATIO_TOLERANCE]
    print(table.to_string(index=False))
    print(f"cells={len(cells)} groups_checked={table['group'].nunique()} "
          f"outside_tolerance={len(outside)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
