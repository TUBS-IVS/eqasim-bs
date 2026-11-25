"""Read-only probe on prod100_20261031: which MiD trips make the long walk and bicycle tail of the secondary
distance layers, and how often is a synthesised leg longer (straight line) than the path its own diary trip reports.

Part 1: MiD 2023 donor trips (``braunschweig.data.hts.mid_donor``), walk and bicycle, Monday to Friday (the universe
of the distance layers, ADR-0116), W_GEW-weighted shares, grouped by the detailed purpose W_ZWD (codebook labels).
Part 2: synthesis trips joined with the written trips: share of legs whose realised straight-line distance exceeds
the reported path length of their own diary trip (wegkm_imp = diary euclidean_distance * detour factor 1.3).

Model output and survey data of one run, NOT a validation. Usage on felix: python sampling_tail_probe.py ~/prod100_20261031
"""
from __future__ import annotations

import glob
import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from distance_probe import GPKG, PRIMARY, TRIPS, od_length  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.float_format", lambda v: f"{v:,.2f}")

RUN = Path(sys.argv[1]).expanduser()
DETOUR = 1.3
GROUPS = {
    710: "710 Spaziergang/Spazierfahrt", 711: "711 Hund ausfuehren", 704: "704 Sport aktiv",
    708: "708/709/722 Ausflug, Urlaub, Kurzreise", 709: "708/709/722 Ausflug, Urlaub, Kurzreise",
    722: "708/709/722 Ausflug, Urlaub, Kurzreise", 701: "701 Besuch", 716: "716 Begleitung Kinder",
}


def group_of(code: float, coarse: float) -> str:
    if code in GROUPS:
        return GROUPS[code]
    if 700 <= code < 800:
        return "other leisure detail"
    if 500 <= code < 700:
        return "shop or errand detail"
    if coarse == 8:
        return "no detail: W_ZWECK 8 nach Hause"
    if coarse == 9:
        return "no detail: W_ZWECK 9 Rueckweg"
    if coarse in (7, 14, 15, 16, 10):
        return "no detail: leisure/other (PAPI, child, k.A.)"
    return "no detail: other purposes"


