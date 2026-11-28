"""Read-only probe on the MiD 2023 donor trips of prod100_20261031: can the diary chain alone tell that a leisure trip
on foot or by bicycle is a loop (a stroll, a dog walk), for the trips without a detailed purpose?

The rules are tested on the trips WITH a detailed leisure purpose (W_ZWD 7xx; loop-like = 710 stroll, 711 dog) and
then counted on the leisure trips WITHOUT one (paper questionnaire, children). Monday to Friday, unweighted counts.
Survey data, NOT a validation. Usage: python loop_rule_probe.py ~/prod100_20261031
"""
from __future__ import annotations

import glob
import pickle
import sys
from pathlib import Path

import pandas as pd

RUN = Path(sys.argv[1]).expanduser()
LEISURE_COARSE = {7, 10, 14, 15, 16}
LOOP_CODES = {710, 711}
pd.set_option("display.width", 220)
pd.set_option("display.float_format", lambda v: f"{v:,.3f}")


def main() -> int:
    path = glob.glob(str(RUN / "cache" / "braunschweig.data.hts.mid_donor__*.p"))[0]
    with open(path, "rb") as handle:
        _, _, trips = pickle.load(handle)
    columns = ["person_id", "trip_index", "mode", "W_ZWECK", "W_ZWD", "wegkm_imp", "trip_duration", "ST_WOTAG",
               "preceding_purpose", "activity_duration"]
    trips = trips[columns].copy()
    for column in ("W_ZWECK", "W_ZWD", "wegkm_imp", "trip_duration", "ST_WOTAG", "activity_duration"):
        trips[column] = pd.to_numeric(trips[column], errors="coerce")
    trips = trips[trips["ST_WOTAG"].between(1, 5)].sort_values(["person_id", "trip_index"])
    following = trips.groupby("person_id").shift(-1)
    trips["last"] = following["W_ZWECK"].isna()
    trips["next_home"] = following["W_ZWECK"].isin([8, 9])
    trips["from_home"] = trips["preceding_purpose"].eq("home")
    trips["zero_stay"] = trips["activity_duration"].fillna(-1).eq(0)
    speed = trips["wegkm_imp"] / (trips["trip_duration"] / 3600.0).where(trips["trip_duration"] > 0)
    valid = (trips["wegkm_imp"] > 0) & (trips["wegkm_imp"] < 2000) & trips["W_ZWECK"].isin(LEISURE_COARSE)
    rules = {
        "R1 last trip of the day": trips["last"],
        "R2 starts at home, no way home follows": trips["from_home"] & ~trips["next_home"],
        "R3 starts at home and is the last trip": trips["from_home"] & trips["last"],
        "R4 R3 or (starts at home, stay of 0 min, no way home)":
            (trips["from_home"] & trips["last"]) | (trips["from_home"] & trips["zero_stay"] & ~trips["next_home"]),
        "R5 R3 and 20 min or longer": trips["from_home"] & trips["last"] & (trips["trip_duration"] >= 1200),
    }
    for mode, slow in (("walk", 6.0), ("bicycle", 25.0)):
        sub = trips[valid & (trips["mode"] == mode)]
        labelled = sub[sub["W_ZWD"].between(700, 799)]
        unlabelled = sub[~sub["W_ZWD"].between(500, 799)]
        loop = labelled["W_ZWD"].isin(LOOP_CODES)
        print(f"=== {mode}: leisure trips {len(sub)}; with detail {len(labelled)} (loop-like 710/711: {int(loop.sum())} "
              f"= {100.0 * loop.mean():.1f} %); without detail {len(unlabelled)}")
        rows = []
        for name, mask in rules.items():
            hit = mask.reindex(labelled.index)
            rows.append({
                "rule": name, "fires_on_labelled": int(hit.sum()),
                "precision_loop": float((hit & loop).sum() / max(hit.sum(), 1)),
                "recall_loop": float((hit & loop).sum() / max(loop.sum(), 1)),
                "fires_on_unlabelled": int(mask.reindex(unlabelled.index).sum()),
                "share_of_unlabelled": float(mask.reindex(unlabelled.index).mean()),
            })
        print(pd.DataFrame(rows).to_string(index=False))
        by_code = labelled.assign(R3=rules["R3 starts at home and is the last trip"].reindex(labelled.index))
        table = by_code.groupby("W_ZWD").agg(n=("R3", "size"), share_R3=("R3", "mean"),
                                             share_last=("last", "mean"), share_next_home=("next_home", "mean"))
        print("per detailed purpose (n >= 200):")
        print(table[table["n"] >= 200].to_string())
        _ = slow, speed
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
