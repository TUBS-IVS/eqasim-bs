"""Cross-cordon in-commuter (Einpendler) synthesis helpers.

This module assembles injected in-commuter agents for the cross-cordon external-
demand feature. It composes the already-tested building blocks:

  - demand:        BA-Pendler OD -> per-agent (orig/dest Kreis) counts.
  - gate_assignment: population-gravity Kreis->gate volumes + per-agent gate draw.
  - mode_reference / plans: Mikrozensus fixed mode + resident-schema plan frames.
  - gate_entry:    network-entry time at the gate (work start - in-ZGB travel).

It also derives the **PT entry stops** (rail/bus) per source Kreis from the cut
transit schedule: stops on a route that also serves a ZGB stop, i.e. a one-seat
(no-transfer) ride into the region, where PT in-commuters board.

Region-neutral; the synpp stage wires the data sources. See
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from braunschweig.data.cordon.demand import (
    expand_to_agents, make_incommuter_ids, select_inbound_flows)
from braunschweig.data.cordon.gate_assignment import sample_gate_per_agent
from braunschweig.data.cordon.mode_reference import (
    MID_DISTANCE_EDGES, restrict_to_modes, route_distance_band)
from braunschweig.data.cordon.plans import (
    assign_fixed_mode, build_incommuter_activities, build_incommuter_locations,
    build_incommuter_trips, extract_commute_times, sample_donors,
    select_commuter_donors, straight_line_distance_km)

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
_INCOMMUTER_PERSON_DEFAULTS = dict(
    studies=False, household_size=1, consumption_units=1.0,
    socioprofessional_class=6, number_of_bicycles=0, number_of_cars=1,
    bicycle_availability="all", license_type="ja", has_license=True,
    has_pt_subscription=False, pt_subscription_type="fahre_nie",
    household_income="3000", household_income_eur=3000, high_income=False,
    is_bs_resident=False, is_urban_resident=False, age_range="higher_education",
)


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
    halt (see :func:`_pt_home_coords`). A stop appearing on the same physical line in
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
                            detour_factor=1.3, pt_entry_stops=None,
                            commute_modes=("car", "pt"),
                            pt_min_zgb_routes=2, pt_prefer_rail=True):
    """Assemble every in-commuter frame.

    Returns a dict with keys persons, trips, activities, locations, vehicles,
    households. Deterministic given ``rng``. Home activities are tagged ``outside``
    (eqasim fixes their mode); PT agents board at a well-connected (regional-rail /
    multi-line) one-seat entry stop near their gate -- ``pt_min_zgb_routes`` is the
    multi-line threshold and ``pt_prefer_rail`` whether rail stations are preferred
    (see :func:`_pt_home_coords`).
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
    modes = assign_fixed_mode(
        dist_km, restricted_reference,
        lambda d: route_distance_band(d, detour_factor=detour_factor, edges=band_edges), rng)

    # 3b) PT agents board at the nearest PT entry stop of their Kreis (if any). The
    # primary/fallback split is counted + logged inside _pt_home_coords (the returned
    # counts are not threaded further here; the home coords drawn are unchanged).
    home_x, home_y, _pt_home_counts = _pt_home_coords(
        orig_ars, modes, gate_x, gate_y, pt_entry_stops,
        min_zgb_routes=pt_min_zgb_routes, prefer_rail=pt_prefer_rail)

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

    persons = _build_persons(ids, donors, person_col, modes)
    households = _build_households(ids)
    vehicles = _build_vehicles(person_ids, modes)
    # Per-agent validation record (one row per in-commuter): source Kreis, direction,
    # fixed mode, and the GATE it enters through (gate coords, not the PT-moved home),
    # for the per-run commuter_validation + gate-flow outputs.
    validation = pd.DataFrame({
        "ars5": orig_ars, "direction": "ein", "mode": np.asarray(modes),
        "gate_id": gate_ids, "gate_x": gate_x, "gate_y": gate_y,
    })
    return dict(persons=persons, trips=trips, activities=activities,
                locations=locations, vehicles=vehicles, households=households,
                validation=validation)


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


