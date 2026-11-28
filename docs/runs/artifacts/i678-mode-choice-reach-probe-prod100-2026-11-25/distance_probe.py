"""Read-only probe on prod100_20261031: secondary distance layers and diary versus synthesised trip distances.

Part 1 prints, per purpose and for walk and pt, the travel-time bin a 150 min (walk) / 120 min (pt) diary trip falls
into in the run's distance layers (``braunschweig.popsim.distance_distributions``) and the distances that bin holds.

Part 2 joins the synthesis trips that go to MATSim (``braunschweig.synthesis.outside_tour_modes.stage``: mode, diary
distance ``euclidean_distance`` = wegkm_imp / detour factor, diary duration) with the realised straight-line distance
of the written trips (``output/*_trips.gpkg``) and compares both per mode.

Model output of one run, NOT a validation. Usage on felix:
  python distance_probe.py ~/prod100_20261031
"""
from __future__ import annotations

import math
import pickle
import sqlite3
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_rows", 300)
pd.set_option("display.float_format", lambda v: f"{v:,.2f}")

RUN = Path(sys.argv[1]).expanduser()
CACHE = RUN / "cache"
DISTANCES = CACHE / "braunschweig.popsim.distance_distributions__9741273ebd5e3c56ac5ddb2b944ffadb.p"
TRIPS = CACHE / "braunschweig.synthesis.outside_tour_modes.stage__e4615b7799e3661dd55de11d3ef273ff.p"
GPKG = RUN / "output" / "braunschweig_100pct_prod20261031_trips.gpkg"
PRIMARY = {"home", "work", "education"}
T0 = time.time()


def weighted_quantiles(values, weights, quantiles):
    order = np.argsort(values)
    v, w = np.asarray(values, dtype=float)[order], np.asarray(weights, dtype=float)[order]
    cumulative = np.cumsum(w) / w.sum()
    return [float(v[min(int(np.searchsorted(cumulative, q)), len(v) - 1)]) for q in quantiles]


def part_1() -> None:
    with open(DISTANCES, "rb") as handle:
        layers = pickle.load(handle)
    by_mode = all(isinstance(v, dict) and "bounds" in v for v in layers.values())
    if by_mode:
        layers = {"(all purposes)": layers}
    print("=== part 1: distance layers; purposes:", list(layers.keys()))
    for purpose, modes in layers.items():
        inner = list(modes.keys()) if isinstance(modes, dict) else type(modes).__name__
        print(f"  layer {purpose!r}: {inner}")
    rows = []
    for purpose, modes in layers.items():
        if not isinstance(modes, dict):
            continue
        for mode, travel_time in (("walk", 9000.0), ("pt", 7200.0)):
            if mode not in modes or "bounds" not in modes[mode]:
                continue
            bounds = np.asarray(modes[mode]["bounds"], dtype=float)
            index = int(np.count_nonzero(travel_time > bounds))
            chosen = modes[mode]["distributions"][index]
            values = np.asarray(chosen["values"], dtype=float) / 1000.0
            weights = np.asarray(chosen["weights"], dtype=float)
            p50, p90, p99 = weighted_quantiles(values, weights, (0.5, 0.9, 0.99))
            share = lambda limit: float(weights[values > limit].sum() / weights.sum())
            rows.append({
                "purpose": purpose, "mode": mode, "n_bins": len(bounds), "bin_index": index,
                "bin_from_min": (bounds[index - 1] / 60.0) if index > 0 else 0.0,
                "bin_to_min": bounds[index] / 60.0, "n_obs": len(values),
                "km_p50": p50, "km_p90": p90, "km_p99": p99, "km_max": float(values.max()),
                "share_over_5km": share(5.0), "share_over_10km": share(10.0), "share_over_20km": share(20.0),
            })
    print(pd.DataFrame(rows).to_string(index=False))


