"""#575 group-3 summary: freeze cause per frozen leg from the per-leg table of i575_group3_frozen_cause.py.

Usage: python i575_group3_summary.py [path to group3_frozen_cause.csv.gz]
(default: the committed table next to this script).
"""
import sys
from pathlib import Path

import pandas as pd

source = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("group3_frozen_cause.csv.gz")
f = pd.read_csv(source)
fr = f[f["frozen"].eq(True)].copy()


def cause(s):
    """The routing modes of the trips that exit the extent in the leg's tour, as one label."""
    parts = [p for p in str(s).split(";") if p]
    exits = {p.split(">")[0] for p in parts if p.endswith(">outside")}
    if not parts:
        return "no_outside_in_tour"
    if exits == {"pt"}:
        return "pt_trip_cut"
    if "pt" in exits:
        return "pt_and_" + "_".join(sorted(exits - {"pt"})) + "_cut"
    return "_".join(sorted(exits)) + "_cut"


fr["cause"] = fr["tour_outside"].map(cause)
print("frozen legs by cause:", fr["cause"].value_counts().to_dict())
print("frozen legs, uncut plan has outside:", fr["uncut_outside_in_plan"].value_counts().to_dict())
print("frozen legs, prepared mode:", fr["prepared_mode"].value_counts().to_dict())
print("not frozen, prepared mode:", f[f["frozen"].eq(False)]["prepared_mode"].value_counts().to_dict())
print("distance km frozen p50/p90:", fr["distance_km"].quantile([0.5, 0.9]).round(1).tolist())
print(fr.sample(5, random_state=1)[["identifier", "distance_km", "prepared_mode", "tour_outside"]].to_string())
