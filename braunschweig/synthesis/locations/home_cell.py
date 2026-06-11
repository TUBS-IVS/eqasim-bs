"""Cell-accurate home placement for the PopulationSim workflows.

The legacy home-location chain (``synthesis.population.spatial.home.zones`` ->
``synthesis.locations.home.locations`` -> ``synthesis.population.spatial.home.locations``)
samples each household a home building anywhere inside its Gemeinde (IRIS),
weighted by building footprint area. That discards the spatial precision the
PopulationSim workflows produce: every household carries its real Zensus 2022
100 m INSPIRE cell (``ZENSUS100m``, EPSG:3035), so it can be placed in a building
INSIDE that 100 m cell instead of anywhere in the commune.

This module is a drop-in replacement for the FINAL home-point stage
``synthesis.population.spatial.home.locations``. It produces the IDENTICAL output
schema (``[household_id, commune_id, home_location_id, geometry]``, one row per
household, geometry = the chosen building point in the buildings' native
EPSG:25832), so every downstream stage (commute distance, primary/secondary
locations, MATSim writers) is unaffected in shape. Only the popsim configs alias
this stage; the legacy/IPF configs keep the Gemeinde-level home stage untouched.

Draw method
-----------
Within each 100 m cell a household draws a building proportional to its
``weight`` (= footprint area, the dwelling-capacity proxy already used by the
legacy area-weighted sampler), seeded deterministically. This is more faithful
than the pure round-robin of ``braunschweig.popsim.handoff`` (a 1000 m^2 block
and a 40 m^2 single-family house should NOT receive equal household counts); it
keeps the legacy area-weighting semantics while adding the cell constraint. The
cell-grouping / fallback-reporting structure mirrors ``popsim.handoff``.

Fallback (CLAUDE.md "Fallback transparency", no silent fallbacks)
-----------------------------------------------------------------
PRIMARY path: the household's 100 m cell contains at least one building -> the
draw is restricted to that cell (cell-accurate). FALLBACK path: the cell has no
building -> the household falls back to a commune-level area-weighted draw
(restricted to its own commune, the legacy behaviour). The cell-accurate vs
commune-fallback split is counted and logged; households whose commune ALSO has
no building (should be ~0) are logged loudly and left unplaced (they would be
dropped, never silently placed elsewhere).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# CRS of the Zensus INSPIRE grid (LAEA Europe). Building centroids are reprojected
# here to compute their 100 m cell id.
ZENSUS_CRS = "EPSG:3035"

# The buildings' native CRS (UTM zone 32N). The output geometry stays in this CRS
# to match exactly what the legacy home-location stage emits.
BUILDINGS_CRS = "EPSG:25832"

# Zensus INSPIRE 100 m cell edge length in metres.
CELL_SIZE_M = 100

# Deterministic seed offset for the home-cell draw, so this stage's RNG stream is
# independent of every other ``random_seed``-derived stream in the pipeline.
RANDOM_SEED_OFFSET = 91207


def building_cell_id(north_m: float, east_m: float) -> str:
    """Return the 100 m INSPIRE cell id (``CRS3035RES100m...``) of a 3035 point.

    The INSPIRE id encodes the cell's south-west corner, so the SW corner is the
    point's coordinates floored to the enclosing 100 m grid line
    (``floor(coord / 100) * 100``). This is the inverse of
    :func:`braunschweig.popsim.cells.parse_inspire_id`.

    Parameters
    ----------
    north_m, east_m:
        A point's EPSG:3035 northing / easting in metres.

    Returns
    -------
    str
        The 100 m cell id whose square contains the point.
    """
    north_corner = int(math.floor(north_m / CELL_SIZE_M) * CELL_SIZE_M)
    east_corner = int(math.floor(east_m / CELL_SIZE_M) * CELL_SIZE_M)
    return f"CRS3035RES100mN{north_corner}E{east_corner}"


@dataclass(frozen=True)
class HomeCellReport:
    """Outcome of the cell-accurate home placement.

    Attributes
    ----------
    n_households:
        Total households to place.
    n_in_cell:
        Households placed in a building of their OWN 100 m cell (primary).
    n_commune_fallback:
        Households whose cell had no building, placed via the commune-level
        area-weighted fallback.
    n_unplaced:
        Households whose commune also had no building (left unplaced; should be
        ~0). Logged loudly, never silently relocated.
    in_cell_rate:
        ``n_in_cell / n_households`` (0..1).
    """

    n_households: int
    n_in_cell: int
    n_commune_fallback: int
    n_unplaced: int
    in_cell_rate: float


def _weighted_choice(rng: np.random.RandomState, row_indices: np.ndarray,
                     weights: np.ndarray) -> int:
    """Draw one row index proportional to ``weights`` (defensive on zero/NaN)."""
    weights = np.asarray(weights, dtype=float)
    weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 0.0)
    total = weights.sum()
    if total <= 0.0:
        # Degenerate weights (all zero): fall back to a uniform draw so a real
        # building is still chosen rather than crashing.
        return int(rng.choice(row_indices))
    probabilities = weights / total
    return int(rng.choice(row_indices, p=probabilities))


def assign_homes_to_cell_buildings(
    households: pd.DataFrame,
    buildings: gpd.GeoDataFrame,
    *,
    random_seed: int,
    cell_col: str = "ZENSUS100m",
    commune_col: str = "commune_id",
    household_id_col: str = "household_id",
    weight_col: str = "weight",
) -> tuple[gpd.GeoDataFrame, HomeCellReport]:
    """Place each household in an area-weighted building of its own 100 m cell.

    For every household: if its ``ZENSUS100m`` cell contains buildings, draw one
    proportional to ``weight`` (PRIMARY, cell-accurate); otherwise draw an
    area-weighted building of its commune (FALLBACK); if the commune is empty too,
    leave it unplaced (logged loudly). The draw is seeded
    (``random_seed + RANDOM_SEED_OFFSET``) and deterministic.

    Parameters
    ----------
    households:
        One row per household with ``household_id_col``, ``commune_col`` and the
        ``cell_col`` (100 m INSPIRE id).
    buildings:
        Building GeoDataFrame from ``braunschweig.data.buildings`` (``building_id``,
        ``weight``, ``commune_id``, geometry in EPSG:25832). A per-building 100 m
        cell id is computed here from the reprojected centroid.
    random_seed:
        Base seed; the effective seed is ``random_seed + RANDOM_SEED_OFFSET``.

    Returns
    -------
    tuple[geopandas.GeoDataFrame, HomeCellReport]
        The ``[household_id, commune_id, home_location_id, geometry]`` frame (CRS
        EPSG:25832, one row per placed household) and the placement report.

    Raises
    ------
    ValueError
        If required columns are absent (fail-fast).
    """
    missing_h = [c for c in (household_id_col, commune_col, cell_col)
                 if c not in households.columns]
    if missing_h:
        raise ValueError(
            f"households frame is missing required column(s) {missing_h}; "
            f"available: {list(households.columns)}."
        )
    for col in ("building_id", weight_col, commune_col):
        if col not in buildings.columns:
            raise ValueError(
                f"buildings frame is missing required column {col!r}; available: "
                f"{list(buildings.columns)}."
            )
    if buildings.crs is None:
        raise ValueError("buildings frame has no CRS; cannot reproject to 3035.")

    rng = np.random.RandomState(int(random_seed) + RANDOM_SEED_OFFSET)

    # 1) Compute each building's 100 m cell id from its EPSG:3035 centroid.
    buildings = buildings.reset_index(drop=True)
    centroids_3035 = buildings.geometry.to_crs(ZENSUS_CRS)
    cell_ids = [
        building_cell_id(north_m=geom.y, east_m=geom.x) for geom in centroids_3035
    ]
    buildings = buildings.copy()
    buildings["_cell_id"] = cell_ids

    # 2) Index rows by cell and by commune for O(1) candidate lookup.
    cell_to_rows: dict[str, np.ndarray] = {
        str(cell): np.asarray(group.index.tolist(), dtype=int)
        for cell, group in buildings.groupby("_cell_id", sort=False)
    }
    commune_to_rows: dict[str, np.ndarray] = {
        str(commune): np.asarray(group.index.tolist(), dtype=int)
        for commune, group in buildings.groupby(commune_col, sort=False)
    }
    weights = buildings[weight_col].to_numpy(dtype=float)

    n_in_cell = 0
    n_commune_fallback = 0
    n_unplaced = 0

    chosen_household_ids: list = []
    chosen_commune_ids: list = []
    chosen_building_ids: list = []
    chosen_geometries: list = []

    building_id_values = buildings["building_id"].to_numpy()
    geometry_values = buildings.geometry.to_numpy()

    for row in households.itertuples(index=False):
        household_id = getattr(row, household_id_col)
        commune_id = str(getattr(row, commune_col))
        cell_id = str(getattr(row, cell_col))

        candidate_rows = cell_to_rows.get(cell_id)
        if candidate_rows is not None and len(candidate_rows) > 0:
            n_in_cell += 1
        else:
            candidate_rows = commune_to_rows.get(commune_id)
            if candidate_rows is not None and len(candidate_rows) > 0:
                n_commune_fallback += 1
            else:
                # No building in the cell AND none in the commune: leave unplaced
                # rather than silently relocating to another commune.
                n_unplaced += 1
                continue

        picked = _weighted_choice(rng, candidate_rows, weights[candidate_rows])
        chosen_household_ids.append(household_id)
        chosen_commune_ids.append(getattr(row, commune_col))
        chosen_building_ids.append(building_id_values[picked])
        chosen_geometries.append(geometry_values[picked])

    n_households = len(households)
    in_cell_rate = (n_in_cell / n_households) if n_households else 0.0

    report = HomeCellReport(
        n_households=n_households,
        n_in_cell=n_in_cell,
        n_commune_fallback=n_commune_fallback,
        n_unplaced=n_unplaced,
        in_cell_rate=in_cell_rate,
    )

    log = logger.warning if (n_commune_fallback or n_unplaced) else logger.info
    log(
        "[home_cell] %d households: %d placed in their 100m cell (%.1f%%), "
        "%d commune-fallback (no building in cell), %d unplaced (no building in "
        "commune)",
        n_households, n_in_cell, in_cell_rate * 100.0,
        n_commune_fallback, n_unplaced,
    )
    if n_unplaced:
        logger.error(
            "[home_cell] %d households left UNPLACED: their commune has no "
            "residential building footprint at all -- check the ALKIS coverage / "
            "commune_id join.", n_unplaced,
        )

    result = gpd.GeoDataFrame(
        {
            "household_id": chosen_household_ids,
            "commune_id": chosen_commune_ids,
            "home_location_id": chosen_building_ids,
            "geometry": chosen_geometries,
        },
        crs=buildings.crs,
    )
    # Match the buildings' native CRS in the output (the legacy stage emits 25832).
    if result.crs is not None and result.crs.to_epsg() != 25832:
        result = result.to_crs(BUILDINGS_CRS)
    return result, report


def configure(context):
    context.stage("braunschweig.data.buildings")
    context.stage("synthesis.population.sampled")
    context.config("random_seed")


def execute(context):
    buildings = context.stage("braunschweig.data.buildings")
    df_sampled = context.stage("synthesis.population.sampled")

    if "ZENSUS100m" not in df_sampled.columns:
        raise ValueError(
            "[home_cell] synthesis.population.sampled has no 'ZENSUS100m' column; "
            "this stage is intended for the PopulationSim workflows where every "
            "household carries its 100 m cell. Use the legacy home stage for the "
            "IPF/open workflows."
        )

    households = (
        df_sampled[["household_id", "ZENSUS100m", "commune_id"]]
        .drop_duplicates("household_id")
        .reset_index(drop=True)
    )

    result, _ = assign_homes_to_cell_buildings(
        households, buildings, random_seed=context.config("random_seed"),
    )
    return result[["household_id", "commune_id", "home_location_id", "geometry"]]
