"""Pure functions for commuter (Pendler) analysis from MATSim/eqasim run records.

All functions are side-effect-free: they accept plain Python dicts / GeoDataFrames
and return plain Python or pandas objects. No I/O is performed here; the caller
is responsible for reading inputs and writing outputs.

Data shapes assumed
-------------------
od_matrix (from run record)
    ``record["matsim"]["od_matrix"]`` is a dict with keys:
    - ``"zones"``      : list of ars5 strings, last entry may be ``"external"``
    - ``"zone_names"`` : parallel list of human-readable names
    - ``"purposes"``   : list of trip-purpose strings present in ``"matrices"``
    - ``"matrices"``   : dict mapping purpose -> 2-D list (rows=origin, cols=dest)

commutes_gdf
    GeoDataFrame of per-person home->work LineStrings, CRS EPSG:25832.
    The START point of each LineString is the home location, the END point is
    the work location.

kreise_gdf
    GeoDataFrame with columns ``ars5`` (5-digit ARS Kreis code) and polygon
    ``geometry`` covering the 8 ZGB Kreise, CRS EPSG:25832.
"""
from __future__ import annotations

import copy
from typing import Optional

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point


_EXTERNAL_ZONE = "external"


def commute_matrix_from_record(
    record: dict,
    purpose: str = "work",
) -> Optional[tuple[list[str], list[list[int]]]]:
    """Extract a commute OD matrix from a run record.

    Parameters
    ----------
    record:
        Run record dict as produced by ``build_dashboard.assemble_run_record``.
        Must contain ``record["matsim"]["od_matrix"]``.
    purpose:
        Trip purpose to extract (e.g. ``"work"``).  Must be a key in
        ``od_matrix["matrices"]``.

    Returns
    -------
    ``(zones, matrix)`` where *zones* is the list of zone labels and *matrix*
    is a 2-D list of integer counts (deep copies; mutating the return value
    does not affect *record*).  Returns ``None`` if the od_matrix key or the
    requested purpose is absent, so the caller can skip the step with a log
    message instead of raising.
    """
    try:
        od = record["matsim"]["od_matrix"]
    except (KeyError, TypeError):
        return None

    matrices: dict | None = od.get("matrices")
    if not matrices or purpose not in matrices:
        return None

    zones: list[str] = copy.copy(od["zones"])
    matrix: list[list[int]] = copy.deepcopy(matrices[purpose])
    return zones, matrix


def commute_matrix_from_synthesis(
    commutes_gdf: gpd.GeoDataFrame,
    kreise_gdf: gpd.GeoDataFrame,
) -> tuple[list[str], list[list[int]]]:
    """Build a Kreis x Kreis commute OD matrix from per-person LineStrings.

    Points that fall outside all Kreise polygons are classified as
    ``"external"``.  The returned zone list is ``sorted(ars5_values) +
    ["external"]`` so it matches the convention used in
    :func:`commute_matrix_from_record`.

    The spatial classification uses a vectorised ``geopandas.sjoin`` with
    ``predicate="within"`` — one spatial index query covers the whole frame,
    avoiding per-row Python loops.

    Parameters
    ----------
    commutes_gdf:
        GeoDataFrame of LineString geometries (home -> work), CRS EPSG:25832.
        The start coordinate (``coords[0]``) is treated as the home location;
        the last coordinate (``coords[-1]``) is treated as the work location.
    kreise_gdf:
        GeoDataFrame with columns ``ars5`` (str) and polygon ``geometry``,
        CRS EPSG:25832, covering the ZGB Kreise.

    Returns
    -------
    ``(zones, matrix)`` where *zones* is sorted ars5 codes followed by
    ``"external"`` and *matrix* is a 2-D list of ``int`` counts with shape
    ``len(zones) x len(zones)``.
    """
    sorted_kreise: list[str] = sorted(kreise_gdf["ars5"].tolist())
    zones: list[str] = sorted_kreise + [_EXTERNAL_ZONE]
    n: int = len(zones)
    zone_index: dict[str, int] = {z: i for i, z in enumerate(zones)}
    ext_idx: int = zone_index[_EXTERNAL_ZONE]

    # Initialise the count matrix as a numpy array for fast indexing.
    counts = np.zeros((n, n), dtype=np.int64)

    if commutes_gdf.empty:
        return zones, counts.tolist()

    # Extract home and work points from the LineString geometries.
    # Using numpy-level coordinate access avoids a Python loop.
    coords_array = commutes_gdf.geometry.apply(lambda g: list(g.coords))
    home_points = gpd.GeoDataFrame(
        {"orig_idx": range(len(commutes_gdf))},
        geometry=coords_array.apply(lambda c: Point(c[0])),
        crs=commutes_gdf.crs,
    )
    work_points = gpd.GeoDataFrame(
        {"orig_idx": range(len(commutes_gdf))},
        geometry=coords_array.apply(lambda c: Point(c[-1])),
        crs=commutes_gdf.crs,
    )

    def _classify_points(points_gdf: gpd.GeoDataFrame) -> pd.Series:
        """Return a Series indexed by orig_idx with the ars5 or 'external'."""
        joined = points_gdf.sjoin(
            kreise_gdf[["ars5", "geometry"]],
            how="left",
            predicate="within",
        )
        # sjoin may produce duplicates when a point touches two polygon edges;
        # keep the first match per orig_idx.
        joined = joined.drop_duplicates(subset="orig_idx", keep="first")
        result = joined.set_index("orig_idx")["ars5"]
        result = result.reindex(range(len(points_gdf)))
        result = result.fillna(_EXTERNAL_ZONE)
        return result

    home_zone = _classify_points(home_points)
    work_zone = _classify_points(work_points)

    for origin, destination in zip(home_zone, work_zone):
        i = zone_index.get(origin, ext_idx)
        j = zone_index.get(destination, ext_idx)
        counts[i, j] += 1

    # Convert to nested plain Python int lists for JSON-serialisable output.
    matrix: list[list[int]] = [[int(v) for v in row] for row in counts]
    return zones, matrix


