"""Evaluate the three arms of the direct parking-zone smoke (OFF, LEGACY ring, ZONES).

Writes three snake_case CSV tables into --out-dir:

mode_shares_by_arm.csv
    arm, scope, mode, trips, share -- final-iteration trips (eqasim_trips.csv.gz) overall, for trips ending
    within 8 km of Braunschweig Hbf (the legacy ring) and for trips ending inside any parking zone polygon of the
    committed release (spatial join on the destination coordinates, EPSG:25832).
zone_outcomes_chosen_car_trips.csv
    zone_id, zone_type, outcome, car_trips, mean_cost_eur -- the ZONES arm's CHOSEN car trips of the final
    iteration priced with the Python reference braunschweig.parking.cost (the same algorithm the Java side
    runs, pinned by the shared golden cases); arrival = destination activity start, departure = its end, the
    last activity uses the terminal-stay rule. The Java outcome CSV counts evaluated CANDIDATES instead.
centre_paid_share_comparison.csv
    universe, car_arrivals, paid_share -- share of chosen car arrivals in the Braunschweig centre zones
    (bs_zone_* street zones of ParkGO) with a paid outcome, for all car arrivals and for persons whose home lies
    within 8 km of Braunschweig Hbf (APPROXIMATION of "Braunschweig residents"), next to the SrV 2023
    paid_share_overall. A COMPARISON with a universe caveat (SrV: usual centre parking of Braunschweig
    residents, all places incl. garages), never a validation.
"""
from __future__ import annotations

import argparse
import gzip
import math
import re
import sys
from pathlib import Path

import pandas as pd

HBF_E, HBF_N, RING_M = 605170.0, 5790274.0, 8000.0
PERSON_RE = re.compile(r'^\s*<person id="([^"]+)"')
ACTIVITY_RE = re.compile(r'^\s*<activity type="([^"]+)"')
PLAN_RE = re.compile(r"^\s*<plan[\s>]")
ATTRIBUTE_RE = re.compile(r'<attribute name="([^"]+)" class="[^"]+">([^<]*)</attribute>')
CENTRE_ZONE_PREFIX = "bs_zone_"


def read_parking_attributes(population: Path):
    """(person, activity_index) -> {parkingZone, parkingFree}; person -> residentParkingZone."""
    activity_attrs, person_attrs = {}, {}
    person, in_plan, index = None, False, -1
    with gzip.open(population, "rt", encoding="utf-8") as stream:
        for line in stream:
            m = PERSON_RE.match(line)
            if m:
                person, in_plan, index = int(m.group(1)), False, -1
                continue
            if PLAN_RE.match(line):
                in_plan = True
                continue
            m = ACTIVITY_RE.match(line)
            if m and "interaction" not in m.group(1):
                index += 1
                continue
            m = ATTRIBUTE_RE.search(line)
            if m and person is not None:
                name, value = m.group(1), m.group(2)
                if not in_plan and name == "residentParkingZone":
                    person_attrs[person] = value
                elif in_plan and name in ("parkingZone", "parkingFree"):
                    activity_attrs.setdefault((person, index), {})[name] = value
    return activity_attrs, person_attrs


