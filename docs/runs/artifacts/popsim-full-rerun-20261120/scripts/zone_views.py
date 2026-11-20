"""Zone-level views of one PopulationSim run for the full-rerun report (issue #648).

Reuses the loaders of ``braunschweig.analysis.popsim_batch_diagnostics`` (``load_batch_folder``,
``household_incidence``, ``control_totals``) and the reuse measure ``donor_neff``, so every number
is computed the way the committed diagnostics compute it; only the aggregation differs: the
diagnostics pool zones per batch, this script keeps them per zone.

Usage (run from a checkout of the run's commit, eqasim conda env):
    python zone_views.py WORK_DIR OUT_DIR

Writes:
    cells_1km.csv        one row per 1 km cell (ZENSUS1km zone): EPSG:3035 lower-left corner in km
                         (from the grid id), Kreis, household and person targets and synthetic counts,
                         and per control group the target sum and the sum of |synthetic - target|
                         over the cell's 100 m zones (groups at ZENSUS100m) or over the cell itself
                         (groups at ZENSUS1km); distinct seed households and Kish size per cell.
    cellsize_bins.csv    per control group at ZENSUS100m and bin of the 100 m cell's household target:
                         number of cells, target sum, absolute error, WAPE.
    donor_usage.csv      number of copies of every used seed household over the whole run (for the
                         concentration curve and the copies histogram).
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compare_runs import control_group  # noqa: E402  (sibling artifact script)

from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import (  # noqa: E402
    control_totals, household_incidence, list_batch_folders, load_batch_folder)
from braunschweig.analysis.popsim_batch_diagnostics.reuse import donor_neff  # noqa: E402

logger = logging.getLogger("zone_views")

# Bins of the 100 m cell's household target; the left edge is inclusive, the right edge exclusive.
CELL_SIZE_BINS = [(0, 1, "0"), (1, 6, "1-5"), (6, 11, "6-10"), (11, 21, "11-20"), (21, 31, "21-30"),
                  (31, 51, "31-50"), (51, 101, "51-100"), (101, 10**9, "101+")]
GRID_ID = re.compile(r"N(\d+)E(\d+)$")


def _grid_corner_km(zone_id: str) -> tuple[float, float]:
    match = GRID_ID.search(str(zone_id))
    if match is None:
        raise ValueError(f"zone id {zone_id!r} is not a Zensus grid id (…N<metres>E<metres>)")
    return int(match.group(2)) / 1000.0, int(match.group(1)) / 1000.0


def _zone_errors(batch, geography: str, incidence: pd.DataFrame, household_pos: np.ndarray):
    """Per zone and control: target and synthetic count (as control_fit computes them)."""
    spec = batch.control_spec
    controls = spec.loc[spec["geography"] == geography, "target"].tolist()
    targets = control_totals(batch, geography)[controls]
    expanded = batch.expanded
    zones = targets.index.union(pd.Index(expanded[geography].unique()))
    zone_pos = zones.get_indexer(expanded[geography])
    counts = sparse.csr_matrix((np.ones(len(expanded)), (zone_pos, household_pos)),
                               shape=(len(zones), len(incidence)))
    synthetic = pd.DataFrame(counts @ incidence[controls].to_numpy(dtype=float), index=zones,
                             columns=controls)
    target = targets.reindex(zones).fillna(0.0).astype(float)
    return target, synthetic


def run(work_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    cell_rows, bin_rows, usage_parts = [], [], []
    folders = list_batch_folders(work_dir)
    for index, folder in enumerate(folders, start=1):
        batch = load_batch_folder(folder)
        logger.info("batch %d/%d: %s", index, len(folders), batch.name)
        incidence = household_incidence(batch)
        id_col = batch.household_id_col
        household_pos = incidence.index.get_indexer(batch.expanded[id_col])
        if (household_pos < 0).any():
            raise ValueError(f"{batch.name}: expanded household ids missing from the seed")
        crosswalk = pd.read_csv(batch.path / "data" / "geo_cross_walk.csv", dtype=str)
        parent_of = crosswalk.drop_duplicates("ZENSUS100m").set_index("ZENSUS100m")["ZENSUS1km"]
        kreis_of = crosswalk.drop_duplicates("ZENSUS1km").set_index("ZENSUS1km")["KREIS"]

        per_cell = {}
        t100, s100 = _zone_errors(batch, "ZENSUS100m", incidence, household_pos)
        unmapped = ~t100.index.isin(parent_of.index)
        if unmapped.any():
            raise ValueError(f"{batch.name}: {int(unmapped.sum())} 100 m zone(s) without a 1 km parent")
        parent = parent_of.reindex(t100.index).to_numpy()
        groups100 = {c: control_group(c) for c in t100.columns}
        error100 = (s100 - t100).abs()
        for group in sorted(set(groups100.values())):
            cols = [c for c, g in groups100.items() if g == group]
            frame = pd.DataFrame({"parent": parent, "target": t100[cols].sum(axis=1).to_numpy(),
                                  "synthetic": s100[cols].sum(axis=1).to_numpy(),
                                  "abs_error": error100[cols].sum(axis=1).to_numpy()})
            per_cell[group] = frame.groupby("parent")[["target", "synthetic", "abs_error"]].sum()
            # Cell-size bins by the 100 m cell's household target.
            hh = t100[batch.total_hh_control] if batch.total_hh_control in t100 else None
            if hh is None:
                raise ValueError(f"{batch.name}: household total {batch.total_hh_control} not at 100 m")
            for low, high, label in CELL_SIZE_BINS:
                member = ((hh >= low) & (hh < high)).to_numpy()
                bin_rows.append({"batch": batch.name, "group": group, "cell_households": label,
                                 "n_cells": int(member.sum()),
                                 "target_sum": float(frame["target"].to_numpy()[member].sum()),
                                 "abs_error": float(frame["abs_error"].to_numpy()[member].sum())})
        if "ZENSUS1km" in batch.control_spec["geography"].unique():
            t1, s1 = _zone_errors(batch, "ZENSUS1km", incidence, household_pos)
            groups1 = {c: control_group(c) for c in t1.columns}
            error1 = (s1 - t1).abs()
            for group in sorted(set(groups1.values())):
                cols = [c for c, g in groups1.items() if g == group]
                per_cell[f"{group}@1km"] = pd.DataFrame({
                    "target": t1[cols].sum(axis=1), "synthetic": s1[cols].sum(axis=1),
                    "abs_error": error1[cols].sum(axis=1)})

        expanded = batch.expanded
        donors = []
        for zone, group in expanded.groupby("ZENSUS1km", sort=True):
            donors.append({"zone": zone, "n_distinct_donors": int(group[id_col].nunique()),
                           "kish_n_eff": float(donor_neff(group[[id_col]], id_col)["n_eff"])})
        donors = pd.DataFrame(donors).set_index("zone")
        zones = sorted(set().union(*[f.index for f in per_cell.values()], donors.index))
        for zone in zones:
            x_km, y_km = _grid_corner_km(zone)
            row = {"zone": zone, "batch": batch.name, "kreis": kreis_of.get(zone, ""),
                   "x_km": x_km, "y_km": y_km}
            for key, frame in per_cell.items():
                name = key.replace("@1km", "_1km") if key.endswith("@1km") else f"{key}_100m"
                if zone in frame.index:
                    row[f"{name}_target"] = float(frame.at[zone, "target"])
                    row[f"{name}_synthetic"] = float(frame.at[zone, "synthetic"])
                    row[f"{name}_abs_error"] = float(frame.at[zone, "abs_error"])
            if zone in donors.index:
                row["n_distinct_donors"] = int(donors.at[zone, "n_distinct_donors"])
                row["kish_n_eff"] = float(donors.at[zone, "kish_n_eff"])
            cell_rows.append(row)
        usage_parts.append(expanded[id_col].value_counts())

    cells = pd.DataFrame(cell_rows)
    duplicated = cells["zone"].duplicated(keep=False)
    if duplicated.any():
        # A 1 km cell split over batches (boundary cells): sum the counts, keep donor stats of the
        # larger part and flag the row.
        logger.warning("%d 1 km cell row(s) appear in more than one batch; summed",
                       int(duplicated.sum()))
    numeric = [c for c in cells.columns if c.endswith(("_target", "_synthetic", "_abs_error"))]
    agg = {c: "sum" for c in numeric}
    agg.update({"batch": lambda s: "+".join(sorted(set(s))), "kreis": "first", "x_km": "first",
                "y_km": "first", "n_distinct_donors": "max", "kish_n_eff": "max"})
    cells = cells.groupby("zone", as_index=False).agg(agg)
    cells["split_over_batches"] = cells["batch"].str.contains(r"\+")
    cells.to_csv(out_dir / "cells_1km.csv", index=False)

    bins = pd.DataFrame(bin_rows).groupby(["group", "cell_households"], as_index=False)[
        ["n_cells", "target_sum", "abs_error"]].sum()
    bins["wape"] = bins["abs_error"] / bins["target_sum"].where(bins["target_sum"] > 0)
    bins.to_csv(out_dir / "cellsize_bins.csv", index=False)

    usage = pd.concat(usage_parts).groupby(level=0).sum().rename("copies")
    usage.index.name = "seed_household_id"
    usage.reset_index().to_csv(out_dir / "donor_usage.csv", index=False)
    logger.info("wrote %d cells, %d bin rows, %d used donors to %s", len(cells), len(bins),
                len(usage), out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    run(Path(sys.argv[1]), Path(sys.argv[2]))