def od_length(blob: bytes) -> float:
    """Straight-line length between the first and the last point of a GeoPackage line string blob."""
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 7]
    offset = 8 + envelope
    order = "<" if blob[offset] == 1 else ">"
    geometry_type, n_points = struct.unpack_from(order + "II", blob, offset + 1)
    if geometry_type % 1000 != 2:
        raise ValueError(f"not a line string: WKB type {geometry_type}")
    dimension = 2 + (1 if geometry_type // 1000 in (1, 3) else 0) + (1 if geometry_type // 1000 in (2, 3) else 0)
    x0, y0 = struct.unpack_from(order + "dd", blob, offset + 9)
    x1, y1 = struct.unpack_from(order + "dd", blob, offset + 9 + (n_points - 1) * 8 * dimension)
    return math.hypot(x1 - x0, y1 - y0)


def describe(frame: pd.DataFrame, label: str) -> None:
    rows = []
    for mode, group in frame.groupby("mode"):
        row = {"mode": mode, "n": len(group)}
        for name in ("diary_km", "synth_km"):
            series = group[name]
            row.update({f"{name}_mean": series.mean(), f"{name}_p50": series.quantile(0.5),
                        f"{name}_p90": series.quantile(0.9), f"{name}_p99": series.quantile(0.99)})
        rows.append(row)
    print(f"--- {label}")
    print(pd.DataFrame(rows).to_string(index=False))


def part_2() -> None:
    with open(TRIPS, "rb") as handle:
        trips = pickle.load(handle)
    if isinstance(trips, (tuple, list)):
        print("trips object is a sequence of", [type(t).__name__ for t in trips])
        trips = next(t for t in trips if isinstance(t, pd.DataFrame) and "mode" in t.columns)
    print(f"=== part 2: trips {trips.shape} loaded in {time.time() - T0:.0f} s")
    print("columns with mode/outside/portal:", [c for c in trips.columns
                                                  if any(k in c.lower() for k in ("mode", "outside", "portal"))])
    keep = ["person_id", "trip_index", "mode", "euclidean_distance", "trip_duration", "preceding_purpose",
            "following_purpose", "HP_ALTER"]
    trips = trips[[c for c in keep if c in trips.columns]].copy()
    print("persons:", trips["person_id"].nunique(), "modes:", trips["mode"].value_counts().to_dict())

    connection = sqlite3.connect(str(GPKG))
    table = connection.execute("select table_name from gpkg_contents where data_type = 'features'").fetchone()[0]
    column = connection.execute("select column_name from gpkg_geometry_columns where table_name = ?",
                                (table,)).fetchone()[0]
    persons, indices, lengths = [], [], []
    for person_id, trip_index, blob in connection.execute(
            f'select person_id, trip_index, "{column}" from "{table}"'):
        persons.append(person_id)
        indices.append(trip_index)
        lengths.append(od_length(blob) if blob is not None else float("nan"))
    written = pd.DataFrame({"person_id": persons, "trip_index": indices, "synth_m": lengths})
    print(f"written trips {len(written)} read in {time.time() - T0:.0f} s; without geometry "
          f"{int(written['synth_m'].isna().sum())}")

    keys = ["person_id", "trip_index"]
    print("duplicate keys: synthesis", int(trips.duplicated(keys).sum()), "written", int(written.duplicated(keys).sum()),
          "; key dtypes", trips[keys].dtypes.to_dict(), written[keys].dtypes.to_dict())
    frame = trips.merge(written, on=keys, how="inner")
    print(f"joined {len(frame)} of {len(trips)} synthesis trips and {len(written)} written trips")
    frame["diary_km"] = frame["euclidean_distance"] / 1000.0
    frame["synth_km"] = frame["synth_m"] / 1000.0
    frame["duration_min"] = frame["trip_duration"] / 60.0
    outside = frame["preceding_purpose"].eq("outside") | frame["following_purpose"].eq("outside")
    print("trips at an outside activity (left out below):", int(outside.sum()))
    frame = frame[~outside & frame["diary_km"].notna() & frame["synth_km"].notna()].copy()
    frame["secondary"] = ~(frame["preceding_purpose"].isin(PRIMARY) & frame["following_purpose"].isin(PRIMARY))

    describe(frame, "all trips: diary distance (wegkm_imp / detour factor) vs realised straight line, km")
    describe(frame[frame["secondary"]], "trips with a secondary end")
    describe(frame[~frame["secondary"]], "trips between home, work and education only")

    distance_bins = [0, 1, 2, 5, 10, 20, np.inf]
    labels = ["<=1", "1-2", "2-5", "5-10", "10-20", ">20"]
    for mode in ("walk", "bicycle"):
        sub = frame[frame["mode"] == mode].copy()
        if sub.empty:
            continue
        sub["diary_band"] = pd.cut(sub["diary_km"], distance_bins, labels=labels, include_lowest=True)
        sub["synth_band"] = pd.cut(sub["synth_km"], distance_bins, labels=labels, include_lowest=True)
        print(f"--- {mode}: diary band (rows) x realised band (columns), trips")
        print(pd.crosstab(sub["diary_band"], sub["synth_band"], margins=True).to_string())
        sub["duration_class"] = pd.cut(sub["duration_min"], [0, 15, 30, 60, 120, np.inf],
                                       labels=["<=15", "15-30", "30-60", "60-120", ">120"], include_lowest=True)
        limit = 5.0 if mode == "walk" else 20.0
        speed_limit = 6.0 if mode == "walk" else 25.0
        sub["over"] = sub["synth_km"] > limit
        sub["speed_kmh"] = sub["synth_km"] / (sub["duration_min"] / 60.0).where(sub["duration_min"] > 0)
        sub["too_fast"] = sub["speed_kmh"] > speed_limit
        table_ = sub.groupby("duration_class").agg(
            trips=("over", "size"), over=("over", "sum"), diary_over=("diary_km", lambda s: int((s > limit).sum())),
            too_fast=("too_fast", "sum"))
        table_["share_over"] = table_["over"] / table_["trips"]
        print(f"--- {mode}: by diary duration (min); over = realised straight line > {limit:.0f} km; "
              f"too_fast = realised straight line / diary duration > {speed_limit:.0f} km/h")
        print(table_.to_string())
        over = sub[sub["over"]]
        print(f"{mode} > {limit:.0f} km realised: {len(over)} trips of {len(sub)} "
              f"({100.0 * len(over) / len(sub):.2f} %), persons {over['person_id'].nunique()}; "
              f"with a secondary end {int(over['secondary'].sum())}, primary only {int((~over['secondary']).sum())}; "
              f"diary distance of these: median {over['diary_km'].median():.2f} km, "
              f"share with diary <= 2 km {100.0 * (over['diary_km'] <= 2).mean():.1f} %")
        print(f"{mode} > {limit:.0f} km in the DIARY: {int((sub['diary_km'] > limit).sum())} trips")
        pairs = (over["preceding_purpose"] + " > " + over["following_purpose"]).value_counts().head(10)
        print(f"top purpose pairs of {mode} > {limit:.0f} km realised:", pairs.to_dict())
    ratio = frame[frame["secondary"] & (frame["diary_km"] > 0.2)]
    ratio = (ratio["synth_km"] / ratio["diary_km"])
    print("--- trips with a secondary end and diary > 0.2 km: realised / diary distance ratio quantiles")
    print(ratio.quantile([0.05, 0.25, 0.5, 0.75, 0.95, 0.99]).to_string())
    print(f"share with ratio > 3: {100.0 * (ratio > 3).mean():.1f} %, ratio < 1/3: {100.0 * (ratio < 1 / 3).mean():.1f} %")
    print(f"done in {time.time() - T0:.0f} s")


if __name__ == "__main__":
    part_1()
    part_2()
