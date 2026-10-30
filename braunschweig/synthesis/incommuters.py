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

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig import constants as _constants
from braunschweig import synpp_tokens
from braunschweig.constants import ROUTED_DETOUR_FACTOR
from braunschweig.data.cordon import demand as _demand
from braunschweig.data.cordon.demand import (
    expand_to_agents, make_incommuter_ids, select_inbound_flows)
from braunschweig.data.cordon import gate_assignment as _gate_assignment
from braunschweig.data.cordon.gate_assignment import sample_gate_per_agent
from braunschweig.data.cordon import mode_balancer as _mode_balancer
from braunschweig.data.cordon.mode_balancer import balance_incommuter_modes, rebalance_with_timetable
from braunschweig.data.cordon import mode_reference as _mode_reference
from braunschweig.data.cordon.mode_reference import (
    MID_DISTANCE_EDGES, restrict_to_modes, route_distance_band)
from braunschweig.data.cordon import plans as _plans
from braunschweig.data.cordon.plans import (
    assign_fixed_mode, assign_fixed_mode_per_agent, build_incommuter_activities,
    build_incommuter_locations, build_incommuter_trips, extract_commute_times,
    impute_incommuter_times,
    redraw_times_within_bound, sample_donors, select_commuter_donors, straight_line_distance_km)
# Per-Bundesland in-commuter mode reference (#129): origin-Kreis ARS -> Bundesland.
# mikrozensus.reference imports only from cordon.mode_reference (no cycle with this module).
from braunschweig.data.mikrozensus import reference as _mikrozensus_reference
from braunschweig.data.mikrozensus.reference import bundesland_of_ars
# Timetable check of the pt days (#488) and the day-end rule it shares with the population writer.
from braunschweig.matsim.scenario import day_end_margin as _day_end_margin
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS
from braunschweig.synthesis import incommuter_pt_connections as _pt_connections

logger = logging.getLogger(__name__)

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
    has_pt_subscription=False, pt_subscription_type="never_pt",
    high_income=False,
    is_bs_resident=False, is_urban_resident=False, age_range="higher_education",
    # In-commuters live outside the ZGB cordon, so the MiD-derived
    # ``housing_tenure`` completeness attribute (synthesise_housing_tenure,
    # braunschweig.synthesis.population.enriched) is not applicable to them.
    # Without an explicit default, ``concat_frame``'s reindex to the resident
    # columns fills NaN for this column, and the MATSim writer's ``str(NaN)``
    # then emits the literal string "nan" as the housingTenure attribute
    # value. "unknown" documents the gap explicitly instead.
    housing_tenure="unknown",
)


#: Re-draws of an in-commuter donor whose day ends after the simulated-day bound (#467); count, >= 0.
KEY_TIME_REDRAW_MAX_ATTEMPTS = "braunschweig.incommuters.time_redraw_max_attempts"
DEFAULT_TIME_REDRAW_MAX_ATTEMPTS = 20


def validate_time_redraw_max_attempts(value) -> int:
    """``KEY_TIME_REDRAW_MAX_ATTEMPTS`` as an int: an integral number >= 0 (0 = no re-draw), else ValueError.

    Matches the check of ``plans.redraw_times_within_bound`` (>= 0); a non-integral value is rejected, not truncated."""
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        as_float = float("nan")
    if isinstance(value, bool) or not np.isfinite(as_float) or as_float != int(as_float) or int(as_float) < 0:
        raise ValueError(f"{KEY_TIME_REDRAW_MAX_ATTEMPTS} must be an integer >= 0 (0 = no re-draw), got {value!r}")
    return int(as_float)

#: ASSUMPTION (#467): relative tolerance of the pt re-balancing after the timetable check; the in-commuter pt count is
#: pulled back until it is at least (1 - tolerance) times the balancer's Mikrozensus target. In [0, 1); not calibrated.
KEY_PT_SHARE_TOLERANCE = "braunschweig.incommuters.pt_share_tolerance"
DEFAULT_PT_SHARE_TOLERANCE = 0.01
#: Cap on the promotion rounds of the pt re-balancing after the timetable check (#467); count, >= 0 (0 disables it).
KEY_REBALANCE_MAX_ITERATIONS = "braunschweig.incommuters.rebalance_max_iterations"
DEFAULT_REBALANCE_MAX_ITERATIONS = 10

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