def mode_share_rows(arm: str, trips: pd.DataFrame, zones) -> list[dict]:
    import geopandas as gpd
    from shapely.geometry import Point

    rows = []
    d2 = (trips["destination_x"] - HBF_E) ** 2 + (trips["destination_y"] - HBF_N) ** 2
    points = gpd.GeoDataFrame({"_i": range(len(trips))}, crs="EPSG:25832",
                              geometry=[Point(x, y) for x, y in zip(trips["destination_x"], trips["destination_y"])])
    joined = gpd.sjoin(points, zones[["zone_id", "geometry"]], how="left", predicate="within")
    in_zone = joined.groupby("_i")["zone_id"].first().reindex(range(len(trips))).notna().to_numpy()
    for scope, mask in (("all_trips", pd.Series(True, index=trips.index)),
                        ("trips_ending_within_8km_of_hbf", d2 <= RING_M ** 2),
                        ("trips_ending_in_a_parking_zone", pd.Series(in_zone, index=trips.index))):
        subset = trips.loc[mask]
        counts = subset["mode"].value_counts()
        for mode, n in counts.items():
            rows.append({"arm": arm, "scope": scope, "mode": mode, "trips": int(n),
                         "share": round(n / len(subset), 4)})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--smoke-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--prefix", default="braunschweig_cordon_gatecheck_")
    parser.add_argument("--zones-arm", default="zones", help="directory name of the ZONES arm (probe runs use another)")
    args = parser.parse_args(argv)
    repo, smoke, out = Path(args.repo).resolve(), Path(args.smoke_dir).resolve(), Path(args.out_dir)
    zones_arm = args.zones_arm
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import prepare_zones_arm
    release = prepare_zones_arm.load_release(repo, repo / "eqasim-data" / "data", smoke / zones_arm / "tmp" / "eval_cache")
    from braunschweig.parking import cost, tariff_export
    zones = release["zones"]
    tariffs = {row["zone_id"]: tariff_export.tariff_row_to_zone(row)
               for row in release["tariffs"].to_dict(orient="records")}

    share_rows = []
    for arm, directory in (("off", "off"), ("legacy", "legacy"), ("zones", zones_arm)):
        trips = pd.read_csv(smoke / directory / "simulation_output" / "eqasim_trips.csv.gz", sep=";")
        share_rows.extend(mode_share_rows(arm, trips, zones))
    pd.DataFrame(share_rows).to_csv(out / "mode_shares_by_arm.csv", index=False, lineterminator="\n")

    activity_attrs, person_attrs = read_parking_attributes(smoke / zones_arm / f"{args.prefix}population.xml.gz")
    output = smoke / zones_arm / "simulation_output"
    trips = pd.read_csv(output / "eqasim_trips.csv.gz", sep=";")
    activities = pd.read_csv(output / "eqasim_activities.csv.gz", sep=";")
    activities = activities.set_index(["person_id", "activity_index"])
    homes = activities[activities["purpose"] == "home"].reset_index().groupby("person_id")[["x", "y"]].first()
    home_in_ring = ((homes["x"] - HBF_E) ** 2 + (homes["y"] - HBF_N) ** 2 <= RING_M ** 2)

    records = []
    car = trips[trips["mode"] == "car"]
    for trip in car.itertuples(index=False):
        key = (int(trip.person_id), int(trip.person_trip_id) + 1)
        attrs = activity_attrs.get(key, {})
        zone_id = attrs.get("parkingZone")
        if key not in activities.index:
            raise SystemExit(f"no destination activity {key} for a car trip")
        act = activities.loc[key]
        arrival = int(round(float(act["start_time"])))
        end = float(act["end_time"])
        tariff = tariffs.get(zone_id) if zone_id else None
        if zone_id and tariff is None:
            raise SystemExit(f"unknown zone {zone_id!r} on {key}")
        if math.isfinite(end):
            departure = max(arrival, int(round(end)))
        else:
            departure = cost.terminal_departure_s(arrival, tariff.fee_end_s) if tariff else arrival
        cents, outcome = cost.parking_cost_cents(
            tariff, arrival, departure, purpose=str(act["purpose"]),
            parking_free=attrs.get("parkingFree") == "true",
            resident_of_zone=zone_id is not None and person_attrs.get(key[0]) == zone_id)
        records.append({"person_id": key[0], "zone_id": zone_id or "", "zone_type": tariff.zone_type if tariff else "",
                        "purpose": str(act["purpose"]), "outcome": outcome, "cents": cents,
                        "home_within_8km": bool(home_in_ring.get(key[0], False))})
    priced = pd.DataFrame(records)
    table = (priced.groupby(["zone_id", "zone_type", "outcome"])
             .agg(car_trips=("cents", "size"), mean_cost_eur=("cents", lambda c: round(c.mean() / 100.0, 2)))
             .reset_index())
    table.to_csv(out / "zone_outcomes_chosen_car_trips.csv", index=False, lineterminator="\n")

    srv = pd.read_csv(repo / "eqasim-data/data/braunschweig/srv/srv2023_city_center_parking.csv", comment="#")
    srv_paid = float(srv.loc[srv["parking_type"] == "paid_share_overall", "share"].iloc[0])
    # The SrV question asks where Braunschweig residents usually park when they DRIVE TO the city centre, so
    # arrivals at home are outside its universe; commute arrivals follow the separate SrV commute table (the
    # employer-free draw), so the closest model universe is the non-home, non-commute arrivals of persons whose
    # home lies within 8 km of Braunschweig Hbf (APPROXIMATION of "Braunschweig residents").
    centre = priced[priced["zone_id"].str.startswith(CENTRE_ZONE_PREFIX)]
    non_home = centre[centre["purpose"] != "home"]
    non_commute = non_home[~non_home["purpose"].isin(["work", "education"])]
    rows = []
    for universe, subset in (
            ("model_all_car_arrivals_in_bs_centre_zones", centre),
            ("model_non_home_car_arrivals_in_bs_centre_zones", non_home),
            ("model_non_home_non_commute_car_arrivals_in_bs_centre_zones", non_commute),
            ("model_non_home_non_commute_car_arrivals_in_bs_centre_zones_home_within_8km",
             non_commute[non_commute["home_within_8km"]])):
        paid = subset["outcome"].str.startswith("PAID").mean() if len(subset) else float("nan")
        outside = (subset["outcome"] == "OUTSIDE_FEE_HOURS").mean() if len(subset) else float("nan")
        rows.append({"universe": universe, "car_arrivals": len(subset), "paid_share": round(paid, 4),
                     "outside_fee_hours_share": round(outside, 4)})
    rows.append({"universe": "srv2023_bs_residents_usual_centre_parking_all_places", "car_arrivals": "",
                 "paid_share": srv_paid, "outside_fee_hours_share": ""})
    pd.DataFrame(rows).to_csv(out / "centre_paid_share_comparison.csv", index=False, lineterminator="\n")
    print(pd.DataFrame(rows).to_string(index=False))
    print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