def commuter_balance(
    zones: list[str],
    matrix: list[list[int]],
) -> pd.DataFrame:
    """Compute commuter balance statistics per real Kreis.

    ``"external"`` is treated as a *source* of in-commuters but does **not**
    get its own output row — it is not a Kreis whose balance we report.

    Parameters
    ----------
    zones:
        Zone labels as returned by :func:`commute_matrix_from_record` or
        :func:`commute_matrix_from_synthesis` — sorted ars5 codes followed by
        ``"external"``.
    matrix:
        Square 2-D list of integer counts, ``matrix[i][j]`` = number of
        commuters with origin zone ``i`` and destination zone ``j``.

    Returns
    -------
    DataFrame with one row per real Kreis, sorted by ``ars5``, and columns:

    - ``ars5``
    - ``binnen`` — within-Kreis commuters (diagonal)
    - ``einpendler_intern`` — in-commuters from other ZGB Kreise
    - ``einpendler_extern`` — in-commuters from outside ZGB ("external" row)
    - ``einpendler_gesamt`` — total in-commuters
    - ``auspendler`` — out-commuters to any other zone (including external)
    - ``netto`` — einpendler_gesamt minus auspendler
    """
    arr = np.array(matrix, dtype=np.int64)

    ext_label = _EXTERNAL_ZONE
    ext_idx: int = zones.index(ext_label) if ext_label in zones else -1

    real_zones = [z for z in zones if z != ext_label]
    real_indices = [zones.index(z) for z in real_zones]

    rows = []
    for z, j in zip(real_zones, real_indices):
        binnen = int(arr[j, j])

        # In-commuters from other real Kreise (exclude self and external row).
        einpendler_intern = int(
            sum(arr[i, j] for i, zone in enumerate(zones)
                if zone != ext_label and i != j)
        )

        # In-commuters from outside ZGB.
        einpendler_extern = int(arr[ext_idx, j]) if ext_idx >= 0 else 0

        einpendler_gesamt = einpendler_intern + einpendler_extern

        # Out-commuters: all destinations except j itself.
        auspendler = int(arr[j, :].sum()) - binnen

        netto = einpendler_gesamt - auspendler

        rows.append({
            "ars5": z,
            "binnen": binnen,
            "einpendler_intern": einpendler_intern,
            "einpendler_extern": einpendler_extern,
            "einpendler_gesamt": einpendler_gesamt,
            "auspendler": auspendler,
            "netto": netto,
        })

    df = pd.DataFrame(rows, columns=[
        "ars5", "binnen", "einpendler_intern", "einpendler_extern",
        "einpendler_gesamt", "auspendler", "netto",
    ])
    df = df.sort_values("ars5").reset_index(drop=True)
    # Ensure integer dtypes for count columns.
    for col in ["binnen", "einpendler_intern", "einpendler_extern",
                "einpendler_gesamt", "auspendler"]:
        df[col] = df[col].astype(int)
    df["netto"] = df["netto"].astype(int)
    return df


def top_relations(
    zones: list[str],
    matrix: list[list[int]],
    n: int = 10,
) -> pd.DataFrame:
    """Return the top ``n`` off-diagonal OD flows, sorted descending by value.

    Diagonal cells (within-zone) are excluded by definition: this function
    reports *inter*-zone flows only.

    Parameters
    ----------
    zones:
        Zone labels (ars5 / ``"external"``).
    matrix:
        Square 2-D list of integer counts.
    n:
        Maximum number of rows to return.  If fewer off-diagonal flows exist,
        all are returned.

    Returns
    -------
    DataFrame with columns ``from``, ``to``, ``value``, sorted descending by
    ``value``.
    """
    records = []
    for i, origin in enumerate(zones):
        for j, destination in enumerate(zones):
            if i == j:
                continue
            records.append({
                "from": origin,
                "to": destination,
                "value": int(matrix[i][j]),
            })

    df = pd.DataFrame(records, columns=["from", "to", "value"])
    df = df.sort_values("value", ascending=False).head(n).reset_index(drop=True)
    return df