def assemble_incommuter_core_frames(person_ids, home_x, home_y, mid_x, mid_y,
                                    mid_location_ids, depart_home_s, arrive_mid_s,
                                    depart_mid_s, arrive_home_s, modes,
                                    crs, middle_purpose="work", *, repair_chronology=True):
    """Build the shared Home->MIDDLE->Home core frames (trips, activities,
    locations) for an in-commuter subpopulation. ``middle_purpose`` is "work" for
    SvB commuters and "education" for students. ``mid_location_ids`` are the unique
    per-agent middle-activity facility ids. Returns dict[trips, activities,
    locations]; ``trips['mode']`` is the per-agent mode repeated over the 2 legs."""
    # Complete any item-nonresponse in the donor timings BEFORE building trips + activities
    # so both frames share the same finite times -- a non-final activity without an end time
    # makes MATSim's PlanRouter abort ("undefined activity end time"). See impute_incommuter_times.
    depart_home_s, arrive_mid_s, depart_mid_s, arrive_home_s = impute_incommuter_times(
        depart_home_s, arrive_mid_s, depart_mid_s, arrive_home_s,
        middle_purpose=middle_purpose, repair_chronology=repair_chronology)
    trips = build_incommuter_trips(person_ids, depart_home_s, arrive_mid_s,
                                   depart_mid_s, arrive_home_s,
                                   middle_purpose=middle_purpose)
    trips["mode"] = np.repeat(np.asarray(modes), 2)
    activities = build_incommuter_activities(person_ids, depart_home_s, arrive_mid_s,
                                             depart_mid_s, arrive_home_s,
                                             middle_purpose=middle_purpose)
    locations = build_incommuter_locations(person_ids, home_x, home_y, mid_x, mid_y,
                                           mid_location_ids, crs)
    return {"trips": trips, "activities": activities, "locations": locations}


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
                            mode_balance=False,
                            mode_reference_by_bundesland=None,
                            repair_chronology=True,
                            pt_check=None,
                            time_bound_s=float(MAX_PLAN_TIME_SECONDS),
                            time_redraw_max_attempts=DEFAULT_TIME_REDRAW_MAX_ATTEMPTS,
                            pt_share_tolerance=DEFAULT_PT_SHARE_TOLERANCE,
                            rebalance_max_iterations=DEFAULT_REBALANCE_MAX_ITERATIONS):
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

    ``pt_check`` (default ``None``: no check, byte-identical): a
    :class:`~braunschweig.synthesis.incommuter_pt_connections.PtCheck`. Then, after the
    donor times are imputed, every day whose return arrival is after ``pt_check.bound_s``
    first moves earlier as a whole (the rule of the population writer's day-end margin,
    ``day_end_margin.late_day_shift_s``), and the pt days are checked against the
    timetable and resolved by ``incommuter_pt_connections.resolve_pt_days`` (#488): a
    shifted day keeps its durations; a far pt in-commuter (not a real in-ring origin)
    whose day no shift serves then tries up to ``pt_check.station_candidates - 1`` other
    stations of its source Kreis in weight order (#467) and boards at the first connected
    one; one without any connection drives and boards at its road gate (real in-ring
    origins keep their home), exactly like the pt in-commuters without a station above
    (the day-bound shift itself always runs, with or without a check). The frames then
    carry the report under ``pt_connection``.

    The effective day bound is ``time_bound_s``, or ``min(time_bound_s, pt_check.bound_s)`` when a check is given:
    the donor re-draw and the whole-day shift never use a later bound than the check, so a direct caller with a
    29 h ``PtCheck`` and the default 30 h ``time_bound_s`` still moves the 29-30 h days (the #488 trap). The frames
    always carry the re-draw and shift counts under ``day_bound`` (n, n_redrawn, n_attempts_max, n_unfit, bound_s,
    n_days_moved, n_still_late), with and without a check.

    With ``mode_balance`` AND ``pt_check`` the pt count is then pulled back towards the
    balancer's Mikrozensus target (#467, owner decision; global, not per Bundesland) by
    :func:`~braunschweig.data.cordon.mode_balancer.rebalance_with_timetable`: car
    in-commuters with ``can_board_pt`` that the check did not switch to car are promoted by
    pt propensity; a promoted far agent draws an entry station (``sample_pt_station_per_agent``),
    an in-ring one keeps its home; the home departure follows the new access distance, and the
    day must pass the same chain WITHOUT the switch to car (connection_found, day_shifted,
    station_redrawn). A promoted agent without a connection stays car at its road gate and
    leaves the pool. Up to ``rebalance_max_iterations`` rounds, until the pt count is at least
    ``(1 - pt_share_tolerance)`` times the target. The frames carry the stats and the actions
    of the promotion attempts under ``pt_rebalance`` (kept apart from ``pt_connection``, whose
    rates are over the original pt days only).
    """
    inbound = select_inbound_flows(flows, zgb_kreise, in_ring_kreise=set(assignment["ars5"]))
    # OD validation target for the per-run cordon analysis (#134): the full-population
    # BA inbound SvB flow per external source Kreis, direction "ein". Derived from the
    # SAME select_inbound_flows frame the agents are expanded from, so the downstream
    # realized-vs-target check (od_deviation_vs_target) is a consistency / coverage
    # check rather than an independent validation. Schema [ars5, direction, n_target].
    od_target = (
        inbound.groupby("orig_ars", as_index=False)["flow"].sum()
        .rename(columns={"orig_ars": "ars5", "flow": "n_target"})
    )
    od_target["direction"] = "ein"
    od_target = od_target[["ars5", "direction", "n_target"]]
    agents = expand_to_agents(inbound, sampling_rate)
    n = len(agents)
    if n == 0:
        empty = _empty_frames(zgb_work.crs)
        empty["day_bound"] = dict(n=0, n_redrawn=0, n_attempts_max=0, n_unfit=0, bound_s=float(time_bound_s),
                                  n_days_moved=0, n_still_late=0)
        return empty

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
    restricted_national = restrict_to_modes(mode_reference, allowed=commute_modes)
    band_fn = lambda d: route_distance_band(d, detour_factor=detour_factor, edges=band_edges)

    # Per-agent restricted mode reference (#129).  Default (mode_reference_by_bundesland
    # is None): every agent uses the single national reference and the region-neutral
    # assign_fixed_mode is called -> byte-identical to the pre-#129 behaviour.  When a
    # per-Bundesland reference dict is supplied, each agent is assigned the restricted
    # reference of its origin Bundesland (first 2 ARS digits), falling back to the
    # national reference (observably, logged) for any Bundesland absent from the dict.
    if mode_reference_by_bundesland is None:
        restricted_reference = restricted_national
        agent_references = None
        modes_mikro = list(assign_fixed_mode(dist_km, restricted_reference, band_fn, rng))
    else:
        restricted_by_bl = {
            bl: restrict_to_modes(ref, allowed=commute_modes)
            for bl, ref in mode_reference_by_bundesland.items()
        }
        agent_bl = [bundesland_of_ars(a) for a in orig_ars]
        agent_references = [
            restricted_by_bl.get(bl, restricted_national) for bl in agent_bl
        ]
        n_fallback = sum(1 for bl in agent_bl if bl not in restricted_by_bl)
        if n:
            share = 100.0 * n_fallback / n
            level = "WARNING: " if share > 5.0 else ""
            print(
                f"[braunschweig.incommuters] {level}per-Bundesland mode reference: "
                f"primary {n - n_fallback}/{n} ({100.0 - share:.1f}%), "
                f"national fallback {n_fallback} ({share:.1f}%) in-commuters had no "
                f"per-Bundesland reference for their origin Land -> national blend. A "
                f"high rate means a systematic ARS-prefix vs Bundesland-name key "
                f"mismatch.",
                flush=True,
            )
        modes_mikro = list(assign_fixed_mode_per_agent(
            dist_km, agent_references, band_fn, rng))

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

        # Per-agent PT propensity: P(pt | distance band) from the agent's restricted
        # reference (per-Bundesland when active, else the single national reference).
        pt_propensity = np.array([
            (agent_references[i] if agent_references is not None
             else restricted_reference).get(band_fn(d), {}).get("pt", 0.0)
            for i, d in enumerate(dist_km)
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
    # The drawn station per agent, for the station re-draw of the timetable check (#467): its alternatives exclude it.
    pt_station_ids = np.full(n, None, dtype=object)

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
                pt_station_ids[i] = stop_id
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
    # A donor whose day ends after the bound (``time_bound_s``: the simulated day minus the day-end margin) is
    # re-drawn up to ``time_redraw_max_attempts`` times (#467); the persons follow the final donor.
    bound_s = float(time_bound_s) if pt_check is None else min(float(time_bound_s), float(pt_check.bound_s))
    dist_km_all, home_to_gate_km_all = np.asarray(dist_km, dtype=float), np.asarray(home_to_gate_km, dtype=float)
    donors, (arrive_work, depart_work, depart_home, arrive_home), redraw_stats = redraw_times_within_bound(
        select_commuter_donors(hts_persons, hts_trips, person_col), n, rng,
        lambda d, pos: _agent_times(d, hts_trips, person_col, dist_km_all[pos], home_to_gate_km_all[pos],
                                    gate_speed_kmh, detour_factor),
        bound_s=bound_s, max_attempts=validate_time_redraw_max_attempts(time_redraw_max_attempts))

    # 4a) Impute the missing donor times (assemble_incommuter_core_frames imputes the same way; on complete times
    # the second imputation changes nothing). A day that still ends after the bound moves earlier as a whole, as
    # the population writer would move it (always, not only with a timetable check): the last resort for days no
    # re-draw fitted. The timetable is then asked at the times that are finally written. Never drops an agent.
    depart_home, arrive_work, depart_work, arrive_home = impute_incommuter_times(
        depart_home, arrive_work, depart_work, arrive_home, middle_purpose="work",
        repair_chronology=repair_chronology)
    move_s, _late, _unresolved = _day_end_margin.late_day_shift_s(
        depart_home, arrive_home, target_s=bound_s)
    depart_home, arrive_work, depart_work, arrive_home = (
        depart_home - move_s, arrive_work - move_s, depart_work - move_s, arrive_home - move_s)
    n_days_moved = int((move_s > 0).sum())
    n_still_late = int(_unresolved.sum())
    shift_message = (f"[braunschweig.incommuters] day bound {bound_s:.0f} s: "
                     f"{n_days_moved}/{n} days moved earlier as a whole "
                     f"({100.0 * n_days_moved / max(n, 1):.1f}%), {n_still_late} still late")
    if n_days_moved / max(n, 1) > 0.05 or n_still_late:
        logger.warning(shift_message)
    else:
        logger.info(shift_message)

    day_bound = dict(redraw_stats, bound_s=bound_s, n_days_moved=n_days_moved, n_still_late=n_still_late)

    # 4b) Timetable check of the pt days (#488). Only with a check, so the default path stays byte-identical.
    pt_report = None
    probe_calls = {"calls": 0, "tasks": 0}
    if pt_check is not None:
        # Router probe calls are counted (one call = one batch routed; tasks = routed trips) for the log and the
        # ``pt_rebalance`` report: a re-balancing round costs 1 + shift offsets + 1 station probe (ADR-0172).
        def counting_probe(tasks):
            probe_calls["calls"] += 1
            probe_calls["tasks"] += len(tasks)
            return pt_check.probe(tasks)

        # Station re-draw (#467): the other eligible stations of a far pt agent's source Kreis, in weight order, with
        # the home departure recomputed for each station's access distance. None when re-drawing is off (1 candidate)
        # or no station table exists; in-ring origins get none (their home is a Gemeinde point, not a station).
        station_candidates = None
        if weighted is not None and int(pt_check.station_candidates) > 1:
            pt_positions = np.flatnonzero(modes == "pt")
            station_candidates = _station_candidates(
                positions=pt_positions, orig_ars=orig_ars, drawn_stop_ids=pt_station_ids, weighted=weighted,
                gate_xy=np.column_stack([gate_x, gate_y]), arrive_work_s=arrive_work, dist_km=dist_km,
                is_in_ring=is_in_ring, n_candidates=int(pt_check.station_candidates), speed_kmh=gate_speed_kmh,
                detour_factor=detour_factor)
        resolution = _pt_connections.resolve_pt_days(
            person_ids=person_ids, is_pt=modes == "pt", home_xy=np.column_stack([home_x, home_y]),
            work_xy=np.column_stack([work_x, work_y]), depart_home_s=depart_home, arrive_work_s=arrive_work,
            depart_work_s=depart_work, arrive_home_s=arrive_home, probe=counting_probe,
            shift_window_s=pt_check.shift_window_s, shift_step_s=pt_check.shift_step_s,
            bound_s=pt_check.bound_s, car_allowed=pt_check.car_allowed and "car" in commute_modes,
            station_candidates=station_candidates)
        offset = resolution.offsets_s
        depart_home, arrive_work, depart_work, arrive_home = (
            depart_home + offset, arrive_work + offset, depart_work + offset, arrive_home + offset)
        to_car = resolution.actions == _pt_connections.ACTION_TO_CAR
        if to_car.any():
            modes = np.where(to_car, "car", modes)
            # Board at the road gate like the pt in-commuters without a station; a real in-ring origin stays.
            at_gate = to_car if is_in_ring is None else to_car & ~is_in_ring
            home_x = np.where(at_gate, gate_x, home_x)
            home_y = np.where(at_gate, gate_y, home_y)
            # The home departure follows the new access distance, as _agent_times seeds it.
            new_access_km = straight_line_distance_km(home_x, home_y, gate_x, gate_y)
            depart_home = np.where(
                to_car, home_departure_s(arrive_work, dist_km, new_access_km, gate_speed_kmh, detour_factor),
                depart_home)
        redrawn = resolution.actions == _pt_connections.ACTION_STATION_REDRAWN
        if redrawn.any():
            # Board at the chosen station: home = station, home departure recomputed for its access distance (the
            # candidate carries it, computed with home_departure_s); the day is not shifted (offset 0).
            chosen = resolution.stations
            home_x = np.where(redrawn, [np.nan if s is None else s.x for s in chosen], home_x)
            home_y = np.where(redrawn, [np.nan if s is None else s.y for s in chosen], home_y)
            depart_home = np.where(redrawn, [np.nan if s is None else s.depart_home_s for s in chosen], depart_home)
        pt_report = dict(resolution.report, n_days_moved_before_bound=n_days_moved)
        _pt_connections.log_report(pt_report, warn_share=pt_check.warn_share)

    # 4c) Re-balancing of the pt count with timetable-connected agents (#467, owner decision). Only with the balancer
    # and a check, so every other path stays byte-identical (no rng draw).
    rebalance_report = None
    if pt_check is not None and mode_balance:
        promotion_actions = dict.fromkeys(
            (_pt_connections.ACTION_CONNECTION_FOUND, _pt_connections.ACTION_DAY_SHIFTED,
             _pt_connections.ACTION_STATION_REDRAWN, "unconnected"), 0)
        n_promotion_without_station = 0
        probe_rounds = []
        probe_calls_before_rebalance = (probe_calls["calls"], probe_calls["tasks"])
        connected_actions = (_pt_connections.ACTION_CONNECTION_FOUND, _pt_connections.ACTION_DAY_SHIFTED,
                             _pt_connections.ACTION_STATION_REDRAWN)

        def connect(indices):
            """Place the promoted car agents at ``indices`` as pt and run the chain without the switch to car;
            write the placement and times of the connected ones into the final arrays, leave the others as car."""
            nonlocal n_promotion_without_station
            calls_before, tasks_before = probe_calls["calls"], probe_calls["tasks"]
            indices = np.asarray(indices, dtype=int)
            cand_x, cand_y, cand_stop = home_x.copy(), home_y.copy(), pt_station_ids.copy()
            placed = np.zeros(n, dtype=bool)
            placed[indices] = True
            far = indices if is_in_ring is None else indices[~is_in_ring[indices]]
            if len(far):
                draws = (sample_pt_station_per_agent([orig_ars[i] for i in far], weighted, rng)
                         if weighted is not None else [None] * len(far))
                for i, stop_id in zip(far, draws):
                    if stop_id is None or stop_id not in _stop_xy:
                        placed[i] = False      # no station to board at: stays car (counted as unconnected)
                        continue
                    cand_x[i], cand_y[i] = _stop_xy[stop_id]
                    cand_stop[i] = stop_id
            n_promotion_without_station += int(len(indices) - placed[indices].sum())
            positions = np.flatnonzero(placed)
            cand_depart_home = depart_home.copy()
            cand_depart_home[positions] = home_departure_s(
                arrive_work[positions], dist_km_all[positions],
                straight_line_distance_km(cand_x[positions], cand_y[positions], gate_x[positions], gate_y[positions]),
                gate_speed_kmh, detour_factor)
            candidates = None
            if weighted is not None and int(pt_check.station_candidates) > 1:
                candidates = _station_candidates(
                    positions=positions, orig_ars=orig_ars, drawn_stop_ids=cand_stop, weighted=weighted,
                    gate_xy=np.column_stack([gate_x, gate_y]), arrive_work_s=arrive_work, dist_km=dist_km,
                    is_in_ring=is_in_ring, n_candidates=int(pt_check.station_candidates), speed_kmh=gate_speed_kmh,
                    detour_factor=detour_factor)
            # car_allowed=False: an unconnected day ends as kept_no_connection, which here means "not promoted".
            attempt = _pt_connections.resolve_pt_days(
                person_ids=person_ids, is_pt=placed, home_xy=np.column_stack([cand_x, cand_y]),
                work_xy=np.column_stack([work_x, work_y]), depart_home_s=cand_depart_home,
                arrive_work_s=arrive_work, depart_work_s=depart_work, arrive_home_s=arrive_home,
                probe=counting_probe, shift_window_s=pt_check.shift_window_s, shift_step_s=pt_check.shift_step_s,
                bound_s=pt_check.bound_s, car_allowed=False, station_candidates=candidates)
            connected = np.isin(attempt.actions[indices], connected_actions)
            round_calls = probe_calls["calls"] - calls_before
            round_tasks = probe_calls["tasks"] - tasks_before
            probe_rounds.append({"n_promoted": int(len(indices)), "router_probe_calls": round_calls,
                                 "router_probe_tasks": round_tasks})
            logger.info("[braunschweig.incommuters] pt re-balancing round %d: %d promoted, %d router probe calls "
                        "(%d routed trips)", len(probe_rounds), len(indices), round_calls, round_tasks)
            for i, ok in zip(indices, connected):
                if not ok:
                    promotion_actions["unconnected"] += 1
                    continue
                action = attempt.actions[i]
                promotion_actions[action] += 1
                offset = attempt.offsets_s[i]
                home_x[i], home_y[i], pt_station_ids[i] = cand_x[i], cand_y[i], cand_stop[i]
                depart_home[i] = cand_depart_home[i] + offset
                arrive_work[i] += offset
                depart_work[i] += offset
                arrive_home[i] += offset
                if action == _pt_connections.ACTION_STATION_REDRAWN:
                    station = attempt.stations[i]
                    home_x[i], home_y[i], pt_station_ids[i] = station.x, station.y, station.stop_id
                    depart_home[i] = station.depart_home_s
            return connected

        modes, rebalance_stats = rebalance_with_timetable(
            modes, _bal_stats["n_pt_target"], can_board_pt, pt_propensity, connect, rng,
            excluded=resolution.actions == _pt_connections.ACTION_TO_CAR,
            tolerance=float(pt_share_tolerance), max_iterations=int(rebalance_max_iterations))
        rebalance_report = dict(rebalance_stats, promotion_actions=promotion_actions,
                                n_promotion_without_station=n_promotion_without_station,
                                n_router_probe_calls=probe_calls["calls"] - probe_calls_before_rebalance[0],
                                n_router_probe_tasks=probe_calls["tasks"] - probe_calls_before_rebalance[1],
                                router_probe_rounds=probe_rounds,
                                tolerance=float(pt_share_tolerance), max_iterations=int(rebalance_max_iterations))
        # The actions of the promotion attempts as rates over the promoted agents (CLAUDE.md fallback transparency);
        # WARN when more than 5 % found no station to board at (can_board_pt and the station weights disagree).
        n_attempts = rebalance_stats["n_promoted"]
        if n_attempts:
            no_station_share = n_promotion_without_station / n_attempts
            promotion_message = (
                "[braunschweig.incommuters] pt re-balancing: %d promotion attempts: %s; without a station %d (%.1f%%)"
                % (n_attempts, ", ".join("%s %d (%.1f%%)" % (action, count, 100.0 * count / n_attempts)
                                         for action, count in promotion_actions.items()),
                   n_promotion_without_station, 100.0 * no_station_share))
            promotion_message += "; router probe calls %d (%d routed trips)" % (
                rebalance_report["n_router_probe_calls"], rebalance_report["n_router_probe_tasks"])
            if no_station_share > _mode_balancer.UNCONNECTED_WARN_SHARE:
                logger.warning("%s -- promoted agents of a Kreis with can_board_pt drew no entry station: check "
                               "the cordon_pt_gates station weights", promotion_message)
            else:
                logger.info(promotion_message)

    # Home stays a normal "home" activity here. The eqasim scenario cutter converts
    # it to an "outside" activity (its location is at the gate / beyond the cordon),
    # which is the native point where outside activities are created -- creating them
    # pre-cut would leave RunPreparation's LinkAssignment without a facility.
    # Each in-commuter workplace gets a unique facility id so it never collides with a
    # resident work facility; the facilities-writer override registers it. Home keeps
    # the placeholder id (-1); the population writer references home_<household_id>,
    # which the facilities override also registers.
    core = assemble_incommuter_core_frames(
        person_ids, home_x, home_y, work_x, work_y,
        [f"ic_work_{int(pid)}" for pid in person_ids],
        depart_home, arrive_work, depart_work, arrive_home, modes,
        zgb_work.crs, middle_purpose="work", repair_chronology=repair_chronology)
    trips, activities, locations = core["trips"], core["activities"], core["locations"]

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
            person_ids, modes, orig_ars, income_eur,
            persons["age"].to_numpy(dtype=float), data_path, rng)
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
        (agent_references[i] if agent_references is not None
         else restricted_reference).get(band_fn(d), {}).get("pt", 0.0)
        for i, d in enumerate(dist_km)
    ], dtype=float)
    _pt_target_share_pct = float(100.0 * _pt_target_shares.mean()) if n > 0 else 0.0
    _car_target_share_pct = 100.0 - _pt_target_share_pct
    mode_target = pd.DataFrame([
        ("ein", "pt",  _pt_target_share_pct),
        ("ein", "car", _car_target_share_pct),
    ], columns=["direction", "mode", "share_pct_target"])

    frames = dict(persons=persons, trips=trips, activities=activities,
                  locations=locations, vehicles=vehicles,
                  vehicle_types=vehicle_types, households=households,
                  validation=validation, mode_target=mode_target,
                  od_target=od_target)
    frames["day_bound"] = day_bound
    if pt_report is not None:
        frames["pt_connection"] = pt_report
    if rebalance_report is not None:
        frames["pt_rebalance"] = rebalance_report
    return frames


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



def home_departure_s(arrive_work, dist_km, home_to_gate_km, speed_kmh, detour_factor):
    """Home departure (s) of an in-commuter: the work arrival minus the total teleported travel time.

    The travel time is (home->gate outside access ``home_to_gate_km``) + (gate->work inside estimate ``dist_km``),
    both straight-line km, times ``detour_factor`` at ``speed_kmh``; never before 0 s. For car ``home_to_gate_km``
    is 0 (home == gate); for pt it is the boarding-station->gate access, so the departure follows the actual home.
    MATSim re-times the simulated gate->work leg over the iterations.
    """
    if speed_kmh <= 0:
        raise ValueError("home_departure_s: speed_kmh must be > 0")
    travel_s = ((np.asarray(dist_km, dtype=float) + np.asarray(home_to_gate_km, dtype=float))
                * detour_factor) / speed_kmh * 3600.0
    return np.maximum(0.0, np.asarray(arrive_work, dtype=float) - travel_s)


def _station_candidates(*, positions, orig_ars, drawn_stop_ids, weighted, gate_xy, arrive_work_s, dist_km,
                        is_in_ring, n_candidates, speed_kmh, detour_factor):
    """Alternative entry stations per far pt agent for the station re-draw of the timetable check (#467).

    For each agent position: the eligible stations (positive weight) of its source Kreis in ``weighted`` (the output
    of ``weight_entry_stations``, columns ``source_ars5, stop_id, weight, x, y``) without its drawn station, in
    descending weight (ties by stop id, deterministic), at most ``n_candidates - 1``; each as a
    :class:`~braunschweig.synthesis.incommuter_pt_connections.StationCandidate` with the home departure recomputed by
    :func:`home_departure_s` for the station->gate access. Agents with a real in-ring origin (``is_in_ring``) and
    agents without another station get no entry. Pure (no rng).
    """
    n_alternatives = int(n_candidates) - 1
    if n_alternatives < 1:
        return {}
    eligible = weighted[weighted["weight"] > 0].assign(source_ars5=lambda df: df["source_ars5"].astype(str))
    eligible = (eligible.sort_values(["source_ars5", "weight", "stop_id"], ascending=[True, False, True],
                                     kind="mergesort")
                .drop_duplicates(["source_ars5", "stop_id"]))
    by_kreis = {ars5: list(zip(sub["stop_id"], sub["x"].astype(float), sub["y"].astype(float)))
                for ars5, sub in eligible.groupby("source_ars5", sort=False)}
    out = {}
    for i in positions:
        i = int(i)
        if is_in_ring is not None and bool(is_in_ring[i]):
            continue
        stations = [st for st in by_kreis.get(str(orig_ars[i]), []) if st[0] != drawn_stop_ids[i]][:n_alternatives]
        if not stations:
            continue
        sx = np.array([st[1] for st in stations])
        sy = np.array([st[2] for st in stations])
        access_km = straight_line_distance_km(sx, sy, np.full(len(sx), gate_xy[i, 0]), np.full(len(sy), gate_xy[i, 1]))
        depart = home_departure_s(np.full(len(sx), arrive_work_s[i]), np.full(len(sx), dist_km[i]), access_km,
                                  speed_kmh, detour_factor)
        out[i] = [_pt_connections.StationCandidate(str(st[0]), float(st[1]), float(st[2]), float(d))
                  for st, d in zip(stations, depart)]
    return out


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
    # Home departure = work arrival minus the total teleported travel time (home_departure_s).
    if speed_kmh <= 0:
        raise ValueError("_agent_times: speed_kmh must be > 0")
    depart_home = home_departure_s(arrive_work, dist_km, home_to_gate_km, speed_kmh, detour_factor)
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
    # The literal 'never_pt' below is braunschweig.popsim.attributes.PT_TICKET_NEVER,
    # spelled out rather than imported: braunschweig.synthesis must not import from
    # braunschweig.popsim (the one-way layering that
    # braunschweig.popsim.sources.entd_attributes relies on to import safely FROM
    # braunschweig.synthesis without a cycle). If PT_TICKET_NEVER's value ever changes,
    # this literal must be updated to match.
    logger.info(
        "[incommuters] pt_subscription_type: %d in-commuter persons hard-coded to "
        "'never_pt' (control-external source; the 14+ resident Kreis control does not "
        "see them -- see ADR-0099 for the rationale, issue #329)", len(persons))
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


def build_incommuter_fleet(person_ids, modes, orig_ars, income_eur, ages, data_path, rng):
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
    ages : per-agent owner age in years (all modes, agent-ordered; the donor-age
        constant-40 substitution of ``_build_persons`` is applied upstream — those
        cars land in the 40-49 holder-age class through that pre-existing fallback,
        kept visible by the tilt's per-batch rate log).
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
    ages = np.asarray(ages, dtype=float)
    if len(ages) != len(person_ids):
        raise ValueError(
            f"build_incommuter_fleet: ages ({len(ages)}) and person_ids "
            f"({len(person_ids)}) must be agent-aligned."
        )

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
        "owner_age": ages[car_mask],
    })

    # Derive a deterministic integer seed from the in-commuter rng so the fleet
    # draw is reproducible and decoupled from the rng's mutable internal state.
    fleet_seed = int(rng.integers(0, 2**31 - 1))
    # sample_fleet returns a 3-tuple (df_spec, df_types, validation_summary) under
    # consistency_v2 (default True) and the legacy 2-tuple otherwise; handle both
    # robustly (mirrors braunschweig.synthesis.vehicles.cars.household).
    #
    # population_label: with the all-Kreise 46251 tables (ADR-0081) an in-commuter
    # now finds its REAL home-Kreis fuel/euro mix, so this block is no longer
    # expected at ~100% national fallback -- the label keeps its fallback-rate log
    # distinguishable from the residents' one.
    _fleet_result = fleet.sample_fleet(
        df_cars, data_path, random_seed=fleet_seed,
        population_label="in-commuters (origin outside ZGB)")
    if len(_fleet_result) == 3:
        df_spec, df_vehicle_types, _ = _fleet_result
    else:
        df_spec, df_vehicle_types = _fleet_result

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
        mode_target=pd.DataFrame(columns=["direction", "mode", "share_pct_target"]),
        od_target=pd.DataFrame(columns=["ars5", "direction", "n_target"]))


#: Every module whose source decides a value of the in-commuter frames this stage returns.
#: synpp hashes only THIS module's source, so each of them could otherwise be edited while a
#: warm cache kept serving in-commuters built under the previous rule (#327 gate):
#: ``demand`` expands the OD flows into agents and mints their ids, ``gate_assignment`` picks
#: every agent's cordon gate, ``mode_balancer`` decides the realised mode split,
#: ``mode_reference`` owns the distance bands and the mode restriction, ``plans`` owns the
#: shared plan repairs, ``mikrozensus.reference`` maps an origin ARS to the Bundesland whose
#: mode reference is applied, and ``constants`` carries ``ROUTED_DETOUR_FACTOR``, with which
#: every straight-line distance here is converted.
_HELPER_MODULES = (_plans, _constants, _demand, _gate_assignment, _mode_balancer,
                   _mode_reference, _mikrozensus_reference, _pt_connections, _day_end_margin)

#: The same requirement for the five helpers reached through FUNCTION-LEVEL imports, hashed by
#: dotted NAME because no module object exists at this file's top level: ``pt_reachability``
#: (imported locally to break the documented cycle above) weights the entry stations,
#: ``incommuter_origins`` places the origins, ``network`` owns the clip-signature check,
#: ``external_workplaces`` supplies the Gemeinden the destinations are drawn in, and
#: ``fleet_sampling_de`` draws the in-commuters' vehicles.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.cordon.incommuter_origins",
    "braunschweig.data.cordon.network",
    "braunschweig.data.cordon.pt_reachability",
    "braunschweig.data.external_workplaces",
    "braunschweig.synthesis.vehicles.fleet_sampling_de",
    # The rest of this stage's import closure: modules its helpers import, whose code this
    # stage runs without importing it itself (tests/test_audit_synpp_helper_hash.py, ADR-0136).
    "braunschweig.data.cordon.external_points",
    "braunschweig.data.kba.feasible_fuels",
    "braunschweig.data.kba.fleet_tables",
    "braunschweig.data.kba.hsn_tsn",
    "braunschweig.ipf.joint_age_size",
    "braunschweig.synthesis.vehicles.age_income",
    "braunschweig.synthesis.vehicles.fleet_validation",
    "braunschweig.synthesis.vehicles.hbefa",
    "braunschweig.synthesis.vehicles.segment",
    "braunschweig.synthesis.vehicles.wohnmobile_age",
    # The timetable check of the pt days (#488, function-level imports of _pt_check): the batch pt router and
    # its connection rule, the schedule reader, the walk limit (plan_check) and the end of the simulated day
    # (plan_validation, with its own imports).
    "braunschweig.data.transit.connections",
    "braunschweig.data.transit.schedule",
    "braunschweig.matsim.simulation.plan_check",
    "braunschweig.popsim.plan_validation",
    "braunschweig.popsim.closure_dwell",
    "data.hts.hts",
)


def validate(context):
    """Include shared plan repairs in synpp's stage cache identity.

    A deferred module that fails to import raises rather than being skipped: skipping it
    would keep the stale cached in-commuters alive exactly when the helper is broken.

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it).
    """
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def configure(context):
    context.config("cordon_enabled", False)
    context.config("random_seed")
    if not context.config("cordon_enabled"):
        return
    context.config("cordon_incommuter_time_chronology", True)
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
    # Per-Bundesland in-commuter mode reference (#129).  When True, each in-commuter
    # draws its commute mode from its origin Bundesland's Mikrozensus reference
    # (GENESIS 12251-0105/0106) instead of one national blend; origin Laender missing
    # from the margin CSVs fall back to the national reference (logged).  Default True:
    # the traceable regional reference is used by default.  Set to False to reproduce
    # the single-national-reference behaviour (byte-identical for the same seed).
    context.config("cordon_incommuter_mode_reference_by_bundesland", True)
    # Declared unconditionally (independent of real_origin) so execute() can verify the
    # pre-clipped osm/cordon ring was built with the configured source buffer
    # (verify_clip_signature) -- a stale clip would silently change the road extent.
    context.config("cordon_network_source_buffer_m", 45000.0)
    context.config("osm_path", "osm")
    context.config("data_path")
    # Timetable check of the pt days (#488): the batch pt router on the pre-cut network and schedule, as for the
    # portal pt stays. The day-end margin keys are read for the bound of the shift (the population writer moves a
    # day that ends later than the end of the simulated day minus the margin).
    context.config(_pt_connections.KEY_ENABLED, _pt_connections.DEFAULT_ENABLED)
    # Donor re-draw until the day fits the bound (#467); the bound needs the day-end margin keys in any case.
    validate_time_redraw_max_attempts(context.config(KEY_TIME_REDRAW_MAX_ATTEMPTS, DEFAULT_TIME_REDRAW_MAX_ATTEMPTS))
    # Re-balancing of the in-commuter pt count after the timetable check (#467; active with the balancer and the check).
    pt_share_tolerance = context.config(KEY_PT_SHARE_TOLERANCE, DEFAULT_PT_SHARE_TOLERANCE)
    if not 0.0 <= float(pt_share_tolerance) < 1.0:
        raise ValueError(f"{KEY_PT_SHARE_TOLERANCE} must be in [0, 1) (relative), got {pt_share_tolerance}")
    rebalance_max_iterations = context.config(KEY_REBALANCE_MAX_ITERATIONS, DEFAULT_REBALANCE_MAX_ITERATIONS)
    if int(rebalance_max_iterations) < 0:
        raise ValueError(f"{KEY_REBALANCE_MAX_ITERATIONS} must be >= 0, got {rebalance_max_iterations}")
    context.config(_day_end_margin.KEY_ENABLED, _day_end_margin.DEFAULT_ENABLED)
    context.config(_day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S, _day_end_margin.DEFAULT_LAST_ARRIVAL_MARGIN_S)
    if context.config(_pt_connections.KEY_ENABLED):
        _pt_connections.shift_offsets_s(
            float(context.config(_pt_connections.KEY_SHIFT_WINDOW_S, _pt_connections.DEFAULT_SHIFT_WINDOW_S)),
            float(context.config(_pt_connections.KEY_SHIFT_STEP_S, _pt_connections.DEFAULT_SHIFT_STEP_S)))
        context.config(_pt_connections.KEY_WARN_SHARE, _pt_connections.DEFAULT_WARN_SHARE)
        # Station re-draw before the switch to car (#467): entry stations tried in total per pt in-commuter.
        n_station_candidates = context.config(_pt_connections.KEY_STATION_CANDIDATES,
                                              _pt_connections.DEFAULT_STATION_CANDIDATES)
        _pt_connections.validate_station_candidates(n_station_candidates)
        from braunschweig.data.transit import connections as _connections  # noqa: PLC0415
        _connections.configure(context)
        context.stage("matsim.runtime.eqasim")
        context.stage("matsim.runtime.java")
        context.config("processes", volatile=True)
        context.config("matsim_threads", None, volatile=True)
        context.stage("matsim.scenario.supply.processed")
        context.stage("matsim.scenario.supply.gtfs")
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
    # German MiD donor (not ENTD): in-commuter trip TIMING comes from German
    # behaviour. See braunschweig.data.hts.mid_donor + the design spec.
    context.stage("braunschweig.data.hts.mid_donor", alias="hts")
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

    from braunschweig.data.mikrozensus.reference import (
        build_mode_reference_by_bundesland, load_commute_mode_by_distance,
        source_bundeslaender)

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

    # Resolve the BA-Pendler flows once (reused for the OD-target frame inside
    # build_incommuter_frames and to derive the source Bundeslaender below).
    flows = context.stage("braunschweig.data.census.pendler")

    # Per-Bundesland mode reference (#129, default ON).  Build ONLY the references for
    # the Bundeslaender actually present among the in-commuter source Kreise (fewer IPF
    # fits); origin Laender without a reference fall back to national inside
    # build_incommuter_frames (logged).  When the flag is off, pass None so the mode
    # draw is byte-identical to the single-national-reference behaviour.
    if bool(context.config("cordon_incommuter_mode_reference_by_bundesland")):
        present_bundeslaender = source_bundeslaender(flows["orig_ars"])
        mode_reference_by_bundesland = build_mode_reference_by_bundesland(
            context.config("data_path"), bundeslaender=present_bundeslaender)
        print(
            f"[braunschweig.synthesis.incommuters] per-Bundesland mode reference ON: "
            f"built references for {len(mode_reference_by_bundesland)}/"
            f"{len(present_bundeslaender)} source Bundeslaender "
            f"{present_bundeslaender} (others -> national fallback)",
            flush=True,
        )
    else:
        mode_reference_by_bundesland = None

    frames = build_incommuter_frames(
        flows=flows,
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
        mode_reference_by_bundesland=mode_reference_by_bundesland,
        repair_chronology=context.config("cordon_incommuter_time_chronology"),
        pt_check=_pt_check(context) if context.config(_pt_connections.KEY_ENABLED) else None,
        time_bound_s=_time_bound_s(context),
        time_redraw_max_attempts=int(context.config(KEY_TIME_REDRAW_MAX_ATTEMPTS)),
        pt_share_tolerance=float(context.config(KEY_PT_SHARE_TOLERANCE)),
        rebalance_max_iterations=int(context.config(KEY_REBALANCE_MAX_ITERATIONS)),
    )
    print(f"[braunschweig.synthesis.incommuters] {len(frames['persons'])} in-commuters "
          f"injected ({(frames['trips']['mode'] == 'pt').sum() // 2} PT, rest car)")
    return frames


def _time_bound_s(context):
    """The latest allowed return arrival (s) of an in-commuter day: the end of the simulated day minus the day-end
    margin when it is enabled. One number for the donor re-draw (``time_bound_s``) and ``PtCheck.bound_s``."""
    bound_s = float(MAX_PLAN_TIME_SECONDS)
    if context.config(_day_end_margin.KEY_ENABLED):
        bound_s -= _day_end_margin.check_margin(context.config(_day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S), bound_s)
    return bound_s


def _pt_check(context):
    """The timetable check of the pt days (#488): the batch pt router of ``braunschweig.data.transit.connections``
    on the pre-cut network and schedule, its connection rule over all stops of the schedule (an in-commuter's home
    station may lie outside the cut extent), and the shift bound = end of the simulated day minus the day-end margin.
    """
    import functools
    import os
    from braunschweig.data.transit import connections  # noqa: PLC0415
    from braunschweig.data.transit.schedule import read_schedule  # noqa: PLC0415
    from braunschweig.matsim.simulation import plan_check  # noqa: PLC0415

    supply = context.stage("matsim.scenario.supply.processed")
    supply_dir = context.path("matsim.scenario.supply.processed")
    schedule_path = os.path.join(supply_dir, supply["schedule_path"])
    vehicles_path = os.path.join(context.path("matsim.scenario.supply.gtfs"),
                                 context.stage("matsim.scenario.supply.gtfs")["vehicles_path"])
    config_path = connections.write_router_config(
        os.path.join(context.path(), "incommuter_pt_router_config.xml"),
        network_path=os.path.join(supply_dir, supply["network_path"]), schedule_path=schedule_path,
        vehicles_path=vehicles_path)
    router = functools.partial(
        connections.route_batch, context, config_path=config_path,
        threads=int(context.config("matsim_threads") or context.config("processes")),
        work_dir=os.path.join(context.path(), "incommuter_pt_router"))
    all_stop_ids = set(read_schedule(schedule_path).stops)
    walk_max_m = float(plan_check.DEFAULT_WALK_MAX_DISTANCE_M)

    def probe(tasks):
        trips, legs = router(tasks)
        return connections.connection_mask(trips, legs, inside_stop_ids=all_stop_ids, walk_max_m=walk_max_m,
                                           task_ids=tasks["identifier"].astype(str))

    bound_s = _time_bound_s(context)
    return _pt_connections.PtCheck(
        probe=probe, shift_window_s=float(context.config(_pt_connections.KEY_SHIFT_WINDOW_S)),
        shift_step_s=float(context.config(_pt_connections.KEY_SHIFT_STEP_S)), bound_s=bound_s,
        warn_share=float(context.config(_pt_connections.KEY_WARN_SHARE)),
        station_candidates=int(context.config(_pt_connections.KEY_STATION_CANDIDATES)))