def _nearest_index(sx, sy, x, y):
    """Index of the (sx, sy) point nearest to (x, y) by squared Euclidean distance."""
    return int(np.argmin((sx - x) ** 2 + (sy - y) ** 2))


def _major_mask(sub, min_zgb_routes, prefer_rail):
    """Boolean mask over ``sub`` rows that are "major" (well-connected) entry stops.

    A stop is major when it is a regional-rail station (``is_rail``, only counted if
    ``prefer_rail``) OR sits on at least ``min_zgb_routes`` distinct ZGB-serving routes
    (a multi-line hub / higher frequency). If the connectivity columns are absent
    (legacy frames built before this feature), no stop can be classed major and the
    selection degrades to the old nearest-overall behaviour.
    """
    if "n_zgb_routes" not in sub.columns and "is_rail" not in sub.columns:
        return np.zeros(len(sub), dtype=bool)
    mask = np.zeros(len(sub), dtype=bool)
    if prefer_rail and "is_rail" in sub.columns:
        mask = mask | sub["is_rail"].to_numpy(dtype=bool)
    if "n_zgb_routes" in sub.columns:
        mask = mask | (sub["n_zgb_routes"].to_numpy(dtype=float) >= float(min_zgb_routes))
    return mask


def _pt_home_coords(orig_ars, modes, gate_x, gate_y, pt_entry_stops,
                    min_zgb_routes=2, prefer_rail=True):
    """Home coords = road gate for car; PT agents board at a REAL PT entry stop.

    Each PT in-commuter's candidate pool is the one-seat-to-ZGB entry stops of its own
    source Kreis (PRIMARY); if its Kreis has none, the pool is ALL entry stops anywhere
    (Kreis FALLBACK -- never the road gate; PT must use real transit data). Within the
    pool, the agent PREFERS a "major" (well-connected) stop -- a regional-rail station
    (``is_rail``, when ``prefer_rail``) or a multi-line hub on >= ``min_zgb_routes``
    distinct ZGB-serving routes -- and boards the NEAREST such major stop to its road
    gate. This replaces the pure geometric nearest-stop rule, which could pick a single
    low-frequency village bus halt over a nearby regional-rail station. If the pool has
    NO major stop, the agent boards the nearest stop overall (still a one-seat ride into
    ZGB -- the previous behaviour). Only if there are no PT entry stops at all (no
    regional GTFS) does the road gate remain (WORST fallback).

    Selection is fully deterministic (nearest by squared Euclidean distance, no RNG). If
    the connectivity columns (``n_zgb_routes`` / ``is_rail``) are absent (legacy callers
    / test frames), every stop is treated as minor and the behaviour is exactly the old
    nearest-stop rule.

    The outcomes are counted per PT agent and both the primary/fallback Kreis split and
    the major/minor connectivity split are logged (with a "WARNING: " prefix when the
    non-primary or minor share is high) so a systematic PT-stop key mismatch -- or a
    surprising lack of well-connected entry stops -- is visible rather than silent
    (CLAUDE.md "Fallback transparency").

    Returns ``(home_x, home_y, counts)`` where ``counts`` is a dict with integer keys
    ``own_kreis_stop`` (primary Kreis), ``nearest_anywhere_stop`` (Kreis fallback),
    ``road_gate`` (worst fallback), ``major_stop`` (rail / multi-line stop chosen),
    ``minor_stop`` (nearest-only stop chosen), and ``pt_agents`` (the PT total).
    """
    home_x = np.array(gate_x, dtype=float).copy()
    home_y = np.array(gate_y, dtype=float).copy()
    counts = {"own_kreis_stop": 0, "nearest_anywhere_stop": 0, "road_gate": 0,
              "major_stop": 0, "minor_stop": 0, "pt_agents": 0}
    n_pt = int(np.sum(np.asarray(modes) == "pt"))
    counts["pt_agents"] = n_pt
    if pt_entry_stops is None or len(pt_entry_stops) == 0:
        # No PT entry stops at all -> every PT agent keeps the road gate (worst fallback).
        counts["road_gate"] = n_pt
        _log_pt_home_fallback(counts)
        return home_x, home_y, counts
    by_kreis = {k: sub for k, sub in pt_entry_stops.groupby("source_ars5")}
    for i, (ars5, mode) in enumerate(zip(orig_ars, modes)):
        if mode != "pt":
            continue
        sub = by_kreis.get(ars5)
        if sub is not None:
            counts["own_kreis_stop"] += 1
        else:
            sub = pt_entry_stops
            counts["nearest_anywhere_stop"] += 1
        sx = sub["x"].to_numpy(dtype=float)
        sy = sub["y"].to_numpy(dtype=float)
        major = _major_mask(sub, min_zgb_routes, prefer_rail)
        if major.any():
            # Prefer well-connected (rail / multi-line) stops: nearest AMONG the major ones.
            idx = np.flatnonzero(major)
            j = idx[_nearest_index(sx[idx], sy[idx], gate_x[i], gate_y[i])]
            counts["major_stop"] += 1
        else:
            # No major stop in the pool -> nearest overall (still a one-seat ride to ZGB).
            j = _nearest_index(sx, sy, gate_x[i], gate_y[i])
            counts["minor_stop"] += 1
        home_x[i] = float(sx[j]); home_y[i] = float(sy[j])
    _log_pt_home_fallback(counts)
    return home_x, home_y, counts


