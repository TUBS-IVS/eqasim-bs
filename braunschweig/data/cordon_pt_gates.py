"""synpp stage: rail-only PT entry stations reachable to ZGB directly or with one transfer.

**Schema change (Task B4):** The stage previously emitted mode-blind one-seat entry
stops with columns ``[source_ars5, stop_id, x, y, n_zgb_routes, is_rail]``.  It now
emits RAIL-ONLY eligible entry STATIONS (Bahnhoefen) with the new schema::

    [source_ars5, stop_id, x, y, reach, ewz, dist_to_zgb_km]

- ``source_ars5``    — 5-digit ARS of the external source Kreis.
- ``stop_id``        — MATSim transit-stop facility identifier.
- ``x``, ``y``       — projected coordinates (EPSG:25832, metres).
- ``reach``          — ``"direct"`` (one-seat rail route into ZGB) or
                       ``"transfer"`` (one transfer on a second rail route into ZGB).
- ``ewz``            — population of the containing external Gemeinde (persons), as a
                       proxy for the station catchment; used by downstream weighting.
- ``dist_to_zgb_km`` — minimum straight-line (Euclidean, EPSG:25832) distance in km
                       from this station to any ZGB rail stop in the schedule.  Used
                       by :func:`~braunschweig.data.cordon.pt_reachability.weight_entry_stations`
                       as the gravity decay term (Task PT-G).  ``NaN`` when no ZGB
                       reference stops are available.

Bus and tram stops are excluded by design: the entry point must be a rail station
(Bahnhof) offering a realistic cross-cordon commute.  Population attachment uses the
VG250-EW ``vg250_gem`` layer (PRIMARY: containment join; FALLBACK: nearest Gemeinde).

Flag-gated on ``cordon_enabled``; returns an empty frame with the NEW columns when OFF.

The pure core builder is exported as :func:`build_rail_entry_stations` so unit tests
can exercise the data-flow without needing file I/O or a synpp context.
"""
from __future__ import annotations

import os

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig.data.cordon.network import (
    read_external_gemeinden,
    read_kreise,
    read_transit_stops_routes,
)
from braunschweig.data.cordon.pt_reachability import (
    apply_station_distance_cap,
    attach_station_population,
    eligible_rail_entry_stations,
)

# Columns of the empty frame returned when cordon is disabled.
_NEW_COLUMNS = ["source_ars5", "stop_id", "x", "y", "reach", "ewz", "dist_to_zgb_km"]


def _map_stop_kreis(stops: dict, kreise: gpd.GeoDataFrame) -> dict:
    """Build a ``{stop_id -> ars5 | None}`` mapping via point-in-polygon against ``kreise``.

    Each stop in ``stops`` is turned into a Point in the CRS of ``kreise``.  A spatial
    join (predicate ``"within"``) assigns the stop to its containing Kreis polygon.
    Stops that fall outside every polygon (e.g. near a polygon boundary) receive
    ``None`` — they are excluded from the eligibility analysis downstream.

    This mirrors the stop->Kreis mapping logic in
    ``braunschweig.synthesis.incommuters.build_pt_entry_stops``.

    Args:
        stops: ``{stop_id: (x, y)}`` from :func:`read_transit_stops_routes`.
        kreise: GeoDataFrame ``[ars5, geometry]`` (Kreis polygons; same CRS as stops).

    Returns:
        dict ``{stop_id -> str | None}``.  Non-None values are 5-digit Kreis ARS.
    """
    ids = list(stops)
    pts = gpd.GeoDataFrame(
        {"stop_id": ids},
        geometry=[Point(stops[sid]) for sid in ids],
        crs=kreise.crs,
    )
    joined = gpd.sjoin(pts, kreise[["ars5", "geometry"]], predicate="within", how="left")
    joined = joined.drop_duplicates(subset="stop_id")
    # Coerce NaN -> None so that the downstream ``kreis not in zgb`` guard (which uses
    # ``is not None``) correctly filters unmapped stops instead of leaking NaN-keyed rows.
    # A left sjoin against a polygon set returns NaN in the "ars5" column for stops that
    # fall outside every polygon; ``np.nan != None`` and ``np.nan not in zgb`` is True,
    # so without this coercion such stops would survive as NaN-source_ars5 rows -- a
    # silent data corruption.  After coercion they match ``kreis is None`` and are dropped.
    result = {}
    for sid, ars5 in zip(joined["stop_id"], joined["ars5"]):
        result[sid] = None if (ars5 is None or (isinstance(ars5, float) and ars5 != ars5)) else str(ars5)
    return result


