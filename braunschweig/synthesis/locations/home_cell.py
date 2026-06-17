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
from shapely.geometry import Point, box as _shapely_box

from braunschweig.popsim.cells import parse_inspire_id as _parse_inspire_id
from braunschweig.popsim.prepared_cells import load_prepared_cells
from braunschweig.synthesis.locations import building_typing as bt
from braunschweig.synthesis.locations import cell_building_signals as cbs
from braunschweig.synthesis.locations import home_matcher as hm

logger = logging.getLogger(__name__)

# Config key selecting the home-matching mode: "typed" (ALKIS type-aware, default)
# or "legacy" (the area-weighted cell draw, byte-identical to the prior behaviour).
KEY_HOME_MATCHING = "braunschweig.home_matching"

# Default value for the home-matching mode config key.
_DEFAULT_HOME_MATCHING = "typed"

# Config key for the prepared 100 m cell parquet (shared with the popsim stage).
KEY_CELLS_100M = "braunschweig.population.popsim.cells_100m_path"

# MiD ``building_type_3class`` label -> the matcher's 3-class building type.
_BTYPE_MAP = {
    "ein_zweifamilienhaus": "efh_zfh",
    "mehrfamilienhaus": "mfh",
    "sonstiges": "sonst",
}

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

# Upper area cap (m^2) applied ONLY in the legacy area-weighted draw.
# The shared buildings stage is now uncapped so the typed path can use large
# MFH blocks (it bounds via capacity). Legacy has no capacity mechanism, so a
# large footprint would dominate the area-weighted lottery -> restore the
# pre-branch 400 m^2 guard here only.
LEGACY_AREA_MAX = 400.0


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


