"""Plan-frame builders for cross-cordon in-commuter agents.

Ported from the proven ``feature/cordon-incommuters`` branch (verbatim, region-
neutral): given per-agent times and coordinates they build the resident-schema
trips/activities/locations frames so injected in-commuters concatenate cleanly
with the synthesized residents. They are generic -- the gate-based design simply
passes the GATE coordinates as the "home" point and the gate network-entry time
as ``depart_home_s`` (see ``braunschweig.data.cordon.gate_entry``). Mode + donor
timing helpers are included too.

See ``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point


def assign_fixed_mode(distance_km, reference, band_of, rng):
    """Fixed travel mode per agent, drawn from the mode distribution of the
    agent's commute-distance band. ``reference`` maps band -> {mode: prob};
    ``band_of(d)`` maps a distance to its band; ``rng`` a numpy Generator.
    Returns a list of modes in input order."""
    out = []
    for d in distance_km:
        dist = reference[band_of(float(d))]
        modes = list(dist.keys())
        probs = np.array([dist[m] for m in modes], dtype=float)
        probs = probs / probs.sum()
        probs[-1] = max(0.0, 1.0 - probs[:-1].sum())   # clamp float residue
        out.append(modes[int(rng.choice(len(modes), p=probs))])
    return out


def assign_fixed_mode_per_agent(distance_km, references, band_of, rng):
    """Fixed travel mode per agent, each drawn from that agent's OWN mode reference.

    Like :func:`assign_fixed_mode`, but ``references`` is a per-agent list of
    ``{band: {mode: prob}}`` dicts (one per agent, in the same order as
    ``distance_km``). Used by the in-commuter synthesis to draw each agent's mode from
    its origin Bundesland's reference. The inner draw logic is identical to
    ``assign_fixed_mode``, so passing a homogeneous list ``[R] * n`` reproduces
    ``assign_fixed_mode(distance_km, R, band_of, rng)`` exactly (same rng consumption,
    agent-sequential). Returns a list of modes in input order.

    Raises ValueError if ``references`` and ``distance_km`` differ in length.
    """
    distance_km = list(distance_km)
    references = list(references)
    if len(references) != len(distance_km):
        raise ValueError(
            f"references length {len(references)} != distance_km length "
            f"{len(distance_km)}")
    out = []
    for d, reference in zip(distance_km, references):
        dist = reference[band_of(float(d))]
        modes = list(dist.keys())
        probs = np.array([dist[m] for m in modes], dtype=float)
        probs = probs / probs.sum()
        probs[-1] = max(0.0, 1.0 - probs[:-1].sum())   # clamp float residue
        out.append(modes[int(rng.choice(len(modes), p=probs))])
    return out


def select_commuter_donors(hts_persons, hts_trips, person_col):
    """HTS persons usable as in-commuter donors: employed AND with a home->work
    trip. Returns the qualifying subset; raises ValueError if none qualify."""
    work_person_ids = set(hts_trips.loc[hts_trips["following_purpose"] == "work", person_col])
    qualifies = hts_persons["employed"] & hts_persons[person_col].isin(work_person_ids)
    donors = hts_persons[qualifies].reset_index(drop=True)
    if donors.empty:
        raise ValueError("no commuter donors: no employed HTS person has a home-to-work trip")
    return donors


def sample_donors(donors, n, rng):
    """Sample ``n`` donor rows WITH REPLACEMENT (commuters far exceed the pool).
    Returns a fresh-indexed DataFrame preserving all donor columns."""
    idx = rng.integers(0, len(donors), size=n)
    return donors.iloc[idx].reset_index(drop=True)


def build_incommuter_trips(person_ids, depart_home_s, arrive_work_s,
                           depart_work_s, arrive_home_s):
    """Trips frame for a Home->Work->Home day (2 trips/agent) in the upstream
    resident trips schema. Times in seconds since midnight. ``mode`` is attached
    by the caller. Sorted by (person_id, trip_index)."""
    person_ids = np.asarray(person_ids)
    n = len(person_ids)
    depart_home_s = np.asarray(depart_home_s, dtype=float)
    arrive_work_s = np.asarray(arrive_work_s, dtype=float)
    depart_work_s = np.asarray(depart_work_s, dtype=float)
    arrive_home_s = np.asarray(arrive_home_s, dtype=float)
    outbound = pd.DataFrame({
        "person_id": person_ids, "trip_index": 0,
        "departure_time": depart_home_s, "arrival_time": arrive_work_s,
        "preceding_purpose": "home", "following_purpose": "work",
        "is_first_trip": True, "is_last_trip": False,
        "trip_duration": arrive_work_s - depart_home_s,
        "activity_duration": depart_work_s - arrive_work_s,
    })
    inbound = pd.DataFrame({
        "person_id": person_ids, "trip_index": 1,
        "departure_time": depart_work_s, "arrival_time": arrive_home_s,
        "preceding_purpose": "work", "following_purpose": "home",
        "is_first_trip": False, "is_last_trip": True,
        "trip_duration": arrive_home_s - depart_work_s,
        "activity_duration": np.full(n, np.nan),
    })
    trips = pd.concat([outbound, inbound], ignore_index=True)
    return trips.sort_values(["person_id", "trip_index"]).reset_index(drop=True)


def build_incommuter_activities(person_ids, depart_home_s, arrive_work_s,
                                depart_work_s, arrive_home_s):
    """Activities frame for a Home->Work->Home day (3 activities/agent) in the
    upstream resident activities schema. First activity start_time and last
    end_time are NaN (day open/close). Sorted by (person_id, activity_index)."""
    person_ids = np.asarray(person_ids)
    n = len(person_ids)
    depart_home_s = np.asarray(depart_home_s, dtype=float)
    arrive_work_s = np.asarray(arrive_work_s, dtype=float)
    depart_work_s = np.asarray(depart_work_s, dtype=float)
    arrive_home_s = np.asarray(arrive_home_s, dtype=float)
    home_start = pd.DataFrame({
        "person_id": person_ids, "activity_index": 0, "trip_index": 0,
        "purpose": "home", "start_time": np.full(n, np.nan), "end_time": depart_home_s,
        "is_first": True, "is_last": False, "duration": np.full(n, np.nan),
    })
    work = pd.DataFrame({
        "person_id": person_ids, "activity_index": 1, "trip_index": 1,
        "purpose": "work", "start_time": arrive_work_s, "end_time": depart_work_s,
        "is_first": False, "is_last": False, "duration": depart_work_s - arrive_work_s,
    })
    home_end = pd.DataFrame({
        "person_id": person_ids, "activity_index": 2, "trip_index": -1,
        "purpose": "home", "start_time": arrive_home_s, "end_time": np.full(n, np.nan),
        "is_first": False, "is_last": True, "duration": np.full(n, np.nan),
    })
    acts = pd.concat([home_start, work, home_end], ignore_index=True)
    return acts.sort_values(["person_id", "activity_index"]).reset_index(drop=True)


def straight_line_distance_km(x1, y1, x2, y2):
    """Euclidean distance (metric CRS, metres) in KILOMETRES. Scalars or arrays."""
    dx = np.asarray(x2, dtype=float) - np.asarray(x1, dtype=float)
    dy = np.asarray(y2, dtype=float) - np.asarray(y1, dtype=float)
    return np.sqrt(dx * dx + dy * dy) / 1000.0


def build_incommuter_locations(person_ids, home_x, home_y, work_x, work_y,
                               work_location_id, crs):
    """Per-activity location GeoDataFrame for Home->Work->Home days (3 rows/agent).
    Home activities carry location_id = -1 (eqasim home placeholder) at the home
    (gate) point; work carries the work-pool id. object-dtype location_id matches
    the resident spatial-locations convention. Sorted by (person_id, activity_index)."""
    person_ids = np.asarray(person_ids)
    home_x = np.asarray(home_x, dtype=float)
    home_y = np.asarray(home_y, dtype=float)
    work_x = np.asarray(work_x, dtype=float)
    work_y = np.asarray(work_y, dtype=float)
    work_location_id = list(work_location_id)
    rows = []
    for i, pid in enumerate(person_ids):
        rows.append((pid, 0, -1, Point(home_x[i], home_y[i])))
        rows.append((pid, 1, work_location_id[i], Point(work_x[i], work_y[i])))
        rows.append((pid, 2, -1, Point(home_x[i], home_y[i])))
    frame = pd.DataFrame(rows, columns=["person_id", "activity_index", "location_id", "geometry"])
    frame = frame.sort_values(["person_id", "activity_index"]).reset_index(drop=True)
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=crs)


def extract_commute_times(trips):
    """Donor Home->Work->Home timings (seconds since midnight) from HTS ``trips``:
    (depart_home, arrive_work, depart_work, arrive_home). Falls back to first/last
    trip if a clean leg is absent so four ordered times are always returned."""
    t = trips.sort_values("departure_time").reset_index(drop=True)
    outbound = t[(t["preceding_purpose"] == "home") & (t["following_purpose"] == "work")]
    inbound = t[(t["preceding_purpose"] == "work") & (t["following_purpose"] == "home")]
    first = t.iloc[0]
    last = t.iloc[-1]
    ob = outbound.iloc[0] if len(outbound) else first
    ib = inbound.iloc[-1] if len(inbound) else last
    return (float(ob["departure_time"]), float(ob["arrival_time"]),
            float(ib["departure_time"]), float(ib["arrival_time"]))
