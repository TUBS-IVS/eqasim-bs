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
from braunschweig.data.cordon.gate_entry import gate_entry_time_s
from braunschweig.data.cordon.mode_reference import MID_DISTANCE_EDGES, route_distance_band
from braunschweig.data.cordon.plans import (
    assign_fixed_mode, build_incommuter_activities, build_incommuter_locations,
    build_incommuter_trips, extract_commute_times, sample_donors,
    select_commuter_donors, straight_line_distance_km)

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
        DataFrame [source_ars5, stop_id, x, y], one row per external entry stop.
    """
    ids = list(stops)
    pts = gpd.GeoDataFrame({"stop_id": ids},
                           geometry=[Point(stops[i]) for i in ids], crs=kreise.crs)
    joined = gpd.sjoin(pts, kreise[["ars5", "geometry"]], predicate="within", how="left")
    joined = joined.drop_duplicates(subset="stop_id")
    stop_kreis = dict(zip(joined["stop_id"], joined["ars5"]))
    entry = direct_ride_stops(routes, stop_kreis, zgb_kreise)
    rows = []
    for ars5, stop_set in entry.items():
        for stop_id in stop_set:
            x, y = stops[stop_id]
            rows.append((ars5, stop_id, x, y))
    return pd.DataFrame(rows, columns=["source_ars5", "stop_id", "x", "y"])


def build_incommuter_frames(flows, zgb_kreise, sampling_rate, gates, assignment,
                            zgb_work, mode_reference, hts_persons,
                            hts_trips, person_col, n_residents, n_resident_households,
                            rng, band_edges=MID_DISTANCE_EDGES, gate_speed_kmh=30.0,
                            detour_factor=1.3, pt_entry_stops=None):
    """Assemble every in-commuter frame.

    Returns a dict with keys persons, trips, activities, locations, vehicles,
    households. Deterministic given ``rng``. Home activities are tagged ``outside``
    (eqasim fixes their mode); PT agents board at their Kreis's nearest PT entry stop.
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

    # 2) workplace inside the destination ZGB Kreis (employment-weighted).
    work_x, work_y, work_loc = _sample_workplaces(agents["dest_ars"].to_numpy(), zgb_work, rng)

    # 3) fixed mode from the Mikrozensus reference by commute-distance band (gate->work).
    dist_km = straight_line_distance_km(gate_x, gate_y, work_x, work_y)
    modes = assign_fixed_mode(
        dist_km, mode_reference,
        lambda d: route_distance_band(d, detour_factor=detour_factor, edges=band_edges), rng)

    # 3b) PT agents board at the nearest PT entry stop of their Kreis (if any).
    home_x, home_y = _pt_home_coords(orig_ars, modes, gate_x, gate_y, pt_entry_stops)

    # 4) timings from HTS donors; gate-entry = work arrival - in-ZGB travel.
    donors = sample_donors(select_commuter_donors(hts_persons, hts_trips, person_col), n, rng)
    arrive_work, depart_work, depart_home, arrive_home = _agent_times(
        donors, hts_trips, person_col, dist_km, gate_speed_kmh, detour_factor)

    trips = build_incommuter_trips(person_ids, depart_home, arrive_work, depart_work, arrive_home)
    trips["mode"] = np.repeat(np.asarray(modes), 2)
    activities = build_incommuter_activities(person_ids, depart_home, arrive_work,
                                             depart_work, arrive_home)
    activities.loc[activities["purpose"] == "home", "purpose"] = "outside"
    locations = build_incommuter_locations(person_ids, home_x, home_y, work_x, work_y,
                                           work_loc, zgb_work.crs)

    persons = _build_persons(ids, donors, person_col, modes)
    households = _build_households(ids)
    vehicles = _build_vehicles(person_ids, modes)
    return dict(persons=persons, trips=trips, activities=activities,
                locations=locations, vehicles=vehicles, households=households)


def _sample_workplaces(dest_ars, zgb_work, rng):
    """Sample an employment-weighted ZGB workplace per agent within its dest Kreis."""
    w = zgb_work[~zgb_work["commune_id"].astype(str).str.startswith("EXT")].copy()
    w["kreis"] = w["commune_id"].astype(str).str[:5]
    by_kreis = {k: sub for k, sub in w.groupby("kreis")}
    xs, ys, ids = [], [], []
    for dest in dest_ars:
        pool = by_kreis.get(str(dest), w)
        prob = pool["employees"].to_numpy(dtype=float)
        prob = prob / prob.sum() if prob.sum() > 0 else None
        row = pool.iloc[int(rng.choice(len(pool), p=prob))]
        xs.append(row.geometry.x); ys.append(row.geometry.y); ids.append(row["location_id"])
    return np.array(xs, dtype=float), np.array(ys, dtype=float), ids


def _pt_home_coords(orig_ars, modes, gate_x, gate_y, pt_entry_stops):
    """Home coords = gate point, except PT agents board at their Kreis's nearest PT stop."""
    home_x = np.array(gate_x, dtype=float).copy()
    home_y = np.array(gate_y, dtype=float).copy()
    if pt_entry_stops is None or len(pt_entry_stops) == 0:
        return home_x, home_y
    by_kreis = {k: sub for k, sub in pt_entry_stops.groupby("source_ars5")}
    for i, (ars5, mode) in enumerate(zip(orig_ars, modes)):
        if mode == "pt" and ars5 in by_kreis:
            sub = by_kreis[ars5]
            d = (sub["x"].to_numpy() - gate_x[i]) ** 2 + (sub["y"].to_numpy() - gate_y[i]) ** 2
            row = sub.iloc[int(np.argmin(d))]
            home_x[i] = float(row["x"]); home_y[i] = float(row["y"])
    return home_x, home_y


def _agent_times(donors, hts_trips, person_col, dist_km, speed_kmh, detour_factor):
    """Per-agent (arrive_work, depart_work, depart_home, arrive_home) seconds."""
    by_person = {pid: sub for pid, sub in hts_trips.groupby(person_col)}
    arrive_work, depart_work, arrive_home = [], [], []
    for _, donor in donors.iterrows():
        _dh, aw, dw, ah = extract_commute_times(by_person[donor[person_col]])
        arrive_work.append(aw); depart_work.append(dw); arrive_home.append(ah)
    arrive_work = np.array(arrive_work, dtype=float)
    depart_work = np.array(depart_work, dtype=float)
    arrive_home = np.array(arrive_home, dtype=float)
    depart_home = np.array([gate_entry_time_s(aw, dkm, speed_kmh, detour_factor)
                            for aw, dkm in zip(arrive_work, dist_km)], dtype=float)
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
        households=pd.DataFrame(columns=["household_id", "person_id"]))


def configure(context):
    context.config("cordon_enabled", False)
    context.config("random_seed")
    if not context.config("cordon_enabled"):
        return
    context.config("data_path")
    context.config("sampling_rate")
    context.config("braunschweig.political_prefix")
    context.config("cordon_gate_speed_kmh", 30.0)
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
        pt_entry_stops=context.stage("braunschweig.data.cordon_pt_gates"))
    print(f"[braunschweig.synthesis.incommuters] {len(frames['persons'])} in-commuters "
          f"injected ({(frames['trips']['mode'] == 'pt').sum() // 2} PT, rest car)")
    return frames
