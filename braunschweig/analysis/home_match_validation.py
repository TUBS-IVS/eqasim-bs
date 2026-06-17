"""Headline metrics for the typed home matcher: type-match share + size assortativity.

**placed input contract** (for CLI / ``home_match_report``)
-----------------------------------------------------------
The ``placed`` parquet is the output of the typed home stage joined to the sampled
population on ``household_id``, with at least the following columns:

    household_id          -- unique per household
    building_type_3class  -- MiD label: "ein_zweifamilienhaus" | "mehrfamilienhaus"
                             | "sonstiges"
    household_size        -- integer
    home_location_id      -- building_id that the household was matched to
                             (may be pd.NA for zero-building-cell fallbacks)
"""
from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from braunschweig.synthesis.locations.building_typing import assign_building_types, build_slots
from braunschweig.synthesis.locations.cell_building_signals import (
    THREE_CLASSES,
    cell_signals,
)
from braunschweig.synthesis.locations.home_cell import building_cell_id

_BTYPE_MAP = {"ein_zweifamilienhaus": "efh_zfh", "mehrfamilienhaus": "mfh", "sonstiges": "sonst"}

# Census column for vacant dwellings (Leerstand).  May be absent in test frames;
# treated as 0 when missing.
_VACANT_COL = "LeerstehendWhg_Leerstand_100m_Gitter"
# Occupied dwellings column (matches OCCUPIED_COL in cell_building_signals).
_OCCUPIED_COL = "BewohntWhg_Leerstand_100m_Gitter"


def home_match_metrics(placed: pd.DataFrame, buildings_btype: pd.DataFrame) -> dict:
    df = placed.merge(buildings_btype, left_on="home_location_id", right_on="building_id", how="inner")
    hh_btype = df["building_type_3class"].map(_BTYPE_MAP)
    match = (hh_btype == df["btype"]).mean() if len(df) else float("nan")
    if len(df) >= 3 and df["size"].nunique() > 1 and df["household_size"].nunique() > 1:
        rho = spearmanr(df["household_size"], df["size"]).correlation
    else:
        rho = float("nan")
    return {"type_match_share": float(match), "size_assortativity": float(rho),
            "n_households": int(len(placed))}


# ---------------------------------------------------------------------------
# derive_buildings_btype
# ---------------------------------------------------------------------------