def build_rail_entry_stations(
    stops: dict,
    routes: list,
    kreise: gpd.GeoDataFrame,
    gemeinden: gpd.GeoDataFrame,
    zgb_kreise,
    max_station_distance_km=None,
) -> tuple:
    """Build the rail-only eligible entry stations frame (the testable pure core).

    This is the heart of the stage: it accepts the in-memory data structures produced
    by the file readers and returns the final DataFrame and fallback count without any
    file I/O.  The synpp ``execute`` wires the file reads to this function; unit tests
    call it directly with synthetic fixtures.

    Flow:
    1. Map every schedule stop to its Kreis by point-in-polygon against ``kreise``.
    2. Call :func:`eligible_rail_entry_stations` to find rail-only direct/transfer
       entry stops outside ZGB (bus/tram excluded).
    3. Attach ``x, y`` coordinates from ``stops`` for each returned ``stop_id``.
    4. Optionally apply a straight-line distance cap via
       :func:`~braunschweig.data.cordon.pt_reachability.apply_station_distance_cap`
       to exclude stations that are farther than ``max_station_distance_km`` from
       every ZGB rail stop.  The cap is skipped when ``max_station_distance_km`` is
       ``None`` (default).  See :func:`apply_station_distance_cap` for the ASSUMPTION
       note: the default value of 150 km in ``configure`` has no committed reference.
       The drop count is logged inside this function (CLAUDE.md no-silent-drop).
    5. Call :func:`attach_station_population` to join the population (``ewz``) of the
       containing external Gemeinde; the nearest-Gemeinde fallback count is returned so
       the caller can log the primary vs fallback rate.

    The distance cap (step 4) is applied BEFORE the population sjoin (step 5) to avoid
    unnecessary spatial work for stations that would be dropped anyway.

    Args:
        stops: ``{stop_id: (x, y)}`` as produced by
            :func:`braunschweig.data.cordon.network.read_transit_stops_routes`.
        routes: list of ``(mode, [stop_id, ...])`` transit routes (stop order).
        kreise: GeoDataFrame ``[ars5, geometry]`` (Kreis polygons; CRS EPSG:25832).
        gemeinden: GeoDataFrame ``[ars5, ewz, geometry]`` (external Gemeinden; CRS
            EPSG:25832) as produced by
            :func:`braunschweig.data.cordon.network.read_external_gemeinden`.
        zgb_kreise: iterable of 5-digit ZGB Kreis ARS strings.
        max_station_distance_km: optional float.  When set, stations whose minimum
            straight-line distance to any ZGB rail stop exceeds ``max_station_distance_km``
            kilometres are dropped before population attachment.  ``None`` (default)
            disables the cap and preserves the pre-B4b behaviour exactly — existing
            callers and tests that do not pass this argument are unaffected.

    Returns:
        ``(df, n_nearest_fallback)`` where:

        - ``df``: DataFrame ``[source_ars5, stop_id, x, y, reach, ewz, dist_to_zgb_km]``,
          one row per eligible external rail entry station.  ``dist_to_zgb_km`` is the
          minimum straight-line distance (km, EPSG:25832 Euclidean) from the station to
          any ZGB rail stop; ``NaN`` when no ZGB reference stops are found.  This column
          feeds the gravity decay term in
          :func:`~braunschweig.data.cordon.pt_reachability.weight_entry_stations`
          (Task PT-G).
        - ``n_nearest_fallback`` (int): stations whose population was filled via the
          nearest-Gemeinde fallback rather than a containment join (CLAUDE.md
          no-silent-fallback contract: the caller logs this count).
    """
    zgb_set = set(zgb_kreise)
    stop_kreis = _map_stop_kreis(stops, kreise)
    stations_base = eligible_rail_entry_stations(routes, stop_kreis, zgb_set)

    if len(stations_base) == 0:
        # Return an empty frame with all required columns including x, y, ewz, dist_to_zgb_km.
        empty = pd.DataFrame(columns=_NEW_COLUMNS)
        return empty, 0

    # Attach x, y coordinates from the schedule stop table.
    stations_base["x"] = stations_base["stop_id"].map(lambda sid: stops[sid][0])
    stations_base["y"] = stations_base["stop_id"].map(lambda sid: stops[sid][1])

    # Build the ZGB reference point array once (reused by both the distance cap and the
    # dist_to_zgb_km column).  These are the coordinates of all schedule stops whose
    # Kreis maps to the ZGB set -- the same set the distance cap uses.
    zgb_stop_coords = [
        stops[sid]
        for sid, kreis in stop_kreis.items()
        if kreis is not None and kreis in zgb_set and sid in stops
    ]
    if zgb_stop_coords:
        zgb_pts = np.array(zgb_stop_coords, dtype=float)
    else:
        zgb_pts = np.empty((0, 2))

    # Optional distance cap: drop stations farther than max_station_distance_km from
    # every ZGB rail stop.  Applied BEFORE population attachment to avoid unneeded
    # spatial work.  See apply_station_distance_cap for the ASSUMPTION note on the
    # default value used in configure().
    if max_station_distance_km is not None:
        n_before_cap = len(stations_base)
        stations_base, n_distance_dropped = apply_station_distance_cap(
            stations_base, zgb_pts, max_km=max_station_distance_km
        )
        # Log the drop so no station is silently removed (CLAUDE.md no-silent-drop).
        if zgb_pts.shape[0] == 0:
            print(
                f"[braunschweig.data.cordon_pt_gates] distance cap ({max_station_distance_km} km) "
                "is INACTIVE: no ZGB rail stops found to compute distances against.",
                flush=True,
            )
        else:
            print(
                f"[braunschweig.data.cordon_pt_gates] distance cap {max_station_distance_km} km: "
                f"kept {n_before_cap - n_distance_dropped}/{n_before_cap} candidate stations, "
                f"dropped {n_distance_dropped} ({100.0 * n_distance_dropped / n_before_cap:.1f}%) "
                "as unreachable by straight-line distance (ASSUMPTION, configurable).",
                flush=True,
            )
        if n_distance_dropped > 0 and n_distance_dropped == n_before_cap:
            print(
                "[braunschweig.data.cordon_pt_gates] WARNING: distance cap dropped ALL "
                f"{n_before_cap} candidate stations -- check the cap value "
                f"({max_station_distance_km} km) and the ZGB rail stop set.",
                flush=True,
            )

    if len(stations_base) == 0:
        empty = pd.DataFrame(columns=_NEW_COLUMNS)
        return empty, 0

    # Attach population catchment from the containing external Gemeinde.
    df, n_nearest_fallback = attach_station_population(stations_base, gemeinden)

    # Compute dist_to_zgb_km: minimum straight-line distance (EPSG:25832 metres -> km)
    # from each station to the nearest ZGB rail stop.  This feeds the gravity decay term
    # in weight_entry_stations (Task PT-G).  NaN when no ZGB reference stops are available.
    if zgb_pts.shape[0] > 0:
        sx = df["x"].to_numpy(dtype=float)
        sy = df["y"].to_numpy(dtype=float)
        # Vectorised: (n_stations, 1) distances to (1, n_zgb) reference points.
        dx = sx[:, np.newaxis] - zgb_pts[:, 0][np.newaxis, :]
        dy = sy[:, np.newaxis] - zgb_pts[:, 1][np.newaxis, :]
        min_dist_m = np.sqrt((dx ** 2 + dy ** 2).min(axis=1))
        df["dist_to_zgb_km"] = min_dist_m / 1000.0
    else:
        df["dist_to_zgb_km"] = float("nan")

    # Enforce the canonical column order.
    return df[_NEW_COLUMNS], n_nearest_fallback


