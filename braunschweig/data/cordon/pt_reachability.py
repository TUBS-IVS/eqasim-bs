"""Rail-only PT entry stations reachable to ZGB directly or with one transfer.

A cross-cordon PT in-commuter boards at a real BAHNHOF (rail/regional/long-distance
station) from which ZGB is reachable on the schedule either directly (a single train
that also serves a ZGB stop) or with exactly one transfer (board train A, change once
to a train B that serves ZGB). Buses and trams are excluded by design -- the entry
point must be a rail station. Region-neutral; operates on the cut transit schedule
structure of ``braunschweig.data.cordon.network.read_transit_stops_routes``.

Population+accessibility weighting and per-agent station draw (B2) are provided by
:func:`weight_entry_stations` and :func:`sample_pt_station_per_agent`, mirroring the
road-gate pattern in ``braunschweig.data.cordon.gate_assignment``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

from braunschweig.synthesis.incommuters import RAIL_LIKE_MODES


def eligible_rail_entry_stations(
    routes,
    stop_kreis,
    zgb_kreise,
    rail_like=RAIL_LIKE_MODES,
) -> pd.DataFrame:
    """Return all external rail stops reachable to ZGB directly or with one transfer.

    A stop is an "entry station" if it lies outside the ZGB region, has a known
    source Kreis, and a rail-only commuter can reach ZGB from it with at most one
    transfer:

    - **direct**: the stop is on a rail route that also serves at least one ZGB stop
      (no transfer needed).
    - **transfer**: the stop is on a rail route that shares at least one stop with
      any direct route (i.e. one transfer at that shared stop reaches a ZGB-bound
      service).

    When a stop qualifies for both, "direct" takes precedence.

    ZGB stops themselves are never returned; stops whose Kreis is ``None`` (not
    mapped) are silently skipped because they cannot be attributed to a source
    Kreis for the in-commuter demand model.

    Args:
        routes: Iterable of ``(mode, [stop_id, ...])`` pairs representing transit
            routes in stop order, as produced by
            ``braunschweig.data.cordon.network.read_transit_stops_routes``.
            ``mode`` is compared lower-cased against ``rail_like``; ``None`` is
            treated as non-rail.
        stop_kreis: Mapping ``stop_id -> 5-digit Kreis ARS`` (or ``None`` if the
            stop could not be attributed to any Kreis).
        zgb_kreise: Iterable of 5-digit Kreis ARS strings identifying the ZGB
            in-scope region.
        rail_like: Set of transport-mode strings (lower-cased) treated as rail.
            Defaults to ``RAIL_LIKE_MODES``; covers heavy rail, regional rail,
            subway, and light rail. Buses and trams are excluded.

    Returns:
        DataFrame with columns ``["source_ars5", "stop_id", "reach"]`` (one row per
        external entry station). ``source_ars5`` is the 5-digit Kreis ARS of the
        stop. ``reach`` is ``"direct"`` or ``"transfer"``. ``"direct"`` beats
        ``"transfer"`` when a stop qualifies for both.
    """
    zgb = {str(k) for k in zgb_kreise}
    rail = {str(m).strip().lower() for m in rail_like}

    # Restrict to rail-mode routes only (bus/tram excluded).
    rail_routes = [
        stops for mode, stops in routes
        if mode is not None and str(mode).strip().lower() in rail
    ]

    def serves_zgb(stops):
        """Return True if any stop on this route maps to a ZGB Kreis."""
        return any(stop_kreis.get(s) in zgb for s in stops)

    # Direct routes: rail routes with at least one ZGB stop.
    direct_routes = [stops for stops in rail_routes if serves_zgb(stops)]

    # Transfer hubs: all stops appearing on any direct route.
    # A rail route that shares >= 1 stop with this set offers a one-transfer
    # connection to ZGB (change at any hub stop to a direct-route service).
    hubs = {s for stops in direct_routes for s in stops}

    # ZGB stops must never appear in the result.
    zgb_stops = {s for s, k in stop_kreis.items() if k in zgb}

    # best[stop_id] = "direct" | "transfer"; "direct" is final once set.
    best: dict[str, str] = {}

    def mark(stop_id: str, reach: str) -> None:
        """Record reach for stop_id, skipping ZGB stops and honouring direct-first."""
        if stop_id in zgb_stops:
            return
        if best.get(stop_id) == "direct":
            return
        if reach == "direct" or stop_id not in best:
            best[stop_id] = reach

    # Pass 1 -- mark stops on direct routes as "direct".
    for stops in direct_routes:
        for s in stops:
            mark(s, "direct")

    # Pass 2 -- mark stops on one-transfer-eligible routes as "transfer"
    # (only applied if not already "direct").
    for stops in rail_routes:
        if any(s in hubs for s in stops):
            for s in stops:
                mark(s, "transfer")

    # Build result: external stops only, with a known non-ZGB Kreis.
    rows = []
    for stop_id, reach in best.items():
        kreis = stop_kreis.get(stop_id)
        if kreis is not None and kreis not in zgb:
            rows.append((kreis, stop_id, reach))

    return pd.DataFrame(rows, columns=["source_ars5", "stop_id", "reach"])


def weight_entry_stations(stations: pd.DataFrame,
                          transfer_penalty: float = 0.5,
                          beta: float = 0.0) -> pd.DataFrame:
    """Per (Kreis, station) selection weight = ewz * accessibility * decay, normalised per Kreis.

    The weight reflects how attractive a rail entry station is for cross-cordon PT
    in-commuters from its source Kreis: a station with a larger population catchment
    (``ewz``), a direct rail connection, and a location closer to ZGB is preferred.

    - accessibility = 1.0 for ``reach == "direct"`` (no transfer needed).
    - accessibility = ``transfer_penalty`` for ``reach == "transfer"`` (one transfer
      required); must be in (0, 1] so transfer stations are downweighted but never
      excluded.
    - decay = ``exp(beta * dist_to_zgb_km)`` when the ``stations`` frame contains a
      ``dist_to_zgb_km`` column AND ``beta != 0.0``; otherwise ``decay = 1.0``.
      ``beta`` should be negative so that stations closer to ZGB (smaller
      ``dist_to_zgb_km``) receive a higher decay factor.  This mirrors the road
      cordon gravity in ``braunschweig.data.cordon.gate_assignment.population_gravity_gate_assignment``
      and is calibrated to break within-city ties (e.g. all Magdeburg Bahnhoefen
      having the same ``ewz``) in favour of the geographically closest station.
      **ASSUMPTION**: ``beta = -0.05`` per km (the default used by the calling stage)
      mirrors the road cordon ``cordon_gravity_beta`` default and has no committed
      calibration target in this repository.  It is tunable via the
      ``cordon_pt_gravity_beta`` config key.  ``dist_to_zgb_km`` is a straight-line
      (Euclidean, EPSG:25832) proxy for travel distance; the real rail travel time is
      determined by the MATSim router after network injection.
    - NaN ``dist_to_zgb_km`` values (arising when ZGB reference stops are unavailable)
      are treated as ``decay = 1.0`` (no distance penalty) so no weight becomes NaN.
    - ``ewz`` is floored at 1.0 before multiplication to guard against zero or
      negative values (which would silently suppress a valid station).
    - Weights are normalised within each ``source_ars5`` so they form a proper
      probability distribution for per-agent sampling.  If a Kreis total is zero
      (theoretically impossible after the ewz floor, but guarded), all weights
      remain 0.

    Args:
        stations: DataFrame with at least columns ``[source_ars5, stop_id, reach,
            ewz]``.  Additional columns (e.g. ``x``, ``y``, ``dist_to_zgb_km``) are
            preserved unchanged.  ``ewz`` is the station population catchment
            (inhabitants in the Gemeinden nearest to that station, set upstream).
            ``reach`` must be ``"direct"`` or ``"transfer"``.
        transfer_penalty: accessibility multiplier for one-transfer stations; default
            0.5.  Must be in (0, 1].
        beta: distance-decay slope per km (should be <= 0).  Applied as
            ``exp(beta * dist_to_zgb_km)`` when ``stations`` has a ``dist_to_zgb_km``
            column and ``beta != 0.0``; otherwise the decay term is 1.0
            (backward-compatible default).

    Returns:
        Copy of ``stations`` with an additional ``weight`` column (float).  Weights
        sum to 1.0 within each ``source_ars5`` (provided the total is positive).
    """
    s = stations.copy()
    s["ewz"] = s["ewz"].astype(float).clip(lower=1.0)
    access = np.where(s["reach"].to_numpy() == "direct", 1.0, float(transfer_penalty))

    # Distance-decay gravity term: exp(beta * dist_to_zgb_km).
    # Applied only when the column exists and beta is non-zero; otherwise decay=1.0
    # for full backward-compatibility (beta=0 default -> no change to existing weights).
    # NaN distances are guarded: fillna(0.0) gives exp(beta*0)=1.0, the neutral value.
    if float(beta) != 0.0 and "dist_to_zgb_km" in s.columns:
        dist_km = s["dist_to_zgb_km"].astype(float).fillna(0.0).to_numpy()
        decay = np.exp(float(beta) * dist_km)
    else:
        decay = 1.0

    s["weight"] = s["ewz"].to_numpy() * access * decay
    tot = s.groupby("source_ars5")["weight"].transform("sum")
    s["weight"] = np.where(tot.to_numpy() > 0, s["weight"] / tot.to_numpy(), 0.0)
    return s


def sample_pt_station_per_agent(orig_ars, weighted: pd.DataFrame,
                                rng) -> list:
    """Draw one entry station per PT agent from its Kreis's weighted stations.

    Each injected cross-cordon PT in-commuter belongs to an external source Kreis;
    this samples its rail entry station categorically, weighted by that Kreis's
    per-station weights from :func:`weight_entry_stations`.  Agents from a Kreis
    therefore spread over that Kreis's eligible stations in the same proportions the
    population+accessibility model produces.

    The caller is responsible for logging and handling ``None`` returns -- they
    indicate an agent whose Kreis has no eligible rail entry station in the schedule,
    which is a data gap that must not be silently snapped to a road gate.

    Args:
        orig_ars: iterable of each agent's external source Kreis ARS (5-digit
            string).
        weighted: output of :func:`weight_entry_stations`; must contain columns
            ``[source_ars5, stop_id, weight]``.
        rng: a ``numpy.random.Generator`` (seeded by the caller for reproducibility).

    Returns:
        list of ``stop_id`` values (one per agent).  ``None`` where the agent's
        Kreis has no station with positive weight; the caller logs and handles this
        fallback explicitly.
    """
    dist: dict = {}
    for ars5, sub in weighted.groupby("source_ars5"):
        sub = sub[sub["weight"] > 0]
        if len(sub):
            w = sub["weight"].to_numpy(dtype=float)
            dist[str(ars5)] = (sub["stop_id"].to_numpy(), w / w.sum())
    out = []
    for a in orig_ars:
        choice = dist.get(str(a))
        if choice is None:
            out.append(None)
        else:
            stop_ids, probs = choice
            out.append(stop_ids[int(rng.choice(len(stop_ids), p=probs))])
    return out


def apply_station_distance_cap(
    stations: pd.DataFrame,
    zgb_points,
    max_km: float,
) -> tuple:
    """Drop candidate entry stations that are unreachably far from the ZGB region.

    A station is kept iff its minimum straight-line (Euclidean) distance to ANY ZGB
    reference point is <= ``max_km * 1000`` metres.  Stations beyond this cap are
    dropped.

    **Straight-line distance is an explicit approximation.**  True rail travel distance
    would require a timetable-based routing query, which is unavailable at this stage
    of the pipeline.  Straight-line distance is a lower bound on travel distance and
    will therefore never incorrectly drop a station that is actually within reach;
    it may however retain a few stations that are marginally over the cap by straight
    line but further by rail.  At the default of 150 km this approximation is
    conservative and scientifically defensible.

    **ASSUMPTION: the default cap of 150 km has no committed reference source in this
    repository.**  It is chosen to retain the typical rail-commuter catchment
    (Magdeburg ~145 km, Hannover ~65 km, Goettingen ~100 km, Hildesheim ~50 km from
    Braunschweig HBF) while excluding absurd topological chains (e.g. a station
    300 km away that merely happens to share a transfer stop with a ZGB-serving line).
    Callers MUST expose this value as a configurable parameter and document the
    assumption.  Re-calibration against observed in-commuter origins is recommended
    when origin data become available.

    When ``zgb_points`` is empty (zero rows), the cap is inactive: all stations are
    returned unchanged and ``n_dropped=0``.  The caller should log that the cap was
    skipped in this case.

    Args:
        stations: DataFrame with at least ``x`` and ``y`` columns (EPSG:25832
            projected coordinates in metres).  All other columns are preserved.
        zgb_points: (N, 2) array-like of ZGB reference point coordinates
            ``[[x0, y0], [x1, y1], ...]`` in EPSG:25832 metres.  Typically the
            coordinates of the in-scope rail stops from the transit schedule.
            May be empty (shape (0, 2)) to disable the cap.
        max_km: Distance cap in kilometres.  Stations whose minimum straight-line
            distance to any ZGB point exceeds ``max_km * 1000`` metres are dropped.

    Returns:
        ``(kept_stations, n_dropped)`` where:

        - ``kept_stations``: plain ``pd.DataFrame`` with the same columns as
          ``stations``, containing only rows that passed the cap.  If ``zgb_points``
          is empty, this is a copy of ``stations`` unchanged.
        - ``n_dropped`` (int): number of rows removed by the cap.  Zero when the
          cap is inactive (empty ``zgb_points``) or all stations are within range.
    """
    pts = np.asarray(zgb_points, dtype=float)

    if len(stations) == 0 or pts.shape[0] == 0:
        # Cap inactive or no stations to filter.
        return stations.copy(), 0

    max_m = max_km * 1000.0
    # Station coordinates as (n_stations, 2) array.
    sx = stations["x"].to_numpy(dtype=float)
    sy = stations["y"].to_numpy(dtype=float)
    # zgb_points as (n_zgb, 2).  Vectorise via broadcasting to avoid a Python loop
    # over all station-ZGB pairs.
    # Shape: (n_stations, 1) - (1, n_zgb) -> (n_stations, n_zgb) per axis.
    dx = sx[:, np.newaxis] - pts[:, 0][np.newaxis, :]
    dy = sy[:, np.newaxis] - pts[:, 1][np.newaxis, :]
    dist2 = dx ** 2 + dy ** 2
    # For each station: minimum distance to any ZGB point.
    min_dist = np.sqrt(dist2.min(axis=1))
    mask = min_dist <= max_m
    n_dropped = int((~mask).sum())
    return stations[mask].reset_index(drop=True), n_dropped


def attach_station_population(
    stations: pd.DataFrame,
    gemeinden: gpd.GeoDataFrame,
) -> tuple:
    """Attach the population (``ewz``) of the containing Gemeinde to each entry station.

    Each station is represented as a POINT (built from ``x``, ``y`` in EPSG:25832) and
    joined to the external Gemeinde polygons.  The PRIMARY method is a spatial
    containment join (``predicate="within"``): a station that falls inside a Gemeinde
    polygon receives that Gemeinde's ``ewz``.  The FALLBACK (nearest-Gemeinde join) is
    applied only to stations that matched no polygon -- a situation that can arise for
    stations sitting exactly on a polygon boundary or slightly outside due to projection
    rounding.

    No-silent-fallback contract (CLAUDE.md): the function returns the fallback count
    ``n_nearest_fallback`` so the caller can log a primary-vs-fallback rate.  The caller
    is responsible for emitting that log line.  A high fallback rate (e.g. all stations)
    almost always indicates a CRS mismatch or a data problem and must be surfaced.

    The ``ewz`` column is never silently filled with a constant; the nearest-Gemeinde
    fallback IS the documented fallback and its count is always returned.

    Args:
        stations: DataFrame with at least columns ``[source_ars5, stop_id, reach, x, y]``.
            ``x`` and ``y`` are projected coordinates in EPSG:25832 (metres).  Additional
            columns are preserved unchanged in the output.
        gemeinden: GeoDataFrame ``[ars5, ewz, geometry]`` (external Gemeinden; polygon
            geometry; CRS EPSG:25832) as produced by
            ``braunschweig.data.cordon.network.read_external_gemeinden``.

    Returns:
        ``(stations_with_ewz, n_nearest_fallback)`` where:

        - ``stations_with_ewz``: plain ``pd.DataFrame`` (no geometry column) preserving
          all input columns and adding ``ewz`` (float), one row per input station in
          input order, regardless of the caller's index.
        - ``n_nearest_fallback`` (int): number of stations that were matched via the
          nearest-Gemeinde fallback rather than by containment.  Zero means the primary
          containment join covered all stations.
    """
    # Reset to a clean 0-based positional index so the post-join dedup keys on
    # station identity rather than the caller's (potentially non-unique) index.
    # The original caller index is intentionally discarded; output order matches
    # the input row order.
    stations_reset = stations.reset_index(drop=True)

    # Build a GeoDataFrame of station POINTS in EPSG:25832.
    points = gpd.GeoDataFrame(
        stations_reset.copy(),
        geometry=gpd.points_from_xy(stations_reset["x"], stations_reset["y"]),
        crs="EPSG:25832",
    )

    # Rename the Gemeinde ars5 column to avoid a column-name clash with the station's
    # own source_ars5 after the join.
    gem = gemeinden[["ars5", "ewz", "geometry"]].rename(columns={"ars5": "gem_ars5"})

    # PRIMARY: containment join (predicate="within") gives each station the ewz of
    # the Gemeinde polygon it strictly falls inside.  Note: "within" excludes the
    # polygon boundary, so a station exactly on a shared edge matches NO polygon and
    # falls to the nearest-Gemeinde fallback below.  The dedup here guards against
    # the rare case of overlapping polygons, where a single station point could lie
    # strictly inside more than one polygon simultaneously.
    contained = gpd.sjoin(points, gem, predicate="within", how="left")

    # De-duplicate on the clean positional index: collapse multiple polygon matches
    # for ONE station to a single row while preserving every DISTINCT station.
    # This is safe because stations_reset has a unique 0-based index.
    contained = contained[~contained.index.duplicated(keep="first")]

    # FALLBACK: stations that matched no polygon (ewz is NaN) -> nearest Gemeinde.
    missing_mask = contained["ewz"].isna()
    n_nearest_fallback = int(missing_mask.sum())

    if n_nearest_fallback > 0:
        unmatched = points[missing_mask]
        nearest = gpd.sjoin_nearest(unmatched, gem, how="left")
        # De-duplicate nearest results on the same clean positional index.
        nearest = nearest[~nearest.index.duplicated(keep="first")]
        # Use a column-level assignment to avoid the pandas iloc-inplace warning.
        ewz_series = contained["ewz"].copy()
        ewz_series[missing_mask] = nearest["ewz"].values
        contained = contained.assign(ewz=ewz_series)

    # Return a plain DataFrame; drop geometry and join helper columns.
    drop_cols = [c for c in ("geometry", "index_right") if c in contained.columns]
    result = pd.DataFrame(contained.drop(columns=drop_cols))

    # Restore the original column order: all input columns first, then ewz.
    original_cols = list(stations_reset.columns)
    output_cols = original_cols + ["ewz"]
    # Keep any extra join artefact columns out of the output.
    result = result[[c for c in output_cols if c in result.columns]]

    # Ensure ewz is always float, regardless of the source column dtype.
    result["ewz"] = result["ewz"].astype(float)

    return result, n_nearest_fallback