def _log_pt_home_fallback(counts):
    """Log the PT-boarding Kreis and connectivity splits as rates (CLAUDE.md transparency).

    Two orthogonal splits are reported per PT agent:

    * Kreis split: primary = boards a one-seat entry stop of its OWN source Kreis;
      non-primary = nearest-anywhere stop fallback + road-gate (no-GTFS) worst fallback.
      A high non-primary share means a systematic PT-stop -> source-Kreis key mismatch.
    * Connectivity split: major = boards a regional-rail / multi-line hub; minor = no
      major stop in the pool, so the nearest stop overall was used. A high minor share
      means few well-connected one-seat entry stops were available (or the connectivity
      columns were absent, e.g. a legacy frame).

    A high non-primary OR minor share is prefixed "WARNING: " so either issue is visible
    rather than silent.
    """
    n_pt = counts["pt_agents"]
    if n_pt == 0:
        return
    primary = counts["own_kreis_stop"]
    nearest = counts["nearest_anywhere_stop"]
    gate = counts["road_gate"]
    major = counts.get("major_stop", 0)
    minor = counts.get("minor_stop", 0)
    non_primary = nearest + gate
    primary_share = 100.0 * primary / n_pt
    non_primary_share = 100.0 * non_primary / n_pt
    major_share = 100.0 * major / n_pt
    minor_share = 100.0 * minor / n_pt
    level = "WARNING: " if (non_primary_share > 5.0 or minor_share > 50.0) else ""
    print(
        f"[braunschweig.incommuters] {level}PT boarding: "
        f"Kreis split primary {primary}/{n_pt} ({primary_share:.1f}%) own-Kreis entry "
        f"stop, fallback {non_primary} ({non_primary_share:.1f}%) = "
        f"{nearest} nearest-anywhere stop + {gate} road gate (no PT entry stop in their "
        f"Kreis); connectivity split major {major} ({major_share:.1f}%) rail/multi-line "
        f"hub, minor {minor} ({minor_share:.1f}%) nearest-only (no major stop in pool). "
        f"A high fallback rate means a PT-stop vs source-Kreis key mismatch; a high minor "
        f"rate means few well-connected one-seat entry stops were available.",
        flush=True,
    )


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