def derive_buildings_btype(
    buildings: pd.DataFrame,
    cells: pd.DataFrame,
    random_seed: int,
) -> pd.DataFrame:
    """Derive per-building btype and mean slot size from ALKIS footprints + census cells.

    This mirrors what the typed home matcher does internally so the derived btype
    matches the run's assignment when validated post-hoc.

    Parameters
    ----------
    buildings:
        DataFrame with at least ``building_id``, ``area_m2``, and ``geometry``
        (EPSG:25832 or any CRS that can be reprojected to EPSG:3035).  Also
        accepted: a GeoDataFrame.  If no ``geometry`` column is present, columns
        ``north_3035`` / ``east_3035`` (pre-projected centroid coordinates in
        EPSG:3035 metres) may be supplied directly.
    cells:
        Prepared 100 m cell frame carrying the Gebaeudetyp / Wohnung / occupancy /
        dwelling-size columns (output of ``load_prepared_cells``).
    random_seed:
        Seed for the building-type RNG (must match the seed used during the run).

    Returns
    -------
    pandas.DataFrame
        One row per building with columns ``building_id``, ``btype``, ``size``
        (mean slot size in m² for that building, or NaN when no slots were
        assigned to that building).
    """
    rng = np.random.RandomState(int(random_seed))

    # --- compute each building's 100 m cell id ----------------------------
    buildings = buildings.copy()
    if "geometry" in buildings.columns:
        try:
            import geopandas as gpd  # optional; may not be available in test env
            gdf = buildings if hasattr(buildings, "crs") else gpd.GeoDataFrame(
                buildings, geometry="geometry"
            )
            cent3035 = gdf.geometry.to_crs("EPSG:3035")
            buildings["_cell_id"] = [
                building_cell_id(north_m=g.y, east_m=g.x) for g in cent3035
            ]
        except Exception:
            # If geopandas unavailable or CRS missing, fall through to coordinate
            # columns below.
            pass
    if "_cell_id" not in buildings.columns:
        if "north_3035" in buildings.columns and "east_3035" in buildings.columns:
            buildings["_cell_id"] = [
                building_cell_id(north_m=float(n), east_m=float(e))
                for n, e in zip(buildings["north_3035"], buildings["east_3035"])
            ]
        else:
            raise ValueError(
                "derive_buildings_btype: buildings must have either a 'geometry' "
                "column (reprojected to EPSG:3035) or 'north_3035'/'east_3035' "
                "columns (pre-projected centroids in EPSG:3035 metres)."
            )

    sig_df = cell_signals(cells).set_index("ZENSUS100m")

    result_rows: list[dict] = []
    for cell_id, grp in buildings.groupby("_cell_id", sort=False):
        fps = grp[["building_id", "area_m2"]].copy()
        s = sig_df.loc[str(cell_id)] if str(cell_id) in sig_df.index else None
        geb = {
            c: float(s[f"geb_{c}"]) if s is not None and f"geb_{c}" in s.index else 0.0
            for c in THREE_CLASSES
        }
        whg = {
            c: float(s[f"whg_{c}"]) if s is not None and f"whg_{c}" in s.index else 0.0
            for c in THREE_CLASSES
        }
        occ = float(s["occupied"]) if s is not None else float(len(grp))
        size_hist = s["size_hist"] if s is not None else []

        typed = assign_building_types(fps, geb, rng)
        slots = build_slots(typed, whg, max(occ, len(grp)), size_hist, rng)

        # Per-building mean slot size.
        # If slots have a degenerate (all-zero / constant) size column — which
        # happens when size_hist is empty and build_slots falls back to 0.0 —
        # substitute each building's own area_m2 as a size proxy so that
        # size_assortativity has genuine cross-building variation to correlate.
        if not slots.empty:
            mean_size = slots.groupby("building_id")["size"].mean().rename("size")
            # Detect degenerate: all slot sizes identical (typically 0.0).
            if mean_size.nunique() <= 1:
                # Fallback: use building area_m2 as a proportional size proxy.
                area_ser = grp.set_index("building_id")["area_m2"]
                mean_size = area_ser.rename("size").astype(float)
        else:
            mean_size = pd.Series(dtype=float, name="size")

        # Merge btype + size onto typed buildings
        btype_ser = typed.set_index("building_id")["btype"]
        for bid in grp["building_id"]:
            result_rows.append({
                "building_id": bid,
                "btype": btype_ser.get(bid, None),
                "size": float(mean_size.get(bid, math.nan)),
            })

    if not result_rows:
        return pd.DataFrame(columns=["building_id", "btype", "size"])

    return pd.DataFrame(result_rows).reset_index(drop=True)


# ---------------------------------------------------------------------------
# home_match_report  (C3 metrics)
# ---------------------------------------------------------------------------

def home_match_report(
    placed: pd.DataFrame,
    buildings_btype: pd.DataFrame,
    cells: pd.DataFrame,
    *,
    n_overcapacity: int = 0,
    n_zero_building_cells: int = 0,
) -> dict:
    """Return C3 metrics: all base metrics from home_match_metrics plus vacancy / overflow.

    Parameters
    ----------
    placed:
        Home output joined to sampled population (see module docstring for columns).
    buildings_btype:
        Output of :func:`derive_buildings_btype`.
    cells:
        Prepared 100 m cell frame (same as passed to ``derive_buildings_btype``).
    n_overcapacity:
        Number of households the matcher had to over-occupy a building for
        (from ``TypedHomeReport.n_overcapacity``).
    n_zero_building_cells:
        Number of households whose cell had no building (from
        ``TypedHomeReport.n_zero_building_cells``).

    Returns
    -------
    dict
        Keys: everything from ``home_match_metrics`` plus ``realized_vacancy``,
        ``overflow_rate``, ``orphan_cells``.
    """
    base = home_match_metrics(placed, buildings_btype)

    # Realized vacancy = LeerstehendWhg / (BewohntWhg + LeerstehendWhg)
    occupied_total = (
        cells[_OCCUPIED_COL].fillna(0).sum()
        if _OCCUPIED_COL in cells.columns
        else 0.0
    )
    vacant_total = (
        cells[_VACANT_COL].fillna(0).sum()
        if _VACANT_COL in cells.columns
        else 0.0
    )
    denom = float(occupied_total) + float(vacant_total)
    realized_vacancy = float(vacant_total / denom) if denom > 0 else float("nan")

    n_hh = base["n_households"]
    overflow_rate = float(n_overcapacity / n_hh) if n_hh > 0 else float("nan")

    return {
        **base,
        "realized_vacancy": realized_vacancy,
        "overflow_rate": overflow_rate,
        "orphan_cells": int(n_zero_building_cells),
    }


