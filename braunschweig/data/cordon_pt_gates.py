"""synpp stage: rail-only PT entry stations reachable to ZGB directly or with one transfer.

**Schema change (Task B4):** The stage previously emitted mode-blind one-seat entry
stops with columns ``[source_ars5, stop_id, x, y, n_zgb_routes, is_rail]``.  It now
emits RAIL-ONLY eligible entry STATIONS (Bahnhoefen) with the new schema::

    [source_ars5, stop_id, x, y, reach, ewz]

- ``source_ars5`` — 5-digit ARS of the external source Kreis.
- ``stop_id``     — MATSim transit-stop facility identifier.
- ``x``, ``y``    — projected coordinates (EPSG:25832, metres).
- ``reach``       — ``"direct"`` (one-seat rail route into ZGB) or
                    ``"transfer"`` (one transfer on a second rail route into ZGB).
- ``ewz``         — population of the containing external Gemeinde (persons), as a
                    proxy for the station catchment; used by downstream weighting.

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
import pandas as pd
from shapely.geometry import Point

from braunschweig.data.cordon.network import (
    read_external_gemeinden,
    read_kreise,
    read_transit_stops_routes,
)
from braunschweig.data.cordon.pt_reachability import (
    attach_station_population,
    eligible_rail_entry_stations,
)

# Columns of the empty frame returned when cordon is disabled.
_NEW_COLUMNS = ["source_ars5", "stop_id", "x", "y", "reach", "ewz"]


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
    return dict(zip(joined["stop_id"], joined["ars5"]))


def build_rail_entry_stations(
    stops: dict,
    routes: list,
    kreise: gpd.GeoDataFrame,
    gemeinden: gpd.GeoDataFrame,
    zgb_kreise,
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
    4. Call :func:`attach_station_population` to join the population (``ewz``) of the
       containing external Gemeinde; the nearest-Gemeinde fallback count is returned so
       the caller can log the primary vs fallback rate.

    Args:
        stops: ``{stop_id: (x, y)}`` as produced by
            :func:`braunschweig.data.cordon.network.read_transit_stops_routes`.
        routes: list of ``(mode, [stop_id, ...])`` transit routes (stop order).
        kreise: GeoDataFrame ``[ars5, geometry]`` (Kreis polygons; CRS EPSG:25832).
        gemeinden: GeoDataFrame ``[ars5, ewz, geometry]`` (external Gemeinden; CRS
            EPSG:25832) as produced by
            :func:`braunschweig.data.cordon.network.read_external_gemeinden`.
        zgb_kreise: iterable of 5-digit ZGB Kreis ARS strings.

    Returns:
        ``(df, n_nearest_fallback)`` where:

        - ``df``: DataFrame ``[source_ars5, stop_id, x, y, reach, ewz]``, one row per
          eligible external rail entry station.
        - ``n_nearest_fallback`` (int): stations whose population was filled via the
          nearest-Gemeinde fallback rather than a containment join (CLAUDE.md
          no-silent-fallback contract: the caller logs this count).
    """
    stop_kreis = _map_stop_kreis(stops, kreise)
    stations_base = eligible_rail_entry_stations(routes, stop_kreis, zgb_kreise)

    if len(stations_base) == 0:
        # Return an empty frame with all required columns including x, y, ewz.
        empty = pd.DataFrame(columns=_NEW_COLUMNS)
        return empty, 0

    # Attach x, y coordinates from the schedule stop table.
    stations_base["x"] = stations_base["stop_id"].map(lambda sid: stops[sid][0])
    stations_base["y"] = stations_base["stop_id"].map(lambda sid: stops[sid][1])

    # Attach population catchment from the containing external Gemeinde.
    df, n_nearest_fallback = attach_station_population(stations_base, gemeinden)

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
    df, n_fallback = build_rail_entry_stations(stops, routes, kreise, gemeinden, zgb)

    # --- Logging (CLAUDE.md no-silent-fallback) ---
    n_total = len(df)
    n_direct = int((df["reach"] == "direct").sum()) if n_total else 0
    n_transfer = int((df["reach"] == "transfer").sum()) if n_total else 0
    n_kreise = int(df["source_ars5"].nunique()) if n_total else 0
    fallback_rate = 100.0 * n_fallback / n_total if n_total else 0.0

    print(
        f"[braunschweig.data.cordon_pt_gates] {n_total} eligible rail entry stations "
        f"across {n_kreise} source Kreise "
        f"({n_direct} direct, {n_transfer} one-transfer); "
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