def _build_persons(ids, donors, person_col, modes):
    """Persons frame for injected in-commuters with the resident attribute schema."""
    age = donors["age"].to_numpy() if "age" in donors.columns else 40
    sex = donors["sex"].to_numpy() if "sex" in donors.columns else "male"
    hts_id = donors[person_col].to_numpy() if person_col in donors.columns else -1
    persons = pd.DataFrame({
        "person_id": ids["person_id"].to_numpy(),
        "household_id": ids["household_id"].to_numpy(),
        "census_person_id": ids["person_id"].to_numpy(),
        "census_household_id": ids["household_id"].to_numpy(),
        "hts_id": hts_id, "hts_household_id": -1,
        "age": age, "sex": sex, "employed": True,
        "subpopulation": "incommuter",
        "car_availability": np.where(np.asarray(modes) == "car", "all", "none"),
    })
    for key, value in _INCOMMUTER_PERSON_DEFAULTS.items():
        persons[key] = value
    return persons


def _build_households(ids):
    """One single-person household per injected in-commuter."""
    return pd.DataFrame({
        "household_id": ids["household_id"].to_numpy(),
        "person_id": ids["person_id"].to_numpy(),
        "census_household_id": ids["household_id"].to_numpy(),
        "household_income": "3000", "high_income": False,
        "car_availability": "all", "bicycle_availability": "all",
    })


def _build_vehicles(person_ids, modes):
    """One car vehicle per car-mode in-commuter (others use PT/walk/bike).

    Columns match the resident vehicles frame (synthesis.vehicles.cars.default) so
    the concat-wrapper appends cleanly: owner_id, mode, vehicle_id, type_id, critair,
    technology, age, euro.
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


def _empty_frames(crs):
    """Zero-row frames (the flag-OFF / no-agent path) so the merge is a perfect no-op."""
    return dict(
        persons=pd.DataFrame(columns=["person_id"]),
        trips=pd.DataFrame(columns=["person_id"]),
        activities=pd.DataFrame(columns=["person_id"]),
        locations=gpd.GeoDataFrame({"person_id": []}, geometry=[], crs=crs),
        vehicles=pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"]),
        households=pd.DataFrame(columns=["household_id", "person_id"]),
        validation=pd.DataFrame(columns=["ars5", "direction", "mode", "gate_id",
                                         "gate_x", "gate_y"]))


def configure(context):
    context.config("cordon_enabled", False)
    context.config("random_seed")
    if not context.config("cordon_enabled"):
        return
    context.config("data_path")
    context.config("sampling_rate")
    context.config("braunschweig.political_prefix")
    context.config("cordon_gate_speed_kmh", 30.0)
    # PT in-commuter boarding-stop selection (CLAUDE.md cordon PT realism): prefer
    # well-connected one-seat entry stops (regional rail / multi-line hubs) over the
    # geometrically nearest stop. ``cordon_pt_min_zgb_routes`` is the multi-line
    # threshold (>= this many distinct ZGB-serving routes -> "major"); when
    # ``cordon_pt_prefer_rail`` is True a regional-rail station also counts as major.
    context.config("cordon_pt_min_zgb_routes", 2)
    context.config("cordon_pt_prefer_rail", True)
    context.stage("braunschweig.synthesis.cordon_gates")
    context.stage("braunschweig.data.cordon_pt_gates")
    context.stage("braunschweig.data.census.pendler")
    context.stage("braunschweig.locations.work")
    context.stage("braunschweig.synthesis.population.enriched")  # RAW, for n_residents
    context.stage("data.hts.selected", alias="hts")


def execute(context):
    crs = "EPSG:25832"
    if not context.config("cordon_enabled"):
        return _empty_frames(crs)

    from braunschweig.data.mikrozensus.reference import load_commute_mode_by_distance

    gate_volume = context.stage("braunschweig.synthesis.cordon_gates")
    residents = context.stage("braunschweig.synthesis.population.enriched")
    _hts_households, hts_persons, hts_trips = context.stage("hts")
    rng = np.random.default_rng(int(context.config("random_seed")) + 100000)

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
        pt_min_zgb_routes=int(context.config("cordon_pt_min_zgb_routes")),
        pt_prefer_rail=bool(context.config("cordon_pt_prefer_rail")))
    print(f"[braunschweig.synthesis.incommuters] {len(frames['persons'])} in-commuters "
          f"injected ({(frames['trips']['mode'] == 'pt').sum() // 2} PT, rest car)")
    return frames
