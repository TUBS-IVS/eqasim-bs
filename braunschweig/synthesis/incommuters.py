"""Cross-cordon in-commuter (Einpendler) synthesis helpers.

This module assembles injected in-commuter agents for the cross-cordon external-
demand feature. It composes the already-tested building blocks:

  - demand:        BA-Pendler OD -> per-agent (orig/dest Kreis) counts.
  - gate_assignment: population-gravity Kreis->gate volumes + per-agent gate draw.
  - mode_reference / plans: Mikrozensus fixed mode + resident-schema plan frames.
  - gate_entry:    network-entry time at the gate (work start - in-ZGB travel).

PT in-commuters board at a real rail STATION (Bahnhof) drawn from their source
Kreis's eligible entry stations via population+accessibility weighting (B2/B3/B4).
The station frame is produced by the ``braunschweig.data.cordon_pt_gates`` stage
(schema: ``[source_ars5, stop_id, x, y, reach, ewz]``) and consumed here.  Car
agents continue to board at their assigned road gate (unchanged).

Region-neutral; the synpp stage wires the data sources. See
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig.constants import ROUTED_DETOUR_FACTOR
from braunschweig.data.cordon.demand import (
    expand_to_agents, make_incommuter_ids, select_inbound_flows)
from braunschweig.data.cordon.gate_assignment import sample_gate_per_agent
from braunschweig.data.cordon.mode_balancer import balance_incommuter_modes
from braunschweig.data.cordon.mode_reference import (
    MID_DISTANCE_EDGES, restrict_to_modes, route_distance_band)
from braunschweig.data.cordon.plans import (
    assign_fixed_mode, build_incommuter_activities, build_incommuter_locations,
    build_incommuter_trips, extract_commute_times, sample_donors,
    select_commuter_donors, straight_line_distance_km)
# NOTE: braunschweig.data.cordon.pt_reachability imports RAIL_LIKE_MODES from this
# module (incommuters), which creates a circular dependency if imported at module level.
# weight_entry_stations and sample_pt_station_per_agent are therefore imported LOCALLY
# inside build_incommuter_frames to break the cycle.  They are pure data functions and
# the lazy import has no performance impact at production scale (called once per run).

# Transit modes that count as regional rail for in-commuter boarding-stop selection.
# A regional-rail (or S-Bahn / regional train) station is the realistic access point
# for a cross-cordon PT commuter: it offers a fast, high-frequency, one-seat ride into
# ZGB. Local feeders (tram, bus) are deliberately EXCLUDED -- they are the kind of
# single-line, low-frequency village halt the geometry-only nearest-stop rule wrongly
# preferred over a nearby station. The set is kept conservative and matched against the
# MATSim transit ``transportMode`` (lower-cased) verbatim; the common GTFS->MATSim mode
# strings for heavy/regional/urban rail are covered. Tram/bus -> not rail.
RAIL_LIKE_MODES = frozenset({"rail", "train", "subway", "light_rail", "regional_rail"})

# Person attribute defaults for injected in-commuters: non-residents with minimal but
# VALID attributes so the MATSim writer + downstream logic accept them. Their commute
# mode is fixed by the eqasim "outside" tour (OutsideFilter), so car/PT availability
# only needs to be self-consistent with the chosen mode.
# NOTE: household_income / household_income_eur are NOT in this constant dict -- they
# are set per agent from the origin-Kreis INKAR income level (see _build_persons /
# draw_incommuter_income), so a Wolfsburg in-commuter no longer gets the same income
# as a Goslar one.
_INCOMMUTER_PERSON_DEFAULTS = dict(
    studies=False, household_size=1, consumption_units=1.0,
    socioprofessional_class=6, number_of_bicycles=0, number_of_cars=1,
    bicycle_availability="all", license_type="ja", has_license=True,
    has_pt_subscription=False, pt_subscription_type="fahre_nie",
    high_income=False,
    is_bs_resident=False, is_urban_resident=False, age_range="higher_education",
)


# Base monthly household income (EUR) for cross-cordon in-commuters, scaled per agent
# by the INKAR Kreis factor of the agent's ORIGIN Kreis. This replaces the legacy
# hardcoded constant "3000" (which gave every in-commuter the same income regardless
# of where they live). The base equals the former constant so the regional mean is
# unchanged; only the per-Kreis spread is added.
INCOMMUTER_BASE_INCOME_EUR = 3000.0

# Fallback-rate threshold above which the INKAR origin-Kreis miss is escalated to a
# WARNING (CLAUDE.md "Fallback transparency"). A high rate means a systematic origin
# Kreis vs INKAR ars5 key mismatch, not a few genuinely out-of-table Kreise.
INCOMMUTER_INCOME_FALLBACK_WARN_RATE = 5.0


def draw_incommuter_income(orig_ars, df_inkar, base_eur=INCOMMUTER_BASE_INCOME_EUR,
                           rng=None):
    """Per-agent monthly household income (EUR) drawn from the origin-Kreis INKAR level.

    Each in-commuter lives OUTSIDE the ZGB cordon, so its household income should
    reflect its home (origin) Kreis, not a single constant. The income is the base
    monthly household income scaled by the INKAR per-Kreis ``scale`` factor of the
    agent's origin Kreis (the same multiplicative factor the resident enrichment
    applies in ``braunschweig.synthesis.population.enriched._apply_inkar_income_scale``),
    with a small multiplicative log-normal jitter so agents from the same Kreis are not
    all identical (the resident population varies within a Kreis too).

    Fallback transparency: the PRIMARY path looks the origin Kreis up in the INKAR
    ``scale`` table; if the Kreis is absent (out-of-table / ars5-format mismatch), the
    agent FALLS BACK to the regional mean (scale 1.0). The fallback count + rate are
    logged, escalated to a WARNING above
    :data:`INCOMMUTER_INCOME_FALLBACK_WARN_RATE`, so a systematic key mismatch is
    visible rather than silent.

    Args:
        orig_ars: array-like of per-agent 5-digit origin Kreis ARS.
        df_inkar: INKAR household-income frame with columns ``ars5`` and ``scale``
            (from ``braunschweig.data.inkar.household_income``).
        base_eur: base monthly household income (EUR) before the Kreis scale.
        rng: ``numpy.random.Generator`` for the within-Kreis jitter (seeded by caller).

    Returns:
        ``(income_eur, n_fallback)`` -- a float ndarray of per-agent monthly household
        income and the number of agents that used the regional-mean fallback.
    """
    if rng is None:
        rng = np.random.default_rng()
    orig = np.asarray([str(a) for a in orig_ars])
    n = len(orig)
    scale_lookup = dict(zip(df_inkar["ars5"].astype(str), df_inkar["scale"].astype(float)))
    in_table = np.array([a in scale_lookup for a in orig])
    n_fallback = int((~in_table).sum())
    scale = np.array([scale_lookup.get(a, 1.0) for a in orig], dtype=float)
    # Multiplicative log-normal jitter (sigma 0.20 in log space) so within-Kreis income
    # varies; mean is ~1.0 so the per-Kreis mean stays at base_eur * scale.
    jitter = rng.lognormal(mean=0.0, sigma=0.20, size=n)
    income = (float(base_eur) * scale * jitter).round(0)

    if n:
        share = 100.0 * n_fallback / n
        if n_fallback:
            level = "WARNING: " if share > INCOMMUTER_INCOME_FALLBACK_WARN_RATE else ""
            print(
                f"[braunschweig.incommuters] {level}INKAR origin-Kreis income fallback: "
                f"primary {n - n_fallback}/{n} ({100.0 - share:.1f}%), "
                f"fallback {n_fallback} ({share:.1f}%) in-commuters had no INKAR scale "
                f"for their origin Kreis -> regional mean (scale 1.0). A high rate means "
                f"a systematic origin-Kreis vs INKAR ars5 key mismatch.",
                flush=True,
            )
        else:
            print(
                f"[braunschweig.incommuters] INKAR origin-Kreis income PRIMARY lookup "
                f"hit all {n}/{n} in-commuters (fallback rate 0.0%).",
                flush=True,
            )
    return income, n_fallback


def direct_ride_stops(routes, stop_kreis, zgb_kreise):
    """Per source Kreis, the transit stops offering a one-seat ride into ZGB.

    A route gives a one-seat ride into the region if it serves at least one stop in
    a ZGB Kreis. Every NON-ZGB stop on such a route is an entry stop for its source
    Kreis -- a PT in-commuter from that Kreis can board there and reach ZGB without
    transferring. (Rail and bus are treated identically; the route mode is ignored
    here.)

    Args:
        routes: iterable of ``(mode, [stop_id, ...])`` transit routes (stop order).
        stop_kreis: mapping ``stop_id -> 5-digit Kreis ARS`` (None if unmapped).
        zgb_kreise: iterable of in-scope ZGB 5-digit Kreis ARS.

    Returns:
        dict ``{source_ars5: set(stop_id)}`` -- the entry stops per external Kreis.
    """
    zgb = {str(k) for k in zgb_kreise}
    entry: dict[str, set] = {}
    for _mode, stops in routes:
        kreise = [stop_kreis.get(s) for s in stops]
        if not any(k in zgb for k in kreise if k is not None):
            continue
        for stop_id, kreis in zip(stops, kreise):
            if kreis is not None and kreis not in zgb:
                entry.setdefault(kreis, set()).add(stop_id)
    return entry


def direct_ride_stop_stats(routes, stop_kreis, zgb_kreise, rail_like=RAIL_LIKE_MODES):
    """Per external entry stop, the connectivity it gets from ZGB-serving routes.

    A route is "ZGB-serving" if at least one of its stops maps to a ZGB Kreis (a
    one-seat, no-transfer ride into the region). For each ZGB-serving route, every
    NON-ZGB stop on it is an entry stop and receives:

      - ``n_zgb_routes`` += 1            -- the count of DISTINCT ZGB-serving routes
                                            passing through it, and
      - ``is_rail`` |= (mode in rail_like) -- whether ANY ZGB-serving route through it
                                            runs a regional-rail-like mode.

    These quantify how well-connected each one-seat entry stop is, so a PT in-commuter
    can prefer a regional-rail station / multi-line hub over a single low-frequency bus
    halt. (Legacy/debug helper: the production in-commuter PT path now places agents via
    ``braunschweig.data.cordon.pt_reachability``; this connectivity table is retained for
    ``scripts/extract_incommuter_pt_entry_stops.py`` and tests.) A stop appearing on the
    same physical line in
    several route variants is counted per route entry, matching the schedule's notion of
    distinct serving routes (frequency proxy).

    Args:
        routes: iterable of ``(mode, [stop_id, ...])`` transit routes (stop order).
        stop_kreis: mapping ``stop_id -> 5-digit Kreis ARS`` (None if unmapped).
        zgb_kreise: iterable of in-scope ZGB 5-digit Kreis ARS.
        rail_like: set of transport modes (lower-cased) treated as regional rail.

    Returns:
        dict ``{stop_id: {"n_zgb_routes": int, "is_rail": bool}}`` over entry stops.
    """
    zgb = {str(k) for k in zgb_kreise}
    rail = {str(m).strip().lower() for m in rail_like}
    stats: dict[str, dict] = {}
    for mode, stops in routes:
        kreise = [stop_kreis.get(s) for s in stops]
        if not any(k in zgb for k in kreise if k is not None):
            continue
        mode_is_rail = mode is not None and str(mode).strip().lower() in rail
        for stop_id, kreis in zip(stops, kreise):
            if kreis is not None and kreis not in zgb:
                stat = stats.setdefault(stop_id, {"n_zgb_routes": 0, "is_rail": False})
                stat["n_zgb_routes"] += 1
                stat["is_rail"] = stat["is_rail"] or mode_is_rail
    return stats


def build_pt_entry_stops(stops, routes, kreise, zgb_kreise):
    """PT entry stops per external source Kreis, as a tidy DataFrame.

    Maps each schedule stop to its Kreis by point-in-polygon against ``kreise``
    (GeoDataFrame [ars5, geometry]), then keeps the one-seat-to-ZGB entry stops via
    :func:`direct_ride_stops`.

    Args:
        stops: ``{stop_id: (x, y)}`` (from :func:`read_transit_stops_routes`).
        routes: list of ``(mode, [stop_id, ...])``.
        kreise: GeoDataFrame [ars5, geometry] (Kreis polygons, same CRS as stops).
        zgb_kreise: iterable of in-scope ZGB 5-digit Kreis ARS.

    Returns:
        DataFrame [source_ars5, stop_id, x, y, n_zgb_routes, is_rail], one row per
        external entry stop. ``n_zgb_routes`` is the number of distinct ZGB-serving
        routes through the stop and ``is_rail`` whether any of them is regional-rail-
        like (see :func:`direct_ride_stop_stats`); both quantify how well-connected the
        one-seat entry stop is so PT in-commuters can prefer rail / multi-line hubs.
    """
    ids = list(stops)
    pts = gpd.GeoDataFrame({"stop_id": ids},
                           geometry=[Point(stops[i]) for i in ids], crs=kreise.crs)
    joined = gpd.sjoin(pts, kreise[["ars5", "geometry"]], predicate="within", how="left")
    joined = joined.drop_duplicates(subset="stop_id")
    stop_kreis = dict(zip(joined["stop_id"], joined["ars5"]))
    entry = direct_ride_stops(routes, stop_kreis, zgb_kreise)
    stats = direct_ride_stop_stats(routes, stop_kreis, zgb_kreise)
    rows = []
    for ars5, stop_set in entry.items():
        for stop_id in stop_set:
            x, y = stops[stop_id]
            stat = stats.get(stop_id, {"n_zgb_routes": 0, "is_rail": False})
            rows.append((ars5, stop_id, x, y,
                         int(stat["n_zgb_routes"]), bool(stat["is_rail"])))
    return pd.DataFrame(
        rows, columns=["source_ars5", "stop_id", "x", "y", "n_zgb_routes", "is_rail"])


def build_incommuter_frames(flows, zgb_kreise, sampling_rate, gates, assignment,
                            zgb_work, mode_reference, hts_persons,
                            hts_trips, person_col, n_residents, n_resident_households,
                            rng, band_edges=MID_DISTANCE_EDGES, gate_speed_kmh=30.0,
                            detour_factor=ROUTED_DETOUR_FACTOR, pt_entry_stops=None,
                            commute_modes=("car", "pt"),
                            pt_transfer_penalty=0.5,
                            pt_gravity_beta=0.0,
                            inkar_income=None, data_path=None,
                            real_origin=False, gemeinden=None,
                            zgb_polygon=None, source_buffer_m=45000.0,
                            mode_balance=False):
    """Assemble every in-commuter frame.

    Returns a dict with keys persons, trips, activities, locations, vehicles,
    households, validation. Deterministic given ``rng``.

    Home activities are tagged ``outside`` (eqasim fixes their mode).  PT agents board
    at a real rail STATION drawn from their source Kreis's eligible stations (weighted
    by population+accessibility from :func:`~braunschweig.data.cordon.pt_reachability.weight_entry_stations`).
    Car agents board at the road gate (unchanged).

    If a PT agent's source Kreis has NO eligible rail station (the draw returns None),
    that agent is reassigned to ``"car"`` so it boards at its already-drawn road gate.
    This reassignment is done BEFORE trips/persons/vehicles are built, so ``mode``,
    ``car_availability``, and the vehicle list all reflect the final (post-reassignment)
    mode consistently.  The fallback rate is counted and logged (CLAUDE.md
    no-silent-fallback); a rate above 5 % triggers a WARNING prefix.

    ``pt_transfer_penalty`` is passed to
    :func:`~braunschweig.data.cordon.pt_reachability.weight_entry_stations` (default
    0.5): direct rail stations are preferred over one-transfer stations.

    ``pt_gravity_beta`` is the distance-decay slope (per km) passed to
    :func:`~braunschweig.data.cordon.pt_reachability.weight_entry_stations` (default
    0.0 = no gravity, backward-compatible).  The calling stage sets -0.05 (mirroring
    ``cordon_gravity_beta``) to prefer stations closer to ZGB.

    ``real_origin`` (default ``False``): when ``True``, agents whose origin Kreis has
    at least one Gemeinde representative point within ``zgb_polygon.buffer(source_buffer_m)``
    (the source-network ring) receive a real population-weighted home point drawn from
    those in-ring Gemeinden instead of the road gate / rail station.  The road gate
    drawn in step 1 is PRESERVED as the cordon-entry reference for timing and the
    network entry point; only ``home_x/home_y`` and the validation ``entry_kind`` change.
    Far agents (no in-ring Gemeinde for their source Kreis) keep the existing gate-based
    or station-based placement unchanged.  When ``False`` (default) the function is
    byte-identical to the pre-4b behaviour: no additional rng draws are consumed and
    the result is identical for the same seed.

    ``gemeinden`` must be a GeoDataFrame ``[ars5, gem_ags, ewz, geometry]``
    (external Gemeinden with population, EPSG:25832).  Required when
    ``real_origin=True``.

    ``zgb_polygon`` must be the dissolved ZGB region polygon (EPSG:25832).
    Required when ``real_origin=True``.

    ``source_buffer_m``: source-network ring radius in metres (default 45000).
    A Gemeinde representative point is in-ring iff it lies within
    ``zgb_polygon.buffer(source_buffer_m)``.

    ``mode_balance`` (default ``False``): when ``True``, the Mikrozensus-drawn modes are
    passed through :func:`~braunschweig.data.cordon.mode_balancer.balance_incommuter_modes`
    BEFORE the PT station placement step.  The balancer enforces the hard constraint (every
    PT agent must have ``can_board_pt=True``) while conserving the global PT count by
    promoting an equal number of reachable car agents to PT.  This means a balanced PT
    agent is guaranteed to find a station in the subsequent draw (no more silent PT->car
    inside the station step).  When ``False`` (default), the legacy lossy behaviour is
    preserved: PT agents from rail-less Kreise are silently reassigned to car at the
    station draw step (byte-identical to the pre-balancer behaviour for the same seed).
    The balancer logs its stats (n_pt_target, n_forced, n_promoted, residual) and warns
    if the residual > 0 (rail access too scarce to fully restore the target).
    """
    inbound = select_inbound_flows(flows, zgb_kreise, in_ring_kreise=set(assignment["ars5"]))
    agents = expand_to_agents(inbound, sampling_rate)
    n = len(agents)
    if n == 0:
        return _empty_frames(zgb_work.crs)

    ids = make_incommuter_ids(n, n_residents, n_resident_households)
    person_ids = ids["person_id"].to_numpy()
    orig_ars = agents["orig_ars"].to_numpy()

    # 1) gate per agent (gravity); home coords = gate point.
    gate_ids = sample_gate_per_agent(orig_ars, assignment, rng, weight_col="inbound")
    gate_geom = gates.set_index("gate_id").geometry
    gate_x = np.array([gate_geom[g].x for g in gate_ids], dtype=float)
    gate_y = np.array([gate_geom[g].y for g in gate_ids], dtype=float)

    # 2) workplace inside the destination ZGB Kreis (employment-weighted); the pool
    # location_id is discarded -- in-commuters get their own unique work facility id.
    # n_work_fallback: in-commuters that fell back to the whole-ZGB pool (logged).
    work_x, work_y, _, _n_work_fallback = _sample_workplaces(
        agents["dest_ars"].to_numpy(), zgb_work, rng)

    # 3) fixed mode from the Mikrozensus reference by commute-distance band (gate->work).
    # Cross-cordon commuters realistically use only car or PT -- walk/bike over the
    # cordon are negligible, so the reference is restricted to ``commute_modes`` (the
    # dropped probability mass is redistributed proportionally; see restrict_to_modes).
    dist_km = straight_line_distance_km(gate_x, gate_y, work_x, work_y)
    restricted_reference = restrict_to_modes(mode_reference, allowed=commute_modes)
    band_fn = lambda d: route_distance_band(d, detour_factor=detour_factor, edges=band_edges)
    modes_mikro = list(assign_fixed_mode(dist_km, restricted_reference, band_fn, rng))

    # 3a) Mode balancer (flag-gated, default OFF via mode_balance=False).
    # When ON: pass Mikrozensus modes through balance_incommuter_modes so the global PT
    # count is conserved under the rail-access constraint.  The balancer:
    #   - Forces PT agents whose source Kreis has no rail station to car (hard constraint).
    #   - Promotes an equal number of reachable car agents back to PT (conservation).
    # A balanced PT agent is guaranteed can_board_pt=True, so the station draw below
    # always finds a station (no more silent PT->car inside the station placement step).
    # When OFF: the legacy list is used directly (byte-identical for same seed).
    #
    # can_board_pt: True if the agent's source Kreis has >=1 station in pt_entry_stops.
    # pt_propensity: per-agent P(pt | distance band) from the restricted reference.
    #   For each agent, this is the pt probability in its distance band (0.0 if the band
    #   has no pt or if pt is not in commute_modes).  Used to weight which car agents
    #   are promoted (higher propensity -> more likely to be promoted to PT).
    if mode_balance:
        _has_stations_schema = (
            pt_entry_stops is not None
            and len(pt_entry_stops) > 0
            and "source_ars5" in pt_entry_stops.columns
        )
        if _has_stations_schema:
            # Kreise that have at least one eligible rail station.
            _rail_kreise = set(pt_entry_stops["source_ars5"].astype(str))
        else:
            _rail_kreise = set()

        # Per-agent reachability: True iff source Kreis has a station.
        can_board_pt = np.array([str(a) in _rail_kreise for a in orig_ars], dtype=bool)

        # Per-agent PT propensity: P(pt | distance band) from the restricted reference.
        pt_propensity = np.array([
            restricted_reference.get(band_fn(d), {}).get("pt", 0.0)
            for d in dist_km
        ], dtype=float)

        modes_balanced, _bal_stats = balance_incommuter_modes(
            modes_mikro, can_board_pt, pt_propensity, rng)

        # Log balancer stats (CLAUDE.md no-silent-fallback).
        _n = len(modes_balanced)
        print(
            f"[braunschweig.incommuters] mode balancer: "
            f"mikrozensus_target {_bal_stats['n_pt_target']}/{_n} PT "
            f"({100.0 * _bal_stats['n_pt_target'] / _n:.1f}%), "
            f"forced_to_car {_bal_stats['n_forced_car']}, "
            f"promoted {_bal_stats['n_promoted']}, "
            f"final_pt {_bal_stats['n_pt_final']}/{_n} "
            f"({100.0 * _bal_stats['n_pt_final'] / _n:.1f}%), "
            f"residual {_bal_stats['residual']}",
            flush=True,
        )
        if _bal_stats["residual"] > 0:
            print(
                f"[braunschweig.incommuters] WARNING: mode balancer residual "
                f"{_bal_stats['residual']} -- rail access too scarce to fully restore "
                f"the Mikrozensus PT target; realized PT count is "
                f"{_bal_stats['n_pt_final']} vs target {_bal_stats['n_pt_target']}. "
                f"Check cordon_pt_gates coverage for the source Kreise.",
                flush=True,
            )
        modes = list(modes_balanced)
    else:
        # Legacy path: use the Mikrozensus draw directly (byte-identical for same seed).
        modes = modes_mikro

    # 3b) PT agents board at a real rail STATION drawn from their source Kreis's eligible
    # stations (population+accessibility weighted).  This decouples PT boarding from road
    # gates: a PT in-commuter is placed at a Bahnhof, not a motorway interchange.
    #
    # The new schema from the cordon_pt_gates stage is [source_ars5, stop_id, x, y, reach, ewz].
    # Guard: if pt_entry_stops is None/empty or lacks the required columns (legacy/empty
    # frames), the whole PT group is treated as having no eligible stations -> all
    # reassigned to car (road-gate path) with a WARNING logged.
    home_x = np.array(gate_x, dtype=float).copy()
    home_y = np.array(gate_y, dtype=float).copy()

    _has_new_schema = (
        pt_entry_stops is not None
        and len(pt_entry_stops) > 0
        and "reach" in pt_entry_stops.columns
        and "ewz" in pt_entry_stops.columns
    )

    if _has_new_schema:
        # Local import to avoid the circular dependency between incommuters and
        # pt_reachability (pt_reachability imports RAIL_LIKE_MODES from this module).
        from braunschweig.data.cordon.pt_reachability import (  # noqa: PLC0415
            sample_pt_station_per_agent, weight_entry_stations)
        weighted = weight_entry_stations(pt_entry_stops, transfer_penalty=pt_transfer_penalty,
                                         beta=pt_gravity_beta)
        # Build a coordinate lookup: stop_id -> (x, y) from the full pt_entry_stops table.
        _stop_xy = dict(zip(pt_entry_stops["stop_id"], zip(
            pt_entry_stops["x"].astype(float), pt_entry_stops["y"].astype(float))))
    else:
        weighted = None

    # Collect PT agent indices to process.
    pt_indices = [i for i, m in enumerate(modes) if m == "pt"]
    n_pt = len(pt_indices)
    n_pt_at_station = 0   # primary: placed at a real rail station
    n_pt_to_car = 0       # fallback: reassigned to car (no station in source Kreis)

    if n_pt > 0 and weighted is not None:
        pt_orig_ars = [orig_ars[i] for i in pt_indices]
        station_draws = sample_pt_station_per_agent(pt_orig_ars, weighted, rng)
        for list_pos, i in enumerate(pt_indices):
            stop_id = station_draws[list_pos]
            if stop_id is not None and stop_id in _stop_xy:
                # PRIMARY: place PT agent at the drawn rail station.
                sx, sy = _stop_xy[stop_id]
                home_x[i] = sx
                home_y[i] = sy
                n_pt_at_station += 1
            else:
                # FALLBACK: source Kreis has no eligible rail station; reassign to car so
                # the agent boards at its already-drawn road gate.  The home coords already
                # hold gate_x/gate_y (from the copy above), so no coordinate change needed.
                modes[i] = "car"
                n_pt_to_car += 1
    elif n_pt > 0:
        # No eligible stations at all (None, empty, or legacy frame): reassign all PT to car.
        for i in pt_indices:
            modes[i] = "car"
        n_pt_to_car = n_pt

    # Log the PT placement split (CLAUDE.md no-silent-fallback).
    if n_pt > 0:
        fallback_share = 100.0 * n_pt_to_car / n_pt
        warn = "WARNING: " if fallback_share > 5.0 else ""
        if n_pt_to_car > 0:
            print(
                f"[braunschweig.incommuters] {warn}PT station placement: "
                f"primary (rail station) {n_pt_at_station}/{n_pt} "
                f"({100.0 * n_pt_at_station / n_pt:.1f}%), "
                f"fallback (reassigned to car) {n_pt_to_car}/{n_pt} "
                f"({fallback_share:.1f}%). A high fallback rate means many PT-demand "
                f"Kreise lack a reachable rail entry station -- check the cordon_pt_gates "
                f"stage and the cordon_pt_max_station_distance_km setting.",
                flush=True,
            )
        else:
            print(
                f"[braunschweig.incommuters] PT station placement: "
                f"all {n_pt}/{n_pt} PT agents placed at rail stations (fallback 0.0%).",
                flush=True,
            )

    # modes is now the FINAL mode list (post PT->car reassignment where needed).
    modes = np.asarray(modes)

    # 3d) Real in-ring origin override (flag-gated, default OFF).
    # When ON: agents whose source Kreis has at least one Gemeinde representative
    # point within the source-network ring (zgb_polygon.buffer(source_buffer_m))
    # get their home_x/home_y replaced by a real population-weighted Gemeinde
    # point drawn from those in-ring Gemeinden.  The road GATE drawn in step 1
    # is preserved (it is the cordon-entry reference for timing and network
    # entry).  Far agents keep their existing home_x/home_y unchanged.
    # IMPORTANT: this entire block is inside the ``if real_origin:`` branch so
    # the rng stream is UNCHANGED when the flag is OFF (byte-identical guarantee).
    is_in_ring = None  # None means flag is OFF; set to bool array when ON.
    if real_origin:
        from braunschweig.data.cordon.incommuter_origins import (  # noqa: PLC0415
            incommuter_origin_homes)
        # Build per-Kreis inbound flow table [ars5, flow] from the selected
        # inbound frame (already filtered to in-ring origins above).
        inbound_by_kreis = (
            inbound.groupby("orig_ars", as_index=False)["flow"]
            .sum()
            .rename(columns={"orig_ars": "ars5"})
        )
        hx, hy, is_in_ring = incommuter_origin_homes(
            orig_ars, inbound_by_kreis, gemeinden, zgb_polygon, source_buffer_m, rng)
        # Override home coordinates for in-ring agents (both car and PT).
        home_x = np.where(is_in_ring, hx, home_x)
        home_y = np.where(is_in_ring, hy, home_y)
        # Log in-ring vs far split (CLAUDE.md no-silent-fallback).
        n_in_ring = int(is_in_ring.sum())
        n_far = int((~is_in_ring).sum())
        n_total = len(is_in_ring)
        print(
            f"[braunschweig.incommuters] real-origin override: "
            f"in-ring primary {n_in_ring}/{n_total} "
            f"({100.0 * n_in_ring / n_total:.1f}%), "
            f"far gate/station fallback {n_far} "
            f"({100.0 * n_far / n_total:.1f}%)",
            flush=True,
        )

    # 3c) OUTSIDE access distance home->gate: the non-simulated portion of the trip.
    # The agent enters the simulated network at the gate; the home->gate leg (0 for
    # car, where home == gate; the boarding-stop->gate access for PT) is teleported
    # at the gate speed. The inside gate->work leg (dist_km) is simulated by MATSim.
    home_to_gate_km = straight_line_distance_km(home_x, home_y, gate_x, gate_y)

    # 4) timings from HTS donors. depart_home is seeded so that the outside access
    # (home->gate) plus the inside leg (gate->work) lands at the donor work arrival;
    # MATSim then re-times the simulated gate->work leg over the iterations.
    donors = sample_donors(select_commuter_donors(hts_persons, hts_trips, person_col), n, rng)
    arrive_work, depart_work, depart_home, arrive_home = _agent_times(
        donors, hts_trips, person_col, dist_km, home_to_gate_km,
        gate_speed_kmh, detour_factor)

    trips = build_incommuter_trips(person_ids, depart_home, arrive_work, depart_work, arrive_home)
    trips["mode"] = np.repeat(np.asarray(modes), 2)
    # Home stays a normal "home" activity here. The eqasim scenario cutter converts
    # it to an "outside" activity (its location is at the gate / beyond the cordon),
    # which is the native point where outside activities are created -- creating them
    # pre-cut would leave RunPreparation's LinkAssignment without a facility.
    activities = build_incommuter_activities(person_ids, depart_home, arrive_work,
                                             depart_work, arrive_home)
    # Each in-commuter workplace gets a unique facility id so it never collides with a
    # resident work facility; the facilities-writer override registers it. Home keeps
    # the placeholder id (-1); the population writer references home_<household_id>,
    # which the facilities override also registers.
    work_facility_ids = [f"ic_work_{int(pid)}" for pid in person_ids]
    locations = build_incommuter_locations(person_ids, home_x, home_y, work_x, work_y,
                                           work_facility_ids, zgb_work.crs)

    # Per-agent monthly household income (EUR) from the origin-Kreis INKAR level,
    # replacing the legacy hardcoded constant. Falls back to the regional mean (logged)
    # for any origin Kreis absent from INKAR. When no INKAR frame is supplied (legacy
    # callers / tests), every agent keeps the base constant so behaviour is unchanged.
    if inkar_income is not None:
        income_eur, _n_income_fallback = draw_incommuter_income(orig_ars, inkar_income,
                                                                rng=rng)
    else:
        income_eur = np.full(n, INCOMMUTER_BASE_INCOME_EUR, dtype=float)

    persons = _build_persons(ids, donors, person_col, modes, income_eur)
    households = _build_households(ids, income_eur)
    # In-commuter cars are drawn from the German NDS fleet (Task F6) when a
    # ``data_path`` is supplied (the production stage); legacy callers / unit tests
    # that omit it keep the single default-car row so their behaviour is unchanged.
    if data_path is not None:
        vehicle_types, vehicles = build_incommuter_fleet(
            person_ids, modes, orig_ars, income_eur, data_path, rng)
    else:
        vehicle_types = pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                              "hbefa_cat", "hbefa_tech", "hbefa_size",
                                              "hbefa_emission"])
        vehicles = _build_legacy_vehicles(person_ids, modes)
    # Every in-commuter -- like every resident (synthesis.vehicles.passengers.default) --
    # must own a car_passenger vehicle. car_passenger is a network-routed mode (eqasim
    # core NETWORK_MODES = [car, car_passenger, truck]) that the in-loop discrete mode
    # choice can assign to ANY agent regardless of car ownership; without the vehicle the
    # MATSim router aborts with "Could not retrieve vehicle id ... for mode car_passenger".
    # The car builders above emit only "car" (and only for car-mode agents), so the
    # car_passenger vehicle is added here for every in-commuter. The default_car_passenger
    # vehicle TYPE is already in the scenario type table (added by the resident passengers
    # stage and de-duplicated in braunschweig.matsim.scenario.vehicles), so only the
    # per-agent vehicles are emitted.
    vehicles = pd.concat([vehicles, _build_incommuter_passenger_vehicles(person_ids)],
                         ignore_index=True)
    # Per-agent validation record (one row per in-commuter): source Kreis, direction,
    # final mode (post PT->car reassignment), and the ACTUAL boarding point.
    #
    # entry_kind: "real_origin" for in-ring agents (real_origin=True flag, Task 4b);
    #             "rail_station" for PT agents placed at a rail station;
    #             "road_gate" for car agents (incl. PT agents reassigned to car).
    # entry_x/entry_y: the actual boarding point coordinates -- real origin for in-ring
    #                  (home_x/home_y after override), rail station for far PT, road gate
    #                  for far car.  gate_id is kept for traceability (the road gate the
    #                  agent is assigned to for both network entry and mode-fallback
    #                  purposes).  Task B6 will consume entry_x/entry_y/entry_kind.
    mode_arr = np.asarray(modes)

    if real_origin and is_in_ring is not None:
        # in-ring agents get "real_origin"; far agents get mode-based kind.
        _far_kind = np.where(mode_arr == "pt", "rail_station", "road_gate")
        entry_kind = np.where(is_in_ring, "real_origin", _far_kind)
        # entry coords: in-ring -> home_x/home_y (real origin); far PT -> home_x/home_y
        # (station); far car -> gate_x/gate_y.  Because home_x/home_y was already
        # overridden for in-ring agents and is still station/gate for far agents, we
        # can use home_x/home_y for PT/in-ring and gate_x/gate_y only for far car.
        _far_entry_x = np.where(mode_arr == "pt", home_x, gate_x)
        _far_entry_y = np.where(mode_arr == "pt", home_y, gate_y)
        entry_x = np.where(is_in_ring, home_x, _far_entry_x)
        entry_y = np.where(is_in_ring, home_y, _far_entry_y)
    else:
        entry_kind = np.where(mode_arr == "pt", "rail_station", "road_gate")
        # entry coords: for PT -> home_x/home_y (the station); for car -> gate_x/gate_y.
        entry_x = np.where(mode_arr == "pt", home_x, gate_x)
        entry_y = np.where(mode_arr == "pt", home_y, gate_y)

    validation = pd.DataFrame({
        "ars5": orig_ars, "direction": "ein", "mode": mode_arr,
        "entry_kind": entry_kind, "entry_x": entry_x, "entry_y": entry_y,
        "gate_id": gate_ids,
    })

    # MiD/Mikrozensus target modal split for the "ein" direction:
    # aggregate P(pt) over the actual agent distance distribution from the
    # restricted reference (pre-balance, what the reference assigns on average).
    # This is the honest proxy -- the balancer conserves this count exactly when
    # the flexible pool suffices (residual == 0), so it is both the target and
    # the expected outcome. Expressed as a [direction, mode, share_pct_target]
    # frame consumed by write_cordon_validation's modal_split_deviation.
    _pt_target_shares = np.array([
        restricted_reference.get(band_fn(d), {}).get("pt", 0.0)
        for d in dist_km
    ], dtype=float)
    _pt_target_share_pct = float(100.0 * _pt_target_shares.mean()) if n > 0 else 0.0
    _car_target_share_pct = 100.0 - _pt_target_share_pct
    mode_target = pd.DataFrame([
        ("ein", "pt",  _pt_target_share_pct),
        ("ein", "car", _car_target_share_pct),
    ], columns=["direction", "mode", "share_pct_target"])

    return dict(persons=persons, trips=trips, activities=activities,
                locations=locations, vehicles=vehicles,
                vehicle_types=vehicle_types, households=households,
                validation=validation, mode_target=mode_target)


def _sample_workplaces(dest_ars, zgb_work, rng):
    """Sample an employment-weighted ZGB workplace per agent within its dest Kreis.

    PRIMARY method: sample within the agent's destination Kreis (commune_id[:5]).
    FALLBACK: if the dest Kreis has no workplaces (absent / ARS-format mismatch),
    sample from the whole-ZGB pool. The fallback rate is logged and returned so a
    high rate (which would mean a systematic Kreis-key mismatch, not a few empty
    cells) is visible rather than silent (CLAUDE.md "Fallback transparency").

    Returns ``(x, y, location_ids, n_fallback)``.
    """
    w = zgb_work[~zgb_work["commune_id"].astype(str).str.startswith("EXT")].copy()
    w["kreis"] = w["commune_id"].astype(str).str[:5]
    by_kreis = {k: sub for k, sub in w.groupby("kreis")}
    xs, ys, ids = [], [], []
    n_fallback = 0
    for dest in dest_ars:
        pool = by_kreis.get(str(dest))
        if pool is None:
            pool = w
            n_fallback += 1
        prob = pool["employees"].to_numpy(dtype=float)
        prob = prob / prob.sum() if prob.sum() > 0 else None
        row = pool.iloc[int(rng.choice(len(pool), p=prob))]
        xs.append(row.geometry.x); ys.append(row.geometry.y); ids.append(row["location_id"])
    n = len(ids)
    if n_fallback:
        share = 100.0 * n_fallback / n if n else 0.0
        level = "WARNING: " if share > 5.0 else ""
        print(
            f"[braunschweig.incommuters] {level}workplace Kreis fallback: "
            f"primary {n - n_fallback}/{n} ({100.0 - share:.1f}%), "
            f"fallback {n_fallback} ({share:.1f}%) in-commuters had no workplace in "
            f"their destination Kreis -> sampled from the whole-ZGB pool. A high rate "
            f"means a systematic dest_ars vs commune_id[:5] key mismatch.",
            flush=True,
        )
    return np.array(xs, dtype=float), np.array(ys, dtype=float), ids, n_fallback



def _agent_times(donors, hts_trips, person_col, dist_km, home_to_gate_km,
                 speed_kmh, detour_factor):
    """Per-agent (arrive_work, depart_work, depart_home, arrive_home) seconds.

    Donors are sampled WITH REPLACEMENT, so the same HTS person can back many
    agents. ``extract_commute_times`` is therefore memoised ONCE per unique donor
    id and mapped onto the agents -- the per-agent result is identical to calling
    it per agent (the function is a pure read of the donor's trips), but the work
    is done once per distinct donor instead of once per agent. The trip table is
    grouped only over the donors actually used.

    ``dist_km`` is the inside (gate->work) straight-line distance; ``home_to_gate_km``
    is the outside (home->gate) access distance (0 for car, where home == gate). The
    home departure is seeded so the total teleported access plus the (initially
    estimated) inside leg lands at the donor work arrival; MATSim re-times the
    simulated gate->work leg over the iterations.
    """
    donor_ids = donors[person_col].to_numpy()
    unique_ids = pd.unique(donor_ids)
    used = hts_trips[hts_trips[person_col].isin(unique_ids)]
    by_person = {pid: sub for pid, sub in used.groupby(person_col)}
    # Memoise the four commute times per UNIQUE donor id (same donor -> same times).
    times_by_id = {pid: extract_commute_times(by_person[pid]) for pid in unique_ids}
    arrive_work = np.array([times_by_id[pid][1] for pid in donor_ids], dtype=float)
    depart_work = np.array([times_by_id[pid][2] for pid in donor_ids], dtype=float)
    arrive_home = np.array([times_by_id[pid][3] for pid in donor_ids], dtype=float)
    # Home departure = work arrival minus the total teleported travel time:
    # (home->gate outside access) + (gate->work inside estimate), each at gate speed
    # with the detour factor. For car home_to_gate_km is 0, so this is identical to
    # the previous gate->work-only seed; for PT it adds the boarding-stop->gate
    # access so the departure is consistent with the actual (moved) home coordinate.
    if speed_kmh <= 0:
        raise ValueError("_agent_times: speed_kmh must be > 0")
    dist_km = np.asarray(dist_km, dtype=float)
    home_to_gate_km = np.asarray(home_to_gate_km, dtype=float)
    travel_s = ((dist_km + home_to_gate_km) * detour_factor) / speed_kmh * 3600.0
    depart_home = np.maximum(0.0, arrive_work - travel_s)
    return arrive_work, depart_work, depart_home, arrive_home


def _build_persons(ids, donors, person_col, modes, income_eur):
    """Persons frame for injected in-commuters with the resident attribute schema.

    ``income_eur`` is the per-agent monthly household income (EUR) drawn from the
    origin-Kreis INKAR level (:func:`draw_incommuter_income`). The categorical
    ``household_income`` placeholder string (read by the legacy Bavaria writer) is set
    to the same value so the two stay consistent.
    """
    age = donors["age"].to_numpy() if "age" in donors.columns else 40
    sex = donors["sex"].to_numpy() if "sex" in donors.columns else "male"
    hts_id = donors[person_col].to_numpy() if person_col in donors.columns else -1
    income_eur = np.asarray(income_eur, dtype=float)
    persons = pd.DataFrame({
        "person_id": ids["person_id"].to_numpy(),
        "household_id": ids["household_id"].to_numpy(),
        "census_person_id": ids["person_id"].to_numpy(),
        "census_household_id": ids["household_id"].to_numpy(),
        "hts_id": hts_id, "hts_household_id": -1,
        "age": age, "sex": sex, "employed": True,
        "subpopulation": "incommuter",
        "car_availability": np.where(np.asarray(modes) == "car", "all", "none"),
        "household_income_eur": income_eur,
        "household_income": [str(int(v)) for v in income_eur],
    })
    for key, value in _INCOMMUTER_PERSON_DEFAULTS.items():
        persons[key] = value
    return persons


def _build_households(ids, income_eur):
    """One single-person household per injected in-commuter.

    ``income_eur`` is the per-agent monthly household income (EUR) drawn from the
    origin-Kreis INKAR level (:func:`draw_incommuter_income`).
    """
    income_eur = np.asarray(income_eur, dtype=float)
    return pd.DataFrame({
        "household_id": ids["household_id"].to_numpy(),
        "person_id": ids["person_id"].to_numpy(),
        "census_household_id": ids["household_id"].to_numpy(),
        "household_income": [str(int(v)) for v in income_eur],
        "household_income_eur": income_eur,
        "high_income": False,
        "car_availability": "all", "bicycle_availability": "all",
    })


# Monthly household income (EUR) quintile thresholds used to map a cross-cordon
# in-commuter's INKAR-scaled income onto the 5-class MiD economic status the German
# fleet's segment IPF expects. The income is INCOMMUTER_BASE_INCOME_EUR (the regional
# mean, mapped to "medium") scaled by the origin-Kreis INKAR factor; the thresholds
# are placed symmetrically around the base so that an at-mean income is "medium" and
# the spread maps the richer / poorer origin Kreise onto the higher / lower classes.
# This is a documented, transparent mapping (the in-commuter income carries no MiD
# income CLASS, only a continuous EUR value), not a free parameter.
_INCOMMUTER_STATUS_THRESHOLDS_EUR = (
    0.70 * INCOMMUTER_BASE_INCOME_EUR,   # < -> very_low
    0.90 * INCOMMUTER_BASE_INCOME_EUR,   # < -> low
    1.10 * INCOMMUTER_BASE_INCOME_EUR,   # < -> medium
    1.30 * INCOMMUTER_BASE_INCOME_EUR,   # < -> high ; else very_high
)
_INCOMMUTER_STATUS_LABELS = ("very_low", "low", "medium", "high", "very_high")


def _economic_status_from_income(income_eur):
    """Map per-agent monthly household income (EUR) to the 5-class economic status.

    Uses :data:`_INCOMMUTER_STATUS_THRESHOLDS_EUR` (centred on the regional-mean
    base income) so an in-commuter from a richer origin Kreis draws from a higher
    economic status -- the same income->status coupling the resident fleet has via
    the MiD H4 income classes, expressed here on the continuous INKAR-scaled EUR.
    """
    income_eur = np.asarray(income_eur, dtype=float)
    idx = np.digitize(income_eur, _INCOMMUTER_STATUS_THRESHOLDS_EUR)
    return np.array([_INCOMMUTER_STATUS_LABELS[i] for i in idx])


def _incommuter_kreis_ags5(orig_ars):
    """Origin Kreis AGS-5 from the in-commuter origin ARS (first 5 digits).

    The KBA fleet tables are ZGB-only, so an out-of-ZGB origin Kreis is not in the
    per-Kreis powertrain table; the fleet sampler then falls back (observably,
    logged) to the national P(powertrain|segment). The Kreis code is still carried
    through so the (rare) cross-cordon commuter from an in-table Kreis is typed
    locally.
    """
    return np.array([str(a)[:5] for a in orig_ars])


def build_incommuter_fleet(person_ids, modes, orig_ars, income_eur, data_path, rng):
    """Draw cross-cordon in-commuter cars from the German NDS fleet (Task F6).

    Replaces the legacy single hardcoded ``Gazole/euro6`` row: each car-mode
    in-commuter gets one vehicle drawn from the same KBA/HBEFA generative chain as
    residents (:func:`braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet`),
    using the agent's origin-Kreis-scaled economic status. The origin Gemeinde /
    RegioStaR raumtyp are outside the ZGB scope and unavailable, so the Gemeinde
    BEV tilt and the raumtyp-specific segment IPF fall back to the Kreis / national
    levels -- this is logged by the sampler (no-silent-fallback rule).

    Parameters
    ----------
    person_ids : per-agent in-commuter person ids (all modes).
    modes : per-agent fixed commute mode; only ``"car"`` agents get a vehicle.
    orig_ars : per-agent 5-digit origin Kreis ARS.
    income_eur : per-agent monthly household income (EUR), origin-Kreis scaled.
    data_path : the synpp ``data_path`` (parent of ``braunschweig/kba/...``).
    rng : the in-commuter :class:`numpy.random.Generator` (seed propagated to the
        fleet draw so the whole in-commuter synthesis stays reproducible).

    Returns
    -------
    (df_vehicle_types, df_vehicles) :
        ``df_vehicle_types`` are the distinct HBEFA :class:`VehicleType` records of
        the drawn in-commuter cars (to be merged into the scenario type table);
        ``df_vehicles`` is one row per car-mode in-commuter with the legacy writer
        columns (``owner_id, mode, vehicle_id, type_id, critair, technology, age,
        euro``) plus the German per-vehicle spec columns the F6 writer emits
        (``segment, powertrain, euro_class, brand, model``).
    """
    from braunschweig.synthesis.vehicles import fleet_sampling_de as fleet

    person_ids = np.asarray(person_ids)
    modes = np.asarray(modes)
    orig_ars = np.asarray(orig_ars)
    income_eur = np.asarray(income_eur, dtype=float)

    car_mask = modes == "car"
    car_person_ids = person_ids[car_mask]
    empty_types = pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                        "hbefa_cat", "hbefa_tech", "hbefa_size",
                                        "hbefa_emission"])
    if len(car_person_ids) == 0:
        empty_vehicles = pd.DataFrame(columns=[
            "owner_id", "mode", "vehicle_id", "type_id", "critair", "technology",
            "age", "euro", "segment", "powertrain", "euro_class", "brand", "model"])
        return empty_types, empty_vehicles

    df_cars = pd.DataFrame({
        "economic_status": _economic_status_from_income(income_eur[car_mask]),
        "kreis_ags5": _incommuter_kreis_ags5(orig_ars[car_mask]),
        # Origin Gemeinde + RegioStaR raumtyp are outside ZGB -> not available; the
        # sampler falls back to the Kreis / national levels (logged).
        "gemeinde": np.nan,
        "raumtyp": np.nan,
    })

    # Derive a deterministic integer seed from the in-commuter rng so the fleet
    # draw is reproducible and decoupled from the rng's mutable internal state.
    fleet_seed = int(rng.integers(0, 2**31 - 1))
    df_spec, df_vehicle_types = fleet.sample_fleet(
        df_cars, data_path, random_seed=fleet_seed)

    df_vehicles = pd.DataFrame({
        "owner_id": car_person_ids,
        "mode": "car",
        "vehicle_id": [f"{pid}:car" for pid in car_person_ids],
        "type_id": df_spec["type_id"].to_numpy(),
        # critair is empty for the German fleet (HBEFA attributes live on the type);
        # technology/age/euro mirror the resident household frame writer columns.
        "critair": "",
        "technology": df_spec["powertrain"].to_numpy(),
        "age": df_spec["age"].to_numpy(),
        "euro": df_spec["euro_class"].to_numpy(),
        "segment": df_spec["segment"].to_numpy(),
        "powertrain": df_spec["powertrain"].to_numpy(),
        "euro_class": df_spec["euro_class"].to_numpy(),
        "brand": df_spec["brand"].to_numpy(),
        "model": df_spec["model"].to_numpy(),
    })
    return df_vehicle_types, df_vehicles


def _build_legacy_vehicles(person_ids, modes):
    """Legacy single-type in-commuter cars (no ``data_path`` supplied).

    Kept for legacy callers / unit tests that build in-commuter frames without a
    German fleet ``data_path``: one ``default_car`` per car-mode in-commuter with
    the four legacy attributes, exactly the pre-F6 behaviour (byte-identical).
    """
    owners = [pid for pid, m in zip(person_ids, modes) if m == "car"]
    return pd.DataFrame({
        "owner_id": owners,
        "mode": "car",
        "vehicle_id": [f"{pid}:car" for pid in owners],
        "type_id": "default_car",
        "critair": "Crit'air 1",
        "technology": "Gazole",
        "age": 0,
        "euro": 6,
    })


def _build_incommuter_passenger_vehicles(person_ids):
    """One ``car_passenger`` vehicle per in-commuter, mirroring
    ``synthesis.vehicles.passengers.default`` for residents.

    ``car_passenger`` is a network-routed mode (eqasim core
    ``NETWORK_MODES = [car, car_passenger, truck]``) that the in-loop discrete mode
    choice can assign to ANY agent regardless of car ownership. Without a
    ``car_passenger`` vehicle the MATSim router aborts with
    "Could not retrieve vehicle id from person ... for mode: car_passenger". The car
    builders emit only ``car`` (and only for car-mode agents), so every in-commuter is
    given a ``car_passenger`` vehicle here. The writer columns mirror
    :func:`_build_legacy_vehicles` / ``synthesis.vehicles.passengers.default`` exactly;
    the ``default_car_passenger`` type is contributed once by the resident passengers
    stage and de-duplicated downstream, so it is not re-emitted here.
    """
    person_ids = np.asarray(person_ids)
    return pd.DataFrame({
        "owner_id": person_ids,
        "mode": "car_passenger",
        "vehicle_id": [f"{pid}:car_passenger" for pid in person_ids],
        "type_id": "default_car_passenger",
        "critair": "Crit'air 1",
        "technology": "Gazole",
        "age": 0,
        "euro": 6,
    })


def _empty_frames(crs):
    """Zero-row frames (the flag-OFF / no-agent path) so the merge is a perfect no-op."""
    return dict(
        persons=pd.DataFrame(columns=["person_id"]),
        trips=pd.DataFrame(columns=["person_id"]),
        activities=pd.DataFrame(columns=["person_id"]),
        locations=gpd.GeoDataFrame({"person_id": []}, geometry=[], crs=crs),
        vehicles=pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"]),
        vehicle_types=pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                            "hbefa_cat", "hbefa_tech", "hbefa_size",
                                            "hbefa_emission"]),
        households=pd.DataFrame(columns=["household_id", "person_id"]),
        validation=pd.DataFrame(columns=["ars5", "direction", "mode", "entry_kind",
                                         "entry_x", "entry_y", "gate_id"]),
        mode_target=pd.DataFrame(columns=["direction", "mode", "share_pct_target"]))


def configure(context):
    context.config("cordon_enabled", False)
    context.config("random_seed")
    if not context.config("cordon_enabled"):
        return
    context.config("data_path")
    context.config("sampling_rate")
    context.config("braunschweig.political_prefix")
    # The German NDS fleet draw for in-commuter cars (Task F6) is active only when
    # the resident fleet uses the household method; otherwise the in-commuter cars
    # keep the legacy single default-car row (OFF-equivalence).
    context.config("vehicles_method", "default")
    context.config("cordon_gate_speed_kmh", 30.0)
    # PT in-commuter boarding: accessibility multiplier for one-transfer rail stations.
    # direct stations weight 1.0; transfer stations weight ``cordon_pt_transfer_penalty``.
    # A lower value further de-emphasises transfer stations relative to direct ones.
    # Must be in (0, 1]; default 0.5 (transfer stations get half the weight of direct).
    context.config("cordon_pt_transfer_penalty", 0.5)
    # PT station distance-gravity: distance-decay slope (per km) applied as
    # exp(beta * dist_to_zgb_km) in weight_entry_stations.  Should be <= 0 (closer
    # stations preferred).  Default -0.05 mirrors ``cordon_gravity_beta`` (road cordon
    # gravity); set to 0.0 to disable (flat within-Kreis decay).
    # ASSUMPTION: -0.05/km is chosen to mirror the road cordon gravity and has no
    # committed calibration target in this repository.  Straight-line distance is a
    # proxy for travel time; actual routing is handled by the MATSim router after network
    # injection.  Re-calibrate against observed in-commuter PT boarding origins when data
    # are available.
    context.config("cordon_pt_gravity_beta", -0.05)
    # Real in-ring in-commuter origin placement (Task 4b).  When True, agents whose
    # source Kreis has at least one Gemeinde representative point within the source-
    # network ring receive a real population-weighted origin home point drawn from those
    # in-ring Gemeinden instead of the road gate / rail station.  Default True:
    # the feature is active by default so it is not silently skipped; set to False to
    # reproduce the pre-4b byte-identical behaviour (no additional rng draws consumed).
    context.config("cordon_incommuter_real_origin", True)
    # Mode balancer (Task B7).  When True, the Mikrozensus-drawn modes are passed through
    # balance_incommuter_modes before the PT station placement step so the global PT count
    # is conserved under the rail-access constraint (no net PT loss from rail-less Kreise).
    # Default True: the feature is active by default.  Set to False to reproduce the
    # legacy lossy PT->car reassignment (byte-identical for same rng seed).
    context.config("cordon_incommuter_mode_balance", True)
    # Declared unconditionally (independent of real_origin) so execute() can verify the
    # pre-clipped osm/cordon ring was built with the configured source buffer
    # (verify_clip_signature) -- a stale clip would silently change the road extent.
    context.config("cordon_network_source_buffer_m", 45000.0)
    context.config("osm_path", "osm")
    context.config("data_path")
    context.stage("braunschweig.synthesis.cordon_gates")
    context.stage("braunschweig.data.cordon_pt_gates")
    context.stage("braunschweig.data.census.pendler")
    context.stage("braunschweig.locations.work")
    # Aliased name (config maps it to the popsim enriched_adapter or the IPF
    # braunschweig.synthesis.population.enriched). Depending on the hard-coded IPF
    # module bypassed the alias and dragged the eqasim HTS-matching subtree
    # (synthesis.population.matched) into popsim runs. Only the max resident
    # person_id / household_id are read (id collision avoidance), which both variants
    # provide, so the population-agnostic aliased dependency is correct.
    context.stage("synthesis.population.enriched")  # RAW, for n_residents
    context.stage("braunschweig.data.inkar.household_income")  # origin-Kreis income level
    context.stage("data.hts.selected", alias="hts")
    if context.config("cordon_incommuter_real_origin"):
        # ZGB polygon for in-ring test + VG250 config for external Gemeinden loading.
        context.stage("data.spatial.municipalities")
        context.config("cordon_network_source_buffer_m", 45000.0)
        # Mirror the keys declared by braunschweig.data.external_workplaces.configure()
        # so _load_gemeinden(context) can resolve the VG250-EW archive path.
        context.config(
            "germany.population_path",
            "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
        context.config(
            "germany.population_source",
            "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg")


def execute(context):
    crs = "EPSG:25832"
    if not context.config("cordon_enabled"):
        return _empty_frames(crs)

    # Guard: the pre-clipped osm/cordon ring must have been built with the configured
    # cordon_network_source_buffer_m, else data.osm.cleaned silently uses a stale road
    # extent. Warns if the ring predates the clip-signature guard; raises on a mismatch.
    import os as _os
    from braunschweig.data.cordon.network import verify_clip_signature
    verify_clip_signature(
        _os.path.join(context.config("data_path"), context.config("osm_path")),
        float(context.config("cordon_network_source_buffer_m")),
    )

    from braunschweig.data.mikrozensus.reference import load_commute_mode_by_distance

    gate_volume = context.stage("braunschweig.synthesis.cordon_gates")
    residents = context.stage("synthesis.population.enriched")
    _hts_households, hts_persons, hts_trips = context.stage("hts")
    rng = np.random.default_rng(int(context.config("random_seed")) + 100000)

    real_origin = bool(context.config("cordon_incommuter_real_origin"))
    mode_balance = bool(context.config("cordon_incommuter_mode_balance"))
    gemeinden_df = None
    zgb_polygon = None
    source_buffer_m = 45000.0

    if real_origin:
        from braunschweig.data.external_workplaces import _load_gemeinden  # noqa: PLC0415
        # Dissolve ZGB municipalities into a single polygon for the in-ring test.
        df_muni = context.stage("data.spatial.municipalities")
        zgb_polygon = df_muni.geometry.union_all()
        source_buffer_m = float(context.config("cordon_network_source_buffer_m"))
        # Reuse the canonical loader (includes gem_ags length/prefix assertions).
        gemeinden_raw = _load_gemeinden(context)
        gemeinden_df = gemeinden_raw.to_crs(crs)[["ars5", "gem_ags", "ewz", "geometry"]].copy()
        print(
            f"[braunschweig.incommuters] real-origin ON: "
            f"{len(gemeinden_df)} external Gemeinden loaded for in-ring origin draw "
            f"(source_buffer_m={source_buffer_m:.0f} m)",
            flush=True,
        )

    frames = build_incommuter_frames(
        flows=context.stage("braunschweig.data.census.pendler"),
        zgb_kreise={str(p) for p in context.config("braunschweig.political_prefix")},
        sampling_rate=float(context.config("sampling_rate")),
        gates=gate_volume["gates"], assignment=gate_volume["assignment"],
        zgb_work=context.stage("braunschweig.locations.work"),
        mode_reference=load_commute_mode_by_distance(context.config("data_path")),
        hts_persons=hts_persons, hts_trips=hts_trips, person_col="person_id",
        # Offsets are max+1, NOT count/nunique: resident person/household ids need not
        # be a contiguous [0, n) range, and any overlap would make injected ids
        # collide with residents -> interleaved (household_id, person_id) sort ->
        # a person ends up with zero activities (population writer assertion).
        n_residents=int(residents["person_id"].max()) + 1,
        n_resident_households=int(residents["household_id"].max()) + 1,
        rng=rng,
        gate_speed_kmh=float(context.config("cordon_gate_speed_kmh")),
        pt_entry_stops=context.stage("braunschweig.data.cordon_pt_gates"),
        pt_transfer_penalty=float(context.config("cordon_pt_transfer_penalty")),
        pt_gravity_beta=float(context.config("cordon_pt_gravity_beta")),
        inkar_income=context.stage("braunschweig.data.inkar.household_income"),
        # data_path drives the German NDS fleet draw for the in-commuter cars
        # (Task F6); only set it when the German fleet is active so OFF keeps the
        # legacy single default-car in-commuter rows.
        data_path=(context.config("data_path")
                   if context.config("vehicles_method") == "household"
                   else None),
        real_origin=real_origin,
        gemeinden=gemeinden_df,
        zgb_polygon=zgb_polygon,
        source_buffer_m=source_buffer_m,
        mode_balance=mode_balance,
    )
    print(f"[braunschweig.synthesis.incommuters] {len(frames['persons'])} in-commuters "
          f"injected ({(frames['trips']['mode'] == 'pt').sum() // 2} PT, rest car)")
    return frames