def part_1() -> None:
    path = glob.glob(str(RUN / "cache" / "braunschweig.data.hts.mid_donor__*.p"))[0]
    with open(path, "rb") as handle:
        _, _, trips = pickle.load(handle)
    trips = trips[["mode", "W_ZWECK", "W_ZWD", "wegkm_imp", "trip_duration", "W_GEW", "ST_WOTAG",
                   "preceding_purpose", "following_purpose"]].copy()
    for column in ("W_ZWECK", "W_ZWD", "wegkm_imp", "trip_duration", "W_GEW", "ST_WOTAG"):
        trips[column] = pd.to_numeric(trips[column], errors="coerce")
    trips = trips[(trips["wegkm_imp"] > 0) & (trips["wegkm_imp"] < 2000) & trips["ST_WOTAG"].between(1, 5)]
    trips["group"] = [group_of(code, coarse) for code, coarse in zip(trips["W_ZWD"], trips["W_ZWECK"])]
    trips["home_home"] = trips["preceding_purpose"].eq("home") & trips["following_purpose"].eq("home")
    print("=== part 1: MiD donor trips Monday-Friday with a valid wegkm_imp:", len(trips))
    for mode, limit in (("walk", 6.5), ("bicycle", 26.0)):
        sub = trips[trips["mode"] == mode]
        total_w, total_km = sub["W_GEW"].sum(), (sub["W_GEW"] * sub["wegkm_imp"]).sum()
        long = sub[sub["wegkm_imp"] > limit]
        rows = []
        for group, part in sub.groupby("group"):
            tail = part[part["wegkm_imp"] > limit]
            rows.append({
                "group": group, "n": len(part), "share_of_trips": part["W_GEW"].sum() / total_w,
                "share_of_km": (part["W_GEW"] * part["wegkm_imp"]).sum() / total_km,
                "km_p50": part["wegkm_imp"].median(), "km_p90": part["wegkm_imp"].quantile(0.9),
                "min_p50": part["trip_duration"].median() / 60.0,
                "n_long": len(tail), "share_of_long": tail["W_GEW"].sum() / max(long["W_GEW"].sum(), 1e-9),
            })
        table = pd.DataFrame(rows).sort_values("share_of_long", ascending=False)
        print(f"--- {mode}: {len(sub)} trips, path > {limit} km (about {limit / DETOUR:.0f} km straight line): "
              f"{len(long)} trips = {100.0 * long['W_GEW'].sum() / total_w:.2f} % of trips, "
              f"{100.0 * (long['W_GEW'] * long['wegkm_imp']).sum() / total_km:.1f} % of {mode} km")
        print(table.to_string(index=False))
        hh = sub[sub["home_home"]]
        print(f"{mode} home -> home trips: {len(hh)} ({100.0 * hh['W_GEW'].sum() / total_w:.2f} % of trips), "
              f"path km p50 {hh['wegkm_imp'].median():.2f}, p90 {hh['wegkm_imp'].quantile(0.9):.2f}, "
              f"long {int((hh['wegkm_imp'] > limit).sum())}")
        speed = sub["wegkm_imp"] / (sub["trip_duration"] / 3600.0).where(sub["trip_duration"] > 0)
        slow_limit = 2.0 if mode == "walk" else 6.0
        slow = sub[(speed < slow_limit) & (sub["trip_duration"] >= 3600)]
        print(f"{mode} trips of 60 min or more with a path speed below {slow_limit:.0f} km/h: {len(slow)} "
              f"({100.0 * slow['W_GEW'].sum() / total_w:.2f} % of trips); groups "
              f"{slow['group'].value_counts().head(5).to_dict()}")


def part_2() -> None:
    with open(TRIPS, "rb") as handle:
        trips = pickle.load(handle)
    trips = trips[["person_id", "trip_index", "mode", "euclidean_distance", "preceding_purpose",
                   "following_purpose"]].copy()
    connection = sqlite3.connect(str(GPKG))
    table = connection.execute("select table_name from gpkg_contents where data_type = 'features'").fetchone()[0]
    column = connection.execute("select column_name from gpkg_geometry_columns where table_name = ?",
                                (table,)).fetchone()[0]
    rows = [(p, i, od_length(b)) for p, i, b in connection.execute(
        f'select person_id, trip_index, "{column}" from "{table}"')]
    written = pd.DataFrame(rows, columns=["person_id", "trip_index", "synth_m"])
    frame = trips.merge(written, on=["person_id", "trip_index"], how="inner")
    outside = frame["preceding_purpose"].eq("outside") | frame["following_purpose"].eq("outside")
    frame = frame[~outside & frame["euclidean_distance"].notna() & (frame["euclidean_distance"] > 0)].copy()
    frame["path_m"] = frame["euclidean_distance"] * DETOUR
    frame["over_path"] = frame["synth_m"] > frame["path_m"]
    frame["over_2x"] = frame["synth_m"] > 2.0 * frame["path_m"]
    frame["secondary"] = ~(frame["preceding_purpose"].isin(PRIMARY) & frame["following_purpose"].isin(PRIMARY))
    print("=== part 2: realised straight line longer than the path the own diary trip reports")
    for label, part in (("trips with a secondary end", frame[frame["secondary"]]),
                        ("trips between home, work and education", frame[~frame["secondary"]])):
        summary = part.groupby("mode").agg(trips=("over_path", "size"), over_path=("over_path", "mean"),
                                           over_2x=("over_2x", "mean"))
        summary.loc["all"] = [len(part), part["over_path"].mean(), part["over_2x"].mean()]
        print(f"--- {label}")
        print(summary.to_string())


if __name__ == "__main__":
    part_1()
    part_2()