def _legacy_capped_buildings(buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Re-apply the pre-branch <400 m^2 cap for the capacity-free legacy draw.

    The shared buildings stage is now uncapped (typed path uses large blocks);
    legacy has no capacity mechanism so a large footprint would dominate its
    area-weighted draw -> restore the cap here only.

    If ``area_m2`` is absent (should not happen after the shared-stage refactor
    but guarded defensively), the frame is returned unchanged.
    """
    if "area_m2" not in buildings.columns:
        return buildings
    capped = buildings[buildings["area_m2"] < LEGACY_AREA_MAX].copy().reset_index(drop=True)
    capped["building_id"] = np.arange(len(capped))
    return capped


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


@dataclass(frozen=True)
class TypedHomeReport:
    """Outcome of the ALKIS-typed, census-calibrated home placement.

    Attributes
    ----------
    n_households:
        Total households to place.
    in_cell_rate:
        Fraction of households whose 100 m cell contained at least one building
        (so they were matched against in-cell typed slots rather than the
        in-cell random-point fallback).
    type_match_rate:
        Fraction of households assigned a building whose 3-class type equals the
        household's preferred type (``btype``); the primary objective of the
        lexicographic matcher.
    n_zero_building_cells:
        Households whose 100 m cell had no building footprint at all; placed at a
        random point inside the cell (never relocated to another cell/commune).
    n_overcapacity:
        Households the matcher had to over-occupy an existing building for (more
        households than typed dwelling slots in the cell).
    """

    n_households: int
    in_cell_rate: float
    type_match_rate: float
    n_zero_building_cells: int
    n_overcapacity: int


def assign_homes_typed(
    households: pd.DataFrame,
    buildings: gpd.GeoDataFrame,
    cells: pd.DataFrame,
    *,
    random_seed: int,
    cell_col: str = "ZENSUS100m",
    commune_col: str = "commune_id",
    household_id_col: str = "household_id",
) -> tuple[gpd.GeoDataFrame, TypedHomeReport]:
    """Place each household in a type- and size-matched building of its 100 m cell.

    Per 100 m cell: type the cell's ALKIS footprints from the census 3-class
    building-count signal (:func:`building_typing.assign_building_types`), build
    capacitated dwelling slots from the dwelling-count + occupancy + dwelling-size
    signals (:func:`building_typing.build_slots`), then lexicographically match the
    cell's households to slots on type (primary) and size (secondary)
    (:func:`home_matcher.match_cell`). The chosen ``building_id`` maps to that
    building's centroid (EPSG:25832). Households in a cell with NO building are
    placed at a random point inside the cell (:func:`home_matcher.random_point_in_cell`),
    never silently relocated.

    Parameters
    ----------
    households:
        One row per household with ``household_id_col``, ``commune_col``,
        ``cell_col`` (100 m INSPIRE id), ``building_type_3class`` (MiD label) and
        ``household_size``.
    buildings:
        Building GeoDataFrame from ``braunschweig.data.buildings`` (``building_id``,
        ``area_m2``, geometry in EPSG:25832). The per-building 100 m cell id is
        computed here from the reprojected centroid.
    cells:
        Prepared 100 m cell frame (from ``load_prepared_cells``) carrying the
        Gebaeudetyp / Wohnung / occupancy / dwelling-size columns the signal
        extractor reads.
    random_seed:
        Base seed; the effective seed is ``random_seed + RANDOM_SEED_OFFSET``.

    Returns
    -------
    tuple[geopandas.GeoDataFrame, TypedHomeReport]
        The ``[household_id, commune_id, home_location_id, geometry]`` frame (CRS
        EPSG:25832, one row per household) and the placement report.
    """
    rng = np.random.RandomState(int(random_seed) + RANDOM_SEED_OFFSET)
    buildings = buildings.reset_index(drop=True).copy()
    cent3035 = buildings.geometry.to_crs(ZENSUS_CRS)
    buildings["_cell_id"] = [building_cell_id(north_m=g.y, east_m=g.x) for g in cent3035]

    # Sanity-check: household ZENSUS100m ids must use the INSPIRE 100 m format.
    # A mismatch here almost certainly means a wrong CRS or misjoined column,
    # which would silently yield zero matches — better to fail loudly.
    _INSPIRE_PREFIX = "CRS3035RES100m"
    if len(households) > 0:
        _sample = households[cell_col].dropna().iloc[:5]
        _bad = _sample[~_sample.astype(str).str.startswith(_INSPIRE_PREFIX)]
        if not _bad.empty:
            raise ValueError(
                f"[home_typed] household '{cell_col}' values do not start with "
                f"'{_INSPIRE_PREFIX}' (expected INSPIRE 100m format). "
                f"Offending value: {_bad.iloc[0]!r}. "
                "Check that the ZENSUS100m column was joined correctly."
            )

    # building_id -> its centroid Point in the buildings' native CRS (EPSG:25832).
    bcent = buildings.geometry.centroid
    geom_by_bid = dict(zip(buildings["building_id"], bcent))

    # Build cell -> building-rows mapping.
    # INTERSECTION mode (preferred): when the buildings frame carries a ``footprint``
    # polygon column (EPSG:25832), reproject each footprint to EPSG:3035, enumerate
    # the 100 m INSPIRE cells it intersects via bbox + actual geometry check, and add
    # the building row to EACH of those cells.  This ensures that a footprint
    # straddling a cell boundary is a candidate in BOTH cells rather than only the
    # one containing its centroid, eliminating false zero-building orphans.
    #
    # CENTROID mode (backward-compatible fallback): if no ``footprint`` column is
    # present (e.g. legacy callers, unit tests without footprint data), fall back to
    # the centroid-based groupby — byte-identical to the previous behaviour.
    _has_footprint = "footprint" in buildings.columns

    if _has_footprint:
        # Reproject footprint polygons to EPSG:3035 for grid-aligned intersection.
        fp_series_3035 = gpd.GeoSeries(
            buildings["footprint"].values, crs=buildings.crs
        ).to_crs(ZENSUS_CRS)

        fps_by_cell: dict[str, list[int]] = {}  # cell_id -> list of integer row indices
        for row_idx, fp_3035 in zip(buildings.index, fp_series_3035):
            if fp_3035 is None or fp_3035.is_empty:
                # Fall back to centroid cell for degenerate footprints (e.g. placeholders).
                cid = buildings.at[row_idx, "_cell_id"]
                fps_by_cell.setdefault(cid, []).append(row_idx)
                continue
            minx, miny, maxx, maxy = fp_3035.bounds
            # Enumerate all 100 m cells whose SW corner falls within the footprint's bbox.
            e_start = int(math.floor(minx / CELL_SIZE_M) * CELL_SIZE_M)
            n_start = int(math.floor(miny / CELL_SIZE_M) * CELL_SIZE_M)
            e_end = int(math.floor(maxx / CELL_SIZE_M) * CELL_SIZE_M)
            n_end = int(math.floor(maxy / CELL_SIZE_M) * CELL_SIZE_M)
            e_cur = e_start
            while e_cur <= e_end:
                n_cur = n_start
                while n_cur <= n_end:
                    # Quick intersection check: does the footprint actually intersect
                    # this 100 m cell square?
                    cell_square = _shapely_box(e_cur, n_cur, e_cur + CELL_SIZE_M, n_cur + CELL_SIZE_M)
                    if fp_3035.intersects(cell_square):
                        cid = f"CRS3035RES100mN{n_cur}E{e_cur}"
                        fps_by_cell.setdefault(cid, []).append(row_idx)
                    n_cur += CELL_SIZE_M
                e_cur += CELL_SIZE_M

        # Convert row-index lists to sub-DataFrames for compatibility with the cell loop.
        fps_by_cell_df = {
            cid: buildings.iloc[idxs] for cid, idxs in fps_by_cell.items()
        }
    else:
        # Centroid fallback: byte-identical to the original behaviour.
        fps_by_cell_df = {c: g for c, g in buildings.groupby("_cell_id", sort=False)}

    # For intersection mode: home point for an HH placed in cell `c` on building `b`
    # = b's centroid clamped into c's 100 m square (EPSG:3035), then reprojected to
    # EPSG:25832.  This guarantees the home lies inside the household's own cell even
    # when b's centroid sits in a neighbouring cell (the common case for boundary
    # footprints).  For buildings already inside c, clamping is a no-op.
    def _home_point_for_cell(bid: int, cell_id: str) -> object:
        """Return the home point (EPSG:25832) for building ``bid`` placed in ``cell_id``."""
        pt_25832 = geom_by_bid.get(bid)
        if pt_25832 is None:
            return hm.random_point_in_cell(cell_id, rng)
        if not _has_footprint:
            return pt_25832
        # Clamp the centroid into the cell's 100 m square (EPSG:3035).
        _, n_sw, e_sw = _parse_inspire_id(cell_id)
        # Reproject centroid to 3035, clamp, reproject back.
        pt_3035_gs = gpd.GeoSeries([pt_25832], crs=buildings.crs).to_crs(ZENSUS_CRS)
        cx, cy = pt_3035_gs.iloc[0].x, pt_3035_gs.iloc[0].y
        cx_c = max(float(e_sw), min(float(e_sw + CELL_SIZE_M), cx))
        cy_c = max(float(n_sw), min(float(n_sw + CELL_SIZE_M), cy))
        clamped = gpd.GeoSeries(
            [Point(cx_c, cy_c)],
            crs=ZENSUS_CRS,
        ).to_crs(buildings.crs)
        return clamped.iloc[0]

    sig = cbs.cell_signals(cells).set_index("ZENSUS100m")

    hh = households.copy()
    hh["btype"] = hh["building_type_3class"].map(_BTYPE_MAP).fillna("efh_zfh")
    rec_id, rec_comm, rec_bid, rec_geom = [], [], [], []
    n_match = n_zero = n_over = n_in_cell = 0
    for cell_id, grp in hh.groupby(cell_col, sort=False):
        fps = fps_by_cell_df.get(str(cell_id))
        if fps is None or len(fps) == 0:
            for r in grp.itertuples(index=False):
                rec_id.append(getattr(r, household_id_col))
                rec_comm.append(getattr(r, commune_col))
                rec_bid.append(pd.NA)
                rec_geom.append(hm.random_point_in_cell(str(cell_id), rng))
            n_zero += len(grp)
            continue
        n_in_cell += len(grp)
        s = sig.loc[str(cell_id)] if str(cell_id) in sig.index else None
        geb = {c: float(s.get(f"geb_{c}", 0)) if s is not None else 0.0
               for c in cbs.THREE_CLASSES}
        whg = {c: float(s.get(f"whg_{c}", 0)) if s is not None else 0.0
               for c in cbs.THREE_CLASSES}
        occ = float(s["occupied"]) if s is not None else float(len(grp))
        size_hist = s["size_hist"] if s is not None else []
        typed = bt.assign_building_types(fps[["building_id", "area_m2"]], geb, rng)
        slots = bt.build_slots(typed, whg, max(occ, len(grp)), size_hist, rng)
        cell_hh = grp[[household_id_col, "btype", "household_size"]].rename(
            columns={household_id_col: "household_id"})
        amap, rep = hm.match_cell(cell_hh, slots, rng)
        n_match += rep.n_type_match
        n_over += rep.n_overcapacity
        bid_by_hh = dict(zip(amap["household_id"], amap["building_id"]))
        for r in grp.itertuples(index=False):
            hid = getattr(r, household_id_col)
            bid = bid_by_hh.get(hid)
            rec_id.append(hid)
            rec_comm.append(getattr(r, commune_col))
            rec_bid.append(bid)
            rec_geom.append(
                _home_point_for_cell(bid, str(cell_id)) if pd.notna(bid)
                else hm.random_point_in_cell(str(cell_id), rng)
            )

    n = len(hh)
    report = TypedHomeReport(
        n_households=n,
        in_cell_rate=(n_in_cell / n if n else 0.0),
        type_match_rate=(n_match / n if n else 0.0),
        n_zero_building_cells=n_zero,
        n_overcapacity=n_over,
    )
    log = logger.warning if (n_zero or n_over) else logger.info
    log(
        "[home_typed] %d HH: in-cell %.1f%%, type-match %.1f%%, %d zero-building, "
        "%d over-capacity", n, report.in_cell_rate * 100, report.type_match_rate * 100,
        n_zero, n_over,
    )
    result = gpd.GeoDataFrame(
        {"household_id": rec_id, "commune_id": rec_comm,
         "home_location_id": rec_bid, "geometry": rec_geom},
        crs=buildings.crs,
    )
    if result.crs is not None and result.crs.to_epsg() != 25832:
        result = result.to_crs(BUILDINGS_CRS)
    return result[["household_id", "commune_id", "home_location_id", "geometry"]], report


def configure(context):
    context.stage("braunschweig.data.buildings")
    context.stage("synthesis.population.sampled")
    context.config("random_seed")
    # Home-matching mode: "typed" (default, ALKIS type-aware) or "legacy".
    context.config(KEY_HOME_MATCHING, _DEFAULT_HOME_MATCHING)
    # Prepared 100 m cell parquet path (only read on the typed path, but declared
    # here so the stage's config dependency is explicit).
    context.config(KEY_CELLS_100M)


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

    mode = context.config(KEY_HOME_MATCHING, _DEFAULT_HOME_MATCHING)
    if mode == "legacy":
        households = (
            df_sampled[["household_id", "ZENSUS100m", "commune_id"]]
            .drop_duplicates("household_id")
            .reset_index(drop=True)
        )
        result, _ = assign_homes_to_cell_buildings(
            households, _legacy_capped_buildings(buildings),
            random_seed=context.config("random_seed"),
        )
        return result[["household_id", "commune_id", "home_location_id", "geometry"]]

    # Typed path: type- and size-matched placement against ALKIS footprints,
    # calibrated by the prepared 100 m cell signals.
    typed_cols = ["household_id", "ZENSUS100m", "commune_id",
                  "building_type_3class", "household_size"]
    missing = [c for c in typed_cols if c not in df_sampled.columns]
    if missing:
        raise ValueError(
            f"[home_cell] typed home matching needs column(s) {missing} on "
            f"synthesis.population.sampled; available: {list(df_sampled.columns)}. "
            "Set braunschweig.home_matching='legacy' for workflows whose sampled "
            "frame lacks building_type_3class / household_size."
        )
    cells = load_prepared_cells(context.config(KEY_CELLS_100M))
    households = df_sampled[typed_cols].drop_duplicates("household_id").reset_index(drop=True)
    result, _ = assign_homes_typed(
        households, buildings, cells, random_seed=context.config("random_seed"),
    )
    return result[["household_id", "commune_id", "home_location_id", "geometry"]]