# ---------------------------------------------------------------------------
# compare_typed_vs_legacy
# ---------------------------------------------------------------------------

def compare_typed_vs_legacy(
    placed_typed: pd.DataFrame,
    placed_legacy: pd.DataFrame,
    buildings_btype: pd.DataFrame,
) -> dict:
    """Compare type-match share between typed and legacy placed frames.

    Parameters
    ----------
    placed_typed, placed_legacy:
        Home outputs (same schema as ``placed`` in module docstring).
    buildings_btype:
        Output of :func:`derive_buildings_btype`.

    Returns
    -------
    dict
        Keys: ``type_match_typed``, ``type_match_legacy``, ``delta``
        (= typed - legacy).
    """
    m_typed = home_match_metrics(placed_typed, buildings_btype)
    m_legacy = home_match_metrics(placed_legacy, buildings_btype)
    typed_val = m_typed["type_match_share"]
    legacy_val = m_legacy["type_match_share"]
    return {
        "type_match_typed": typed_val,
        "type_match_legacy": legacy_val,
        "delta": float(typed_val) - float(legacy_val),
    }


# ---------------------------------------------------------------------------
# __main__ CLI
# ---------------------------------------------------------------------------

def _fmt_report(report: dict) -> str:
    lines = []
    for k, v in report.items():
        if isinstance(v, float):
            lines.append(f"  {k:<28s}: {v:.4f}")
        else:
            lines.append(f"  {k:<28s}: {v}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a typed home placement run. "
            "--placed is a parquet with columns: household_id, building_type_3class, "
            "household_size, home_location_id."
        )
    )
    parser.add_argument("--placed", required=True, metavar="PARQUET",
                        help="Placed output parquet (home output joined to sampled pop).")
    parser.add_argument("--buildings", required=True, metavar="PARQUET",
                        help="Buildings parquet (building_id, area_m2, geometry).")
    parser.add_argument("--cells", required=True, metavar="PARQUET",
                        help="Prepared 100 m cells parquet.")
    parser.add_argument("--seed", type=int, default=0, metavar="INT",
                        help="Random seed for derive_buildings_btype (default: 0).")
    parser.add_argument("--legacy", default=None, metavar="PARQUET",
                        help="Optional legacy placed parquet for comparison.")
    parser.add_argument("--overcapacity", type=int, default=0, metavar="INT",
                        help="n_overcapacity from TypedHomeReport (default: 0).")
    parser.add_argument("--orphans", type=int, default=0, metavar="INT",
                        help="n_zero_building_cells from TypedHomeReport (default: 0).")
    args = parser.parse_args()

    import geopandas as gpd  # only needed here for parquet with geometry

    placed = pd.read_parquet(args.placed)
    cells = pd.read_parquet(args.cells)

    try:
        buildings = gpd.read_parquet(args.buildings)
    except Exception:
        buildings = pd.read_parquet(args.buildings)

    print("Deriving per-building btypes …")
    btype = derive_buildings_btype(buildings, cells, random_seed=args.seed)

    print("\n=== Home Match Report ===")
    report = home_match_report(
        placed, btype, cells,
        n_overcapacity=args.overcapacity,
        n_zero_building_cells=args.orphans,
    )
    print(_fmt_report(report))

    if args.legacy:
        placed_legacy = pd.read_parquet(args.legacy)
        print("\n=== Typed vs Legacy ===")
        comp = compare_typed_vs_legacy(placed, placed_legacy, btype)
        print(_fmt_report(comp))


if __name__ == "__main__":
    main()
