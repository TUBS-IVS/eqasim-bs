"""Read-only probe on the MiD 2023 donor trips of prod100_20261031: how are strolls, dog walks and active sport
(W_ZWD 710, 711, 704) recorded - as one trip that is the whole loop, or as a trip out followed by a trip back?

Per mode (walk, bicycle), Monday to Friday: what follows such a trip in the same person's diary, and how long the
following way home is compared with it. Survey data, NOT a validation. Usage: python loop_probe.py ~/prod100_20261031
"""
from __future__ import annotations

import glob
import pickle
import sys
from pathlib import Path

import pandas as pd

RUN = Path(sys.argv[1]).expanduser()
LOOP_CODES = {710: "710 stroll", 711: "711 dog", 704: "704 active sport"}


def main() -> int:
    path = glob.glob(str(RUN / "cache" / "braunschweig.data.hts.mid_donor__*.p"))[0]
    with open(path, "rb") as handle:
        _, _, trips = pickle.load(handle)
    columns = ["person_id", "trip_index", "mode", "W_ZWECK", "W_ZWD", "wegkm_imp", "trip_duration", "ST_WOTAG",
               "is_last_trip", "preceding_purpose", "following_purpose", "activity_duration"]
    trips = trips[columns].copy()
    for column in ("W_ZWECK", "W_ZWD", "wegkm_imp", "trip_duration", "ST_WOTAG", "activity_duration"):
        trips[column] = pd.to_numeric(trips[column], errors="coerce")
    trips = trips[trips["ST_WOTAG"].between(1, 5)].sort_values(["person_id", "trip_index"])
    following = trips.groupby("person_id").shift(-1)
    trips["next_zweck"] = following["W_ZWECK"]
    trips["next_km"] = following["wegkm_imp"]
    trips["next_mode"] = following["mode"]
    valid = (trips["wegkm_imp"] > 0) & (trips["wegkm_imp"] < 2000)
    for mode in ("walk", "bicycle"):
        for code, label in LOOP_CODES.items():
            sub = trips[valid & (trips["mode"] == mode) & (trips["W_ZWD"] == code)]
            if len(sub) < 50:
                continue
            last = sub["next_zweck"].isna()
            home_next = sub["next_zweck"].isin([8, 9])
            from_home = sub["preceding_purpose"].eq("home")
            pair = sub[home_next & (sub["next_km"] > 0) & (sub["next_km"] < 2000)]
            ratio = pair["next_km"] / pair["wegkm_imp"]
            stay = sub["activity_duration"]
            print(f"--- {mode}, {label}: {len(sub)} trips; starts at home {100.0 * from_home.mean():.1f} %; "
                  f"last trip of the day {100.0 * last.mean():.1f} %; followed by a way home or back "
                  f"{100.0 * home_next.mean():.1f} %; followed by another purpose "
                  f"{100.0 * (~last & ~home_next).mean():.1f} %")
            print(f"    stay after the trip (min): share 0 = {100.0 * (stay.fillna(-1) == 0).mean():.1f} %, "
                  f"median {stay.median() / 60.0:.0f}")
            if len(pair):
                print(f"    way home after it: same mode {100.0 * (pair['next_mode'] == mode).mean():.1f} %; "
                      f"length ratio home/out p25 {ratio.quantile(0.25):.2f}, p50 {ratio.median():.2f}, "
                      f"p75 {ratio.quantile(0.75):.2f}; share with ratio 0.8-1.25 "
                      f"{100.0 * ratio.between(0.8, 1.25).mean():.1f} %")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