def configure(context):
    context.config("cordon_enabled", False)
    context.config("data_path")
    context.config("cordon_vg250_path",
                   "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    context.config("braunschweig.political_prefix")
    if context.config("cordon_enabled"):
        context.stage("matsim.scenario.supply.processed")
        # ASSUMPTION: 150 km has no committed reference source in this repository.
        # It is chosen to retain the typical rail-commuter catchment (Magdeburg ~145 km,
        # Hannover ~65 km, Goettingen ~100 km, Hildesheim ~50 km from Braunschweig HBF)
        # while excluding absurd topological chains where a station 300+ km away happens
        # to share a transfer stop with a ZGB-serving line.  Straight-line distance is
        # a lower bound on rail travel distance and is therefore conservative (it will
        # never incorrectly exclude a station that is truly within reach).
        # Re-calibrate against observed in-commuter origins when origin data are available.
        context.config("cordon_pt_max_station_distance_km", 150.0)


def execute(context):
    if not context.config("cordon_enabled"):
        return pd.DataFrame(columns=_NEW_COLUMNS)

    supply = context.stage("matsim.scenario.supply.processed")
    schedule_path = "%s/%s" % (context.path("matsim.scenario.supply.processed"),
                               supply["schedule_path"])
    stops, routes = read_transit_stops_routes(schedule_path)

    vg250 = os.path.join(context.config("data_path"), context.config("cordon_vg250_path"))
    crs = "EPSG:25832"
    kreise = read_kreise(vg250, crs=crs)
    gemeinden = read_external_gemeinden(vg250, crs=crs,
                                        zgb_prefixes=context.config(
                                            "braunschweig.political_prefix"))

    zgb = {str(p) for p in context.config("braunschweig.political_prefix")}
    max_km = context.config("cordon_pt_max_station_distance_km")
    df, n_fallback = build_rail_entry_stations(
        stops, routes, kreise, gemeinden, zgb,
        max_station_distance_km=max_km,
    )

    # --- Logging (CLAUDE.md no-silent-fallback) ---
    # Distance cap logging is emitted by build_rail_entry_stations so the count is
    # always visible regardless of whether execute() is the caller.
    n_total = len(df)
    n_direct = int((df["reach"] == "direct").sum()) if n_total else 0
    n_transfer = int((df["reach"] == "transfer").sum()) if n_total else 0
    n_kreise = int(df["source_ars5"].nunique()) if n_total else 0
    fallback_rate = 100.0 * n_fallback / n_total if n_total else 0.0
    cap_label = f"{max_km} km" if max_km is not None else "disabled"

    print(
        f"[braunschweig.data.cordon_pt_gates] {n_total} eligible rail entry stations "
        f"across {n_kreise} source Kreise "
        f"({n_direct} direct, {n_transfer} one-transfer); "
        f"distance cap: {cap_label}; "
        f"population-catchment fallback {n_fallback}/{n_total} "
        f"({fallback_rate:.1f}%) nearest-Gemeinde "
        f"(PRIMARY containment {n_total - n_fallback}/{n_total}).",
        flush=True,
    )
    if n_fallback == n_total and n_total > 0:
        print(
            "[braunschweig.data.cordon_pt_gates] WARNING: ALL stations used the "
            "nearest-Gemeinde fallback -- this almost certainly means a CRS mismatch "
            "between the transit schedule and the VG250-EW Gemeinden layer.",
            flush=True,
        )

    return df
